"""Celery tasks for asynchronous inbound messaging processing and drivers (Findings 1, 2, 3).

Enables true FAST-ACK 202 on webhook ingress by moving heavy orchestration
(user creation, LLM, Truth Store facts, matching, tailoring) into background worker tasks.
Provides runtime driver tasks for outbox dispatch and scheduled reminder sweeping.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import redis
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.messaging.inbound import ProviderEventEnvelope, ProviderEventProcessingStatus
from tapjob_api.messaging.inbound_service import (
    INBOUND_LEASE_SECONDS,
    INBOUND_PROCESS_HARD_TIME_LIMIT_SECONDS,
    INBOUND_PROCESS_SOFT_TIME_LIMIT_SECONDS,
    InboundClaimResult,
    claim_inbound_envelope,
    mark_event_failed,
    mark_event_processed,
    mark_expired_envelope_dead_letter,
)
from tapjob_api.messaging.inbound_service import (
    MAX_INBOUND_RETRIES as MAX_INBOUND_RETRIES,
)
from tapjob_api.messaging.inbound_service import (
    is_transient_inbound_error as is_transient_inbound_error,
)
from tapjob_api.messaging.models import ChannelType
from tapjob_api.messaging.orchestration import dispatch_inbound_message
from tapjob_api.messaging.outbox_service import (
    claim_and_dispatch_outbox_batch,
)
from tapjob_api.messaging.reminders_service import dispatch_due_reminders
from tapjob_api.messaging.service import get_or_create_channel_provider, queue_response_message
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundAttachment,
    InboundMessage,
    MessagingChannel,
)
from tapjob_api.resume_import.errors import ImportLeaseActiveError
from tapjob_api.resume_import.storage import ObjectStoragePort

logger = logging.getLogger(__name__)

PROCESS_INBOUND_ENVELOPE_TASK_NAME = "messaging.process_inbound_envelope"
RECOVER_UNPROCESSED_INBOUND_ENVELOPES_TASK_NAME = "messaging.recover_unprocessed_inbound_envelopes"
DISPATCH_OUTBOX_TASK_NAME = "messaging.dispatch_outbox"
DISPATCH_DUE_REMINDERS_TASK_NAME = "messaging.dispatch_due_reminders"

_session_factory: sessionmaker[Session] | None = None
_redis_client: redis.Redis | None = None
_channel_adapter: MessagingChannel | None = None
_storage: ObjectStoragePort | None = None


def configure_messaging_worker(
    *,
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis | None = None,
    channel_adapter: MessagingChannel | None = None,
    storage: ObjectStoragePort | None = None,
) -> None:
    """Configures database session factory, redis, adapter, and storage for messaging worker
    tasks.
    """
    global _session_factory, _redis_client, _channel_adapter, _storage
    _session_factory = session_factory
    _redis_client = redis_client
    _channel_adapter = channel_adapter
    _storage = storage


def _require_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("Messaging worker session factory not configured")
    return _session_factory


BASE_INBOUND_BACKOFF_SECONDS = 2.0
MAX_INBOUND_BACKOFF_SECONDS = 60.0


def compute_inbound_backoff_seconds(attempt: int) -> float:
    """Computes bounded exponential backoff in seconds for the given attempt number (Finding 5)."""
    if attempt <= 0:
        return float(BASE_INBOUND_BACKOFF_SECONDS)
    return float(
        min(
            BASE_INBOUND_BACKOFF_SECONDS * (2 ** (attempt - 1)),
            MAX_INBOUND_BACKOFF_SECONDS,
        )
    )


@shared_task(  # type: ignore[untyped-decorator]
    name=PROCESS_INBOUND_ENVELOPE_TASK_NAME,
    soft_time_limit=INBOUND_PROCESS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=INBOUND_PROCESS_HARD_TIME_LIMIT_SECONDS,
)
def process_inbound_envelope_task(envelope_id: int) -> None:
    """Processes an inbound provider event envelope asynchronously (Findings 1, 2, 5).

    Steps:
    1. Atomically claim envelope (SELECT ... FOR UPDATE) with fresh lease token & increment
    2. Reconstruct InboundMessage from normalized_event (including attachments)
    3. Resolve ChannelProvider
    4. Execute dispatch_inbound_message (orchestration)
    5. Mark envelope as PROCESSED or FAILED fenced by lease token (with backoff)
    """
    session_factory = _require_session_factory()
    session: Session = session_factory()
    envelope: ProviderEventEnvelope | None = None
    fencing_token: str | None = None
    try:
        envelope, fencing_token, claim_result = claim_inbound_envelope(
            session,
            envelope_id=envelope_id,
            lease_seconds=INBOUND_LEASE_SECONDS,
            max_retries=MAX_INBOUND_RETRIES,
        )
        session.commit()

        if claim_result != InboundClaimResult.CLAIMED or envelope is None or fencing_token is None:
            logger.info(
                "messaging.task.claim_skipped",
                extra={"envelope_id": envelope_id, "claim_result": claim_result},
            )
            return

        # Reconstruct InboundMessage
        normalized = envelope.normalized_event or {}
        sender_external_id = normalized.get("sender_external_id") or ""
        sender_channel = normalized.get("channel") or "whatsapp"
        sender = ChannelIdentity(channel=sender_channel, external_id=sender_external_id)

        received_at_raw = normalized.get("received_at")
        received_at = (
            datetime.fromisoformat(received_at_raw) if received_at_raw else datetime.now(UTC)
        )

        attachments_list: list[InboundAttachment] = []
        for att in normalized.get("attachments", []):
            attachments_list.append(
                InboundAttachment(
                    provider_media_id=str(att.get("provider_media_id", "")),
                    mime_type=str(att.get("mime_type", "")),
                    filename=att.get("filename"),
                    size_bytes=att.get("size_bytes"),
                    kind=str(att.get("kind", "document")),
                )
            )

        inbound_msg = InboundMessage(
            sender=sender,
            provider_message_id=str(normalized.get("provider_message_id", "")),
            received_at=received_at,
            text=normalized.get("text"),
            interactive_reply_id=normalized.get("interactive_reply_id"),
            attachments=tuple(attachments_list),
        )

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name=envelope.provider,
            provider_config_key=f"{envelope.provider}_{envelope.provider_instance}",
        )

        # Run orchestration with injected adapter and storage
        dispatch_inbound_message(
            session,
            _redis_client,
            inbound=inbound_msg,
            provider_id=provider.id,
            channel_adapter=_channel_adapter,
            storage=_storage,
            envelope_id=envelope.id,
        )

        # Mark PROCESSED fenced by current worker lease token
        committed = mark_event_processed(
            session, envelope_id=envelope_id, fencing_token=fencing_token, envelope=envelope
        )
        if committed:
            session.commit()
            logger.info("messaging.task.envelope_processed", extra={"envelope_id": envelope_id})
        else:
            session.rollback()
            logger.warning(
                "messaging.task.fenced_out_processed_dropped",
                extra={"envelope_id": envelope_id, "token": fencing_token},
            )

    except SoftTimeLimitExceeded:
        session.rollback()
        logger.warning(
            "messaging.task.envelope_soft_time_limit_exceeded",
            extra={"envelope_id": envelope_id},
        )
        if envelope is not None and fencing_token is not None:
            try:
                current_attempts = envelope.processing_attempts
                is_retryable = current_attempts < MAX_INBOUND_RETRIES

                if is_retryable:
                    backoff = compute_inbound_backoff_seconds(current_attempts)
                    next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff)
                else:
                    backoff = 0.0
                    next_attempt_at = None

                error_details = {
                    "error": "soft_time_limit_exceeded",
                    "type": "SoftTimeLimitExceeded",
                    "retryable": is_retryable,
                    "attempts": current_attempts,
                    "max_attempts": MAX_INBOUND_RETRIES,
                    "backoff_seconds": backoff if is_retryable else None,
                    "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
                }
                fenced = mark_event_failed(
                    session,
                    envelope_id=envelope_id,
                    fencing_token=fencing_token,
                    error_details=error_details,
                    envelope=envelope,
                )
                if fenced:
                    session.commit()
                else:
                    session.rollback()
                    logger.warning(
                        "messaging.task.fenced_out_failed_dropped",
                        extra={"envelope_id": envelope_id, "token": fencing_token},
                    )
            except Exception:
                session.rollback()
                logger.exception(
                    "messaging.task.mark_failed_error", extra={"envelope_id": envelope_id}
                )

    except ImportLeaseActiveError as exc:
        session.rollback()
        logger.warning(
            "messaging.task.envelope_nested_lease_active",
            extra={
                "envelope_id": envelope_id,
                "retry_at": exc.retry_at.isoformat() if exc.retry_at else None,
            },
        )
        if envelope is not None and fencing_token is not None:
            try:
                # Do not consume attempt budget while waiting for nested active lease (Finding 1)
                if envelope.processing_attempts > 0:
                    envelope.processing_attempts -= 1
                current_attempts = envelope.processing_attempts
                is_retryable = True
                now = datetime.now(UTC)
                retry_target = exc.retry_at or (now + timedelta(seconds=10))
                if retry_target.tzinfo is None:
                    retry_target = retry_target.replace(tzinfo=UTC)
                next_attempt_at = retry_target + timedelta(seconds=2)
                if next_attempt_at < now:
                    next_attempt_at = now + timedelta(seconds=2)
                backoff = (next_attempt_at - now).total_seconds()

                error_details = {
                    "error": "nested_lease_active",
                    "type": "ImportLeaseActiveError",
                    "retryable": True,
                    "attempts": current_attempts,
                    "max_attempts": MAX_INBOUND_RETRIES,
                    "backoff_seconds": backoff,
                    "next_attempt_at": next_attempt_at.isoformat(),
                    "retry_at": exc.retry_at.isoformat() if exc.retry_at else None,
                }
                fenced = mark_event_failed(
                    session,
                    envelope_id=envelope_id,
                    fencing_token=fencing_token,
                    error_details=error_details,
                    envelope=envelope,
                )
                if fenced:
                    session.commit()
                else:
                    session.rollback()
                    logger.warning(
                        "messaging.task.fenced_out_failed_dropped",
                        extra={"envelope_id": envelope_id, "token": fencing_token},
                    )
            except Exception:
                session.rollback()
                logger.exception(
                    "messaging.task.mark_failed_error", extra={"envelope_id": envelope_id}
                )

    except Exception as exc:
        session.rollback()
        logger.exception(
            "messaging.task.envelope_processing_failed",
            extra={"envelope_id": envelope_id, "error": str(exc)},
        )
        if envelope is not None and fencing_token is not None:
            try:
                # Attempts was incremented durably during claim! Do not double-increment.
                current_attempts = envelope.processing_attempts
                is_transient = is_transient_inbound_error(exc)
                is_retryable = is_transient and (current_attempts < MAX_INBOUND_RETRIES)

                if is_retryable:
                    backoff = compute_inbound_backoff_seconds(current_attempts)
                    next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff)
                else:
                    backoff = 0.0
                    next_attempt_at = None

                error_details = {
                    "error": str(exc),
                    "type": type(exc).__name__,
                    "retryable": is_retryable,
                    "attempts": current_attempts,
                    "max_attempts": MAX_INBOUND_RETRIES,
                    "backoff_seconds": backoff if is_retryable else None,
                    "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
                }
                if not is_retryable and envelope.normalized_event:
                    atts = envelope.normalized_event.get("attachments") or []
                    if atts:
                        sender_ext = envelope.normalized_event.get("sender_external_id")
                        if sender_ext:
                            queue_response_message(
                                session,
                                channel_identity_link_id=None,
                                recipient_external_id=str(sender_ext),
                                message_text=(
                                    "📄 *Não conseguimos processar o documento enviado.*\n\n"
                                    "Por favor, certifique-se de que o arquivo esteja em "
                                    "formato PDF válido e tente enviá-lo novamente em instantes. 😊"
                                ),
                                idempotency_key=f"fail_env_{envelope.id}",
                            )

                fenced = mark_event_failed(
                    session,
                    envelope_id=envelope_id,
                    fencing_token=fencing_token,
                    error_details=error_details,
                    envelope=envelope,
                )
                if fenced:
                    session.commit()
                else:
                    session.rollback()
                    logger.warning(
                        "messaging.task.fenced_out_failed_dropped",
                        extra={"envelope_id": envelope_id, "token": fencing_token},
                    )
            except Exception:
                session.rollback()
                logger.exception(
                    "messaging.task.mark_failed_error", extra={"envelope_id": envelope_id}
                )
    finally:
        session.close()


@shared_task(  # type: ignore[untyped-decorator]
    name=RECOVER_UNPROCESSED_INBOUND_ENVELOPES_TASK_NAME
)
def recover_unprocessed_inbound_envelopes_task(
    stale_seconds: float = 30.0,
    limit: int = 50,
) -> int:
    """Sweeps inbound envelopes stuck in ACCEPTED or RETRYABLE FAILED status (Finding 5).

    Guarantees that broker network partitions or transient failures recover automatically.
    """
    session_factory = _require_session_factory()
    session: Session = session_factory()
    try:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=stale_seconds)

        # 1. Stale ACCEPTED envelopes
        stmt_accepted = (
            select(ProviderEventEnvelope)
            .where(
                ProviderEventEnvelope.status == ProviderEventProcessingStatus.ACCEPTED,
                ProviderEventEnvelope.received_at <= cutoff,
            )
            .order_by(ProviderEventEnvelope.received_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        raw_accepted = list(session.execute(stmt_accepted).scalars().all())
        stale_accepted = [
            env for env in raw_accepted if env.status == ProviderEventProcessingStatus.ACCEPTED
        ]

        # 2. Retryable FAILED envelopes
        stmt_failed = (
            select(ProviderEventEnvelope)
            .where(
                ProviderEventEnvelope.status == ProviderEventProcessingStatus.FAILED,
            )
            .order_by(ProviderEventEnvelope.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        failed_candidates = list(session.execute(stmt_failed).scalars().all())
        eligible_failed: list[ProviderEventEnvelope] = []
        for env in failed_candidates:
            if env.status != ProviderEventProcessingStatus.FAILED:
                continue
            details = env.error_details or {}
            if not details.get("retryable", False):
                continue
            attempts = details.get("attempts", 0)
            max_att = details.get("max_attempts", MAX_INBOUND_RETRIES)
            if attempts >= max_att:
                continue
            next_at_str = details.get("next_attempt_at")
            if next_at_str:
                try:
                    next_at = datetime.fromisoformat(next_at_str)
                    if now >= next_at:
                        eligible_failed.append(env)
                except (ValueError, TypeError):
                    eligible_failed.append(env)
            else:
                eligible_failed.append(env)

        # 3. Stale PROCESSING envelopes whose lease expired or worker died
        stmt_processing = (
            select(ProviderEventEnvelope)
            .where(
                ProviderEventEnvelope.status == ProviderEventProcessingStatus.PROCESSING,
            )
            .order_by(ProviderEventEnvelope.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        processing_candidates = list(session.execute(stmt_processing).scalars().all())
        eligible_stale_processing: list[ProviderEventEnvelope] = []
        for env in processing_candidates:
            if env.status != ProviderEventProcessingStatus.PROCESSING:
                continue
            details = dict(env.error_details or {})
            lease_expires = env.processing_lease_expires_at
            if lease_expires is None:
                lease_expires_str = details.get("lease_expires_at")
                if lease_expires_str:
                    try:
                        lease_expires = datetime.fromisoformat(lease_expires_str)
                    except (ValueError, TypeError):
                        lease_expires = None

            started_at = env.processing_started_at
            if started_at is None:
                started_at_str = details.get("processing_started_at")
                if started_at_str:
                    try:
                        started_at = datetime.fromisoformat(started_at_str)
                    except (ValueError, TypeError):
                        started_at = None

            is_stale = False
            if lease_expires is not None:
                if lease_expires.tzinfo is None:
                    lease_expires = lease_expires.replace(tzinfo=UTC)
                is_stale = lease_expires <= now
            elif started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                is_stale = started_at <= cutoff
            else:
                received_at = env.received_at
                if received_at is not None and received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=UTC)
                is_stale = (received_at is None) or (received_at <= cutoff)

            if is_stale:
                attempts = env.processing_attempts or int(details.get("attempts", 0))
                if attempts >= MAX_INBOUND_RETRIES:
                    details["retryable"] = False
                    details["error"] = "max_processing_attempts_exhausted"
                    mark_expired_envelope_dead_letter(
                        session,
                        envelope_id=env.id,
                        error_details=details,
                    )
                    env.status = ProviderEventProcessingStatus.FAILED
                    env.processing_lease_token = None
                    env.processing_lease_expires_at = None
                    env.error_details = details
                else:
                    eligible_stale_processing.append(env)

        session.flush()

        seen_ids: set[int] = set()
        envelopes_to_reprocess: list[ProviderEventEnvelope] = []
        for env in stale_accepted + eligible_failed + eligible_stale_processing:
            if env.id not in seen_ids:
                seen_ids.add(env.id)
                envelopes_to_reprocess.append(env)
        dispatched_count = 0
        for env in envelopes_to_reprocess:
            try:
                process_inbound_envelope_task.delay(env.id)
                dispatched_count += 1
            except Exception:
                logger.warning(
                    "messaging.recovery.re-enqueue_failed",
                    extra={"envelope_id": env.id},
                )
        session.commit()
        return dispatched_count
    finally:
        session.close()


@shared_task(  # type: ignore[untyped-decorator]
    name=DISPATCH_OUTBOX_TASK_NAME
)
def dispatch_outbox_task(limit: int = 10) -> int:
    """Claims and dispatches outbound messages from transactional outbox (Finding 2).

    Guarantees:
    - Opens Session
    - Uses injected adapter or constructs EvolutionAdapter from runtime settings
    - Concurrent worker safe (SELECT ... FOR UPDATE SKIP LOCKED)
    - Enforces bounded exponential backoff
    - Transitions terminal failures to DEAD
    """
    session_factory = _require_session_factory()
    session: Session = session_factory()
    try:
        adapter = _channel_adapter
        if adapter is None:
            from tapjob_api.core.config import RuntimeSettings
            from tapjob_api.messaging.evolution_adapter import EvolutionAdapter

            settings = RuntimeSettings()
            adapter = EvolutionAdapter(
                api_url=settings.evolution_api_url,
                api_key=settings.evolution_api_key,
                instance_name=settings.evolution_instance_name,
            )

        dispatched = claim_and_dispatch_outbox_batch(
            session,
            provider_adapter=adapter,
            limit=limit,
        )
        session.commit()
        return len(dispatched)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(  # type: ignore[untyped-decorator]
    name=DISPATCH_DUE_REMINDERS_TASK_NAME
)
def dispatch_due_reminders_task(limit: int = 50) -> int:
    """Sweeps due reminders and enqueues notification intents into outbox (Finding 3)."""
    session_factory = _require_session_factory()
    session: Session = session_factory()
    try:
        reminders = dispatch_due_reminders(session, limit=limit)
        session.commit()
        return len(reminders)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

"""Transactional outbox service for outbound messaging (Finding 6, Finding 7).

Guarantees & Delivery Semantics:
- Internal Enqueue Idempotency: Deduplication by `idempotency_key` ensures exactly-once
  enqueue within the TapJob database transaction.
- External Provider Delivery Uncertainty: Downstream delivery across external networks and
  third-party aggregators (Evolution API, WhatsApp) operates under at-least-once semantics.
  Network dropouts during upstream receipt acknowledgment may cause duplicate attempts;
  external idempotency is forwarded where supported by provider APIs, but exactly-once
  delivery is never falsely claimed.
- Safe concurrent worker claims via SELECT ... FOR UPDATE SKIP LOCKED.
- Bounded exponential backoff via `next_attempt_at` column.
- Single configuration for max retries: `DEFAULT_MAX_RETRIES = 5`.
- Terminal DEAD state when retry threshold is exhausted.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.ports._common import CallContext, PortError
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    MessagingChannel,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 300.0  # 5 minutes bounded cap
DEFAULT_DISPATCH_TIMEOUT_SECONDS = 10.0


class OutboxError(Exception):
    """Raised when outbox operations fail."""


def calculate_backoff_delay(
    retry_count: int,
    base_seconds: float = BASE_BACKOFF_SECONDS,
    max_seconds: float = MAX_BACKOFF_SECONDS,
) -> float:
    """Calculates bounded exponential backoff delay: base * 2^(retry_count - 1),
    capped at max_seconds.
    """
    delay = base_seconds * (2 ** max(0, retry_count - 1))
    return float(min(delay, max_seconds))


def trigger_immediate_outbox_dispatch() -> bool:
    """Non-blocking trigger to dispatch pending outbox messages immediately after commit.

    If Celery broker is unavailable or fails, catches the exception and logs a warning so
    the primary business transaction is unaffected; the periodic Celery Beat worker (every 5s)
    will pick up the messages as a guaranteed fallback.

    Returns True if the task was successfully dispatched to Celery, False otherwise.
    """
    try:
        from tapjob_api.messaging.tasks import dispatch_outbox_task

        dispatch_outbox_task.delay()
        return True
    except Exception as exc:
        logger.warning(
            "outbox.immediate_dispatch_trigger_failed",
            extra={"error": str(exc)},
        )
        return False


def _on_session_after_commit(sess: Session) -> None:
    """Trigger immediate outbox dispatch when outer transaction commits with pending outbox."""
    if sess.in_nested_transaction():
        return
    info = getattr(sess, "info", None)
    if isinstance(info, dict) and info.pop("has_pending_outbox_dispatch", False):
        trigger_immediate_outbox_dispatch()


def _on_session_after_rollback(sess: Session) -> None:
    """Clear pending outbox dispatch flag on transaction rollback."""
    if sess.in_nested_transaction():
        return
    info = getattr(sess, "info", None)
    if isinstance(info, dict):
        info.pop("has_pending_outbox_dispatch", None)


sa.event.listen(Session, "after_commit", _on_session_after_commit)
sa.event.listen(Session, "after_rollback", _on_session_after_rollback)


def _mark_session_outbox_pending(session: Session) -> None:
    """Marks the session as having pending outbox messages to dispatch post-commit."""
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info["has_pending_outbox_dispatch"] = True


def queue_outbound_message(
    session: Session,
    *,
    channel_identity_link_id: str | None,
    message_content: dict[str, Any],
    idempotency_key: str,
) -> OutboundMessageQueue:
    """Enqueues an outbound message intent within the caller's transaction.

    Idempotent on idempotency_key: if already enqueued, returns the existing record.
    Registers an after_commit hook to trigger immediate Celery outbox dispatch.
    """
    existing = session.execute(
        select(OutboundMessageQueue).where(OutboundMessageQueue.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        _mark_session_outbox_pending(session)
        return existing

    try:
        with session.begin_nested():
            now = datetime.now(UTC)
            record = OutboundMessageQueue(
                channel_identity_link_id=channel_identity_link_id,
                message_content=message_content,
                idempotency_key=idempotency_key,
                status=OutboundMessageStatus.PENDING,
                retry_count=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            _mark_session_outbox_pending(session)
            logger.info(
                "outbox.message.enqueued",
                extra={
                    "outbox_id": record.id,
                    "idempotency_key": idempotency_key,
                },
            )
            return record
    except IntegrityError:
        found = session.execute(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if found is None:
            raise OutboxError(f"Failed to queue outbound message: {idempotency_key}") from None
        _mark_session_outbox_pending(session)
        return found


def claim_and_dispatch_outbox_batch(
    session: Session,
    *,
    provider_adapter: MessagingChannel,
    limit: int = 10,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout_seconds: float = DEFAULT_DISPATCH_TIMEOUT_SECONDS,
) -> list[OutboundMessageQueue]:
    """Claims a batch of pending/retryable messages due for execution using SKIP LOCKED
    and dispatches them.
    """
    now = datetime.now(UTC)
    stmt = (
        select(OutboundMessageQueue)
        .where(
            OutboundMessageQueue.status.in_(
                [OutboundMessageStatus.PENDING, OutboundMessageStatus.RETRYABLE]
            ),
            sa.or_(
                OutboundMessageQueue.next_attempt_at.is_(None),
                OutboundMessageQueue.next_attempt_at <= now,
            ),
        )
        .order_by(OutboundMessageQueue.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    records = list(session.execute(stmt).scalars().all())
    if not records:
        return []

    for item in records:
        item.retry_count += 1
        item.status = OutboundMessageStatus.SENDING
        session.flush()

        ctx = CallContext.with_timeout(
            correlation_id=item.idempotency_key,
            timeout_seconds=timeout_seconds,
        )

        recipient_external = item.message_content.get("recipient_external_id", "")
        outbound_msg = OutboundMessage(
            recipient=ChannelIdentity(channel="whatsapp", external_id=recipient_external),
            idempotency_key=item.idempotency_key,
            text=item.message_content.get("text"),
            template_key=item.message_content.get("template_key"),
        )

        try:
            receipt = asyncio.run(provider_adapter.send(outbound_msg, ctx))
            item.status = OutboundMessageStatus.SENT
            item.sent_at = receipt.accepted_at
            item.provider_message_id = receipt.provider_message_id
            item.last_error = None
            logger.info(
                "outbox.message.sent",
                extra={
                    "outbox_id": item.id,
                    "idempotency_key": item.idempotency_key,
                    "provider_message_id": receipt.provider_message_id,
                },
            )
        except PortError as pe:
            item.last_error = f"{pe.code}: {pe.message}"
            if pe.transient and item.retry_count < max_retries:
                item.status = OutboundMessageStatus.RETRYABLE
                delay_sec = calculate_backoff_delay(item.retry_count)
                item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay_sec)
            else:
                item.status = (
                    OutboundMessageStatus.DEAD
                    if item.retry_count >= max_retries
                    else OutboundMessageStatus.FAILED
                )
            logger.warning(
                "outbox.message.dispatch_port_error",
                extra={
                    "outbox_id": item.id,
                    "idempotency_key": item.idempotency_key,
                    "code": pe.code,
                    "retry_count": item.retry_count,
                    "status": item.status,
                },
            )
        except Exception as exc:
            item.last_error = str(exc)
            if item.retry_count < max_retries:
                item.status = OutboundMessageStatus.RETRYABLE
                delay_sec = calculate_backoff_delay(item.retry_count)
                item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay_sec)
            else:
                item.status = OutboundMessageStatus.DEAD
            logger.warning(
                "outbox.message.dispatch_unexpected_error",
                extra={
                    "outbox_id": item.id,
                    "idempotency_key": item.idempotency_key,
                    "retry_count": item.retry_count,
                    "status": item.status,
                },
            )

        session.flush()

    return records

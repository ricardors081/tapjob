"""Inbound event recording, deduplication, claim, and fencing service (Findings 1, 2).

Guarantees:
- At-most-once inbound webhook processing with atomic DB-level deduplication:
  Unique constraint on (provider, provider_instance, external_event_id).
- Atomic claim semantics with attempt increment during claim before slow execution.
  Hard crashes consume attempts and cannot cause unbounded recovery loops.
- Fenced state transitions for PROCESSED and FAILED:
  Updates require (id == envelope_id, status == PROCESSING, processing_lease_token == token).
  Stale workers whose lease was reclaimed cannot commit side effects or overwrite newer state.
- Bounded processing time:
  SOFT_LIMIT < HARD_LIMIT < LEASE_SECONDS.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.messaging.inbound import (
    InboundEffect,
    InboundEffectState,
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)

logger = logging.getLogger(__name__)

INBOUND_PROCESS_SOFT_TIME_LIMIT_SECONDS = 60
INBOUND_PROCESS_HARD_TIME_LIMIT_SECONDS = 90
INBOUND_LEASE_SECONDS = 120
MAX_INBOUND_RETRIES = 3


class InboundClaimResult(StrEnum):
    """Result of an atomic inbound envelope claim attempt."""

    CLAIMED = "claimed"
    ALREADY_PROCESSED = "already_processed"
    ACTIVE_LEASE = "active_lease"
    MAX_ATTEMPTS_EXHAUSTED = "max_attempts_exhausted"
    TERMINAL_FAILED = "terminal_failed"
    RETRY_BACKOFF_PENDING = "retry_backoff_pending"
    NOT_FOUND = "not_found"


class InboundIdempotencyError(Exception):
    """Raised when inbound event handling fails."""


class InboundFencingError(Exception):
    """Raised when an operation on an envelope is rejected due to invalid/stale fencing token."""


def is_transient_inbound_error(exc: Exception) -> bool:
    """Classifies whether an exception during inbound processing is retryable or terminal
    (Finding 5).
    """
    import httpx
    import redis
    from celery.exceptions import SoftTimeLimitExceeded

    from tapjob_api.jobs.errors import JobUrlError
    from tapjob_api.ports._common import PortError
    from tapjob_api.resume_import.errors import ImportLeaseActiveError, TransientProcessingError
    from tapjob_api.resume_import.storage import ObjectStorageError

    if isinstance(exc, JobUrlError):
        return False

    if isinstance(exc, SoftTimeLimitExceeded):
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    if isinstance(exc, PortError):
        return exc.transient

    if isinstance(exc, (ObjectStorageError, TransientProcessingError, ImportLeaseActiveError)):
        return True

    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True

    if isinstance(exc, (redis.ConnectionError, redis.TimeoutError)):
        return True

    msg = str(exc).lower()
    return any(
        term in msg
        for term in (
            "timeout",
            "timed out",
            "unavailable",
            "503",
            "502",
            "504",
            "connection refused",
            "connection reset",
            "transient",
            "temporary",
        )
    )


def record_inbound_event(
    session: Session,
    *,
    provider: str,
    provider_instance: str,
    external_event_id: str,
    event_type: ProviderEventType,
    normalized_event: dict[str, Any],
) -> tuple[ProviderEventEnvelope, bool]:
    """Records an inbound provider event with transactional idempotency guard.

    Returns:
        (ProviderEventEnvelope, is_new):
        is_new=True when inserted for the first time.
        is_new=False when duplicate was detected and returned.
    """
    try:
        with session.begin_nested():
            envelope = ProviderEventEnvelope(
                provider=provider,
                provider_instance=provider_instance,
                external_event_id=external_event_id,
                event_type=event_type,
                status=ProviderEventProcessingStatus.ACCEPTED,
                normalized_event=normalized_event,
                processing_attempts=0,
            )
            session.add(envelope)
            session.flush()
            logger.info(
                "inbound.event.recorded",
                extra={
                    "provider": provider,
                    "provider_instance": provider_instance,
                    "external_event_id": external_event_id,
                    "is_new": True,
                },
            )
            return envelope, True
    except IntegrityError:
        # Retransmission or concurrent duplicate
        existing = session.execute(
            select(ProviderEventEnvelope).where(
                ProviderEventEnvelope.provider == provider,
                ProviderEventEnvelope.provider_instance == provider_instance,
                ProviderEventEnvelope.external_event_id == external_event_id,
            )
        ).scalar_one_or_none()

        if existing is None:
            raise InboundIdempotencyError(
                f"Failed to record or resolve inbound event for {provider}:{external_event_id}"
            ) from None

        logger.info(
            "inbound.event.duplicate_skipped",
            extra={
                "provider": provider,
                "provider_instance": provider_instance,
                "external_event_id": external_event_id,
                "envelope_id": existing.id,
                "prior_status": existing.status,
                "is_new": False,
            },
        )
        return existing, False


def claim_inbound_envelope(
    session: Session,
    *,
    envelope_id: int,
    lease_seconds: float = INBOUND_LEASE_SECONDS,
    max_retries: int = MAX_INBOUND_RETRIES,
) -> tuple[ProviderEventEnvelope | None, str | None, InboundClaimResult]:
    """Atomically claims an envelope for processing with durable lease token and attempt increment.

    Guarantees:
    - Atomically inspects envelope row with row-level lock (FOR UPDATE).
    - If status is PROCESSED, returns ALREADY_PROCESSED.
    - If status is PROCESSING and lease is still unexpired, returns ACTIVE_LEASE.
    - If status is PROCESSING and lease is expired, reclaims IF processing_attempts < max_retries.
      If attempts >= max_retries, marks FAILED (terminal) and returns MAX_ATTEMPTS_EXHAUSTED.
    - If status is FAILED and not retryable, returns TERMINAL_FAILED.
    - If status is FAILED (retryable) with attempts >= max_retries, marks terminal
      and returns MAX_ATTEMPTS_EXHAUSTED.
    - If status is FAILED and retry backoff is pending, returns RETRY_BACKOFF_PENDING.
    - On successful claim:
      - Generates fresh unpredictable fencing token (uuid.uuid4().hex).
      - Increments processing_attempts DURABLY BEFORE work begins (hard crashes consume attempt).
      - Sets processing_started_at and processing_lease_expires_at.
      - Sets status to PROCESSING.
      - Returns (envelope, fencing_token, InboundClaimResult.CLAIMED).
    """
    envelope = session.execute(
        select(ProviderEventEnvelope)
        .where(ProviderEventEnvelope.id == envelope_id)
        .with_for_update()
    ).scalar_one_or_none()
    if envelope is None:
        return None, None, InboundClaimResult.NOT_FOUND

    if envelope.status == ProviderEventProcessingStatus.PROCESSED:
        return envelope, None, InboundClaimResult.ALREADY_PROCESSED

    now = datetime.now(UTC)
    details = dict(envelope.error_details or {})

    # Check active lease if already PROCESSING
    if envelope.status == ProviderEventProcessingStatus.PROCESSING:
        lease_expires = envelope.processing_lease_expires_at
        if lease_expires is None:
            # Fallback to error_details if column was null
            lease_expires_str = details.get("lease_expires_at")
            if lease_expires_str:
                try:
                    lease_expires = datetime.fromisoformat(lease_expires_str)
                except (ValueError, TypeError):
                    lease_expires = None

        if lease_expires is not None:
            if lease_expires.tzinfo is None:
                lease_expires = lease_expires.replace(tzinfo=UTC)
            if now < lease_expires:
                return envelope, None, InboundClaimResult.ACTIVE_LEASE

        # Lease is expired (stale owner or dead worker): check retry budget
        current_attempts = (
            envelope.processing_attempts
            if envelope.processing_attempts is not None
            else int(details.get("attempts", 0))
        )
        if current_attempts >= max_retries:
            envelope.status = ProviderEventProcessingStatus.FAILED
            envelope.processing_lease_token = None
            envelope.processing_lease_expires_at = None
            details["retryable"] = False
            details["error"] = "max_processing_attempts_exhausted"
            envelope.error_details = details
            session.flush()
            return envelope, None, InboundClaimResult.MAX_ATTEMPTS_EXHAUSTED

    # Check eligibility if FAILED
    elif envelope.status == ProviderEventProcessingStatus.FAILED:
        if not details.get("retryable", False):
            return envelope, None, InboundClaimResult.TERMINAL_FAILED

        current_attempts = (
            envelope.processing_attempts
            if envelope.processing_attempts is not None
            else int(details.get("attempts", 0))
        )
        if current_attempts >= max_retries:
            details["retryable"] = False
            details["error"] = "max_processing_attempts_exhausted"
            envelope.error_details = details
            session.flush()
            return envelope, None, InboundClaimResult.MAX_ATTEMPTS_EXHAUSTED

        next_at_str = details.get("next_attempt_at")
        if next_at_str:
            try:
                next_at = datetime.fromisoformat(next_at_str)
                if next_at.tzinfo is None:
                    next_at = next_at.replace(tzinfo=UTC)
                if now < next_at:
                    return envelope, None, InboundClaimResult.RETRY_BACKOFF_PENDING
            except (ValueError, TypeError):
                pass

    elif envelope.status == ProviderEventProcessingStatus.ACCEPTED:
        current_attempts = (
            envelope.processing_attempts
            if envelope.processing_attempts is not None
            else int(details.get("attempts", 0))
        )
        if current_attempts >= max_retries:
            envelope.status = ProviderEventProcessingStatus.FAILED
            envelope.processing_lease_token = None
            envelope.processing_lease_expires_at = None
            details["retryable"] = False
            details["error"] = "max_processing_attempts_exhausted"
            envelope.error_details = details
            session.flush()
            return envelope, None, InboundClaimResult.MAX_ATTEMPTS_EXHAUSTED

    # Grant atomic claim
    current_attempts = (
        envelope.processing_attempts
        if envelope.processing_attempts is not None
        else int(details.get("attempts", 0))
    )
    fresh_token = uuid.uuid4().hex
    new_attempts = current_attempts + 1
    envelope.processing_attempts = new_attempts
    envelope.processing_started_at = now
    envelope.processing_lease_expires_at = now + timedelta(seconds=lease_seconds)
    envelope.processing_lease_token = fresh_token
    envelope.status = ProviderEventProcessingStatus.PROCESSING

    details["attempts"] = new_attempts
    details["processing_started_at"] = now.isoformat()
    details["lease_expires_at"] = envelope.processing_lease_expires_at.isoformat()
    details["lease_token"] = fresh_token
    envelope.error_details = details
    session.flush()

    logger.info(
        "inbound.event.claimed",
        extra={
            "envelope_id": envelope_id,
            "attempts": envelope.processing_attempts,
            "lease_seconds": lease_seconds,
        },
    )
    return envelope, fresh_token, InboundClaimResult.CLAIMED


def mark_event_processed(
    session: Session,
    *,
    envelope_id: int,
    fencing_token: str,
    envelope: ProviderEventEnvelope | None = None,
) -> bool:
    """Marks an envelope as successfully processed, conditional on holding the active token.

    Invariant: TODA transição de um owner saindo de PROCESSING DEVE apresentar o token.
    Returns True if successfully updated, False if fenced out (stale worker).
    """
    if not fencing_token:
        raise ValueError("fencing_token is required for worker owner transition out of PROCESSING")

    now = datetime.now(UTC)
    stmt = (
        update(ProviderEventEnvelope)
        .where(
            ProviderEventEnvelope.id == envelope_id,
            ProviderEventEnvelope.status == ProviderEventProcessingStatus.PROCESSING,
            ProviderEventEnvelope.processing_lease_token == fencing_token,
        )
        .values(
            status=ProviderEventProcessingStatus.PROCESSED,
            processed_at=now,
            processing_lease_token=None,
            processing_lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
        .returning(ProviderEventEnvelope.id)
    )
    res = session.execute(stmt)
    session.flush()
    val = res.scalar_one_or_none()
    if val is None:
        return False

    env = envelope
    if env is None:
        env = session.execute(
            select(ProviderEventEnvelope).where(ProviderEventEnvelope.id == envelope_id)
        ).scalar_one_or_none()
    if env is not None:
        env.status = ProviderEventProcessingStatus.PROCESSED
        env.processed_at = now
        env.processing_lease_token = None
        env.processing_lease_expires_at = None
    return True


def mark_event_failed(
    session: Session,
    *,
    envelope_id: int,
    fencing_token: str,
    error_details: dict[str, Any],
    envelope: ProviderEventEnvelope | None = None,
) -> bool:
    """Marks an envelope as failed, conditional on holding the active token.

    Invariant: TODA transição de um owner saindo de PROCESSING DEVE apresentar o token.
    Returns True if successfully updated, False if fenced out (stale worker).
    """
    if not fencing_token:
        raise ValueError("fencing_token is required for worker owner transition out of PROCESSING")

    stmt = (
        update(ProviderEventEnvelope)
        .where(
            ProviderEventEnvelope.id == envelope_id,
            ProviderEventEnvelope.status == ProviderEventProcessingStatus.PROCESSING,
            ProviderEventEnvelope.processing_lease_token == fencing_token,
        )
        .values(
            status=ProviderEventProcessingStatus.FAILED,
            error_details=error_details,
            processing_lease_token=None,
            processing_lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
        .returning(ProviderEventEnvelope.id)
    )
    res = session.execute(stmt)
    session.flush()
    val = res.scalar_one_or_none()
    if val is None:
        return False

    env = envelope
    if env is None:
        env = session.execute(
            select(ProviderEventEnvelope).where(ProviderEventEnvelope.id == envelope_id)
        ).scalar_one_or_none()
    if env is not None:
        env.status = ProviderEventProcessingStatus.FAILED
        env.error_details = error_details
        env.processing_lease_token = None
        env.processing_lease_expires_at = None
    return True


def mark_expired_envelope_dead_letter(
    session: Session,
    *,
    envelope_id: int,
    error_details: dict[str, Any],
) -> bool:
    """Marks an expired envelope as dead-lettered FAILED when no active lease is held.

    Guarantees:
    - Precondition: lease must be expired (processing_lease_expires_at <= now or NULL).
    - Status must be PROCESSING.
    - An active owner's unexpired lease can NEVER be mutated by this function.
    """
    now = datetime.now(UTC)
    stmt = (
        update(ProviderEventEnvelope)
        .where(
            ProviderEventEnvelope.id == envelope_id,
            ProviderEventEnvelope.status == ProviderEventProcessingStatus.PROCESSING,
            or_(
                ProviderEventEnvelope.processing_lease_expires_at.is_(None),
                ProviderEventEnvelope.processing_lease_expires_at <= now,
            ),
        )
        .values(
            status=ProviderEventProcessingStatus.FAILED,
            error_details=error_details,
            processing_lease_token=None,
            processing_lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
        .returning(ProviderEventEnvelope.id)
    )
    res = session.execute(stmt)
    session.flush()
    return res.scalar_one_or_none() is not None


def reserve_inbound_effect(
    session: Session,
    *,
    envelope_id: int,
    inbound_message_id: str,
    effect_kind: str,
    resource_id: str,
    effect_payload: dict[str, Any] | None = None,
) -> InboundEffect:
    """Durably reserves an inbound domain effect intent (PENDING) BEFORE slow domain work runs.

    Ensures that if a hard crash occurs after domain work commits, the subsequent retry
    identifies the exact reserved resource/target and recovers deterministically (Finding 1).
    """
    effect = InboundEffect(
        envelope_id=envelope_id,
        inbound_message_id=inbound_message_id,
        effect_kind=effect_kind,
        operation_state=InboundEffectState.PENDING.value,
        resource_id=resource_id,
        effect_payload=effect_payload or {},
    )
    session.add(effect)
    session.flush()
    return effect


def complete_inbound_effect(
    session: Session,
    *,
    envelope_id: int,
    effect_kind: str,
    expected_resource_id: str | None = None,
    resource_id: str | None = None,
    effect_payload: dict[str, Any] | None = None,
) -> InboundEffect:
    """Marks a reserved inbound domain effect as COMPLETED with its final execution payload.

    Invariants:
    - resource_id is immutable once reserved (Finding 2).
    - If expected_resource_id (or resource_id) is provided, it must match effect.resource_id.
    - If effect is already COMPLETED with same resource_id, returns existing (idempotent no-op).
    - If expected_resource_id does not match effect.resource_id, raises ValueError (fail-closed).
    """
    effect = session.execute(
        select(InboundEffect).where(
            InboundEffect.envelope_id == envelope_id,
            InboundEffect.effect_kind == effect_kind,
        )
    ).scalar_one_or_none()
    if effect is None:
        raise ValueError(
            f"Nonexistent inbound effect for envelope {envelope_id} and kind {effect_kind}"
        )
    target_id = expected_resource_id or resource_id
    if target_id is not None and effect.resource_id != target_id:
        raise ValueError(
            f"Inbound effect resource_id mismatch for envelope {envelope_id}: "
            f"expected {target_id}, got {effect.resource_id}"
        )
    if effect.operation_state == InboundEffectState.COMPLETED.value:
        return effect
    effect.operation_state = InboundEffectState.COMPLETED.value
    if effect_payload is not None:
        effect.effect_payload = effect_payload
    effect.completed_at = datetime.now(UTC)
    session.flush()
    return effect


def record_inbound_effect(
    session: Session,
    *,
    envelope_id: int,
    inbound_message_id: str,
    effect_kind: str,
    resource_id: str,
    effect_payload: dict[str, Any] | None = None,
    operation_state: str = InboundEffectState.COMPLETED.value,
) -> InboundEffect:
    """Records an executed or reserved domain effect for an inbound event."""
    effect = InboundEffect(
        envelope_id=envelope_id,
        inbound_message_id=inbound_message_id,
        effect_kind=effect_kind,
        operation_state=operation_state,
        resource_id=resource_id,
        effect_payload=effect_payload or {},
        completed_at=datetime.now(UTC)
        if operation_state == InboundEffectState.COMPLETED.value
        else None,
    )
    session.add(effect)
    session.flush()
    return effect


def get_inbound_effect(
    session: Session,
    *,
    envelope_id: int,
    effect_kind: str,
) -> InboundEffect | None:
    """Finds an existing inbound domain effect strictly by envelope_id and effect_kind.

    Guarantees:
    - Never falls back to message_id across envelopes (Finding 3).
    - Scope is strictly tied to the caller's ProviderEventEnvelope.id.
    """
    effect = session.execute(
        select(InboundEffect).where(
            InboundEffect.envelope_id == envelope_id,
            InboundEffect.effect_kind == effect_kind,
        )
    ).scalar_one_or_none()
    if isinstance(effect, InboundEffect):
        return effect

    return None

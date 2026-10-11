"""Provider event envelope for inbound webhook idempotency (Phase 5).

Ensures replay-safety: if Evolution (or any provider) retransmits the same webhook,
we detect and de-duplicate it before business logic ever runs.

No sensitive payload is persisted here by default, only the provider metadata needed
for idempotency.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tapjob_api.db.base import Base
from tapjob_api.identity.models import _enum_column


class ProviderEventType(StrEnum):
    """Neutral provider event classification."""

    MESSAGE_RECEIVED = "message_received"
    MESSAGE_DELIVERED = "message_delivered"
    MESSAGE_FAILED = "message_failed"
    CONTACT_CHANGED = "contact_changed"


class ProviderEventProcessingStatus(StrEnum):
    """Inbound event processing state machine."""

    ACCEPTED = "accepted"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class ProviderEventEnvelope(Base):
    """Idempotency guard for inbound provider events (e.g. Evolution webhooks).

    Unique constraint on (provider, instance, external_event_id) ensures that
    any retransmission of the same webhook is detected and can be safely skipped.

    `normalized_event` stores the event after provider-specific transport layer
    translation (e.g. Evolution -> InboundMessage shape), never the raw payload.
    """

    __tablename__ = "provider_event_envelopes"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "provider_instance",
            "external_event_id",
            name="uq_provider_event_envelopes_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    provider_instance: Mapped[str] = mapped_column(sa.String(128), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    event_type: Mapped[ProviderEventType] = mapped_column(
        _enum_column(ProviderEventType, name="provider_event_type", length=32),
        nullable=False,
    )
    status: Mapped[ProviderEventProcessingStatus] = mapped_column(
        _enum_column(
            ProviderEventProcessingStatus,
            name="provider_event_processing_status",
            length=16,
        ),
        nullable=False,
        default=ProviderEventProcessingStatus.ACCEPTED,
        server_default=ProviderEventProcessingStatus.ACCEPTED.value,
    )
    normalized_event: Mapped[dict[str, Any]] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=False, default=dict
    )
    error_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    processing_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    processing_lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True, index=True
    )
    processing_lease_token: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)


class InboundEffectKind(StrEnum):
    """Classification of commit-owning domain effects produced by inbound messages."""

    RESUME_IMPORT = "resume_import"
    FINALIZE_REVIEW = "finalize_review"


class InboundEffectState(StrEnum):
    """Lifecycle of an inbound domain effect (intent reservation vs factual completion)."""

    PENDING = "pending"
    COMPLETED = "completed"


class InboundEffect(Base):
    """Tracks commit-owning domain effects triggered by an inbound event.

    Ensures that if a worker suffers a hard crash after committing an internal domain effect
    (e.g. creating/processing a ResumeImport or finalizing a candidate review) but before
    transitioning ProviderEventEnvelope to PROCESSED, the subsequent retry by another worker
    detects the existing effect and does NOT duplicate canonical domain resources or produce
    conflicting conversational replies.

    Lifecycle:
    - PENDING: Intent reserved durably BEFORE commit-owning domain operations run.
    - COMPLETED: Marked factual AFTER domain operation commits successfully.
    """

    __tablename__ = "inbound_effects"
    __table_args__ = (
        sa.UniqueConstraint(
            "envelope_id",
            "effect_kind",
            name="uq_inbound_effects_envelope_kind",
        ),
        sa.Index(
            "ix_inbound_effects_message_kind",
            "inbound_message_id",
            "effect_kind",
        ),
        sa.CheckConstraint(
            "operation_state IN ('pending', 'completed')",
            name="ck_inbound_effects_operation_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    envelope_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("provider_event_envelopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inbound_message_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    effect_kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    operation_state: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        default=InboundEffectState.PENDING.value,
        server_default=InboundEffectState.PENDING.value,
    )
    resource_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    effect_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

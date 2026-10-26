"""Transactional outbox for outbound messaging (Phase 6).

Ensures reliable delivery and prevents duplicate sends on retries.

The outbox pattern decouples message creation (within a business transaction) from
delivery (asynchronous worker or immediate send). This guarantees:
- Message persisted atomically with business state
- No lost messages on process crash
- Safe retries without duplication
- Clear delivery state for monitoring/debugging
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tapjob_api.db.base import Base
from tapjob_api.identity.models import _enum_column


class OutboundMessageStatus(StrEnum):
    """Message delivery state machine."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RETRYABLE = "retryable"
    DEAD = "dead"
    CANCELLED = "cancelled"


class OutboundMessageQueue(Base):
    """Outbound message queue with transactional delivery tracking.

    One row per message intent. Atomic insertion within the business transaction
    that triggered the message. Later, a worker process picks up PENDING rows,
    attempts delivery, and updates status.

    Unique `idempotency_key` ensures a caller's retry (e.g. due to timeout) does not
    create duplicate messages.
    """

    __tablename__ = "outbound_message_queue"
    __table_args__ = (
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbound_message_queue_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    channel_identity_link_id: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, index=True
    )
    status: Mapped[OutboundMessageStatus] = mapped_column(
        _enum_column(OutboundMessageStatus, name="outbound_message_status", length=16),
        nullable=False,
        default=OutboundMessageStatus.PENDING,
        server_default=OutboundMessageStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    message_content: Mapped[dict[str, Any]] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=False, default=dict
    )
    provider_message_id: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    retry_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

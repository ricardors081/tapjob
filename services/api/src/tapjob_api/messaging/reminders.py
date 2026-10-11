"""SQLAlchemy ORM models for Reminders / Notifications (Finding 11).

Provides channel-neutral persistent reminders for candidate follow-ups.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tapjob_api.db.base import Base
from tapjob_api.identity.models import _enum_column


class ReminderStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (sa.UniqueConstraint("idempotency_key", name="uq_reminders_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_identity_link_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("channel_identity_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    remind_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )
    intent: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=False, default=dict
    )
    status: Mapped[ReminderStatus] = mapped_column(
        _enum_column(ReminderStatus, name="reminder_status", length=16),
        nullable=False,
        default=ReminderStatus.PENDING,
        server_default=ReminderStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

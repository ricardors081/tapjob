"""SQLAlchemy ORM models for Applications and Application Kits (Finding 10).

Represents candidate application tracking and immutable application kits.
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


class ApplicationStatus(StrEnum):
    SAVED = "saved"
    PREPARING = "preparing"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplyMode(StrEnum):
    OFFICIAL_API = "official_api"
    ASSISTED_EXTENSION = "assisted_extension"
    DEEP_LINK = "deep_link"
    MANUAL = "manual"
    DISABLED = "disabled"


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum_column(ApplicationStatus, name="application_status", length=32),
        nullable=False,
        default=ApplicationStatus.SAVED,
        server_default=ApplicationStatus.SAVED.value,
    )
    apply_mode: Mapped[ApplyMode] = mapped_column(
        _enum_column(ApplyMode, name="apply_mode", length=32),
        nullable=False,
        default=ApplyMode.MANUAL,
        server_default=ApplyMode.MANUAL.value,
    )
    provider: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    provider_application_id: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class ApplicationKit(Base):
    __tablename__ = "application_kits"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("match_results.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    profile_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kit_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB(astext_type=sa.Text()), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

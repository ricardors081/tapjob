"""SQLAlchemy ORM models for WP2.1-A (ADR-0012).

Models:
- Company: canonical company entities with unique partial index on verified domain.
- Job: canonical job posting with normalized representation, status, and provenance summary.
- JobOrigin: individual source observations of a job with nullable external_id.
- IdempotencyKey: atomic multi-phase idempotency reservation for mutation endpoints.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapjob_api.db.base import Base

_NOW = sa.text("now()")


def _enum_column(enum_type: type[StrEnum], *, name: str, length: int) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class JobStatus(StrEnum):
    """Canonical lifecycle and market liveness state of a Job."""

    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class JobWorkplaceType(StrEnum):
    """Standardized workplace arrangement."""

    REMOTE = "remote"
    HYBRID = "hybrid"
    ON_SITE = "on_site"
    UNKNOWN = "unknown"


class ObservationStatus(StrEnum):
    """Observation status for an individual job origin."""

    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class IdempotencyStatus(StrEnum):
    """Status of an idempotency reservation."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class Company(Base):
    """Canonical organization entity."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_companies_name_not_blank"),
        sa.CheckConstraint(
            "length(btrim(name_normalized)) > 0", name="ck_companies_name_normalized_not_blank"
        ),
        sa.Index(
            "uq_companies_domain",
            "domain",
            unique=True,
            postgresql_where=sa.text("domain IS NOT NULL"),
        ),
    )


class Job(Base):
    """Canonical job posting entity."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    title_normalized: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    location_raw: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    location_normalized: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    workplace_type: Mapped[JobWorkplaceType] = mapped_column(
        _enum_column(JobWorkplaceType, name="workplace_type", length=32),
        nullable=False,
        default=JobWorkplaceType.UNKNOWN,
    )
    description_raw: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description_cleaned: Mapped[str] = mapped_column(sa.Text, nullable=False)
    similarity_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus, name="job_status", length=32),
        nullable=False,
        default=JobStatus.UNKNOWN,
        index=True,
    )
    provenance_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    company: Mapped[Company] = relationship("Company", back_populates="jobs")
    origins: Mapped[list["JobOrigin"]] = relationship(
        "JobOrigin", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_jobs_title_not_blank"),
        sa.CheckConstraint(
            "length(btrim(description_raw)) > 0", name="ck_jobs_description_raw_not_blank"
        ),
    )


class JobOrigin(Base):
    """An observation / discovery source for a canonical Job."""

    __tablename__ = "job_origins"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    observation_status: Mapped[ObservationStatus] = mapped_column(
        _enum_column(ObservationStatus, name="observation_status", length=32),
        nullable=False,
        default=ObservationStatus.UNKNOWN,
    )
    consecutive_not_found_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_observed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    provenance_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    job: Mapped[Job] = relationship("Job", back_populates="origins")

    __table_args__ = (
        sa.CheckConstraint("length(btrim(provider)) > 0", name="ck_job_origins_provider_not_blank"),
        sa.Index(
            "uq_job_origins_provider_external_id",
            "provider",
            "external_id",
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL"),
        ),
        sa.Index(
            "ix_job_origins_canonical_url",
            "canonical_url",
            unique=False,
            postgresql_where=sa.text("canonical_url IS NOT NULL"),
        ),
    )


class IdempotencyKey(Base):
    """Atomic multi-phase reservation for mutation idempotency."""

    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    operation: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        _enum_column(IdempotencyStatus, name="idempotency_status", length=32),
        nullable=False,
    )
    response_status: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reservation_token: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(status = 'PENDING' AND response_status IS NULL AND "
            "(response_body IS NULL OR response_body = 'null'::jsonb)) OR "
            "(status = 'COMPLETED' AND response_status IS NOT NULL AND "
            "response_body IS NOT NULL AND response_body != 'null'::jsonb)",
            name="ck_idempotency_keys_status_coherence",
        ),
    )


class SavedJobState(StrEnum):
    """User intent state for a saved job."""

    SAVED = "saved"
    DISMISSED = "dismissed"


class AlertNotificationStatus(StrEnum):
    """Lifecycle status of a job alert notification."""

    PENDING = "pending"
    QUEUED = "queued"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobAlertDeliveryChannel(StrEnum):
    """Supported delivery channels for proactive job alerts (WP2.3 WhatsApp-only)."""

    WHATSAPP = "whatsapp"


class SavedJob(Base):
    """User intent and bookmarking around a job (WP2.3)."""

    __tablename__ = "saved_jobs"

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
    state: Mapped[SavedJobState] = mapped_column(
        _enum_column(SavedJobState, name="saved_job_state", length=32),
        nullable=False,
        default=SavedJobState.SAVED,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    job: Mapped[Job] = relationship("Job")

    __table_args__ = (
        sa.UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),
        sa.Index("ix_saved_jobs_user_state", "user_id", "state"),
    )


class CompanyWatch(Base):
    """User subscription/watch for a canonical Company (WP2.3)."""

    __tablename__ = "company_watches"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )

    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        sa.UniqueConstraint("user_id", "company_id", name="uq_company_watches_user_company"),
    )


class JobAlertPreference(Base):
    """User preferences and subscription for proactive job alerts (WP2.3)."""

    __tablename__ = "job_alert_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    min_score: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=50)
    min_eligibility: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="eligible")
    delivery_channel: Mapped[JobAlertDeliveryChannel] = mapped_column(
        _enum_column(JobAlertDeliveryChannel, name="job_alert_delivery_channel", length=32),
        nullable=False,
        default=JobAlertDeliveryChannel.WHATSAPP,
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "min_score >= 0 AND min_score <= 100",
            name="ck_job_alert_preferences_min_score",
        ),
        sa.CheckConstraint(
            "min_eligibility IN ('eligible', 'review_required')",
            name="ck_job_alert_preferences_min_eligibility",
        ),
        sa.CheckConstraint(
            "delivery_channel = 'whatsapp'",
            name="ck_job_alert_preferences_delivery_channel",
        ),
    )


class JobAlertNotification(Base):
    """Durable notification intent and deduplication record for job alerts (WP2.3)."""

    __tablename__ = "job_alert_notifications"

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
    dedupe_key: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    channel: Mapped[JobAlertDeliveryChannel] = mapped_column(
        _enum_column(JobAlertDeliveryChannel, name="job_alert_delivery_channel", length=32),
        nullable=False,
        default=JobAlertDeliveryChannel.WHATSAPP,
    )
    match_score: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    status: Mapped[AlertNotificationStatus] = mapped_column(
        _enum_column(AlertNotificationStatus, name="alert_notification_status", length=32),
        nullable=False,
        default=AlertNotificationStatus.PENDING,
        index=True,
    )
    outbound_message_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("outbound_message_queue.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship("Job")

    __table_args__ = (
        sa.CheckConstraint(
            "match_score >= 0 AND match_score <= 100",
            name="ck_job_alert_notifications_match_score",
        ),
        sa.CheckConstraint(
            "channel = 'whatsapp'",
            name="ck_job_alert_notifications_channel",
        ),
    )

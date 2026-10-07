"""SQLAlchemy ORM models for WP1.1 (docs/adr/0009-identity-auth-user-account.md).

Every enum-shaped column uses ``sa.Enum(..., native_enum=False, validate_strings=True)`` -- a
`VARCHAR` plus a `CHECK` constraint, not a PostgreSQL native `ENUM` type
(docs/ENGINEERING_STANDARDS.md §23): adding a new provider/platform/status value later is an
ordinary `CHECK` constraint migration, never the destructive `ALTER TYPE ... ADD VALUE` dance a
native Postgres enum would require.
"""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapjob_api.db.base import Base

# Neutral, never inferred from server/request location (2026 WP1.1 review): a user's real timezone
# is supplied later by their own device/explicit setting. "Etc/UTC" is a valid, DST-free IANA zone
# name, not a placeholder that happens to look like one.
DEFAULT_TIMEZONE = "Etc/UTC"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class IdentityProvider(StrEnum):
    """Only `firebase` today; `whatsapp` and other future channel identities are expected to join
    this enum later without an architectural migration (docs/adr/0009-...md "future identities")."""

    FIREBASE = "firebase"


class DevicePlatform(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


class ConsentDecision(StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class DeletionRequestStatus(StrEnum):
    REQUESTED = "requested"
    CANCELLED = "cancelled"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditEventType(StrEnum):
    """Allowlisted, append-only account/security event categories (docs/adr/0009-...md §"audit").
    Deliberately a Python-level enum, not a DB `CHECK` constraint: this vocabulary is expected to
    grow faster than a schema migration should gate, and nothing here is a foreign-key-like
    integrity concern -- only a safe, typed set of values the service layer is allowed to log."""

    ACCOUNT_CREATED = "account_created"
    ACCOUNT_SETTINGS_UPDATED = "account_settings_updated"
    DEVICE_REGISTERED = "device_registered"
    DEVICE_REVOKED = "device_revoked"
    CONSENT_RECORDED = "consent_recorded"
    SESSIONS_REVOKED = "sessions_revoked"
    DELETION_REQUESTED = "deletion_requested"
    DELETION_CANCELLED = "deletion_cancelled"
    AUTH_TOKEN_REJECTED = "auth_token_rejected"  # noqa: S105 -- event-type label, not a credential
    # WP1.2 (docs/adr/0010-career-profile-truth-store.md "audit"): reuses this SAME append-only
    # mechanism rather than a second, near-identical table -- a Truth Store mutation is an
    # account-level event in exactly the sense this table already models.
    PROFILE_UPDATED = "profile_updated"
    PROFILE_FACT_ADDED = "profile_fact_added"
    PROFILE_FACT_REPLACED = "profile_fact_replaced"
    PROFILE_FACT_RETRACTED = "profile_fact_retracted"
    # WP1.3 (docs/adr/0011-resume-import.md "audit"): same append-only mechanism again -- never
    # resume text, a candidate's own value, or a signed URL in `metadata`.
    RESUME_IMPORT_CREATED = "resume_import_created"
    RESUME_IMPORT_FINALIZED = "resume_import_finalized"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


def _enum_column(enum_type: type[StrEnum], *, name: str, length: int) -> sa.Enum:
    # `create_constraint=True` is required explicitly: SQLAlchemy 1.4+ no longer builds the CHECK
    # constraint by default for a non-native enum (`native_enum=False` alone is not enough).
    #
    # `values_callable` is equally required (2026 review, real bug caught by CI's integration job):
    # without it, `sa.Enum` stores a Python `Enum`/`StrEnum` member's `.name` (e.g. the uppercase
    # "ACTIVE") in the database, NOT its `.value` ("active") -- even though `StrEnum.ACTIVE ==
    # "active"` is true in Python. The migration's hand-written CHECK constraint lists the lowercase
    # `.value`s (matching every other reference to these enums, e.g. `UserStatus.ACTIVE.value` used
    # as this same column's `server_default`), so a real insert raised `CheckViolation` against a
    # row containing "ACTIVE" until this was added.
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class User(Base):
    """The one canonical TapJob identity. Primary key is a TapJob-minted UUID, never a Firebase UID
    or any other external provider's identifier (docs/adr/0009-...md "identity invariant")."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[UserStatus] = mapped_column(
        _enum_column(UserStatus, name="user_status", length=16),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    # BCP-47-compatible tag; service.py seeds this from product-config's defaultLocale at creation
    # time, never hardcoded here (docs/ENGINEERING_STANDARDS.md §2).
    locale: Mapped[str] = mapped_column(sa.String(35), nullable=False)
    timezone: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
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

    external_identities: Mapped[list["ExternalIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    devices: Mapped[list["UserDevice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ExternalIdentity(Base):
    """Links one external provider identity to exactly one TapJob `User`. `UNIQUE(provider,
    provider_subject)` is the concurrency-safety backstop for first-login races (docs/adr/0009-
    ...md "concurrency") -- two concurrent bootstrap requests for the same Firebase UID can only
    ever result in one row here, never two Users."""

    __tablename__ = "external_identities"
    __table_args__ = (sa.UniqueConstraint("provider", "provider_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[IdentityProvider] = mapped_column(
        _enum_column(IdentityProvider, name="identity_provider", length=16), nullable=False
    )
    # Firebase UID today; some future provider's own subject identifier later. Never this
    # service's own primary key.
    provider_subject: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    # A snapshot only -- not re-read live from the provider, and never used to auto-link a second,
    # independently-created User just because the value matches (docs/adr/0009-...md "never
    # auto-link by email").
    email_snapshot: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    email_verified: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="external_identities")


class UserDevice(Base):
    """A mobile installation, identified by a TapJob-minted random `installation_id` -- never an
    IMEI, hardware serial, advertising ID or other invasive fingerprint (docs/SECURITY_PRIVACY.md,
    docs/adr/0009-...md "device registration")."""

    __tablename__ = "user_devices"
    __table_args__ = (sa.UniqueConstraint("user_id", "installation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform: Mapped[DevicePlatform] = mapped_column(
        _enum_column(DevicePlatform, name="device_platform", length=16), nullable=False
    )
    app_version: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    # Future FCM delivery (not WP1.1): treated as a sensitive operational identifier, never logged
    # (docs/adr/0009-...md "device registration").
    push_token: Mapped[str | None] = mapped_column(sa.String(4096), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="devices")


class ConsentRecord(Base):
    """Append-only: a changed decision is a NEW row, never an overwrite of a prior one
    (docs/SECURITY_PRIVACY.md, docs/adr/0009-...md "consent model"). No purpose-key catalog is
    enforced at the DB level yet -- no final legal-purpose list has been approved for the product
    (docs/OPEN_QUESTIONS.md); this table is the infrastructure to record one once it exists."""

    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    decision: Mapped[ConsentDecision] = mapped_column(
        _enum_column(ConsentDecision, name="consent_decision", length=16), nullable=False
    )
    source: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    user: Mapped["User"] = relationship()


class AccountDeletionRequest(Base):
    """Deletion SKELETON only (docs/adr/0009-...md "deletion skeleton"): this row never implies
    data was actually erased. A future deletion orchestrator reads `COMPLETED`/`FAILED` rows to
    decide what still needs doing; nothing in WP1.1 performs that orchestration."""

    __tablename__ = "account_deletion_requests"
    __table_args__ = (
        # At most one REQUESTED/PROCESSING row per user at a time -- the structural backstop for
        # "duplicate request is idempotent" (docs/adr/0009-...md "deletion endpoint").
        sa.Index(
            "uq_account_deletion_requests_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=sa.text("status IN ('requested', 'processing')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DeletionRequestStatus] = mapped_column(
        _enum_column(DeletionRequestStatus, name="deletion_request_status", length=16),
        nullable=False,
        default=DeletionRequestStatus.REQUESTED,
        server_default=DeletionRequestStatus.REQUESTED.value,
    )
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    user: Mapped["User"] = relationship()


class AccountAuditEvent(Base):
    """Append-only security/account audit trail. `metadata_` holds only allowlisted, already-safe
    fields the service layer constructs explicitly -- never a raw request body, password, token or
    Authorization header (docs/SECURITY_PRIVACY.md, docs/adr/0009-...md "security audit")."""

    __tablename__ = "account_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    outcome: Mapped[AuditOutcome] = mapped_column(
        _enum_column(AuditOutcome, name="audit_outcome", length=16), nullable=False
    )
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    correlation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    user: Mapped["User"] = relationship()

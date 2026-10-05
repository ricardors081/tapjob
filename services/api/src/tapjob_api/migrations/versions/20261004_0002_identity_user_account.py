"""WP1.1: identity & user account tables (docs/adr/0009-identity-auth-user-account.md, D-051).

Deliberately does NOT import `tapjob_api.identity.models` (the standard Alembic caution against
importing live ORM models into a migration: a future WP1.2+ column added to `User` would otherwise
retroactively leak into THIS migration's `upgrade()` at run time instead of its own). Every column/
constraint/index below is a literal, independent snapshot -- constraint/index names are computed by
hand to match `tapjob_api.db.base.NAMING_CONVENTION` exactly, so the schema this migration produces
is indistinguishable from what the ORM models declare.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "disabled",
                name="user_status",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("locale", sa.String(length=35), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Etc/UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "firebase",
                name="identity_provider",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider_subject", sa.String(length=128), nullable=False),
        sa.Column("email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_external_identities"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_external_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_external_identities_provider"),
    )
    op.create_index(
        "ix_external_identities_user_id", "external_identities", ["user_id"], unique=False
    )

    op.create_table(
        "user_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "platform",
            sa.Enum(
                "ios",
                "android",
                "web",
                name="device_platform",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("push_token", sa.String(length=4096), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_devices"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_devices_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "installation_id", name="uq_user_devices_user_id"),
    )
    op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"], unique=False)

    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose_key", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "granted",
                "withdrawn",
                name="consent_decision",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_consent_records"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_consent_records_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_consent_records_user_id", "consent_records", ["user_id"], unique=False)

    op.create_table(
        "account_deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "requested",
                "cancelled",
                "processing",
                "completed",
                "failed",
                name="deletion_request_status",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
            server_default="requested",
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_account_deletion_requests"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_deletion_requests_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_account_deletion_requests_user_id",
        "account_deletion_requests",
        ["user_id"],
        unique=False,
    )
    # At most one REQUESTED/PROCESSING row per user at a time -- the structural backstop for
    # "duplicate request is idempotent" (docs/adr/0009-...md "deletion endpoint").
    op.create_index(
        "uq_account_deletion_requests_active_per_user",
        "account_deletion_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'processing')"),
    )

    op.create_table(
        "account_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "success",
                "failure",
                name="audit_outcome",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_account_audit_events"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_audit_events_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_account_audit_events_user_id", "account_audit_events", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("account_audit_events")
    op.drop_index(
        "uq_account_deletion_requests_active_per_user", table_name="account_deletion_requests"
    )
    op.drop_table("account_deletion_requests")
    op.drop_table("consent_records")
    op.drop_table("user_devices")
    op.drop_table("external_identities")
    op.drop_table("users")

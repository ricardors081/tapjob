"""WP-Alpha Phase 10-11: Applications, Application Kits, and Reminders.

Creates tables:
- applications: job application tracker
- application_kits: immutable tailored application package
- reminders: persistent reminder intents for follow-up and notifications

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="saved"),
        sa.Column("apply_mode", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_application_id", sa.String(length=256), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.CheckConstraint(
            "status IN ('saved', 'preparing', 'applied', 'interviewing', 'rejected', 'withdrawn')",
            name="applications_status_check",
        ),
        sa.CheckConstraint(
            "apply_mode IN ('official_api', 'assisted_extension', "
            "'deep_link', 'manual', 'disabled')",
            name="applications_apply_mode_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )
    op.create_index(
        "ix_applications_user_id",
        "applications",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_applications_job_id",
        "applications",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "application_kits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column(
            "kit_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["match_results.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_kits_application_id",
        "application_kits",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_kits_user_id",
        "application_kits",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_kits_job_id",
        "application_kits",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "reminders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_identity_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'cancelled')",
            name="reminders_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_identity_link_id"],
            ["channel_identity_links.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_reminders_idempotency_key"),
    )
    op.create_index(
        "ix_reminders_user_id",
        "reminders",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_reminders_remind_at",
        "reminders",
        ["remind_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("reminders")
    op.drop_table("application_kits")
    op.drop_table("applications")

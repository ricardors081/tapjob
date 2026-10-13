"""WP2.3: Job Feed, Alerts, Saved Jobs, and Company Watchlist.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Saved Jobs (saved or dismissed state)
    op.create_table(
        "saved_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'saved'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),
        sa.CheckConstraint(
            "state IN ('saved', 'dismissed')",
            name="ck_saved_jobs_state",
        ),
    )
    op.create_index("ix_saved_jobs_user_id", "saved_jobs", ["user_id"])
    op.create_index("ix_saved_jobs_job_id", "saved_jobs", ["job_id"])
    op.create_index("ix_saved_jobs_user_state", "saved_jobs", ["user_id", "state"])

    # 2. Company Watchlist
    op.create_table(
        "company_watches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "company_id", name="uq_company_watches_user_company"),
    )
    op.create_index("ix_company_watches_user_id", "company_watches", ["user_id"])
    op.create_index("ix_company_watches_company_id", "company_watches", ["company_id"])

    # 3. Job Alert Preferences (Opt-in by default disabled)
    op.create_table(
        "job_alert_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "min_score",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("50"),
        ),
        sa.Column(
            "min_eligibility",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'eligible'"),
        ),
        sa.Column(
            "delivery_channel",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'whatsapp'"),
        ),
        sa.Column(
            "last_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
    op.create_index("ix_job_alert_preferences_user_id", "job_alert_preferences", ["user_id"])
    op.create_index("ix_job_alert_preferences_enabled", "job_alert_preferences", ["enabled"])
    op.create_index(
        "ix_job_alert_preferences_last_eval", "job_alert_preferences", ["last_evaluated_at"]
    )

    # 4. Job Alert Notifications (Dedupe & Intent Record)
    op.create_table(
        "job_alert_notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dedupe_key",
            sa.String(length=255),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "channel",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'whatsapp'"),
        ),
        sa.Column(
            "match_score",
            sa.SmallInteger(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "outbound_message_id",
            sa.BigInteger(),
            sa.ForeignKey("outbound_message_queue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'delivered', 'failed', 'skipped')",
            name="ck_job_alert_notifications_status",
        ),
        sa.CheckConstraint(
            "channel = 'whatsapp'",
            name="ck_job_alert_notifications_channel",
        ),
        sa.CheckConstraint(
            "match_score >= 0 AND match_score <= 100",
            name="ck_job_alert_notifications_match_score",
        ),
    )
    op.create_index("ix_job_alert_notifications_user_id", "job_alert_notifications", ["user_id"])
    op.create_index("ix_job_alert_notifications_job_id", "job_alert_notifications", ["job_id"])
    op.create_index(
        "ix_job_alert_notifications_dedupe_key",
        "job_alert_notifications",
        ["dedupe_key"],
    )
    op.create_index("ix_job_alert_notifications_status", "job_alert_notifications", ["status"])
    op.create_index(
        "ix_job_alert_notifications_outbound_msg_id",
        "job_alert_notifications",
        ["outbound_message_id"],
    )

    # 5. Outbound message queue status check extension (cancelled state support)
    op.drop_constraint(
        "outbound_message_queue_status_check", "outbound_message_queue", type_="check"
    )
    op.create_check_constraint(
        "outbound_message_queue_status_check",
        "outbound_message_queue",
        "status IN ('pending', 'sending', 'sent', 'failed', 'retryable', 'dead', 'cancelled')",
    )


def downgrade() -> None:
    # 5. Revert outbound message queue status check extension
    op.execute(
        sa.text("UPDATE outbound_message_queue SET status = 'failed' WHERE status = 'cancelled'")
    )
    op.drop_constraint(
        "outbound_message_queue_status_check", "outbound_message_queue", type_="check"
    )
    op.create_check_constraint(
        "outbound_message_queue_status_check",
        "outbound_message_queue",
        "status IN ('pending', 'sending', 'sent', 'failed', 'retryable', 'dead')",
    )

    # 4. Job Alert Notifications
    op.drop_index(
        "ix_job_alert_notifications_outbound_msg_id", table_name="job_alert_notifications"
    )
    op.drop_index("ix_job_alert_notifications_status", table_name="job_alert_notifications")
    op.drop_index("ix_job_alert_notifications_dedupe_key", table_name="job_alert_notifications")
    op.drop_index("ix_job_alert_notifications_job_id", table_name="job_alert_notifications")
    op.drop_index("ix_job_alert_notifications_user_id", table_name="job_alert_notifications")
    op.drop_table("job_alert_notifications")

    # 3. Job Alert Preferences
    op.drop_index("ix_job_alert_preferences_last_eval", table_name="job_alert_preferences")
    op.drop_index("ix_job_alert_preferences_enabled", table_name="job_alert_preferences")
    op.drop_index("ix_job_alert_preferences_user_id", table_name="job_alert_preferences")
    op.drop_table("job_alert_preferences")

    # 2. Company Watchlist
    op.drop_index("ix_company_watches_company_id", table_name="company_watches")
    op.drop_index("ix_company_watches_user_id", table_name="company_watches")
    op.drop_table("company_watches")

    # 1. Saved Jobs
    op.drop_index("ix_saved_jobs_user_state", table_name="saved_jobs")
    op.drop_index("ix_saved_jobs_job_id", table_name="saved_jobs")
    op.drop_index("ix_saved_jobs_user_id", table_name="saved_jobs")
    op.drop_table("saved_jobs")

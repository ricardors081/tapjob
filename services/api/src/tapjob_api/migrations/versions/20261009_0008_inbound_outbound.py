"""WP-Alpha Phase 5-6: Inbound Idempotency & Outbound Outbox.

Creates tables:
- provider_event_envelopes: idempotency guard for inbound webhooks
- outbound_message_queue: transactional outbox for reliable delivery

These two tables implement the foundational reliable messaging pattern:
- Inbound: dedup/replay protection
- Outbound: delivery guarantee + idempotent retries

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "provider_event_envelopes",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_instance", sa.String(length=128), nullable=False),
        sa.Column("external_event_id", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "normalized_event",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "error_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "event_type IN ('message_received', 'message_delivered', "
            "'message_failed', 'contact_changed')",
            name="provider_event_envelopes_event_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'processing', 'processed', 'failed', 'duplicate')",
            name="provider_event_envelopes_status_check",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_instance",
            "external_event_id",
            name="uq_provider_event_envelopes_dedup",
        ),
    )
    op.create_index(
        "ix_provider_event_envelopes_provider",
        "provider_event_envelopes",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_provider_event_envelopes_provider_instance",
        "provider_event_envelopes",
        ["provider_instance"],
        unique=False,
    )

    op.create_table(
        "outbound_message_queue",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("channel_identity_link_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "message_content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("provider_message_id", sa.String(length=256), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'retryable', 'dead')",
            name="outbound_message_queue_status_check",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbound_message_queue_idempotency",
        ),
    )
    op.create_index(
        "ix_outbound_message_queue_channel_identity_link_id",
        "outbound_message_queue",
        ["channel_identity_link_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbound_message_queue_pending_due",
        "outbound_message_queue",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("outbound_message_queue")
    op.drop_table("provider_event_envelopes")

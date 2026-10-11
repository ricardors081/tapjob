"""WP-Alpha: Inbound Effects table for replay-safe commit-owning domain effects.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbound_effects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "envelope_id",
            sa.BigInteger(),
            sa.ForeignKey("provider_event_envelopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inbound_message_id", sa.String(length=256), nullable=False),
        sa.Column("effect_kind", sa.String(length=64), nullable=False),
        sa.Column(
            "operation_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column(
            "effect_payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "envelope_id",
            "effect_kind",
            name="uq_inbound_effects_envelope_kind",
        ),
        sa.CheckConstraint(
            "operation_state IN ('pending', 'completed')",
            name="ck_inbound_effects_operation_state",
        ),
    )
    op.create_index(
        "ix_inbound_effects_envelope_id",
        "inbound_effects",
        ["envelope_id"],
    )
    op.create_index(
        "ix_inbound_effects_message_kind",
        "inbound_effects",
        ["inbound_message_id", "effect_kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_effects_message_kind", table_name="inbound_effects")
    op.drop_index("ix_inbound_effects_envelope_id", table_name="inbound_effects")
    op.drop_table("inbound_effects")

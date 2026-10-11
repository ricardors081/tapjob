"""WP-Alpha: Provider Event Envelope Lease and Fencing Columns.

Adds processing_attempts, processing_started_at, processing_lease_expires_at,
and processing_lease_token columns to provider_event_envelopes for durable
claim tracking, hard crash recovery, and lease fencing.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_event_envelopes",
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "provider_event_envelopes",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_event_envelopes",
        sa.Column("processing_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_event_envelopes",
        sa.Column("processing_lease_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_provider_event_envelopes_lease_expires",
        "provider_event_envelopes",
        ["processing_lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_event_envelopes_lease_expires",
        table_name="provider_event_envelopes",
    )
    op.drop_column("provider_event_envelopes", "processing_lease_token")
    op.drop_column("provider_event_envelopes", "processing_lease_expires_at")
    op.drop_column("provider_event_envelopes", "processing_started_at")
    op.drop_column("provider_event_envelopes", "processing_attempts")

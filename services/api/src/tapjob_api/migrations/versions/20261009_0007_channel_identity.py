"""WP-Alpha Phase 3: Channel Identity & Linking (WhatsApp vertical slice foundation).

Creates tables:
- channel_providers: messaging provider instances (e.g. Evolution API)
- channel_identities: external identities (e.g. WhatsApp phone numbers)
- channel_identity_links: canonical mappings to TapJob Users

Provider-neutral design preserves migration path to WhatsApp Cloud API.
No secrets or business logic are stored here.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "channel_providers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("provider_config_key", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
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
            "channel IN ('whatsapp')",
            name="channel_providers_channel_check",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_config_key"),
    )
    op.create_index(
        "ix_channel_providers_channel",
        "channel_providers",
        ["channel"],
        unique=False,
    )

    op.create_table(
        "channel_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["channel_providers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id",
            "external_id",
            name="uq_channel_identities_provider_external",
        ),
    )
    op.create_index(
        "ix_channel_identities_provider_id",
        "channel_identities",
        ["provider_id"],
        unique=False,
    )

    op.create_table(
        "channel_identity_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "verified_at",
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
            "status IN ('pending', 'verified', 'broken')",
            name="channel_identity_links_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["channel_identity_id"],
            ["channel_identities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_identity_id",
            "user_id",
            name="uq_channel_identity_links_identity_user",
        ),
    )
    op.create_index(
        "ix_channel_identity_links_channel_identity_id",
        "channel_identity_links",
        ["channel_identity_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_identity_links_user_id",
        "channel_identity_links",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_channel_identity_links_single_active",
        "channel_identity_links",
        ["channel_identity_id"],
        unique=True,
        postgresql_where=sa.text("status != 'broken'"),
    )


def downgrade() -> None:
    op.drop_table("channel_identity_links")
    op.drop_table("channel_identities")
    op.drop_table("channel_providers")

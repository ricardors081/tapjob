"""WP1.2: career profile & professional truth store tables
(docs/adr/0010-career-profile-truth-store.md, D-052).

Deliberately does NOT import `tapjob_api.career_profile.models` (the same standard Alembic caution
WP1.1's `0002` migration already documents: a future column added to the live ORM model would
otherwise retroactively leak into THIS migration's `upgrade()` at run time instead of its own).
Every column/constraint/index below is a literal, independent snapshot -- constraint/index names
are computed by hand to match `tapjob_api.db.base.NAMING_CONVENTION` exactly.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "career_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("headline", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "target_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_career_profiles"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_career_profiles_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", name="uq_career_profiles_user_id"),
        # Composite-FK target for `profile_facts` below (docs/adr/0010-...md "referential
        # consistency") -- Postgres requires an explicit UNIQUE on the exact column pair a
        # composite FK references, even though `id` alone is already unique.
        sa.UniqueConstraint("id", "user_id", name="uq_career_profiles_id_user_id"),
        sa.CheckConstraint("profile_version >= 0", name="ck_career_profiles_version_non_negative"),
    )
    op.create_index("ix_career_profiles_user_id", "career_profiles", ["user_id"], unique=False)

    op.create_table(
        "profile_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("career_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "fact_type",
            sa.Enum(
                "experience",
                "education",
                "skill",
                "certification",
                "language",
                "project",
                "link",
                "preference",
                "work_authorization",
                name="profile_fact_type",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum(
                "user_direct",
                name="profile_fact_source_kind",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("evidence_ref", sa.String(length=256), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "superseded",
                "retracted",
                name="profile_fact_status",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supersedes_fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_from_profile_version", sa.Integer(), nullable=False),
        sa.Column("inactive_from_profile_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_profile_facts"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_profile_facts_user_id_users", ondelete="CASCADE"
        ),
        # Composite FK (docs/adr/0010-...md "referential consistency", round-1 independent review
        # "BLOCKER 2"): replaces a plain `career_profile_id -> career_profiles.id` FK, which
        # structurally permitted a fact's `user_id` to disagree with its `career_profile_id`'s real
        # owner. This forces the DB itself to reject that mismatch.
        sa.ForeignKeyConstraint(
            ["career_profile_id", "user_id"],
            ["career_profiles.id", "career_profiles.user_id"],
            name="fk_profile_facts_career_profile_id_user_id_career_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fact_id"],
            ["profile_facts.id"],
            name="fk_profile_facts_supersedes_fact_id_profile_facts",
        ),
        sa.CheckConstraint(
            "inactive_from_profile_version IS NULL "
            "OR inactive_from_profile_version >= active_from_profile_version",
            name="ck_profile_facts_version_order",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_profile_facts_confidence_range",
        ),
        sa.CheckConstraint(
            "active_from_profile_version >= 1", name="ck_profile_facts_active_from_at_least_one"
        ),
        sa.CheckConstraint(
            "(status = 'active' AND inactive_from_profile_version IS NULL) "
            "OR (status != 'active' AND inactive_from_profile_version IS NOT NULL)",
            name="ck_profile_facts_inactive_version_matches_status",
        ),
    )
    op.create_index("ix_profile_facts_user_id", "profile_facts", ["user_id"], unique=False)
    op.create_index(
        "ix_profile_facts_career_profile_id", "profile_facts", ["career_profile_id"], unique=False
    )
    op.create_index("ix_profile_facts_lineage_id", "profile_facts", ["lineage_id"], unique=False)
    # At most one ACTIVE fact per lineage at a time (docs/adr/0010-...md "replacement model") -- a
    # real DB invariant, not merely a service-layer check.
    op.create_index(
        "uq_profile_facts_active_lineage",
        "profile_facts",
        ["lineage_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # Historical top-level truth (docs/adr/0010-...md "historical top-level revision", round-1
    # independent review "BLOCKER 1"): one row per profile_version at which headline/summary/
    # target_roles actually changed -- never one row per every version bump.
    op.create_table(
        "career_profile_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("career_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("headline", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "target_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_career_profile_revisions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_career_profile_revisions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["career_profile_id", "user_id"],
            ["career_profiles.id", "career_profiles.user_id"],
            name="fk_career_profile_revisions_career_profile_id_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "career_profile_id", "profile_version", name="uq_career_profile_revisions_version"
        ),
        sa.CheckConstraint(
            "profile_version >= 0", name="ck_career_profile_revisions_version_non_negative"
        ),
    )
    op.create_index(
        "ix_career_profile_revisions_user_id", "career_profile_revisions", ["user_id"], unique=False
    )
    op.create_index(
        "ix_career_profile_revisions_career_profile_id",
        "career_profile_revisions",
        ["career_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_career_profile_revisions_career_profile_id", table_name="career_profile_revisions"
    )
    op.drop_index("ix_career_profile_revisions_user_id", table_name="career_profile_revisions")
    op.drop_table("career_profile_revisions")
    op.drop_index("uq_profile_facts_active_lineage", table_name="profile_facts")
    op.drop_index("ix_profile_facts_lineage_id", table_name="profile_facts")
    op.drop_index("ix_profile_facts_career_profile_id", table_name="profile_facts")
    op.drop_index("ix_profile_facts_user_id", table_name="profile_facts")
    op.drop_table("profile_facts")
    op.drop_index("ix_career_profiles_user_id", table_name="career_profiles")
    op.drop_table("career_profiles")

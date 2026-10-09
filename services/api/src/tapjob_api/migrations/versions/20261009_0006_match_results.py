"""WP2.2: Opportunity Matching Engine results persistence (ADR-0014).

Creates table:
- match_results: canonical persisted match evaluations with unique version composite index

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "match_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("job_match_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("evaluation_as_of_date", sa.Date(), nullable=False),
        sa.Column("deterministic_result_digest", sa.String(length=64), nullable=False),
        sa.Column("overall_score", sa.SmallInteger(), nullable=False),
        sa.Column("eligibility", sa.String(length=32), nullable=False),
        sa.Column(
            "hard_filter_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "blockers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "strengths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "critical_unknowns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "explanation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "grounded_fact_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_match_results"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_match_results_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_match_results_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_match_results_overall_score_range",
        ),
        sa.CheckConstraint(
            "eligibility IN ('eligible', 'ineligible', 'review_required')",
            name="ck_match_results_eligibility",
        ),
    )
    op.create_index(
        "ix_match_results_user_id",
        "match_results",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_match_results_job_id",
        "match_results",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "uq_match_results_user_job_versions",
        "match_results",
        [
            "user_id",
            "job_id",
            "profile_version",
            "job_match_fingerprint",
            "policy_version",
            "evaluation_as_of_date",
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_match_results_user_job_versions", table_name="match_results")
    op.drop_index("ix_match_results_job_id", table_name="match_results")
    op.drop_index("ix_match_results_user_id", table_name="match_results")
    op.drop_table("match_results")

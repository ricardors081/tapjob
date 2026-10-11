"""WP-Alpha: Candidate Review Drafts.

Creates table candidate_review_drafts to persist intermediate conversational review
decisions (ACCEPT, REJECT, EDIT_ACCEPT) without acceptance/rejection by omission.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "candidate_review_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("resume_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("edited_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "decision IN ('accept', 'reject', 'edit_accept')",
            name="candidate_review_drafts_decision_check",
        ),
        sa.ForeignKeyConstraint(
            ["resume_import_id"],
            ["resume_imports.id"],
            name="fk_candidate_review_drafts_import",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["resume_import_candidates.id"],
            name="fk_candidate_review_drafts_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_candidate_review_drafts_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_review_drafts"),
        sa.UniqueConstraint(
            "resume_import_id",
            "candidate_id",
            name="uq_candidate_review_drafts_import_candidate",
        ),
    )
    op.create_index(
        "ix_candidate_review_drafts_resume_import_id",
        "candidate_review_drafts",
        ["resume_import_id"],
    )
    op.create_index(
        "ix_candidate_review_drafts_user_id",
        "candidate_review_drafts",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_table("candidate_review_drafts")

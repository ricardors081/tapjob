"""WP1.3: resume import tables (docs/adr/0011-resume-import.md, D-053).

Deliberately does NOT import `tapjob_api.resume_import.models` (the same standard Alembic caution
every prior migration already documents). Every column/constraint/index below is a literal,
independent snapshot -- constraint/index names are computed by hand to match
`tapjob_api.db.base.NAMING_CONVENTION` exactly.

Also extends WP1.2's `profile_fact_source_kind` CHECK constraint to additionally allow
`resume_import` (docs/adr/0010-career-profile-truth-store.md's `FactSourceKind` enum, now joined by
a real second member) -- the constraint's own name is `profile_fact_source_kind` itself (the
`sa.Enum(..., name=...)` type's own name becomes its generated `CheckConstraint`'s name, not a
`ck_`-prefixed convention name, since an explicit `name` was given to the `Enum` type).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.drop_constraint("profile_fact_source_kind", "profile_facts", type_="check")
    op.create_check_constraint(
        "profile_fact_source_kind",
        "profile_facts",
        "source_kind IN ('user_direct', 'resume_import')",
    )

    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("is_master", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_resumes"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_resumes_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"], unique=False)
    # At most one MASTER resume per user (docs/adr/0011-...md "master resume creation") -- a real
    # DB invariant, not merely a service-layer check.
    op.create_index(
        "uq_resumes_user_id_master",
        "resumes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_master"),
    )

    op.create_table(
        "resume_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_resume_versions"),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            name="fk_resume_versions_resume_id_resumes",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("resume_id", "version", name="uq_resume_versions_resume_id_version"),
        sa.CheckConstraint("version >= 1", name="ck_resume_versions_version_at_least_one"),
    )
    op.create_index("ix_resume_versions_resume_id", "resume_versions", ["resume_id"], unique=False)

    op.create_table(
        "resume_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "processing",
                "ready_for_review",
                "finalized",
                "failed",
                name="resume_import_status",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("parser_identity", sa.String(length=64), nullable=True),
        sa.Column(
            "failure_code",
            sa.Enum(
                "unsupported_document_type",
                "document_too_large",
                "malformed_document",
                "encrypted_document",
                "text_not_extractable",
                "no_reviewable_candidates",
                "internal_error",
                name="resume_import_failure_code",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("processing_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_dispatch_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_resume_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finalized_resume_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resume_imports"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_resume_imports_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["finalized_resume_id"],
            ["resumes.id"],
            name="fk_resume_imports_finalized_resume_id_resumes",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_resume_version_id"],
            ["resume_versions.id"],
            name="fk_resume_imports_finalized_resume_version_id_resume_versions",
        ),
        sa.CheckConstraint(
            "(status = 'finalized') = (finalized_resume_version_id IS NOT NULL)",
            name="ck_resume_imports_finalized_version_matches_status",
        ),
        # Composite-FK target for `resume_import_candidates` below (round-1 independent review
        # "Hardening 9"): `id` alone is already the PK/unique, but Postgres still requires an
        # explicit UNIQUE on the EXACT column pair a composite FK references.
        sa.UniqueConstraint("id", "user_id", name="uq_resume_imports_id_user_id"),
    )
    op.create_index("ix_resume_imports_user_id", "resume_imports", ["user_id"], unique=False)

    op.create_table(
        "resume_import_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resume_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_kind", sa.String(length=32), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_excerpt", sa.String(length=300), nullable=True),
        sa.Column(
            "review_state",
            sa.Enum(
                "pending",
                "accepted",
                "rejected",
                name="resume_import_candidate_review_state",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_resume_import_candidates"),
        # Composite FK (round-1 independent review "Hardening 9"): `resume_import_id` and `user_id`
        # were previously two INDEPENDENT FKs, structurally permitting a candidate whose `user_id`
        # belongs to one User but whose `resume_import_id` points at a DIFFERENT User's
        # `ResumeImport`. This makes the DB itself reject a mismatched pair.
        sa.ForeignKeyConstraint(
            ["resume_import_id", "user_id"],
            ["resume_imports.id", "resume_imports.user_id"],
            name="fk_resume_import_candidates_import_user_resume_imports",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_resume_import_candidates_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_resume_import_candidates_resume_import_id",
        "resume_import_candidates",
        ["resume_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_resume_import_candidates_user_id",
        "resume_import_candidates",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    # Round-2 independent review "Finding 7": a real, finalized import leaves behind
    # `profile_facts` rows with `source_kind = 'resume_import'` -- Truth Store data, not scratch
    # data. This downgrade must NEVER (a) silently falsify that provenance to `'user_direct'` just
    # to satisfy the narrower CHECK constraint this downgrade recreates below, nor (b) silently
    # delete real data a destructive downgrade would imply. Both are a DATA decision, not a
    # migration one -- fail fast and loud instead, requiring an explicit, separately-approved
    # operational decision (docs/adr/0011-resume-import.md "migration downgrade policy") before
    # this downgrade can proceed. An empty-database round trip is completely unaffected.
    bind = op.get_bind()
    resume_import_fact_count: int = bind.execute(
        sa.text("SELECT count(*) FROM profile_facts WHERE source_kind = 'resume_import'")
    ).scalar_one()
    if resume_import_fact_count:
        raise RuntimeError(
            f"cannot downgrade migration 0004: {resume_import_fact_count} profile_facts row(s) "
            "have source_kind='resume_import'. Downgrading would require either falsifying their "
            "provenance to 'user_direct' (never acceptable) or silently deleting real Truth "
            "Store data -- neither is something an automatic migration step may decide on its "
            "own (docs/adr/0011-resume-import.md 'migration downgrade policy'). Resolve the "
            "data first, under an explicit, separately-approved runbook, then retry."
        )

    op.drop_index("ix_resume_import_candidates_user_id", table_name="resume_import_candidates")
    op.drop_index(
        "ix_resume_import_candidates_resume_import_id", table_name="resume_import_candidates"
    )
    op.drop_table("resume_import_candidates")
    op.drop_index("ix_resume_imports_user_id", table_name="resume_imports")
    op.drop_table("resume_imports")
    op.drop_index("ix_resume_versions_resume_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("uq_resumes_user_id_master", table_name="resumes")
    op.drop_index("ix_resumes_user_id", table_name="resumes")
    op.drop_table("resumes")

    op.drop_constraint("profile_fact_source_kind", "profile_facts", type_="check")
    op.create_check_constraint(
        "profile_fact_source_kind", "profile_facts", "source_kind IN ('user_direct')"
    )

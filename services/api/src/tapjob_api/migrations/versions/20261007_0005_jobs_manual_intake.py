"""WP2.1-A: jobs domain, manual intake & normalization foundation (ADR-0012).

Creates tables:
- companies: canonical company entities with unique partial index on corporate domain
- jobs: canonical job postings with normalized representations and status
- job_origins: individual observations/sources of a job with nullable external_id
- idempotency_keys: atomic multi-phase idempotency reservation (PENDING/COMPLETED)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_normalized", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_companies_name_not_blank"),
        sa.CheckConstraint(
            "length(btrim(name_normalized)) > 0", name="ck_companies_name_normalized_not_blank"
        ),
    )
    op.create_index(
        "uq_companies_domain",
        "companies",
        ["domain"],
        unique=True,
        postgresql_where=sa.text("domain IS NOT NULL"),
    )
    op.create_index(
        "ix_companies_name_normalized",
        "companies",
        ["name_normalized"],
        unique=False,
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("title_normalized", sa.String(length=255), nullable=False),
        sa.Column("location_raw", sa.String(length=255), nullable=True),
        sa.Column("location_normalized", sa.String(length=255), nullable=True),
        sa.Column(
            "workplace_type",
            sa.Enum(
                "remote",
                "hybrid",
                "on_site",
                "unknown",
                name="workplace_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("description_cleaned", sa.Text(), nullable=False),
        sa.Column("similarity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "unavailable",
                "closed",
                "expired",
                "unknown",
                name="job_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "provenance_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_jobs_company_id_companies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_jobs_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_jobs_title_not_blank"),
        sa.CheckConstraint(
            "length(btrim(description_raw)) > 0", name="ck_jobs_description_raw_not_blank"
        ),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"], unique=False)
    op.create_index("ix_jobs_created_by_user_id", "jobs", ["created_by_user_id"], unique=False)
    op.create_index(
        "ix_jobs_similarity_fingerprint",
        "jobs",
        ["similarity_fingerprint"],
        unique=False,
    )
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)

    op.create_table(
        "job_origins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column(
            "observation_status",
            sa.Enum(
                "active",
                "unavailable",
                "closed",
                "unknown",
                name="observation_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "consecutive_not_found_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "first_observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "provenance_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name="pk_job_origins"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_origins_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(btrim(provider)) > 0", name="ck_job_origins_provider_not_blank"),
    )
    op.create_index("ix_job_origins_job_id", "job_origins", ["job_id"], unique=False)
    op.create_index(
        "uq_job_origins_provider_external_id",
        "job_origins",
        ["provider", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "ix_job_origins_canonical_url",
        "job_origins",
        ["canonical_url"],
        unique=False,
        postgresql_where=sa.text("canonical_url IS NOT NULL"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "COMPLETED",
                name="idempotency_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reservation_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "key", "operation", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_idempotency_keys_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND response_status IS NULL AND "
            "(response_body IS NULL OR response_body = 'null'::jsonb)) OR "
            "(status = 'COMPLETED' AND response_status IS NOT NULL AND "
            "response_body IS NOT NULL AND response_body != 'null'::jsonb)",
            name="ck_idempotency_keys_status_coherence",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("job_origins")
    op.drop_table("jobs")
    op.drop_table("companies")

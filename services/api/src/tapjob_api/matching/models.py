"""SQLAlchemy ORM model for WP2.2 MatchResult (ADR-0014 Decision 9)."""

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tapjob_api.db.base import Base

_NOW = sa.text("now()")


class MatchResult(Base):
    """Canonical persisted match evaluation."""

    __tablename__ = "match_results"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    job_match_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    evaluation_as_of_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    deterministic_result_digest: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    overall_score: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    eligibility: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    hard_filter_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    blockers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    strengths: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    missing_requirements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    critical_unknowns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    grounded_fact_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=sa.func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_match_results_overall_score_range",
        ),
        sa.CheckConstraint(
            "eligibility IN ('eligible', 'ineligible', 'review_required')",
            name="ck_match_results_eligibility",
        ),
        sa.Index(
            "uq_match_results_user_job_versions",
            "user_id",
            "job_id",
            "profile_version",
            "job_match_fingerprint",
            "policy_version",
            "evaluation_as_of_date",
            unique=True,
        ),
    )

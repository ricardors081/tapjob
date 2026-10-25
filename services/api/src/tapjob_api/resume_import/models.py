"""SQLAlchemy ORM models for WP1.3 (docs/adr/0011-resume-import.md, D-053). Same enum-column
convention as WP1.1/WP1.2 (docs/ENGINEERING_STANDARDS.md §23): `sa.Enum(..., native_enum=False,
create_constraint=True, values_callable=...)` -- a `VARCHAR` + `CHECK` constraint, never a native
Postgres `ENUM`.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapjob_api.db.base import Base


class ImportStatus(StrEnum):
    """Lifecycle of one resume import (docs/adr/0011-...md "import lifecycle"). `QUEUED` ->
    `PROCESSING` is claimed atomically by the worker via a conditional `UPDATE` that ALSO matches
    an already-`PROCESSING` row whose `processing_lease_expires_at` has passed -- a lease, not a
    one-way latch (round-1 independent review "Finding 2"): a worker that crashes after claiming a
    row leaves it reclaimable once the lease expires, rather than stuck in `PROCESSING` forever.
    Redelivery of the SAME task while the lease is still active is never silently treated as a
    no-op success -- it raises `ImportLeaseActiveError`, which the Celery task wrapper turns into
    an explicit, bounded retry. `READY_FOR_REVIEW` -> `FINALIZED` is claimed the same way by
    `finalize_resume_import` (a single-request, non-leased claim -- finalize has no long-running
    work between claim and commit, so no lease is needed there), which is how two concurrent
    finalize requests resolve to exactly one winner without a separate idempotency-key table."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY_FOR_REVIEW = "ready_for_review"
    FINALIZED = "finalized"
    FAILED = "failed"


class ImportFailureCode(StrEnum):
    """Normalized reason codes (docs/adr/0011-...md "error taxonomy") -- never a raw parser/library
    exception message persisted or returned to a client."""

    UNSUPPORTED_DOCUMENT_TYPE = "unsupported_document_type"
    DOCUMENT_TOO_LARGE = "document_too_large"
    MALFORMED_DOCUMENT = "malformed_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    TEXT_NOT_EXTRACTABLE = "text_not_extractable"
    NO_REVIEWABLE_CANDIDATES = "no_reviewable_candidates"
    INTERNAL_ERROR = "internal_error"


class CandidateReviewState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _enum_column(enum_type: type[StrEnum], *, name: str, length: int) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class ResumeImport(Base):
    """One upload lifecycle (docs/adr/0011-...md "import record"). `storage_key` is a random,
    server-generated reference -- NEVER derived from `original_filename`, which is retained as
    display-only metadata (`tapjob_api.resume_import.storage.new_storage_key`)."""

    __tablename__ = "resume_imports"
    __table_args__ = (
        sa.CheckConstraint(
            "(status = 'finalized') = (finalized_resume_version_id IS NOT NULL)",
            name="ck_resume_imports_finalized_version_matches_status",
        ),
        # Composite-FK target for `ResumeImportCandidate` below (round-1 independent review
        # "Hardening 9", same discipline as `career_profile.models.CareerProfile`'s own
        # `uq_career_profiles_id_user_id`): `id` alone is already the PK/unique, but Postgres still
        # requires an explicit UNIQUE on the EXACT column pair a composite FK references.
        sa.UniqueConstraint("id", "user_id", name="uq_resume_imports_id_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ImportStatus] = mapped_column(
        _enum_column(ImportStatus, name="resume_import_status", length=20),
        nullable=False,
        default=ImportStatus.QUEUED,
        server_default=ImportStatus.QUEUED.value,
    )
    # Display-only metadata -- never used to derive a path/key (docs/adr/0011-...md "import
    # record").
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(sa.String(127), nullable=False)
    byte_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    parser_identity: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    failure_code: Mapped[ImportFailureCode | None] = mapped_column(
        _enum_column(ImportFailureCode, name="resume_import_failure_code", length=32),
        nullable=True,
    )
    # Worker lease/retry (round-1 independent review "Finding 2"). `processing_lease_expires_at`
    # is set only while `status == PROCESSING`; a `NULL` value there is never itself meaningful --
    # `claim_import_for_processing`'s own `WHERE` clause is the single place that interprets it.
    # `processing_attempts` bounds total claim attempts (`limits.MAX_PROCESSING_ATTEMPTS`) so a
    # pathological document that always fails transiently cannot retry forever.
    processing_lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    processing_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # A fresh, random value generated at EVERY successful claim (round-2 independent review
    # "Finding 2") -- the real OWNERSHIP token every later state transition (`READY_FOR_REVIEW`/
    # `FAILED`/back to `QUEUED`) must present to be accepted. `processing_attempts` alone was not
    # enough: it identifies an ATTEMPT NUMBER, not a specific claim's HOLDER, so a worker whose
    # lease expired while it was still (slowly, not dead) running could, in principle, later
    # observe the SAME `processing_attempts` value a reclaiming attempt was also using (e.g. after
    # that reclaiming attempt itself failed and was released back for retry at the SAME attempt
    # count) and be wrongly accepted as if it were still that current, authoritative attempt. A
    # fresh token on every claim makes that structurally impossible: a claim always invalidates
    # whatever token a previous holder had, regardless of attempt-count coincidences.
    processing_lease_token: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    # Round-2 independent review "Finding 4": when the server-owned sweep
    # (`tasks.sweep_stale_resume_import_dispatches_task`) last attempted to re-dispatch this still-
    # `QUEUED` row. `NULL` means "never redispatched" -- `should_redispatch_processing` falls back
    # to `created_at` in that case. Distinct from `updated_at`, which a raw Core `UPDATE` (as this
    # column's own writes are) never touches via its ORM-level `onupdate=` default -- this column
    # is the real, explicit bookkeeping for "was this already retried recently", never inferred
    # from a timestamp another concern also writes to.
    last_dispatch_attempt_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    finalized_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("resumes.id"), nullable=True
    )
    finalized_resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("resume_versions.id"), nullable=True
    )
    review_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "review_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    candidates: Mapped[list["ResumeImportCandidate"]] = relationship(back_populates="resume_import")

    @property
    def ambiguities_count(self) -> int:
        if not self.review_metadata:
            return 0
        return int(self.review_metadata.get("ambiguity_count", 0))

    @property
    def ambiguities(self) -> list[dict[str, Any]]:
        if not self.review_metadata:
            return []
        return list(self.review_metadata.get("ambiguities", []))


class ResumeImportCandidate(Base):
    """A reviewable, UNCONFIRMED extraction result (docs/adr/0011-...md "candidate data"). Never a
    `ProfileFact` -- `service.finalize_resume_import` is the only path that ever turns an ACCEPTED
    candidate into one. Mutable (unlike `ProfileFact`) until finalization, since this is still
    machine-derived scratch data, not Truth Store content."""

    __tablename__ = "resume_import_candidates"
    __table_args__ = (
        # Referential consistency (round-1 independent review "Hardening 9", same discipline as
        # `career_profile.models.ProfileFact`'s own composite FK to `CareerProfile`):
        # `resume_import_id` and `user_id` were previously two INDEPENDENT FKs, which structurally
        # permitted a candidate whose `user_id` belongs to one User but whose `resume_import_id`
        # points at a DIFFERENT User's `ResumeImport` -- nothing in the schema itself rejected
        # that, only (correct, but non-enforced-by-the-DB) service-layer discipline did. This
        # composite FK makes the DB itself reject a mismatched pair.
        sa.ForeignKeyConstraint(
            ["resume_import_id", "user_id"],
            ["resume_imports.id", "resume_imports.user_id"],
            name="fk_resume_import_candidates_import_user_resume_imports",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No inline `sa.ForeignKey(...)` here -- the composite `ForeignKeyConstraint` above is this
    # column's (and `user_id`'s) real FK.
    resume_import_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # Direct column for a single-predicate ownership check, same discipline as `ProfileFact.user_id`
    # (docs/adr/0010-career-profile-truth-store.md "ownership"). Its OWN single-column FK to
    # `users.id` is kept alongside the composite FK above -- a column may participate in more than
    # one FK constraint; this one alone still guards "every candidate belongs to a real User" even
    # considered in isolation.
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # One of the nine canonical `FactType` values, or `headline`/`summary` -- see
    # `tapjob_api.resume_import.candidates.CandidateKind`. Kept as a plain bounded string column
    # (not `_enum_column`) because this vocabulary belongs to the review domain, which already
    # tracks it in a Python-level `StrEnum`; a second DB-level CHECK duplicating the exact same list
    # would be a second source of truth for the same concern the instant one enum gains a member the
    # other does not (docs/ENGINEERING_STANDARDS.md §1).
    candidate_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    value_json: Mapped[dict[str, object]] = mapped_column("value", JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    source_excerpt: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    review_state: Mapped[CandidateReviewState] = mapped_column(
        _enum_column(CandidateReviewState, name="resume_import_candidate_review_state", length=16),
        nullable=False,
        default=CandidateReviewState.PENDING,
        server_default=CandidateReviewState.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    resume_import: Mapped["ResumeImport"] = relationship(back_populates="candidates")


class Resume(Base):
    """Logical resume family (docs/DATA_MODEL.md "Resume"). At most one MASTER resume per user --
    enforced by the partial unique index below, not only application logic."""

    __tablename__ = "resumes"
    __table_args__ = (
        sa.Index(
            "uq_resumes_user_id_master",
            "user_id",
            unique=True,
            postgresql_where=sa.text("is_master"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_master: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="resume")


class ResumeVersion(Base):
    """Immutable rendered/content snapshot (docs/DATA_MODEL.md "ResumeVersion",
    docs/ARCHITECTURE.md §6 "ResumeVersion is immutable after submission; new changes create a new
    version"). `content_json` is a bounded, typed snapshot
    (`tapjob_api.resume_import.schemas.MasterResumeSnapshot`) -- every career claim it carries
    references a confirmed `ProfileFact` id via `fact_ids`, never a copy of fact text divorced from
    its own provenance (docs/adr/0011-...md "resume version grounding")."""

    __tablename__ = "resume_versions"
    __table_args__ = (
        sa.UniqueConstraint("resume_id", "version", name="uq_resume_versions_resume_id_version"),
        sa.CheckConstraint("version >= 1", name="ck_resume_versions_version_at_least_one"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    target_job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    profile_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content_json: Mapped[dict[str, object]] = mapped_column("content", JSONB, nullable=False)
    generated_by: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    resume: Mapped["Resume"] = relationship(back_populates="versions")


class CandidateReviewDraft(Base):
    """Stores intermediate review decisions recorded via conversational channels.

    Prevents 'acceptance/rejection by omission': individual review actions record an explicit
    draft decision for a specific candidate without deciding other candidates implicitly.
    Finalization against Truth Store occurs only when all candidates have explicit decisions.
    """

    __tablename__ = "candidate_review_drafts"
    __table_args__ = (
        sa.UniqueConstraint(
            "resume_import_id",
            "candidate_id",
            name="uq_candidate_review_drafts_import_candidate",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_import_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("resume_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("resume_import_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    edited_value: Mapped[dict[str, object] | None] = mapped_column(
        "edited_value", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

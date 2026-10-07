"""SQLAlchemy ORM models for WP1.2 (docs/adr/0010-career-profile-truth-store.md, D-052).

Same enum-column convention as WP1.1 (docs/ENGINEERING_STANDARDS.md §23): `sa.Enum(...,
native_enum=False, create_constraint=True, values_callable=...)` -- a `VARCHAR` + `CHECK`
constraint, never a native Postgres `ENUM`, and `values_callable` so the DB stores the lowercase
`.value` rather than the Python member's uppercase `.name` (the real WP1.1 bug this now avoids by
construction).
"""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapjob_api.db.base import Base


class FactType(StrEnum):
    """The canonical professional-claim categories WP1.2 must support
    (docs/IMPLEMENTATION_PLAN.md WP1.2 "Deliver"). `application_answer`-shaped facts are explicitly
    out of scope here -- that concept belongs to the future `ApplicationAnswer` entity
    (docs/DATA_MODEL.md), not this Truth Store."""

    EXPERIENCE = "experience"
    EDUCATION = "education"
    SKILL = "skill"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    PROJECT = "project"
    LINK = "link"
    PREFERENCE = "preference"
    WORK_AUTHORIZATION = "work_authorization"


class FactSourceKind(StrEnum):
    """`user_direct` is WP1.2's own source; `resume_import` is added by WP1.3
    (docs/adr/0011-resume-import.md) -- both are server-assigned only, never trusted from a client
    (docs/adr/0010-...md "provenance / no source spoofing"). Further future sources
    (`whatsapp_user_statement`, `agent_suggestion`) are expected to join this enum later, each
    written only by its own internal trusted use case, mirroring how WP1.1's `IdentityProvider`
    started with only `firebase`."""

    USER_DIRECT = "user_direct"
    RESUME_IMPORT = "resume_import"


class FactStatus(StrEnum):
    """A fact's lifecycle state (docs/adr/0010-...md "retraction"). `ACTIVE` facts are the only
    ones a default `TruthSnapshot` ever returns. `SUPERSEDED`/`RETRACTED` facts are never
    hard-deleted -- they remain queryable for history/auditability."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


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


class CareerProfile(Base):
    """The one canonical, versioned professional profile per `User`
    (docs/adr/0010-...md "CareerProfile aggregate"). `profile_version` starts at `0` -- a row at
    version `0` is semantically identical to "no profile yet" from a caller's point of view (see
    `service.get_profile_view`'s virtual-empty-profile behavior for a `User` with no row at all);
    the first REAL content mutation bumps it to `1`. `target_roles` is the one preference kept at
    profile level (docs/DATA_MODEL.md's own pre-existing sketch already lists it alongside
    headline/summary) -- every OTHER preference (salary, location, workplace/employment type) is a
    typed `ProfileFact` instead, so there is exactly one source of truth per preference kind, never
    two (docs/ENGINEERING_STANDARDS.md §1)."""

    __tablename__ = "career_profiles"
    __table_args__ = (
        sa.UniqueConstraint("user_id", name="uq_career_profiles_user_id"),
        # Composite-FK target for `ProfileFact` below (docs/adr/0010-...md "referential
        # consistency"): `id` alone is already the PK/unique, but Postgres still requires an
        # explicit UNIQUE on the EXACT column pair a composite FK references.
        sa.UniqueConstraint("id", "user_id", name="uq_career_profiles_id_user_id"),
        sa.CheckConstraint("profile_version >= 0", name="ck_career_profiles_version_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    headline: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Bounded list[str] of normalized role titles -- length/item-size bounds enforced at the
    # Pydantic layer (docs/adr/0010-...md "value JSON safety"), never trusted raw from JSONB alone.
    target_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    profile_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
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

    facts: Mapped[list["ProfileFact"]] = relationship(back_populates="career_profile")


class ProfileFact(Base):
    """An immutable, provenance-bearing professional claim (docs/adr/0010-...md "fact
    immutability"). A fact's `value_json` is NEVER updated in place once created -- a changed claim
    creates a NEW row with `supersedes_fact_id` pointing at the old one, which transitions to
    `SUPERSEDED` (docs/adr/0010-...md "replacement model"). This is a deliberate improvement over
    `career-ops-hq/career-ops`'s own `career-profile.mjs` (audited `d6a6c12db6d579ae0f3e6875d2474
    09f6b8c9806`), which mutates a fact's `text` in place on re-import with no history -- see
    `docs/SOURCE_REUSE_LEDGER.md`.

    `lineage_id` identifies one logical claim across its full replace/retract history -- it equals
    the fact's own `id` when first created and is copied onto every fact that later supersedes it.
    The partial unique index below is the real concurrency/invariant backstop for "at most one
    ACTIVE fact per lineage", not just application logic (docs/ENGINEERING_STANDARDS.md §23)."""

    __tablename__ = "profile_facts"
    __table_args__ = (
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
        # Lifecycle/status consistency (docs/adr/0010-...md "extra DB hardening"): an ACTIVE fact
        # never has an inactive-from version; a SUPERSEDED/RETRACTED fact always does -- both
        # transitions are set together in the SAME statement throughout service.py, so this has
        # always held in practice; this makes it a real DB invariant too.
        sa.CheckConstraint(
            "(status = 'active' AND inactive_from_profile_version IS NULL) "
            "OR (status != 'active' AND inactive_from_profile_version IS NOT NULL)",
            name="ck_profile_facts_inactive_version_matches_status",
        ),
        # At most one ACTIVE fact per lineage at a time (docs/adr/0010-...md "replacement model") --
        # a real DB invariant, not merely a service-layer check.
        sa.Index(
            "uq_profile_facts_active_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
        # Referential consistency (docs/adr/0010-...md "referential consistency", round-1
        # independent review "BLOCKER 2"): `user_id` and `career_profile_id` were previously two
        # INDEPENDENT FKs, which structurally permitted a fact whose `user_id` belongs to one User
        # but whose `career_profile_id` points at a DIFFERENT User's `CareerProfile` -- nothing in
        # the schema itself rejected that, only (correct, but non-enforced-by-the-DB) service-layer
        # discipline did. This composite FK makes the DB itself reject a mismatched pair: a row can
        # only reference a `(career_profiles.id, career_profiles.user_id)` pair that ACTUALLY
        # belongs together.
        sa.ForeignKeyConstraint(
            ["career_profile_id", "user_id"],
            ["career_profiles.id", "career_profiles.user_id"],
            name="fk_profile_facts_career_profile_id_user_id_career_profiles",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Direct column (not only reachable via career_profile_id) so an ownership check is always a
    # single indexed predicate, never a join (docs/adr/0010-...md "ownership"). Its OWN single-
    # column FK to `users.id` is kept alongside the composite FK below -- a column may participate
    # in more than one FK constraint; this one alone still guards "every fact belongs to a real
    # User" even considered in isolation.
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # No inline `sa.ForeignKey(...)` here -- the composite `ForeignKeyConstraint` in
    # `__table_args__` above is this column's (and `user_id`'s) real FK, enforcing
    # `career_profile_id`/`user_id` consistency together, not two independent references.
    career_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    fact_type: Mapped[FactType] = mapped_column(
        _enum_column(FactType, name="profile_fact_type", length=32), nullable=False
    )
    # Schema-validated at the Pydantic/service layer before ever reaching this column -- never an
    # arbitrary, unbounded client payload (docs/adr/0010-...md "value JSON safety").
    value_json: Mapped[dict[str, object]] = mapped_column("value", JSONB, nullable=False)
    source_kind: Mapped[FactSourceKind] = mapped_column(
        _enum_column(FactSourceKind, name="profile_fact_source_kind", length=32), nullable=False
    )
    # A reference/pointer only -- never a place to dump a full resume/chat/e-mail body
    # (docs/adr/0010-...md "evidence").
    source_ref: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # No column-level default: every insert path must explicitly decide this (WP1.2's own
    # user-direct path always passes `True`) rather than silently inheriting an implicit value
    # (docs/adr/0010-...md "confirmation model").
    user_confirmed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    status: Mapped[FactStatus] = mapped_column(
        _enum_column(FactStatus, name="profile_fact_status", length=16),
        nullable=False,
        default=FactStatus.ACTIVE,
        server_default=FactStatus.ACTIVE.value,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    supersedes_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("profile_facts.id"), nullable=True
    )
    # System/version validity (WHEN this fact entered/left the current truth set, in
    # profile_version terms) -- deliberately distinct from a fact's own DOMAIN validity (e.g. an
    # experience's real-world start/end dates), which lives inside `value_json` for the fact types
    # that have one (docs/adr/0010-...md "system version vs domain validity").
    active_from_profile_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    inactive_from_profile_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    career_profile: Mapped["CareerProfile"] = relationship(back_populates="facts")


class CareerProfileRevision(Base):
    """Append-only snapshot of `CareerProfile`'s own top-level truth (`headline`/`summary`/
    `target_roles`) as of one specific `profile_version` (docs/adr/0010-...md "historical top-level
    revision"). Round-1 independent review's BLOCKER 1: `get_truth_snapshot`'s fact-side historical
    reconstruction was already correct, but `headline`/`summary`/`target_roles` were always read
    from the CURRENT live `CareerProfile` row regardless of the requested `profileVersion` --
    mixing temporal states. A row here is written ONLY when the profile's OWN top-level fields
    change (`service.update_career_profile`) or when the profile is first created
    (`service._get_or_create_career_profile`, at `profile_version=0`, capturing the empty initial
    state) -- never on a fact-only mutation, since those never change this table's own fields. A
    historical read resolves to `SELECT ... WHERE profile_version <= :target ORDER BY
    profile_version DESC LIMIT 1` -- the latest revision at or before the requested version -- which
    is why one isn't needed for every single version bump. Deliberately NOT a general
    event-sourcing/audit-log framework: this table has exactly one purpose."""

    __tablename__ = "career_profile_revisions"
    __table_args__ = (
        sa.UniqueConstraint(
            "career_profile_id", "profile_version", name="uq_career_profile_revisions_version"
        ),
        # Same composite-FK consistency guarantee as `ProfileFact` (docs/adr/0010-...md
        # "referential consistency").
        sa.ForeignKeyConstraint(
            ["career_profile_id", "user_id"],
            ["career_profiles.id", "career_profiles.user_id"],
            name="fk_career_profile_revisions_career_profile_id_user_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "profile_version >= 0", name="ck_career_profile_revisions_version_non_negative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    career_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    profile_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    headline: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    target_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

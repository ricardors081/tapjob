"""Request/response DTOs for `/api/v1/me/career-profile`, `/api/v1/me/profile-facts` and
`/api/v1/me/truth-snapshot` (docs/adr/0010-career-profile-truth-store.md). CamelCase wire format
via explicit per-field `Field(alias=...)`, mirroring `tapjob_api.identity.schemas`'s convention.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from tapjob_api.career_profile.commands import MAX_TARGET_ROLES, CareerProfilePatchCommand
from tapjob_api.career_profile.fact_values import FactValue
from tapjob_api.career_profile.models import FactStatus, FactType


class CareerProfileResponse(BaseModel):
    """A `User` with no `CareerProfile` row yet still gets a response here -- a virtual profile at
    `profileVersion: 0` (docs/adr/0010-...md "profile creation"), never a 404. `createdAt`/
    `updatedAt` are `null` only for that virtual case."""

    model_config = {"populate_by_name": True}

    headline: str | None = None
    summary: str | None = None
    target_roles: list[str] = Field(default_factory=list, alias="targetRoles")
    profile_version: int = Field(alias="profileVersion")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class UpdateCareerProfileRequest(CareerProfilePatchCommand):
    """HTTP-specific wrapper over `CareerProfilePatchCommand` (round-3 independent review
    "BLOCKER 1") -- adds `expectedProfileVersion` and camelCase wire aliasing only.
    `headline`/`summary`/`target_roles`'s own validation (trim/non-blank/control-character
    rejection/bounds/dedupe) and the "at least one mutable field" rule all live on the PARENT,
    inherited here, not duplicated: `service.update_career_profile` requires the SAME
    `CareerProfilePatchCommand` type directly, so a future channel adapter gets identical
    validation without going through this HTTP class at all.

    Optimistic-concurrency contract (docs/adr/0010-...md "optimistic concurrency"):
    `expectedProfileVersion` must match the profile's current version or the request fails with
    `PROFILE_VERSION_CONFLICT` (409) -- never a silent last-write-wins. Only fields actually present
    in the request body are touched (`model_fields_set` distinguishes "absent" from "explicit
    null"); the inherited `None` defaults exist only so a field CAN be omitted, not so sending it
    always clears it."""

    # `populate_by_name=True` is already inherited from `CareerProfilePatchCommand` -- not
    # redeclared here.
    expected_profile_version: int = Field(alias="expectedProfileVersion", ge=0)
    target_roles: list[str] | None = Field(
        default=None, alias="targetRoles", max_length=MAX_TARGET_ROLES
    )


class ProfileFactResponse(BaseModel):
    """`value` is the SAME typed `FactValue` discriminated union the request side uses (round-2
    independent review "BLOCKER 2") -- never `dict[str, object]`/`Record<string, unknown>` on the
    generated TypeScript side. Pydantic validates the stored `value_json` dict against this union
    on every construction, so a corrupted/incompatible DB row fails to build this response at all
    (an unhandled 500, never a 200 with inconsistent data) rather than silently passing through
    whatever bytes happened to be stored."""

    model_config = {"populate_by_name": True}

    id: UUID
    fact_type: FactType = Field(alias="factType")
    value: FactValue
    source_kind: str = Field(alias="sourceKind")
    source_ref: str | None = Field(default=None, alias="sourceRef")
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")
    confidence: float | None = None
    user_confirmed: bool = Field(alias="userConfirmed")
    confirmed_at: datetime | None = Field(default=None, alias="confirmedAt")
    status: FactStatus
    lineage_id: UUID = Field(alias="lineageId")
    supersedes_fact_id: UUID | None = Field(default=None, alias="supersedesFactId")
    active_from_profile_version: int = Field(alias="activeFromProfileVersion")
    inactive_from_profile_version: int | None = Field(
        default=None, alias="inactiveFromProfileVersion"
    )
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def _value_kind_matches_fact_type(self) -> "ProfileFactResponse":
        # Fail-closed defense-in-depth (round-2 independent review "BLOCKER 2"): every write path
        # in `service.py` already guarantees these agree, but the Truth Store must never hand back
        # an inconsistent fact as though it were valid if a future bug (or direct DB edit) ever
        # breaks that guarantee.
        if self.value.kind != self.fact_type.value:
            raise ValueError("stored fact value.kind does not match fact_type")
        return self


class AddProfileFactRequest(BaseModel):
    """`sourceKind`/`sourceRef`/`confidence`/`userConfirmed` are deliberately NOT accepted here --
    the server always assigns `source_kind=USER_DIRECT`/`user_confirmed=True` for this endpoint
    (docs/adr/0010-...md "provenance / no source spoofing"); a public client can never choose its
    own provenance metadata."""

    model_config = {"populate_by_name": True}

    expected_profile_version: int = Field(alias="expectedProfileVersion", ge=0)
    fact_type: FactType = Field(alias="factType")
    value: FactValue

    @model_validator(mode="after")
    def _value_kind_matches_declared_fact_type(self) -> "AddProfileFactRequest":
        # `value` is itself discriminated on its own `kind` (fact_values.py); without this check a
        # client could send `factType: "language"` with a skill-shaped `value` and the mismatch
        # would reach the repository as a stored inconsistency instead of a 422
        # (docs/adr/0010-...md "value JSON safety").
        if self.value.kind != self.fact_type.value:
            raise ValueError("value.kind must match factType")
        return self


class ReplaceProfileFactRequest(BaseModel):
    """The replacement's `value` must match the ORIGINAL fact's `factType` -- enforced at the
    service layer, not re-declared here (docs/adr/0010-...md "replacement model")."""

    model_config = {"populate_by_name": True}

    expected_profile_version: int = Field(alias="expectedProfileVersion", ge=0)
    value: FactValue


class RetractProfileFactRequest(BaseModel):
    model_config = {"populate_by_name": True}

    expected_profile_version: int = Field(alias="expectedProfileVersion", ge=0)


class TruthSnapshotResponse(BaseModel):
    """The ONLY shape any future consumer (resume import review, matching, tailored resumes,
    application answers, the Career Agent, WhatsApp) may use to ground generated external material
    (docs/AI_AGENT.md §6, AGENTS.md §6) -- confirmed, active, non-retracted/superseded facts only
    (docs/adr/0010-...md "truth snapshot filter")."""

    model_config = {"populate_by_name": True}

    user_id: UUID = Field(alias="userId")
    profile_version: int = Field(alias="profileVersion")
    headline: str | None = None
    summary: str | None = None
    target_roles: list[str] = Field(default_factory=list, alias="targetRoles")
    facts: list[ProfileFactResponse] = Field(default_factory=list)


__all__ = [
    "AddProfileFactRequest",
    "CareerProfileResponse",
    "ProfileFactResponse",
    "ReplaceProfileFactRequest",
    "RetractProfileFactRequest",
    "TruthSnapshotResponse",
    "UpdateCareerProfileRequest",
]

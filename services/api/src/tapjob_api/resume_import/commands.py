"""Validated, channel-first domain commands for WP1.3 (docs/adr/0011-resume-import.md "review
decisions", same discipline as `tapjob_api.career_profile.commands`):
`service.finalize_resume_import` requires this typed command, never a loosely-typed `dict`, so a
future non-HTTP channel gets the identical guarantee an HTTP caller gets.
"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from tapjob_api.resume_import.limits import MAX_CANDIDATES_PER_IMPORT


class ReviewDecisionKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT_ACCEPT = "edit_accept"


class CandidateReviewDecision(BaseModel):
    model_config = {"populate_by_name": True}

    candidate_id: UUID = Field(alias="candidateId")
    decision: ReviewDecisionKind
    # Validated against the SAME canonical `FactValue`/top-level-field rules as any other mutation
    # the moment the candidate's own kind is known (`service.py` -- this command alone cannot do it,
    # since it has no access to the candidate's stored `candidate_kind` yet).
    edited_value: dict[str, Any] | None = Field(default=None, alias="editedValue")

    @model_validator(mode="after")
    def _edited_value_matches_decision(self) -> "CandidateReviewDecision":
        if self.decision == ReviewDecisionKind.EDIT_ACCEPT and self.edited_value is None:
            raise ValueError("editedValue is required when decision is edit_accept")
        if self.decision != ReviewDecisionKind.EDIT_ACCEPT and self.edited_value is not None:
            raise ValueError("editedValue is only allowed when decision is edit_accept")
        return self


class FinalizeResumeImportCommand(BaseModel):
    """`decisions` must cover EVERY `PENDING` candidate of the target import, no more and no fewer
    (docs/adr/0011-...md "review decisions": "no acceptance by omission") -- enforced in
    `service.py` once the real pending set is known, not here. Each `candidateId` appearing at
    most ONCE, and the list never exceeding `MAX_CANDIDATES_PER_IMPORT`, ARE enforced here, at the
    command boundary itself -- round-1 independent review "Finding 4": a duplicate `candidateId`
    (e.g. `[ACCEPT, REJECT]` for the SAME id) previously survived `service.py`'s own coverage check
    unnoticed, because `{decision.candidate_id for decision in command.decisions}` is a set and
    silently collapses duplicates -- the LAST decision for a duplicated id would win in the
    dict-building loop while an EARLIER one may already have mutated `accepted_fact_candidates`,
    risking a candidate whose own `review_state` ends up `rejected` while a `ProfileFact` was still
    created from it. Rejecting duplicates here means `service.py` never needs to reason about this
    case at all -- it is structurally unreachable once this command validates."""

    model_config = {"populate_by_name": True}

    expected_profile_version: int = Field(ge=0, alias="expectedProfileVersion")
    decisions: list[CandidateReviewDecision] = Field(
        min_length=1, max_length=MAX_CANDIDATES_PER_IMPORT
    )

    @model_validator(mode="after")
    def _candidate_ids_are_unique(self) -> "FinalizeResumeImportCommand":
        ids = [decision.candidate_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("decisions must not reference the same candidateId more than once")
        return self

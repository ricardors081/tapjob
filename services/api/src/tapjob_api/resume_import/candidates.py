"""Candidate validation: the ONE place a `ports.resume_parser.ExtractedFact` (untyped, provider-
neutral) crosses into TapJob's own canonical, typed vocabulary (docs/adr/0011-resume-import.md
"resume parser port review"). Never a bare `Any`/unchecked cast and never arbitrary JSON persisted
as a trusted candidate (docs/ENGINEERING_STANDARDS.md §17 "AI output is untrusted data" -- applies
equally to this WP's own deterministic parser, since its output is still machine-derived, not yet
user-confirmed). A candidate that fails validation is simply DROPPED (fail-closed) -- never stored
as if it were valid, and never allowed to abort the whole import for one bad candidate.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from tapjob_api.career_profile.commands import HEADLINE_MAX_LENGTH, SUMMARY_MAX_LENGTH
from tapjob_api.career_profile.fact_values import (
    FACT_VALUE_MODELS,
    CertificationFactValue,
    EducationFactValue,
    ExperienceFactValue,
    LanguageFactValue,
    LinkFactValue,
    PreferenceFactValue,
    ProjectFactValue,
    PublicationFactValue,
    SkillFactValue,
    WorkAuthorizationFactValue,
    normalize_description,
    normalize_label,
)
from tapjob_api.ports.resume_parser import ExtractedFact
from tapjob_api.resume_import.limits import MAX_SOURCE_EXCERPT_CHARS


class CandidateKind(StrEnum):
    """The ten canonical `FactType` values, PLUS two WP1.3-only top-level candidate kinds
    (`HEADLINE`/`SUMMARY`) that never become a `ProfileFact` -- they map to `CareerProfile`'s own
    top-level fields instead (docs/adr/0011-...md "career profile top-level fields"). Deliberately a
    SEPARATE enum from `career_profile.models.FactType`, not a reuse/extension of it: mixing "a kind
    of professional claim" and "a kind of review candidate" into one enum would let a future caller
    accidentally construct a `ProfileFact` row with `fact_type="headline"`, which has no meaning in
    the Truth Store schema."""

    EXPERIENCE = "experience"
    EDUCATION = "education"
    SKILL = "skill"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    PROJECT = "project"
    LINK = "link"
    PREFERENCE = "preference"
    WORK_AUTHORIZATION = "work_authorization"
    PUBLICATION = "publication"
    HEADLINE = "headline"
    SUMMARY = "summary"


# fact_type -> the FactValue model that defines a valid candidate shape for it. Built once from the
# same `FACT_VALUE_MODELS` tuple WP1.2 already maintains -- never a second, independently-
# maintained mapping (docs/ENGINEERING_STANDARDS.md §1).
_FACT_VALUE_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    model.model_fields["kind"].default: model for model in FACT_VALUE_MODELS
}

FACT_CANDIDATE_KINDS = frozenset(_FACT_VALUE_MODEL_BY_KIND)
TOP_LEVEL_CANDIDATE_KINDS = frozenset({CandidateKind.HEADLINE.value, CandidateKind.SUMMARY.value})


class HeadlineCandidateValue(BaseModel):
    """The WP1.3-only top-level `headline` candidate shape -- never a `ProfileFact`, maps to
    `CareerProfile.headline` instead (docs/adr/0011-...md "career profile top-level fields").
    Carries its own `kind` so it can share ONE discriminated union with the nine `FactValue`
    models below (round-1 independent review "Finding 5")."""

    kind: Literal["headline"] = "headline"
    text: str


class SummaryCandidateValue(BaseModel):
    """The WP1.3-only top-level `summary` candidate shape -- see `HeadlineCandidateValue`."""

    kind: Literal["summary"] = "summary"
    text: str


# The full, typed candidate wire contract (round-1 independent review "Finding 5"): the nine
# canonical `FactValue` kinds PLUS the two WP1.3-only top-level kinds, discriminated the same way
# `career_profile.fact_values.FactValue` already is. Never `dict[str, Any]`/`Record<string,
# unknown>` on the OpenAPI/generated-TypeScript side -- a candidate is still unconfirmed review
# data, but its SHAPE already passed fail-closed validation in `validate_candidate`/
# `_revalidate_edited_value` before it was ever persisted, so there is no safety reason to widen
# it back to untyped JSON only for the HTTP response.
CandidateValue = Annotated[
    ExperienceFactValue
    | EducationFactValue
    | SkillFactValue
    | CertificationFactValue
    | LanguageFactValue
    | ProjectFactValue
    | LinkFactValue
    | PreferenceFactValue
    | WorkAuthorizationFactValue
    | PublicationFactValue
    | HeadlineCandidateValue
    | SummaryCandidateValue,
    Field(discriminator="kind"),
]


class ValidatedCandidate:
    """A candidate whose `value` has ALREADY passed canonical validation -- `value_json` is the
    normalized, camelCase-aliased JSON this candidate will be stored/compared as, exactly the same
    shape `ProfileFact.value_json` uses for a fact-kind candidate (so accepting it later is a
    straight copy, never a re-shape)."""

    __slots__ = ("confidence", "kind", "source_excerpt", "source_page", "value_json")

    def __init__(
        self,
        *,
        kind: str,
        value_json: dict[str, Any],
        confidence: float,
        source_excerpt: str | None,
        source_page: int | None = None,
    ) -> None:
        self.kind = kind
        self.value_json = value_json
        self.confidence = confidence
        self.source_excerpt = (source_excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None
        if source_page is not None and source_page < 1:
            raise ValueError("source_page must be >= 1")
        self.source_page = source_page


def _validate_top_level_text(kind: str, value: Mapping[str, Any]) -> dict[str, Any] | None:
    text = value.get("text")
    if not isinstance(text, str):
        return None
    try:
        if kind == CandidateKind.HEADLINE.value:
            normalized = normalize_label(text, field_name="headline")
            if normalized is None or len(normalized) > HEADLINE_MAX_LENGTH:
                return None
        else:
            normalized = normalize_description(text, field_name="summary")
            if normalized is None or len(normalized) > SUMMARY_MAX_LENGTH:
                return None
    except ValueError:
        return None
    # `kind` is embedded in the stored value itself, same discipline as every FactValue model's own
    # `kind` field -- makes the candidate's `value_json` self-describing/discriminable without a
    # second lookup against `candidate_kind` (round-1 independent review "Finding 5").
    return {"kind": kind, "text": normalized}


def validate_candidate(fact: ExtractedFact) -> ValidatedCandidate | None:
    """Returns `None` (drop the candidate) when `fact.value` does not match a canonical shape for
    `fact.fact_type` -- fail-closed, never a passthrough of unvalidated structure
    (docs/adr/0011-...md "resume parser port review")."""
    kind = fact.fact_type
    if kind in TOP_LEVEL_CANDIDATE_KINDS:
        normalized = _validate_top_level_text(kind, fact.value)
        if normalized is None:
            return None
        return ValidatedCandidate(
            kind=kind,
            value_json=normalized,
            confidence=fact.confidence,
            source_excerpt=fact.source_excerpt,
            source_page=fact.source_page,
        )

    model = _FACT_VALUE_MODEL_BY_KIND.get(kind)
    if model is None:
        return None
    try:
        validated = model.model_validate(dict(fact.value))
    except ValidationError:
        return None
    return ValidatedCandidate(
        kind=kind,
        value_json=validated.model_dump(mode="json", by_alias=True),
        confidence=fact.confidence,
        source_excerpt=fact.source_excerpt,
        source_page=fact.source_page,
    )

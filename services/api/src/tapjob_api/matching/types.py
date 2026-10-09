"""Domain types, enums, and dataclasses for WP2.2 Matching Engine (ADR-0014).

All models here are internal pure domain types or DTOs, strictly bounded and fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from tapjob_api.career_profile.fact_values import SalaryPeriod
from tapjob_api.jobs.models import JobWorkplaceType

MATCHING_POLICY_VERSION: str = "2026.1"
MINIMUM_ELIGIBLE_SCORE: int = 50


class HardFilterId(StrEnum):
    """The five canonical knock-out gates defined in ADR-0014."""

    WORK_AUTHORIZATION = "work_authorization"
    WORKPLACE_TYPE = "workplace_type"
    LOCATION = "location"
    SALARY_MINIMUM = "salary_minimum"
    ROLE_ALIGNMENT = "role_alignment"


class HardFilterOutcome(StrEnum):
    """Outcome classification for a hard-filter gate evaluation."""

    PASSED = "passed"
    HARD_BLOCKER = "hard_blocker"
    SOFT_MISMATCH = "soft_mismatch"
    UNKNOWN = "unknown"


class MatchEligibility(StrEnum):
    """Final match eligibility decision."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    REVIEW_REQUIRED = "review_required"


class CriticalUnknown(StrEnum):
    """Critical ambiguities requiring user review before application."""

    WORK_AUTHORIZATION_AMBIGUOUS = "work_authorization_ambiguous"
    SPARSE_PROFILE = "sparse_profile"


class JobSeniorityLevel(StrEnum):
    """Standardized seniority levels extracted from job postings."""

    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    STAFF_PRINCIPAL = "staff_principal"


class CandidateSeniorityLevel(StrEnum):
    """Derived seniority level for candidate from confirmed experience."""

    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    STAFF_PRINCIPAL = "staff_principal"
    UNKNOWN = "unknown"


class WorkAuthSemanticValue(StrEnum):
    """Semantic work authorization status mapped question-key-aware."""

    AUTHORIZED = "authorized"
    NOT_AUTHORIZED = "not_authorized"
    REQUIRES_SPONSORSHIP = "requires_sponsorship"
    DOES_NOT_REQUIRE_SPONSORSHIP = "does_not_require_sponsorship"
    UNKNOWN = "unknown"


# -- Job Signals --


@dataclass(frozen=True)
class JobSalarySignal:
    min_minor: int
    max_minor: int
    currency: str
    period: SalaryPeriod | None
    source_span: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_minor": self.min_minor,
            "max_minor": self.max_minor,
            "currency": self.currency,
            "period": self.period.value if self.period else None,
            "source_span": self.source_span,
        }


@dataclass(frozen=True)
class JobWorkAuthSignal:
    requires_existing_authorization: bool
    sponsorship_offered: bool | None
    jurisdiction: str | None
    source_span: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requires_existing_authorization": self.requires_existing_authorization,
            "sponsorship_offered": self.sponsorship_offered,
            "jurisdiction": self.jurisdiction,
            "source_span": self.source_span,
        }


@dataclass(frozen=True)
class DerivedJobMatchSignals:
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    unclassified_skills: tuple[str, ...]
    seniority_signal: JobSeniorityLevel | None
    salary_requirement: JobSalarySignal | None
    work_authorization_requirement: JobWorkAuthSignal | None
    unknown_signals: tuple[str, ...]
    domain_tokens: tuple[str, ...] = ()


# -- Candidate Signals --


@dataclass(frozen=True)
class CandidateSkillSignal:
    canonical_skill: str
    fact_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class CandidateExperienceSignal:
    title: str
    title_normalized: str
    company_name: str
    duration_months: int
    fact_id: uuid.UUID


@dataclass(frozen=True)
class CandidateDomainTokenSignal:
    canonical_token: str
    fact_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class CandidateWorkAuthorizationSignal:
    jurisdiction: str
    question_key: str
    semantic_value: WorkAuthSemanticValue
    fact_ids: tuple[uuid.UUID, ...] = ()

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, WorkAuthSemanticValue):
            return self.semantic_value == other
        if isinstance(other, CandidateWorkAuthorizationSignal):
            return (
                self.jurisdiction == other.jurisdiction
                and self.question_key == other.question_key
                and self.semantic_value == other.semantic_value
                and self.fact_ids == other.fact_ids
            )
        return False


@dataclass(frozen=True)
class CandidateSalaryPreference:
    minor_unit_min: int | None
    minor_unit_max: int | None
    currency: str
    period: SalaryPeriod
    fact_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class CandidateLocationPreference:
    country: str | None
    region: str | None
    city: str | None
    fact_ids: tuple[uuid.UUID, ...] = ()

    @property
    def total_sort_key(self) -> tuple[str, str, str]:
        return (self.country or "", self.region or "", self.city or "")


@dataclass(frozen=True)
class CandidateMatchSignals:
    skills: tuple[CandidateSkillSignal, ...]
    skill_names: frozenset[str]
    experiences: tuple[CandidateExperienceSignal, ...]
    total_experience_months: int
    domain_tokens: tuple[CandidateDomainTokenSignal, ...]
    domain_token_names: frozenset[str]
    target_roles: tuple[str, ...]
    derived_seniority: CandidateSeniorityLevel
    salary_preference: CandidateSalaryPreference | None
    salary_ambiguous: bool
    acceptable_workplace_types: frozenset[JobWorkplaceType]
    acceptable_locations: tuple[CandidateLocationPreference, ...]
    work_authorizations: Mapping[
        tuple[str, str], CandidateWorkAuthorizationSignal | WorkAuthSemanticValue
    ]
    confirmed_fact_count: int
    workplace_preference_fact_ids: tuple[uuid.UUID, ...] = ()


# -- Evaluation Output Types --


@dataclass(frozen=True)
class HardFilterResult:
    filter_id: HardFilterId
    outcome: HardFilterOutcome
    reason: str
    source_facts: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filter_id": self.filter_id.value,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "source_facts": sorted([str(fid) for fid in self.source_facts]),
        }


@dataclass(frozen=True)
class ScoreComponents:
    title: int
    skills: int
    experience: int
    seniority: int
    workplace_location: int

    def to_dict(self) -> dict[str, int]:
        return {
            "title": self.title,
            "skills": self.skills,
            "experience": self.experience,
            "seniority": self.seniority,
            "workplace_location": self.workplace_location,
        }


@dataclass(frozen=True)
class MatchBlocker:
    code: str
    message: str
    filter_id: HardFilterId

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "filter_id": self.filter_id.value,
        }


@dataclass(frozen=True)
class MatchStrength:
    name: str
    description: str
    fact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "fact_ids": sorted([str(fid) for fid in self.fact_ids]),
        }


@dataclass(frozen=True)
class MatchRequirementGap:
    name: str
    description: str
    is_mandatory: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "is_mandatory": self.is_mandatory,
        }


@dataclass(frozen=True)
class MatchExplanation:
    summary_headline: str
    strengths_summary: str
    gaps_summary: str
    verdict_summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "summary_headline": self.summary_headline,
            "strengths_summary": self.strengths_summary,
            "gaps_summary": self.gaps_summary,
            "verdict_summary": self.verdict_summary,
        }


@dataclass(frozen=True)
class DeterministicMatchCore:
    overall_score: int
    eligibility: MatchEligibility
    hard_filter_results: tuple[HardFilterResult, ...]
    score_components: ScoreComponents
    blockers: tuple[MatchBlocker, ...]
    strengths: tuple[MatchStrength, ...]
    missing_requirements: tuple[MatchRequirementGap, ...]
    critical_unknowns: tuple[CriticalUnknown, ...]
    deterministic_template_explanation: MatchExplanation
    grounded_fact_ids: tuple[uuid.UUID, ...]
    profile_version: int
    job_match_fingerprint: str
    policy_version: str
    evaluation_as_of_date: date
    deterministic_result_digest: str


def to_canonical_payload(core: DeterministicMatchCore) -> dict[str, Any]:
    """Produces the bitwise canonical payload for deterministic_result_digest.

    Covers all 14 fields of DeterministicMatchCore with sorted collections.
    """
    return {
        "blockers": [b.to_dict() for b in sorted(core.blockers, key=lambda x: x.code)],
        "critical_unknowns": sorted([u.value for u in core.critical_unknowns]),
        "deterministic_template_explanation": (core.deterministic_template_explanation.to_dict()),
        "eligibility": core.eligibility.value,
        "evaluation_as_of_date": core.evaluation_as_of_date.isoformat(),
        "grounded_fact_ids": sorted([str(fid) for fid in core.grounded_fact_ids]),
        "hard_filter_results": [
            f.to_dict() for f in sorted(core.hard_filter_results, key=lambda x: x.filter_id.value)
        ],
        "job_match_fingerprint": core.job_match_fingerprint,
        "missing_requirements": [
            m.to_dict() for m in sorted(core.missing_requirements, key=lambda x: x.name)
        ],
        "overall_score": core.overall_score,
        "policy_version": core.policy_version,
        "profile_version": core.profile_version,
        "score_components": core.score_components.to_dict(),
        "strengths": [s.to_dict() for s in sorted(core.strengths, key=lambda x: x.name)],
    }


def compute_deterministic_result_digest(core: DeterministicMatchCore) -> str:
    """Computes SHA-256 digest of canonical DeterministicMatchCore payload."""
    payload = to_canonical_payload(core)
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

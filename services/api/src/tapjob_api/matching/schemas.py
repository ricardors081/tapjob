"""Pydantic wire schemas and DTOs for Matching API (WP2.2).

Follows camelCase wire serialization conventions.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from tapjob_api.matching.types import (
    CriticalUnknown,
    HardFilterId,
    HardFilterOutcome,
    MatchEligibility,
)


class ScoreBreakdownDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: int = Field(ge=0, le=100)
    skills: int = Field(ge=0, le=100)
    experience: int = Field(ge=0, le=100)
    seniority: int = Field(ge=0, le=100)
    workplace_location: int = Field(ge=0, le=100, alias="workplaceLocation")


class HardFilterResultDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filter_id: HardFilterId = Field(alias="filterId")
    outcome: HardFilterOutcome
    reason: str
    source_facts: list[uuid.UUID] = Field(default_factory=list, alias="sourceFacts")


class MatchBlockerDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    filter_id: HardFilterId = Field(alias="filterId")


class MatchStrengthDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    fact_ids: list[uuid.UUID] = Field(default_factory=list, alias="factIds")


class MatchRequirementGapDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    is_mandatory: bool = Field(alias="isMandatory")


class MatchExplanationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    summary_headline: str = Field(alias="summaryHeadline")
    strengths_summary: str = Field(alias="strengthsSummary")
    gaps_summary: str = Field(alias="gapsSummary")
    verdict_summary: str = Field(alias="verdictSummary")


class MatchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    job_id: uuid.UUID = Field(alias="jobId")
    overall_score: int = Field(ge=0, le=100, alias="overallScore")
    eligibility: MatchEligibility
    score_breakdown: ScoreBreakdownDto = Field(alias="scoreBreakdown")
    hard_filter_results: list[HardFilterResultDto] = Field(alias="hardFilterResults")
    blockers: list[MatchBlockerDto]
    strengths: list[MatchStrengthDto]
    missing_requirements: list[MatchRequirementGapDto] = Field(alias="missingRequirements")
    critical_unknowns: list[CriticalUnknown] = Field(alias="criticalUnknowns")
    explanation: MatchExplanationDto
    grounded_fact_ids: list[uuid.UUID] = Field(alias="groundedFactIds")
    profile_version: int = Field(alias="profileVersion")
    job_match_fingerprint: str = Field(alias="jobMatchFingerprint")
    policy_version: str = Field(alias="policyVersion")
    evaluation_as_of_date: date = Field(alias="evaluationAsOfDate")
    deterministic_result_digest: str = Field(alias="deterministicResultDigest")
    computed_at: datetime = Field(alias="computedAt")

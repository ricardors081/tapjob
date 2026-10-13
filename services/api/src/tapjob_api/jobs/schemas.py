"""Pydantic schemas and DTOs for Jobs and Manual Intake (WP2.1-A)."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tapjob_api.jobs.models import (
    JobAlertDeliveryChannel,
    JobStatus,
    JobWorkplaceType,
    ObservationStatus,
)


class ProvenanceFieldSource(StrEnum):
    """Origin category of a normalized field value."""

    SOURCE_DECLARED = "source_declared"
    HEURISTIC_EXTRACTED = "heuristic_extracted"
    USER_DIRECT = "user_direct"


class JobProvenanceSummary(BaseModel):
    """Bounded, typed breakdown of field provenance."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: ProvenanceFieldSource
    company: ProvenanceFieldSource
    location: ProvenanceFieldSource | None = None
    workplace_type: ProvenanceFieldSource = Field(alias="workplaceType")
    description: ProvenanceFieldSource


class CompanyDto(BaseModel):
    """Canonical representation of an organization."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    name: str
    name_normalized: str = Field(alias="nameNormalized")
    domain: str | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class JobOriginDto(BaseModel):
    """Observation and discovery origin of a job."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    job_id: UUID = Field(alias="jobId")
    provider: str
    external_id: str | None = Field(None, alias="externalId")
    source_url: str | None = Field(None, alias="sourceUrl")
    canonical_url: str | None = Field(None, alias="canonicalUrl")
    observation_status: ObservationStatus = Field(alias="observationStatus")
    consecutive_not_found_count: int = Field(alias="consecutiveNotFoundCount")
    first_observed_at: datetime = Field(alias="firstObservedAt")
    last_observed_at: datetime = Field(alias="lastObservedAt")
    provenance_metadata: dict[str, Any] = Field(alias="provenanceMetadata")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class JobResponse(BaseModel):
    """Canonical job posting wire representation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    company_id: UUID = Field(alias="companyId")
    company: CompanyDto
    title: str
    title_normalized: str = Field(alias="titleNormalized")
    location_raw: str | None = Field(None, alias="locationRaw")
    location_normalized: str | None = Field(None, alias="locationNormalized")
    workplace_type: JobWorkplaceType = Field(alias="workplaceType")
    description_raw: str = Field(alias="descriptionRaw")
    description_cleaned: str = Field(alias="descriptionCleaned")
    similarity_fingerprint: str = Field(alias="similarityFingerprint")
    status: JobStatus
    provenance_summary: JobProvenanceSummary = Field(alias="provenanceSummary")
    origins: list[JobOriginDto] = Field(default_factory=list)
    expires_at: datetime | None = Field(None, alias="expiresAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class JobPasteIntakeRequest(BaseModel):
    """Payload for manual paste intake."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    company_name: str = Field(..., min_length=1, max_length=255, alias="companyName")
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=10, max_length=100000)
    location_raw: str | None = Field(None, max_length=255, alias="locationRaw")
    source_url: str | None = Field(None, max_length=2048, alias="sourceUrl")

    @field_validator("company_name", "title")
    @classmethod
    def _strip_non_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be blank")
        return stripped


class JobUrlIntakeRequest(BaseModel):
    """Payload for safe URL intake."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    url: str = Field(..., min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _strip_non_blank_url(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("URL cannot be blank")
        return stripped


class JobMatchSummaryDto(BaseModel):
    """Grounded match evaluation summary attached to job detail."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    overall_score: int = Field(ge=0, le=100, alias="overallScore")
    eligibility: str
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[dict[str, Any]] = Field(default_factory=list)
    missing_requirements: list[dict[str, Any]] = Field(
        default_factory=list, alias="missingRequirements"
    )
    critical_unknowns: list[str] = Field(default_factory=list, alias="criticalUnknowns")
    explanation: dict[str, Any] | None = None


class FeedItemResponse(BaseModel):
    """Single opportunity card in the personalized job feed."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    company: CompanyDto
    title: str
    title_normalized: str = Field(alias="titleNormalized")
    location_raw: str | None = Field(None, alias="locationRaw")
    location_normalized: str | None = Field(None, alias="locationNormalized")
    workplace_type: JobWorkplaceType = Field(alias="workplaceType")
    status: JobStatus
    match_score: int | None = Field(None, alias="matchScore")
    eligibility: str | None = None
    is_saved: bool = Field(default=False, alias="isSaved")
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime | None = Field(None, alias="expiresAt")


class JobDetailResponse(JobResponse):
    """Enriched job detail response including WP2.2 match intelligence and user intent."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    match: JobMatchSummaryDto | None = None
    is_saved: bool = Field(default=False, alias="isSaved")
    is_dismissed: bool = Field(default=False, alias="isDismissed")
    is_company_watched: bool = Field(default=False, alias="isCompanyWatched")


class SavedJobResponse(BaseModel):
    """User-saved job item."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    job_id: UUID = Field(alias="jobId")
    state: str
    created_at: datetime = Field(alias="createdAt")
    job: FeedItemResponse


class CompanyWatchResponse(BaseModel):
    """Watched company entry."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    company_id: UUID = Field(alias="companyId")
    company: CompanyDto
    created_at: datetime = Field(alias="createdAt")


class JobAlertPreferenceResponse(BaseModel):
    """Job alert subscription preference."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool
    min_score: int = Field(ge=0, le=100, alias="minScore")
    min_eligibility: str = Field(alias="minEligibility")
    delivery_channel: JobAlertDeliveryChannel = Field(alias="deliveryChannel")


class UpdateJobAlertPreferenceRequest(BaseModel):
    """Payload to update job alert preferences."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    min_score: int = Field(default=50, ge=0, le=100, alias="minScore")
    min_eligibility: str = Field(default="eligible", alias="minEligibility")
    delivery_channel: JobAlertDeliveryChannel = Field(
        default=JobAlertDeliveryChannel.WHATSAPP, alias="deliveryChannel"
    )

    @field_validator("min_eligibility")
    @classmethod
    def _validate_eligibility(cls, v: str) -> str:
        if v not in ("eligible", "review_required"):
            raise ValueError("min_eligibility must be 'eligible' or 'review_required'")
        return v

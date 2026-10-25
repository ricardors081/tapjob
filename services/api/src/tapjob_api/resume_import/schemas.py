"""HTTP request/response DTOs for `/api/v1/me/resume-imports` and `/api/v1/me/resumes/master`
(docs/adr/0011-resume-import.md). CamelCase wire format, same convention as every other WP
(docs/ENGINEERING_STANDARDS.md §9).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from tapjob_api.resume_import.candidates import CandidateValue
from tapjob_api.resume_import.commands import FinalizeResumeImportCommand
from tapjob_api.resume_import.models import CandidateReviewState, ImportFailureCode, ImportStatus

# Re-exported so `router.py` imports the request body type from this module like every other
# schema, while `service.py`/a future non-HTTP channel still depends only on `commands.py`.
FinalizeResumeImportRequest = FinalizeResumeImportCommand


class MasterResumeSnapshot(BaseModel):
    """The bounded, typed shape persisted as `ResumeVersion.content_json`
    (docs/adr/0011-...md "content_json", "resume version grounding"). `fact_ids` is the complete,
    authoritative set of confirmed `ProfileFact` ids this version's claims are grounded in -- a
    future Resume Studio/tailoring consumer reconstructs full fact records by id rather than
    trusting any text copied in here directly."""

    model_config = {"populate_by_name": True}

    headline: str | None = None
    summary: str | None = None
    fact_ids: list[str] = Field(default_factory=list, alias="factIds")
    source_import_id: str = Field(alias="sourceImportId")


class ResumeImportCandidateResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    candidate_kind: str = Field(alias="candidateKind")
    # Typed, discriminated union across the nine `FactValue` kinds AND the two WP1.3-only
    # top-level candidate kinds (`candidates.CandidateValue`) -- same discipline as
    # `ProfileFactResponse.value` in `career_profile.schemas` (round-1 independent review
    # "Finding 5"): a candidate's shape was already fail-closed-validated at parse/edit time
    # (`tapjob_api.resume_import.candidates.validate_candidate`/`_revalidate_edited_value`), so the
    # wire contract reflects that -- never `Record<string, unknown>`/arbitrary JSON on the
    # generated-TypeScript side.
    value: CandidateValue
    confidence: float
    source_excerpt: str | None = Field(default=None, alias="sourceExcerpt")
    review_state: CandidateReviewState = Field(alias="reviewState")
    created_at: datetime = Field(alias="createdAt")


class ResumeImportResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    status: ImportStatus
    original_filename: str = Field(alias="originalFilename")
    media_type: str = Field(alias="mediaType")
    byte_size: int = Field(alias="byteSize")
    failure_code: ImportFailureCode | None = Field(default=None, alias="failureCode")
    # Populated only once `status == READY_FOR_REVIEW` or later -- `None` while queued/processing
    # (docs/adr/0011-...md "import lifecycle").
    candidates: list[ResumeImportCandidateResponse] | None = None
    finalized_resume_id: UUID | None = Field(default=None, alias="finalizedResumeId")
    finalized_resume_version_id: UUID | None = Field(default=None, alias="finalizedResumeVersionId")
    ambiguities_count: int | None = Field(default=None, alias="ambiguitiesCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class FinalizeResumeImportResponse(BaseModel):
    model_config = {"populate_by_name": True}

    resume_id: UUID = Field(alias="resumeId")
    resume_version_id: UUID = Field(alias="resumeVersionId")
    version: int
    profile_version: int = Field(alias="profileVersion")
    headline: str | None
    summary: str | None
    fact_ids: list[str] = Field(alias="factIds")
    created_at: datetime = Field(alias="createdAt")


class MasterResumeResponse(BaseModel):
    model_config = {"populate_by_name": True}

    resume_id: UUID = Field(alias="resumeId")
    resume_version_id: UUID = Field(alias="resumeVersionId")
    version: int
    profile_version: int = Field(alias="profileVersion")
    headline: str | None
    summary: str | None
    fact_ids: list[str] = Field(alias="factIds")
    generated_by: str = Field(alias="generatedBy")
    created_at: datetime = Field(alias="createdAt")

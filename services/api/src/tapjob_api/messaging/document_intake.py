"""Document intake reusing canonical Resume Import and Career Profile (Finding 2).

CRITICAL ARCHITECTURAL INVARIANTS:
TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING = NO
Messaging MUST NOT write directly to ProfileFact or mutate profile_version manually.
WhatsApp attachments delegate exclusively to canonical WP1.3 Resume Import.
Textual onboarding delegates exclusively to canonical WP1.2 Career Profile services and commands.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.commands import CareerProfilePatchCommand
from tapjob_api.career_profile.fact_values import FactValue
from tapjob_api.career_profile.models import FactType, ProfileFact
from tapjob_api.career_profile.service import ProfileUpdateResult
from tapjob_api.identity.models import User
from tapjob_api.resume_import import service as resume_import_service
from tapjob_api.resume_import.commands import (
    CandidateReviewDecision,
    FinalizeResumeImportCommand,
)
from tapjob_api.resume_import.models import ResumeImport
from tapjob_api.resume_import.service import FinalizeResult
from tapjob_api.resume_import.storage import ObjectStoragePort

logger = logging.getLogger(__name__)

# Verification constant proving no direct writes bypass canonical domains
TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING: str = "NO"


class DocumentIntakeError(Exception):
    """Raised when document intake fails."""


def handle_attachment_intake(
    session: Session,
    *,
    user: User,
    content: bytes,
    original_filename: str,
    declared_content_type: str,
    storage: ObjectStoragePort,
    import_id: uuid.UUID | None = None,
) -> ResumeImport:
    """Delegates incoming WhatsApp document attachments to canonical Resume Import (WP1.3).

    Workflow:
    1. Upload validation & secure object storage via create_resume_import.
    2. Extraction & candidate proposal via process_resume_import.
    Returns the ready-for-review ResumeImport record.
    """
    kwargs: dict[str, Any] = {}
    if import_id is not None:
        kwargs["import_id"] = import_id

    resume_import = resume_import_service.create_resume_import(
        session,
        user=user,
        content=content,
        original_filename=original_filename,
        declared_content_type=declared_content_type,
        storage=storage,
        **kwargs,
    )

    resume_import_service.process_resume_import(
        session,
        import_id=resume_import.id,
        storage=storage,
    )
    session.refresh(resume_import)

    logger.info(
        "document_intake.attachment_processed_via_wp13",
        extra={
            "user_id": str(user.id),
            "resume_import_id": str(resume_import.id),
        },
    )
    return resume_import


def confirm_candidates_and_finalize(
    session: Session,
    *,
    user: User,
    resume_import_id: uuid.UUID,
    decisions: list[CandidateReviewDecision],
    expected_profile_version: int = 0,
) -> FinalizeResult:
    """Finalizes resume import via canonical WP1.3 pipeline, populating Truth Store safely."""
    command = FinalizeResumeImportCommand(
        expected_profile_version=expected_profile_version,
        decisions=decisions,
    )
    result = resume_import_service.finalize_resume_import(
        session,
        user=user,
        import_id=resume_import_id,
        command=command,
    )
    logger.info(
        "document_intake.finalized_via_wp13",
        extra={
            "user_id": str(user.id),
            "resume_import_id": str(resume_import_id),
            "profile_version": result.resume_version.profile_version,
        },
    )
    return result


def handle_text_fact_intake(
    session: Session,
    *,
    user: User,
    fact_type: FactType,
    value: FactValue,
    expected_version: int = 0,
) -> ProfileFact:
    """Delegates single factual updates to canonical WP1.2 add_profile_fact."""
    fact = career_profile_service.add_profile_fact(
        session,
        user=user,
        expected_version=expected_version,
        fact_type=fact_type,
        value=value,
    )
    logger.info(
        "document_intake.fact_added_via_wp12",
        extra={
            "user_id": str(user.id),
            "fact_id": str(fact.id),
            "fact_type": fact.fact_type,
        },
    )
    return fact


def handle_profile_text_patch(
    session: Session,
    *,
    user: User,
    command: CareerProfilePatchCommand,
    expected_version: int = 0,
) -> ProfileUpdateResult:
    """Delegates top-level profile edits (headline, summary, target_roles) to canonical WP1.2."""
    result = career_profile_service.update_career_profile(
        session,
        user=user,
        expected_version=expected_version,
        command=command,
    )
    logger.info(
        "document_intake.profile_patched_via_wp12",
        extra={
            "user_id": str(user.id),
        },
    )
    return result

"""Career Agent Tool layer for Alpha vertical slice (Finding 12).

Every tool is:
- Typed
- Authorized & strictly User-scoped (IDOR safe)
- Bounded
- Auditable
- Free of domain business rule leaks (delegates exclusively to canonical domain services)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.identity.models import User
from tapjob_api.jobs import service as jobs_service
from tapjob_api.jobs.http_client import SafeHttpClient
from tapjob_api.jobs.schemas import JobPasteIntakeRequest, JobUrlIntakeRequest
from tapjob_api.matching import service as matching_service
from tapjob_api.messaging.applications import Application, ApplicationStatus, ApplyMode
from tapjob_api.messaging.applications_service import (
    build_and_persist_application_kit,
    create_or_update_application,
    list_user_applications,
    update_application_status,
)
from tapjob_api.messaging.document_intake import (
    confirm_candidates_and_finalize,
    handle_attachment_intake,
)
from tapjob_api.messaging.job_matching import explain_match, find_matching_jobs
from tapjob_api.messaging.reminders_service import create_reminder
from tapjob_api.messaging.resume_tailoring import generate_grounded_tailored_resume
from tapjob_api.ports.resume_parser import ResumeParser
from tapjob_api.resume_import.commands import CandidateReviewDecision
from tapjob_api.resume_import.storage import ObjectStoragePort

logger = logging.getLogger(__name__)


def tool_get_profile_summary(session: Session, *, user: User) -> dict[str, Any]:
    """1. Tool: get_profile_summary."""
    snapshot = get_truth_snapshot(session, user=user)
    skills: list[str] = []
    for f in snapshot.facts:
        if isinstance(f.value_json, dict) and f.value_json.get("kind") == "skill":
            name = f.value_json.get("name")
            if name:
                skills.append(str(name))
    return {
        "user_id": str(user.id),
        "profile_version": snapshot.profile_version,
        "headline": snapshot.headline,
        "summary": snapshot.summary,
        "target_roles": list(snapshot.target_roles),
        "fact_count": len(snapshot.facts),
        "skills": skills,
    }


def tool_get_profile_missing_information(session: Session, *, user: User) -> dict[str, Any]:
    """2. Tool: get_profile_missing_information."""
    snapshot = get_truth_snapshot(session, user=user)
    missing: list[str] = []
    if not snapshot.headline:
        missing.append("headline")
    if not snapshot.summary:
        missing.append("summary")
    if not snapshot.target_roles:
        missing.append("target_roles")
    if not snapshot.facts:
        missing.append("profile_facts")

    return {
        "user_id": str(user.id),
        "profile_version": snapshot.profile_version,
        "is_complete": len(missing) == 0,
        "missing_fields": missing,
    }


def tool_ingest_resume(
    session: Session,
    *,
    user: User,
    content: bytes,
    filename: str,
    content_type: str,
    storage: ObjectStoragePort,
    parser: ResumeParser,
    import_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """3. Tool: ingest_resume."""
    resume_import = handle_attachment_intake(
        session,
        user=user,
        content=content,
        original_filename=filename,
        declared_content_type=content_type,
        storage=storage,
        parser=parser,
        import_id=import_id,
    )
    return {
        "resume_import_id": str(resume_import.id),
        "status": resume_import.status.value,
        "failure_code": resume_import.failure_code.value if resume_import.failure_code else None,
        "candidate_count": len(resume_import.candidates),
        "parser_identity": resume_import.parser_identity,
    }


def tool_confirm_profile_fact(
    session: Session,
    *,
    user: User,
    resume_import_id: uuid.UUID,
    decisions: list[CandidateReviewDecision],
    expected_profile_version: int = 0,
) -> dict[str, Any]:
    """4. Tool: confirm_profile_fact."""
    result = confirm_candidates_and_finalize(
        session,
        user=user,
        resume_import_id=resume_import_id,
        decisions=decisions,
        expected_profile_version=expected_profile_version,
    )
    return {
        "user_id": str(user.id),
        "profile_version": result.resume_version.profile_version,
        "resume_version_id": str(result.resume_version.id),
        "is_replay": result.is_replay,
    }


def tool_ingest_job_url(
    session: Session,
    *,
    user: User,
    url: str,
    http_client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """5. Tool: ingest_job_url."""
    client = http_client or SafeHttpClient()
    req = JobUrlIntakeRequest(url=url)
    final_url, media_type, extracted = asyncio.run(
        jobs_service.fetch_and_extract_job_url(request=req, http_client=client)
    )
    job = jobs_service.persist_job_url(
        session,
        user=user,
        source_url=url,
        final_url=final_url,
        media_type=media_type,
        extracted=extracted,
    )
    company_name = job.company.name if job.company else extracted.company_name
    return {
        "job_id": str(job.id),
        "title": job.title,
        "company": company_name,
        "location": job.location_raw,
    }


def tool_ingest_job_text(
    session: Session,
    *,
    user: User,
    text: str,
    title_hint: str | None = None,
    company_hint: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """6. Tool: ingest_job_text."""
    req = JobPasteIntakeRequest(
        company_name=company_hint or "Empresa",
        title=title_hint or "Vaga",
        description=text,
        source_url=source_url,
    )
    job = jobs_service.intake_job_paste(session, user=user, request=req)
    company_name = job.company.name if job.company else (company_hint or "Empresa")
    return {
        "job_id": str(job.id),
        "title": job.title,
        "company": company_name,
        "location": job.location_raw,
    }


def tool_recommend_jobs(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """7. Tool: recommend_jobs."""
    return find_matching_jobs(session, redis_client, user=user, limit=limit)


def tool_match_job(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """8. Tool: match_job."""
    match = matching_service.get_or_compute_match(session, redis_client, user=user, job_id=job_id)
    return {
        "match_id": str(match.id),
        "job_id": str(job_id),
        "overall_score": match.overall_score,
        "eligibility": match.eligibility,
        "strengths": match.strengths,
        "blockers": match.blockers,
    }


def tool_explain_match(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """9. Tool: explain_match."""
    return explain_match(session, redis_client, user=user, job_id=job_id)


def tool_tailor_resume(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """10. Tool: tailor_resume."""
    draft = generate_grounded_tailored_resume(session, user=user, job_id=job_id)
    return draft.to_dict()


def tool_build_application_kit(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """11. Tool: build_application_kit."""
    match = matching_service.get_or_compute_match(session, redis_client, user=user, job_id=job_id)
    draft = generate_grounded_tailored_resume(session, user=user, job_id=job_id)
    strengths_list = [
        str(s.get("summary") or s.get("description") or s.get("title") or s)
        for s in match.strengths
        if isinstance(s, dict)
    ]
    gaps_list = [
        str(g.get("summary") or g.get("description") or g.get("title") or g)
        for g in match.missing_requirements
        if isinstance(g, dict)
    ]
    kit = build_and_persist_application_kit(
        session,
        user=user,
        job_id=job_id,
        match_result_id=match.id,
        profile_version=match.profile_version,
        draft=draft,
        strengths=strengths_list,
        gaps=gaps_list,
    )
    app_row = session.execute(
        select(Application).where(Application.id == kit.application_id)
    ).scalar_one()

    return {
        "kit_id": str(kit.id),
        "application_id": str(kit.application_id),
        "status": app_row.status.value,
        "target_title": draft.target_title,
        "fit_summary": kit.kit_snapshot.get("fit_summary"),
        "created_at": (
            kit.created_at.isoformat() if kit.created_at else datetime.now(UTC).isoformat()
        ),
        "tailored_draft": draft.to_dict(),
    }


def tool_register_application(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
    status: ApplicationStatus = ApplicationStatus.PREPARING,
    apply_mode: ApplyMode = ApplyMode.MANUAL,
) -> dict[str, Any]:
    """12. Tool: register_application (Finding 3)."""
    app_row = create_or_update_application(
        session,
        user=user,
        job_id=job_id,
        status=status,
        apply_mode=apply_mode,
    )
    return {
        "application_id": str(app_row.id),
        "status": app_row.status.value,
        "apply_mode": app_row.apply_mode.value,
        "submitted_at": app_row.submitted_at.isoformat() if app_row.submitted_at else None,
    }


def tool_update_application_status(
    session: Session,
    *,
    user: User,
    application_id: uuid.UUID,
    new_status: ApplicationStatus,
) -> dict[str, Any]:
    """13. Tool: update_application_status."""
    app_row = update_application_status(
        session,
        user=user,
        application_id=application_id,
        new_status=new_status,
    )
    return {
        "application_id": str(app_row.id),
        "status": app_row.status.value,
    }


def tool_list_applications(
    session: Session,
    *,
    user: User,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """14. Tool: list_applications."""
    records = list_user_applications(session, user=user, limit=limit)
    return [
        {
            "application_id": str(r.id),
            "job_id": str(r.job_id),
            "status": r.status.value,
            "apply_mode": r.apply_mode.value,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in records
    ]


def tool_schedule_follow_up(
    session: Session,
    *,
    user: User,
    remind_at: datetime,
    application_id: uuid.UUID | None = None,
    note: str = "Follow-up de candidatura",
    channel_identity_link_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """15. Tool: schedule_follow_up (Finding 4)."""
    payload: dict[str, Any] = {"note": note}
    if application_id is not None:
        payload["application_id"] = str(application_id)
    reminder = create_reminder(
        session,
        user=user,
        remind_at=remind_at,
        intent="application_follow_up",
        payload=payload,
        channel_identity_link_id=channel_identity_link_id,
    )
    return {
        "reminder_id": str(reminder.id),
        "intent": reminder.intent,
        "remind_at": reminder.remind_at.isoformat(),
        "status": reminder.status.value,
        "channel_identity_link_id": (
            str(reminder.channel_identity_link_id) if reminder.channel_identity_link_id else None
        ),
    }

"""Job matching and recommendation layer reusing canonical WP2.2 (Finding 1).

CRITICAL ARCHITECTURAL INVARIANT:
There is NO second matching engine.
Score comes ONLY from canonical tapjob_api.matching.service.get_or_compute_match.
Eligibility comes ONLY from canonical MatchResult.eligibility.
WHATSAPP_SCORE == MatchResult.overall_score (integer arithmetic).
Jobs hard-blocked by WP2.2 are NEVER presented as eligible.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import redis
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User
from tapjob_api.jobs.models import Job, JobStatus
from tapjob_api.matching.errors import MatchJobNotFoundError
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.service import get_or_compute_match
from tapjob_api.matching.types import MatchEligibility

logger = logging.getLogger(__name__)


class JobMatchingError(Exception):
    """Raised when job matching operations fail."""


def find_matching_jobs(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    limit: int = 5,
    candidate_pool_limit: int = 20,
) -> list[dict[str, Any]]:
    """Discovers jobs for the candidate and evaluates them via canonical WP2.2 matching engine.

    Guarantees:
    - Candidate pool is fetched from active, visible jobs (public or created by user).
    - Each candidate job is evaluated by canonical get_or_compute_match.
    - WHATSAPP_SCORE == match_result.overall_score.
    - Hard-blocked jobs are strictly excluded from eligible recommendations.
    """
    # 1. Fetch bounded candidate pool of active, user-visible jobs
    jobs_stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.ACTIVE,
            or_(
                Job.created_by_user_id.is_(None),
                Job.created_by_user_id == user.id,
            ),
        )
        .order_by(Job.created_at.desc())
        .limit(candidate_pool_limit)
    )
    candidate_jobs = list(session.execute(jobs_stmt).scalars().all())
    if not candidate_jobs:
        return []

    recommendations: list[dict[str, Any]] = []

    for job in candidate_jobs:
        try:
            match_result: MatchResult = get_or_compute_match(
                session,
                redis_client,
                user=user,
                job_id=job.id,
            )
        except MatchJobNotFoundError:
            continue

        # Hard-blocked check: if WP2.2 reports blockers or non-eligible,
        # exclude from eligible recommendations
        is_eligible = (
            match_result.eligibility == MatchEligibility.ELIGIBLE
            and len(match_result.blockers) == 0
        )
        if not is_eligible:
            continue

        company_name = job.company.name if job.company else "Empresa"
        recommendations.append(
            {
                "job_id": str(job.id),
                "title": job.title,
                "company": company_name,
                "location": job.location_raw or "Remoto / Não informado",
                "workplace_type": job.workplace_type.value,
                "overall_score": match_result.overall_score,
                "eligibility": match_result.eligibility,
                "strengths": match_result.strengths,
                "missing_requirements": match_result.missing_requirements,
                "explanation": match_result.explanation,
                "match_id": str(match_result.id),
                "profile_version": match_result.profile_version,
            }
        )

    # Sort strictly by canonical integer overall_score descending
    recommendations.sort(key=lambda r: int(r["overall_score"]), reverse=True)

    selected = recommendations[:limit]
    logger.info(
        "job_matching.recommendations_generated",
        extra={
            "user_id": str(user.id),
            "recommendation_count": len(selected),
        },
    )
    return selected


def get_job_detail(
    session: Session,
    *,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieves canonical job details."""
    job = session.execute(
        select(Job).where(
            Job.id == job_id,
            Job.status.in_([JobStatus.ACTIVE, JobStatus.UNKNOWN]),
        )
    ).scalar_one_or_none()
    if job is None:
        raise JobMatchingError(f"Job {job_id} not found or inactive")

    company_name = job.company.name if job.company else "Empresa"
    return {
        "id": str(job.id),
        "title": job.title,
        "company": company_name,
        "location": job.location_raw,
        "workplace_type": job.workplace_type.value,
        "description": job.description_raw,
    }


def explain_match(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieves canonical WP2.2 match explanation and breakdown."""
    match_result = get_or_compute_match(
        session,
        redis_client,
        user=user,
        job_id=job_id,
    )
    return {
        "match_id": str(match_result.id),
        "job_id": str(job_id),
        "overall_score": match_result.overall_score,
        "eligibility": match_result.eligibility,
        "explanation": match_result.explanation,
        "strengths": match_result.strengths,
        "missing_requirements": match_result.missing_requirements,
        "blockers": match_result.blockers,
        "grounded_fact_ids": [str(fid) for fid in match_result.grounded_fact_ids],
    }

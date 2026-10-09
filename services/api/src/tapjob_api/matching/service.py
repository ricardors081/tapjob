"""Service layer for WP2.2 Matching Engine (ADR-0014 Decisions 7, 8, 10).

Orchestrates:
1. Job visibility and liveness validation
2. Canonical TruthSnapshot boundary consumption
3. Redis caching with pre-cache liveness guard
4. Deterministic engine evaluation
5. Concurrency-safe PostgreSQL persistence via INSERT ... ON CONFLICT DO NOTHING
6. Convergence verification against deterministic_result_digest
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import redis
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.identity.models import User
from tapjob_api.jobs.models import Job, JobStatus
from tapjob_api.matching.cache import (
    build_match_cache_key,
    get_cached_match,
    set_cached_match,
)
from tapjob_api.matching.engine import evaluate_match
from tapjob_api.matching.errors import (
    DeterministicMatchMismatchError,
    MatchJobNotFoundError,
)
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.signals import (
    derive_candidate_match_signals,
    derive_job_match_signals,
)
from tapjob_api.matching.types import MATCHING_POLICY_VERSION

__all__ = [
    "build_match_cache_key",
    "get_or_compute_match",
    "match_result_to_dict",
    "publish_cached_match",
]

logger = logging.getLogger(__name__)


def match_result_to_dict(row: MatchResult) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "job_id": str(row.job_id),
        "profile_version": row.profile_version,
        "job_match_fingerprint": row.job_match_fingerprint,
        "policy_version": row.policy_version,
        "evaluation_as_of_date": row.evaluation_as_of_date.isoformat(),
        "deterministic_result_digest": row.deterministic_result_digest,
        "overall_score": row.overall_score,
        "eligibility": row.eligibility,
        "hard_filter_results": row.hard_filter_results,
        "score_breakdown": row.score_breakdown,
        "blockers": row.blockers,
        "strengths": row.strengths,
        "missing_requirements": row.missing_requirements,
        "critical_unknowns": row.critical_unknowns,
        "explanation": row.explanation,
        "grounded_fact_ids": row.grounded_fact_ids,
        "computed_at": row.computed_at.isoformat(),
    }


def match_result_from_dict(data: dict[str, Any]) -> MatchResult:
    return MatchResult(
        id=uuid.UUID(data["id"]),
        user_id=uuid.UUID(data["user_id"]),
        job_id=uuid.UUID(data["job_id"]),
        profile_version=data["profile_version"],
        job_match_fingerprint=data["job_match_fingerprint"],
        policy_version=data["policy_version"],
        evaluation_as_of_date=date.fromisoformat(data["evaluation_as_of_date"]),
        deterministic_result_digest=data["deterministic_result_digest"],
        overall_score=data["overall_score"],
        eligibility=data["eligibility"],
        hard_filter_results=data["hard_filter_results"],
        score_breakdown=data["score_breakdown"],
        blockers=data["blockers"],
        strengths=data["strengths"],
        missing_requirements=data["missing_requirements"],
        critical_unknowns=data["critical_unknowns"],
        explanation=data["explanation"],
        grounded_fact_ids=data["grounded_fact_ids"],
        computed_at=datetime.fromisoformat(data["computed_at"]),
    )


def publish_cached_match(redis_client: redis.Redis | None, row: MatchResult) -> None:
    """Explicitly publishes a committed MatchResult to Redis cache."""
    if redis_client is None:
        return
    cache_key = build_match_cache_key(
        row.user_id,
        row.job_id,
        row.profile_version,
        row.job_match_fingerprint,
        row.policy_version,
        row.evaluation_as_of_date,
    )
    set_cached_match(redis_client, cache_key, match_result_to_dict(row))


def _register_post_commit_cache(
    session: Session,
    redis_client: redis.Redis | None,
    key: str,
    payload: dict[str, Any],
) -> None:
    """Registers a listener that publishes the cache ONLY after successful session commit."""
    if redis_client is None:
        return

    try:
        executed = False

        def _on_commit(sess: Session) -> None:
            nonlocal executed
            if executed:
                return
            executed = True
            set_cached_match(redis_client, key, payload)

        def _on_rollback(sess: Session) -> None:
            nonlocal executed
            if executed:
                return
            executed = True

        event.listen(session, "after_commit", _on_commit)
        event.listen(session, "after_rollback", _on_rollback)
    except Exception as exc:
        logger.debug("Could not register post-commit event on session: %s", exc)


def get_or_compute_match(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    user: User,
    job_id: uuid.UUID,
    evaluation_as_of_date: date | None = None,
    policy_version: str = MATCHING_POLICY_VERSION,
) -> MatchResult:
    """Retrieves or computes a deterministic MatchResult for the given user and job."""
    # 1. Job lookup & access validation
    job = session.get(Job, job_id)
    if job is None:
        raise MatchJobNotFoundError

    # Non-enumerating visibility check: user-owned manual jobs are private to that user
    if job.created_by_user_id is not None and job.created_by_user_id != user.id:
        raise MatchJobNotFoundError

    # 2. Canonical TruthSnapshot
    snapshot = get_truth_snapshot(session, user=user)

    # 3. Freeze as-of date
    current_utc = datetime.now(UTC)
    as_of_date = evaluation_as_of_date or current_utc.date()

    # 4. Job match fingerprint
    fingerprint = compute_job_match_fingerprint(job)

    # 5. Build cache key
    cache_key = build_match_cache_key(
        user.id,
        job.id,
        snapshot.profile_version,
        fingerprint,
        policy_version,
        as_of_date,
    )

    # Liveness check with cache eviction: closed, unavailable, or expired jobs are not matchable
    if job.status in (JobStatus.CLOSED, JobStatus.UNAVAILABLE, JobStatus.EXPIRED) or (
        job.expires_at is not None and job.expires_at <= current_utc
    ):
        if redis_client is not None:
            with contextlib.suppress(Exception):
                redis_client.delete(cache_key)
        raise MatchJobNotFoundError

    # 6. Check Redis cache
    cached_payload = get_cached_match(redis_client, cache_key, job, user.id, now=current_utc)
    if cached_payload is not None:
        try:
            return match_result_from_dict(cached_payload)
        except Exception as exc:
            logger.warning(
                "Corrupted or incompatible cached match payload for key %s, evicting: %s",
                cache_key,
                exc,
            )
            if redis_client is not None:
                with contextlib.suppress(Exception):
                    redis_client.delete(cache_key)

    # 6. Check canonical PostgreSQL table
    stmt = select(MatchResult).where(
        MatchResult.user_id == user.id,
        MatchResult.job_id == job.id,
        MatchResult.profile_version == snapshot.profile_version,
        MatchResult.job_match_fingerprint == fingerprint,
        MatchResult.policy_version == policy_version,
        MatchResult.evaluation_as_of_date == as_of_date,
    )
    existing_row = session.scalar(stmt)
    if existing_row is not None:
        set_cached_match(redis_client, cache_key, match_result_to_dict(existing_row))
        return existing_row

    # 7. Compute deterministic match
    candidate_signals = derive_candidate_match_signals(snapshot, policy_version, as_of_date)
    job_signals = derive_job_match_signals(job, policy_version)
    core = evaluate_match(
        candidate=candidate_signals,
        job_signals=job_signals,
        job=job,
        evaluation_as_of_date=as_of_date,
        profile_version=snapshot.profile_version,
        policy_version=policy_version,
    )

    # 8. Concurrency-safe persist via INSERT ... ON CONFLICT DO NOTHING
    insert_stmt = (
        insert(MatchResult)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job.id,
            profile_version=snapshot.profile_version,
            job_match_fingerprint=fingerprint,
            policy_version=policy_version,
            evaluation_as_of_date=as_of_date,
            deterministic_result_digest=core.deterministic_result_digest,
            overall_score=core.overall_score,
            eligibility=core.eligibility.value,
            hard_filter_results=[f.to_dict() for f in core.hard_filter_results],
            score_breakdown=core.score_components.to_dict(),
            blockers=[b.to_dict() for b in core.blockers],
            strengths=[s.to_dict() for s in core.strengths],
            missing_requirements=[m.to_dict() for m in core.missing_requirements],
            critical_unknowns=[u.value for u in core.critical_unknowns],
            explanation=core.deterministic_template_explanation.to_dict(),
            grounded_fact_ids=[str(fid) for fid in core.grounded_fact_ids],
            computed_at=current_utc,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "user_id",
                "job_id",
                "profile_version",
                "job_match_fingerprint",
                "policy_version",
                "evaluation_as_of_date",
            ]
        )
        .returning(MatchResult)
    )

    persisted_row = session.scalar(insert_stmt)
    if persisted_row is not None:
        session.flush()
        # Register post-commit cache write: NEVER publish to Redis before DB commit succeeds!
        _register_post_commit_cache(
            session, redis_client, cache_key, match_result_to_dict(persisted_row)
        )
        return persisted_row

    # Concurrency race lost: reload existing row and verify digest convergence
    existing_after_race = session.scalar(stmt)
    if existing_after_race is None:  # pragma: no cover - defensive
        raise AssertionError("Concurrent insert row could not be retrieved")

    if existing_after_race.deterministic_result_digest != core.deterministic_result_digest:
        logger.error(
            "Deterministic match invariant violation! Expected digest %s, found %s",
            core.deterministic_result_digest,
            existing_after_race.deterministic_result_digest,
        )
        raise DeterministicMatchMismatchError(
            expected_digest=core.deterministic_result_digest,
            found_digest=existing_after_race.deterministic_result_digest,
        )

    set_cached_match(redis_client, cache_key, match_result_to_dict(existing_after_race))
    return existing_after_race

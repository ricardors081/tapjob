"""FastAPI endpoints for Matching Engine (WP2.2 / ADR-0014).

Provides authenticated, single-opportunity match evaluation:
POST /api/v1/jobs/{jobId}/match
"""

from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import Any

import redis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from tapjob_api.core.rate_limit import RateLimitPolicy, RedisFixedWindowRateLimiter
from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import ApiError, ErrorCode, ErrorResponse
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity.models import User
from tapjob_api.matching import service
from tapjob_api.matching.errors import (
    DeterministicMatchMismatchError,
    MatchJobNotFoundError,
)
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.schemas import (
    HardFilterResultDto,
    MatchBlockerDto,
    MatchExplanationDto,
    MatchRequirementGapDto,
    MatchResponse,
    MatchStrengthDto,
    ScoreBreakdownDto,
)
from tapjob_api.matching.types import CriticalUnknown, MatchEligibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["matching"])

MATCH_RATE_LIMIT = RateLimitPolicy(key_prefix="rl:jobs:match", limit=60, window_seconds=60)


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_RATE_LIMITED_RESPONSES = _error(HTTPStatus.TOO_MANY_REQUESTS, "Limite de requisições excedido.")
_COMMON_RESPONSES = {
    **_AUTH_RESPONSES,
    **_RATE_LIMITED_RESPONSES,
    **_error(HTTPStatus.NOT_FOUND, "Vaga não encontrada ou não acessível."),
    **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Validação de dados ou identificador inválido."),
    **_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Erro interno durante cálculo de matching."),
}


def _get_redis_client(
    request: Request,
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> redis.Redis | None:
    """Retrieves redis client from limiter or app state."""
    if hasattr(limiter, "_client") and limiter._client is not None:
        return limiter._client
    return getattr(request.app.state, "redis_client", None)


def _to_match_response(row: MatchResult) -> MatchResponse:
    return MatchResponse(
        id=row.id,
        job_id=row.job_id,
        overall_score=row.overall_score,
        eligibility=MatchEligibility(row.eligibility),
        score_breakdown=ScoreBreakdownDto.model_validate(row.score_breakdown),
        hard_filter_results=[
            HardFilterResultDto.model_validate(f) for f in row.hard_filter_results
        ],
        blockers=[MatchBlockerDto.model_validate(b) for b in row.blockers],
        strengths=[MatchStrengthDto.model_validate(s) for s in row.strengths],
        missing_requirements=[
            MatchRequirementGapDto.model_validate(m) for m in row.missing_requirements
        ],
        critical_unknowns=[CriticalUnknown(u) for u in row.critical_unknowns],
        explanation=MatchExplanationDto.model_validate(row.explanation),
        grounded_fact_ids=[uuid.UUID(fid) for fid in row.grounded_fact_ids],
        profile_version=row.profile_version,
        job_match_fingerprint=row.job_match_fingerprint,
        policy_version=row.policy_version,
        evaluation_as_of_date=row.evaluation_as_of_date,
        deterministic_result_digest=row.deterministic_result_digest,
        computed_at=row.computed_at,
    )


@router.post(
    "/{job_id}/match",
    operation_id="matchJob",
    response_model=MatchResponse,
    status_code=HTTPStatus.OK,
    responses=_COMMON_RESPONSES,
)
def match_job(
    job_id: uuid.UUID,
    user: User = Depends(identity_deps.get_current_user),
    db_session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
    redis_client: redis.Redis | None = Depends(_get_redis_client),
) -> MatchResponse:
    """Computes or retrieves cached match result for the authenticated user and job."""
    identity_deps.enforce_rate_limit(limiter, MATCH_RATE_LIMIT, str(user.id))

    try:
        result = service.get_or_compute_match(
            db_session,
            redis_client,
            user=user,
            job_id=job_id,
        )
        db_session.commit()
        return _to_match_response(result)
    except MatchJobNotFoundError as exc:
        raise ApiError(ErrorCode.JOB_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND) from exc
    except DeterministicMatchMismatchError as exc:
        logger.error(
            "Deterministic match invariant violation: expected %s, found %s",
            exc.expected_digest,
            exc.found_digest,
        )
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        ) from exc

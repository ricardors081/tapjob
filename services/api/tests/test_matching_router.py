"""Unit tests for WP2.2 Matching router (POST /api/v1/jobs/{jobId}/match).

Validates:
- 401 Unauthorized without authentication
- 404 Not Found when job does not exist or fails visibility/liveness
- 200 OK returning camelCase MatchResponse contract
- 500 Internal Server Error on deterministic digest mismatch
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier
from sqlalchemy.orm import Session

from tapjob_api.core.rate_limit import RateLimitResult
from tapjob_api.db.session import get_db_session
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity.dependencies import get_current_user
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.main import create_app
from tapjob_api.matching import service
from tapjob_api.matching.errors import (
    DeterministicMatchMismatchError,
    MatchJobNotFoundError,
)
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    MatchEligibility,
)
from tapjob_api.resume_import.storage import InMemoryObjectStorage


def _make_user() -> User:
    return User(
        id=uuid.uuid4(),
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_match_result(user_id: uuid.UUID, job_id: uuid.UUID) -> MatchResult:
    return MatchResult(
        id=uuid.uuid4(),
        user_id=user_id,
        job_id=job_id,
        profile_version=1,
        job_match_fingerprint="fp123456",
        policy_version=MATCHING_POLICY_VERSION,
        evaluation_as_of_date=date(2026, 1, 1),
        deterministic_result_digest="a" * 64,
        overall_score=85,
        eligibility=MatchEligibility.ELIGIBLE.value,
        hard_filter_results=[
            {
                "filter_id": "role_alignment",
                "outcome": "passed",
                "reason": "passed",
                "source_facts": [],
            }
        ],
        score_breakdown={
            "title": 90,
            "skills": 85,
            "experience": 80,
            "seniority": 100,
            "workplace_location": 90,
        },
        blockers=[],
        strengths=[
            {
                "name": "python",
                "description": "Confirmed skill python",
                "fact_ids": [str(uuid.uuid4())],
            }
        ],
        missing_requirements=[],
        critical_unknowns=[],
        explanation={
            "summary_headline": "Good match",
            "strengths_summary": "Strong python skills",
            "gaps_summary": "No gaps identified",
            "verdict_summary": "Eligible for role",
        },
        grounded_fact_ids=[str(uuid.uuid4())],
        computed_at=datetime.now(UTC),
    )


class FakeLimiter:
    def __init__(self) -> None:
        self._client = None

    def check(self, key: str, policy: Any) -> RateLimitResult:
        return RateLimitResult(allowed=True, remaining=59, retry_after_seconds=0)


@pytest.fixture
def app_with_mocks(make_settings: Any) -> tuple[Any, User, MagicMock]:
    user = _make_user()
    mock_session = MagicMock(spec=Session)

    app = create_app(make_settings(), resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: mock_session
    app.dependency_overrides[identity_deps.get_rate_limiter] = lambda: FakeLimiter()

    return app, user, mock_session


def test_matching_endpoint_requires_auth(make_settings: Any) -> None:
    # Unauthenticated request must return 401
    app = create_app(make_settings(), resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[identity_deps.get_identity_verifier] = lambda: FakeIdentityVerifier()
    app.dependency_overrides[identity_deps.get_rate_limiter] = lambda: FakeLimiter()
    with TestClient(app) as test_client:
        random_job_id = uuid.uuid4()
        response = test_client.post(f"/api/v1/jobs/{random_job_id}/match")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "AUTH_TOKEN_MISSING"


def test_matching_endpoint_job_not_found(
    app_with_mocks: tuple[Any, User, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _user, _mock_session = app_with_mocks

    def mock_get_match(*args: Any, **kwargs: Any) -> Any:
        raise MatchJobNotFoundError

    monkeypatch.setattr(service, "get_or_compute_match", mock_get_match)

    with TestClient(app) as test_client:
        job_id = uuid.uuid4()
        response = test_client.post(f"/api/v1/jobs/{job_id}/match")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "JOB_NOT_FOUND"


def test_matching_endpoint_success_contract(
    app_with_mocks: tuple[Any, User, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, user, _mock_session = app_with_mocks
    job_id = uuid.uuid4()
    result = _make_match_result(user.id, job_id)

    monkeypatch.setattr(service, "get_or_compute_match", lambda *args, **kwargs: result)

    with TestClient(app) as test_client:
        response = test_client.post(f"/api/v1/jobs/{job_id}/match")
        assert response.status_code == 200
        data = response.json()

        # Wire contract assertions (camelCase)
        assert data["id"] == str(result.id)
        assert data["jobId"] == str(job_id)
        assert data["overallScore"] == 85
        assert data["eligibility"] == "eligible"
        assert data["policyVersion"] == MATCHING_POLICY_VERSION
        assert data["deterministicResultDigest"] == "a" * 64
        assert data["scoreBreakdown"]["title"] == 90
        assert data["scoreBreakdown"]["skills"] == 85
        assert data["explanation"]["summaryHeadline"] == "Good match"
        assert len(data["strengths"]) == 1
        assert data["strengths"][0]["name"] == "python"


def test_matching_endpoint_digest_mismatch_internal_error(
    app_with_mocks: tuple[Any, User, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _user, _mock_session = app_with_mocks

    def mock_get_match(*args: Any, **kwargs: Any) -> Any:
        raise DeterministicMatchMismatchError(
            expected_digest="expected_hash", found_digest="found_hash"
        )

    monkeypatch.setattr(service, "get_or_compute_match", mock_get_match)

    with TestClient(app) as test_client:
        job_id = uuid.uuid4()
        response = test_client.post(f"/api/v1/jobs/{job_id}/match")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == "INTERNAL_ERROR"


def test_matching_endpoint_rate_limited(
    make_settings: Any,
) -> None:
    user = _make_user()
    mock_session = MagicMock(spec=Session)

    class ExhaustedLimiter:
        def __init__(self) -> None:
            self._client = None

        def check(self, key: str, policy: Any) -> RateLimitResult:
            return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=30)

    app = create_app(make_settings(), resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: mock_session
    app.dependency_overrides[identity_deps.get_rate_limiter] = lambda: ExhaustedLimiter()

    with TestClient(app) as test_client:
        job_id = uuid.uuid4()
        response = test_client.post(f"/api/v1/jobs/{job_id}/match")
        assert response.status_code == 429
        data = response.json()
        assert data["code"] == "RATE_LIMITED"
        assert response.headers["Retry-After"] == "30"

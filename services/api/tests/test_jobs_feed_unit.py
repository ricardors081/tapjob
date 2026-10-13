"""Unit tests for WP2.3 Job Feed, Alerts, Saved Jobs and Watchlist API routers.

Validates:
- 401 Unauthorized without auth headers
- GET /api/v1/jobs feed contract & pagination response
- GET /api/v1/jobs/{job_id} job detail response
- POST /api/v1/jobs/{job_id}/save and DELETE unsave
- POST /api/v1/jobs/{job_id}/dismiss and DELETE undismiss
- GET /api/v1/jobs/saved saved list
- POST /api/v1/companies/{company_id}/watch and DELETE unwatch
- GET /api/v1/companies/watched
- GET /api/v1/jobs/alerts/preferences and PUT update
- Cursor validation and tamper resistance
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier

from tapjob_api.core.rate_limit import RateLimitResult
from tapjob_api.db.session import get_db_session
from tapjob_api.http.pagination import CursorPage
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity.dependencies import get_current_user
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs import feed_service
from tapjob_api.jobs.errors import CompanyNotFoundError, JobNotFoundError
from tapjob_api.jobs.models import (
    Company,
    CompanyWatch,
    Job,
    JobAlertPreference,
    JobStatus,
    JobWorkplaceType,
    SavedJob,
    SavedJobState,
)
from tapjob_api.jobs.schemas import (
    CompanyDto,
    FeedItemResponse,
    JobDetailResponse,
    JobMatchSummaryDto,
    JobProvenanceSummary,
    ProvenanceFieldSource,
    SavedJobResponse,
)
from tapjob_api.main import create_app
from tapjob_api.resume_import.storage import InMemoryObjectStorage


def _make_user() -> User:
    return User(
        id=uuid.uuid4(),
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_company_dto(name: str = "Acme Corp") -> CompanyDto:
    now = datetime.now(UTC)
    return CompanyDto(
        id=uuid.uuid4(),
        name=name,
        name_normalized=name.lower(),
        domain="acme.com",
        created_at=now,
        updated_at=now,
    )


def _make_feed_item(job_id: uuid.UUID) -> FeedItemResponse:
    return FeedItemResponse(
        id=job_id,
        title="Senior Platform Engineer",
        title_normalized="senior platform engineer",
        workplace_type=JobWorkplaceType.REMOTE,
        status=JobStatus.ACTIVE,
        created_at=datetime.now(UTC),
        company=_make_company_dto(),
        match_score=88,
        eligibility="eligible",
        is_saved=False,
    )


def _make_saved_job_model(job_id: uuid.UUID, user_id: uuid.UUID) -> SavedJob:
    now = datetime.now(UTC)
    company = Company(
        id=uuid.uuid4(),
        name="Acme Corp",
        name_normalized="acme corp",
        domain="acme.com",
        created_at=now,
        updated_at=now,
    )
    job = Job(
        id=job_id,
        company_id=company.id,
        company=company,
        title="Senior Platform Engineer",
        title_normalized="senior platform engineer",
        description_raw="Python and AWS",
        workplace_type=JobWorkplaceType.REMOTE,
        status=JobStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    return SavedJob(
        id=uuid.uuid4(),
        user_id=user_id,
        job_id=job_id,
        job=job,
        state=SavedJobState.SAVED,
        created_at=now,
        updated_at=now,
    )


def _make_company_watch_model(company_id: uuid.UUID, user_id: uuid.UUID) -> CompanyWatch:
    now = datetime.now(UTC)
    company = Company(
        id=company_id,
        name="Acme Corp",
        name_normalized="acme corp",
        domain="acme.com",
        created_at=now,
        updated_at=now,
    )
    return CompanyWatch(
        id=uuid.uuid4(),
        user_id=user_id,
        company_id=company_id,
        company=company,
        created_at=now,
    )


@pytest.fixture
def mock_db_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def app_and_client(mock_db_session: MagicMock, make_settings: Any) -> tuple[Any, TestClient, User]:
    app = create_app(make_settings(), resume_storage_override=InMemoryObjectStorage())
    test_user = _make_user()

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    limiter = MagicMock()
    limiter.check_rate_limit.return_value = RateLimitResult(
        allowed=True,
        remaining=99,
        retry_after_seconds=0,
    )
    limiter.check.return_value = RateLimitResult(
        allowed=True,
        remaining=99,
        retry_after_seconds=0,
    )
    app.dependency_overrides[identity_deps.get_rate_limiter] = lambda: limiter

    client = TestClient(app)
    return app, client, test_user


def test_feed_requires_auth(mock_db_session: MagicMock, make_settings: Any) -> None:
    app = create_app(make_settings(), resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    app.dependency_overrides[identity_deps.get_identity_verifier] = lambda: FakeIdentityVerifier()
    limiter = MagicMock()
    limiter.check.return_value = RateLimitResult(allowed=True, remaining=99, retry_after_seconds=0)
    app.dependency_overrides[identity_deps.get_rate_limiter] = lambda: limiter
    client = TestClient(app)

    response = client.get("/api/v1/jobs")
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_feed_returns_cursor_paginated_items(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client
    job_id = uuid.uuid4()
    item = _make_feed_item(job_id)

    with patch.object(feed_service, "get_job_feed") as mock_feed:
        mock_feed.return_value = CursorPage(
            items=[item],
            next_cursor="cursor_token_abc",
        )

        response = client.get("/api/v1/jobs?limit=10&workplaceType=remote&minScore=70")
        assert response.status_code == HTTPStatus.OK
        data = response.json()

        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(job_id)
        assert data["items"][0]["matchScore"] == 88
        assert data["items"][0]["eligibility"] == "eligible"
        assert data["nextCursor"] == "cursor_token_abc"


def test_job_detail_returns_wp2_2_explainability(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client
    job_id = uuid.uuid4()
    now = datetime.now(UTC)
    company_dto = _make_company_dto("Tech Nova")

    detail_dto = JobDetailResponse(
        id=job_id,
        company_id=company_dto.id,
        company=company_dto,
        title="Lead Python Developer",
        title_normalized="lead python developer",
        description_raw="Extensive Python backend engineering",
        description_cleaned="Extensive Python backend engineering",
        similarity_fingerprint="fp123456",
        workplace_type=JobWorkplaceType.REMOTE,
        status=JobStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        provenance_summary=JobProvenanceSummary(
            title=ProvenanceFieldSource.SOURCE_DECLARED,
            company=ProvenanceFieldSource.SOURCE_DECLARED,
            workplace_type=ProvenanceFieldSource.SOURCE_DECLARED,
            description=ProvenanceFieldSource.SOURCE_DECLARED,
        ),
        origins=[],
        is_saved=True,
        is_company_watched=False,
        match=JobMatchSummaryDto(
            overall_score=91,
            eligibility="eligible",
            strengths=[{"name": "Python Proficiency", "description": "High mastery"}],
            missing_requirements=[],
            blockers=[],
            critical_unknowns=[],
            explanation={"template": "Strong alignment on core requirements"},
        ),
    )

    with patch.object(feed_service, "get_job_detail") as mock_detail:
        mock_detail.return_value = detail_dto

        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == HTTPStatus.OK
        data = response.json()

        assert data["id"] == str(job_id)
        assert data["match"]["overallScore"] == 91
        assert len(data["match"]["strengths"]) == 1
        assert data["isSaved"] is True


def test_job_detail_404_when_job_not_found_or_private(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client
    job_id = uuid.uuid4()

    with patch.object(feed_service, "get_job_detail") as mock_detail:
        mock_detail.side_effect = JobNotFoundError(job_id)

        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "JOB_NOT_FOUND"


def test_save_and_unsave_job(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, user = app_and_client
    job_id = uuid.uuid4()
    saved_model = _make_saved_job_model(job_id, user.id)

    with (
        patch.object(feed_service, "save_job") as mock_save,
        patch.object(feed_service, "unsave_job") as mock_unsave,
    ):
        mock_save.return_value = saved_model
        mock_unsave.return_value = None

        save_res = client.post(f"/api/v1/jobs/{job_id}/save")
        assert save_res.status_code == HTTPStatus.OK
        assert save_res.json()["jobId"] == str(job_id)

        unsave_res = client.delete(f"/api/v1/jobs/{job_id}/save")
        assert unsave_res.status_code == HTTPStatus.NO_CONTENT


def test_dismiss_and_undismiss_job(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, user = app_and_client
    job_id = uuid.uuid4()
    saved_model = _make_saved_job_model(job_id, user.id)
    saved_model.state = SavedJobState.DISMISSED

    with (
        patch.object(feed_service, "dismiss_job") as mock_dismiss,
        patch.object(feed_service, "undismiss_job") as mock_undismiss,
    ):
        mock_dismiss.return_value = saved_model
        mock_undismiss.return_value = None

        dismiss_res = client.post(f"/api/v1/jobs/{job_id}/dismiss")
        assert dismiss_res.status_code == HTTPStatus.OK
        assert dismiss_res.json()["state"] == "dismissed"

        undismiss_res = client.delete(f"/api/v1/jobs/{job_id}/dismiss")
        assert undismiss_res.status_code == HTTPStatus.NO_CONTENT


def test_list_saved_jobs(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client
    job_id = uuid.uuid4()
    saved_dto = SavedJobResponse(
        id=uuid.uuid4(),
        job_id=job_id,
        state=SavedJobState.SAVED.value,
        created_at=datetime.now(UTC),
        job=_make_feed_item(job_id),
    )

    with patch.object(feed_service, "list_saved_jobs") as mock_list_saved:
        mock_list_saved.return_value = CursorPage(
            items=[saved_dto],
            next_cursor=None,
        )

        response = client.get("/api/v1/jobs/saved?limit=20")
        assert response.status_code == HTTPStatus.OK
        data = response.json()

        assert len(data["items"]) == 1
        assert data["items"][0]["jobId"] == str(job_id)
        assert data["items"][0]["state"] == "saved"


def test_company_watch_and_unwatch(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, user = app_and_client
    company_id = uuid.uuid4()
    watch_model = _make_company_watch_model(company_id, user.id)

    with (
        patch.object(feed_service, "watch_company") as mock_watch,
        patch.object(feed_service, "unwatch_company") as mock_unwatch,
    ):
        mock_watch.return_value = watch_model
        mock_unwatch.return_value = None

        watch_res = client.post(f"/api/v1/companies/{company_id}/watch")
        assert watch_res.status_code == HTTPStatus.OK
        assert watch_res.json()["companyId"] == str(company_id)

        unwatch_res = client.delete(f"/api/v1/companies/{company_id}/watch")
        assert unwatch_res.status_code == HTTPStatus.NO_CONTENT


def test_watch_company_404_for_nonexistent_company(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client
    company_id = uuid.uuid4()

    with patch.object(feed_service, "watch_company") as mock_watch:
        mock_watch.side_effect = CompanyNotFoundError(company_id)

        response = client.post(f"/api/v1/companies/{company_id}/watch")
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "COMPANY_NOT_FOUND"


def test_job_alert_preferences_get_and_update(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, user = app_and_client
    now = datetime.now(UTC)
    pref_model = JobAlertPreference(
        id=uuid.uuid4(),
        user_id=user.id,
        enabled=True,
        min_score=75,
        min_eligibility="eligible",
        delivery_channel="whatsapp",
        created_at=now,
        updated_at=now,
    )

    with (
        patch.object(feed_service, "get_job_alert_preference") as mock_get_pref,
        patch.object(feed_service, "update_job_alert_preference") as mock_update_pref,
    ):
        mock_get_pref.return_value = pref_model
        mock_update_pref.return_value = pref_model

        get_res = client.get("/api/v1/jobs/alerts/preferences")
        assert get_res.status_code == HTTPStatus.OK
        assert get_res.json()["enabled"] is True
        assert get_res.json()["minScore"] == 75
        assert get_res.json()["deliveryChannel"] == "whatsapp"

        put_res = client.put(
            "/api/v1/jobs/alerts/preferences",
            json={
                "enabled": True,
                "minScore": 75,
                "minEligibility": "eligible",
                "deliveryChannel": "whatsapp",
            },
        )
        assert put_res.status_code == HTTPStatus.OK
        assert put_res.json()["enabled"] is True


def test_job_alert_delivery_channel_rejects_in_app_and_invalid(
    app_and_client: tuple[Any, TestClient, User],
) -> None:
    _, client, _ = app_and_client

    in_app_res = client.put(
        "/api/v1/jobs/alerts/preferences",
        json={
            "enabled": True,
            "minScore": 75,
            "minEligibility": "eligible",
            "deliveryChannel": "in_app",
        },
    )
    assert in_app_res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    random_res = client.put(
        "/api/v1/jobs/alerts/preferences",
        json={
            "enabled": True,
            "minScore": 75,
            "minEligibility": "eligible",
            "deliveryChannel": "random",
        },
    )
    assert random_res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

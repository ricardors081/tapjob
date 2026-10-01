"""Integration tests: real PostgreSQL and Redis, as provisioned by the ``backend-integration`` CI
job (docs/ENGINEERING_STANDARDS.md §27). Run locally with ``pytest -m integration`` against a real
Postgres/Redis pair (set APP_ENV, DATABASE_URL, REDIS_URL first).
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from tapjob_api.core.config import ApiSettings
from tapjob_api.http.readiness import READY_PATH, DependencyName, DependencyStatus
from tapjob_api.main import create_app


@pytest.mark.integration
def test_ready_is_200_against_real_postgresql_and_redis() -> None:
    with TestClient(create_app(ApiSettings())) as client:
        response = client.get(READY_PATH)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["dependencies"][DependencyName.POSTGRESQL] == DependencyStatus.OK
    assert body["dependencies"][DependencyName.REDIS] == DependencyStatus.OK


@pytest.mark.integration
def test_ready_is_503_when_redis_is_unreachable() -> None:
    base = ApiSettings()
    settings = ApiSettings(
        app_env=base.app_env,
        database_url=base.database_url,
        # Port 1 is reserved, so the connection is refused immediately instead of hanging.
        redis_url="redis://localhost:1/0",  # type: ignore[arg-type]
        readiness_timeout_seconds=1.0,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(READY_PATH)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = response.json()
    assert body["dependencies"][DependencyName.REDIS] == DependencyStatus.UNAVAILABLE
    assert body["dependencies"][DependencyName.POSTGRESQL] == DependencyStatus.OK

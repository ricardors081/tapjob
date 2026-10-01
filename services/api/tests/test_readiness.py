"""Unit tests: dependency probes are faked so these never touch real PostgreSQL/Redis (that is
``test_readiness_integration.py``'s job, see docs/ENGINEERING_STANDARDS.md §27).
"""

import time
from collections.abc import Callable, Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

import tapjob_api.http.readiness as readiness
from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.http.readiness import READY_PATH, DependencyName, DependencyStatus, ReadinessStatus
from tapjob_api.main import create_app


def _ok(*_: object, **__: object) -> None:
    return None


def _failing(*_: object, **__: object) -> None:
    raise ConnectionError("boom: user=admin password=hunter2")


@pytest.fixture
def ready_client(make_settings: Callable[..., ApiSettings]) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings())) as client:
        yield client


def test_ready_is_200_when_every_dependency_is_reachable(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(readiness, "_ping_postgresql", _ok)
    monkeypatch.setattr(readiness, "_ping_redis", _ok)

    response = ready_client.get(READY_PATH)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == ReadinessStatus.OK
    assert body["dependencies"] == {
        DependencyName.POSTGRESQL: DependencyStatus.OK,
        DependencyName.REDIS: DependencyStatus.OK,
    }


def test_ready_is_503_when_postgresql_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(readiness, "_ping_postgresql", _failing)
    monkeypatch.setattr(readiness, "_ping_redis", _ok)

    response = ready_client.get(READY_PATH)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = response.json()
    assert body["status"] == ReadinessStatus.DEGRADED
    assert body["dependencies"][DependencyName.POSTGRESQL] == DependencyStatus.UNAVAILABLE
    assert body["dependencies"][DependencyName.REDIS] == DependencyStatus.OK


def test_ready_is_503_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(readiness, "_ping_postgresql", _ok)
    monkeypatch.setattr(readiness, "_ping_redis", _failing)

    response = ready_client.get(READY_PATH)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["dependencies"][DependencyName.REDIS] == DependencyStatus.UNAVAILABLE


def test_ready_response_never_leaks_the_dependency_error_message(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(readiness, "_ping_postgresql", _failing)
    monkeypatch.setattr(readiness, "_ping_redis", _ok)

    response = ready_client.get(READY_PATH)

    assert "hunter2" not in response.text
    assert "admin" not in response.text


def test_ready_times_out_a_hanging_dependency_instead_of_blocking(
    monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    def _hangs(*_: object, **__: object) -> None:
        time.sleep(0.3)

    monkeypatch.setattr(readiness, "_ping_postgresql", _hangs)
    monkeypatch.setattr(readiness, "_ping_redis", _ok)

    settings = ApiSettings(
        app_env=Environment.LOCAL,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
        readiness_timeout_seconds=0.02,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(READY_PATH)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    dependencies = response.json()["dependencies"]
    assert dependencies[DependencyName.POSTGRESQL] == DependencyStatus.UNAVAILABLE

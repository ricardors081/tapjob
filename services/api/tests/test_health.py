from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.http.health import HEALTH_PATH, HealthStatus
from tapjob_api.http.request_context import REQUEST_ID_HEADER
from tapjob_api.main import OPENAPI_PATH, SERVICE_NAME, create_app
from tapjob_product_config import get_product_config


def test_health_reports_liveness(client: TestClient) -> None:
    response = client.get(HEALTH_PATH)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == HealthStatus.OK
    assert body["service"] == SERVICE_NAME
    assert body["environment"] == Environment.LOCAL
    assert body["version"]


def test_every_response_carries_a_generated_request_id(client: TestClient) -> None:
    response = client.get(HEALTH_PATH)

    assert response.headers[REQUEST_ID_HEADER]


def test_valid_inbound_request_id_is_propagated(client: TestClient) -> None:
    response = client.get(HEALTH_PATH, headers={REQUEST_ID_HEADER: "trace-123.abc_DEF"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-123.abc_DEF"


@pytest.mark.parametrize("unsafe", ["contains spaces", "x" * 129, "semi;colon"])
def test_unsafe_inbound_request_id_is_replaced(client: TestClient, unsafe: str) -> None:
    response = client.get(HEALTH_PATH, headers={REQUEST_ID_HEADER: unsafe})

    assert response.headers[REQUEST_ID_HEADER] != unsafe


def test_api_title_comes_from_product_config(client: TestClient) -> None:
    schema = client.get(OPENAPI_PATH).json()

    assert schema["info"]["title"].startswith(get_product_config().product_name)


def test_openapi_is_not_exposed_in_production() -> None:
    # Constructed directly (not via `make_settings`): a PRODUCTION-environment app now also
    # requires R2 object storage config to even start (WP1.3, round-1 independent review
    # "Finding 1") -- unrelated to what this test actually exercises, but still a real, correct
    # constraint `tapjob_api.main`'s lifespan enforces for every PRODUCTION app, including this one.
    settings = ApiSettings(
        app_env=Environment.PRODUCTION,
        database_url="postgresql+psycopg://tapjob@localhost:5432/tapjob_test",  # type: ignore[arg-type]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_bucket="dummy-bucket",
        r2_access_key_id="dummy",
        r2_secret_access_key="dummy",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get(OPENAPI_PATH).status_code == HTTPStatus.NOT_FOUND
        assert client.get(HEALTH_PATH).status_code == HTTPStatus.OK

from collections.abc import Callable
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


def test_openapi_is_not_exposed_in_production(
    make_settings: Callable[[Environment], ApiSettings],
) -> None:
    with TestClient(create_app(make_settings(Environment.PRODUCTION))) as client:
        assert client.get(OPENAPI_PATH).status_code == HTTPStatus.NOT_FOUND
        assert client.get(HEALTH_PATH).status_code == HTTPStatus.OK

"""Exception handlers produce the canonical ErrorResponse envelope for every non-2xx response,
and never leak exception text, stack traces or request data (AGENTS.md §8).

Uses a standalone FastAPI app (same pattern as test_request_context.py) rather than the real
``create_app()``: no product route exists yet to exercise these paths through
(docs/IMPLEMENTATION_PLAN.md WP0.2), so the handlers are exercised directly against purpose-built
routes.
"""

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tapjob_api.http.errors as errors
from tapjob_api.http.errors import ApiError, ErrorCode, register_exception_handlers
from tapjob_api.http.request_context import REQUEST_ID_HEADER, RequestContextMiddleware

# Private by convention (module-internal default), introspected here the same way
# test_readiness.py reaches into tapjob_api.http.readiness's private probe functions: there is no
# public accessor because nothing outside this module needs one, but the test suite still wants to
# assert every ErrorCode has a default message.
_DEFAULT_MESSAGE_BY_CODE = errors._DEFAULT_MESSAGE_BY_CODE

_SECRET_FRAGMENT = "hunter2"  # noqa: S105 (not a real credential, just a leak-detection probe)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/boom-api-error")
    def _raise_api_error() -> None:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            message="Muitas tentativas. Aguarde antes de tentar novamente.",
            details={"limit": 10},
            retry_after_seconds=30,
        )

    @app.get("/boom-default-message")
    def _raise_without_message() -> None:
        raise ApiError(ErrorCode.CONFLICT, status_code=HTTPStatus.CONFLICT)

    @app.get("/boom-unhandled")
    def _raise_unhandled() -> None:
        raise RuntimeError(f"connection failed: user=admin password={_SECRET_FRAGMENT}")

    @app.get("/requires-query")
    def _requires_query(name: str) -> dict[str, str]:
        return {"name": name}

    @app.get("/method-only-get")
    def _method_only_get() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def app_client() -> Iterator[TestClient]:
    with TestClient(_build_app(), raise_server_exceptions=False) as client:
        yield client


def test_every_error_code_has_a_default_message() -> None:
    assert set(_DEFAULT_MESSAGE_BY_CODE) == set(ErrorCode)


def test_api_error_carries_code_message_details_and_retry_after(app_client: TestClient) -> None:
    response = app_client.get("/boom-api-error")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.headers["Retry-After"] == "30"
    body = response.json()
    assert body["code"] == ErrorCode.RATE_LIMITED
    assert body["message"] == "Muitas tentativas. Aguarde antes de tentar novamente."
    assert body["details"] == {"limit": 10}
    assert body["requestId"]
    assert "errors" not in body


def test_api_error_without_message_falls_back_to_the_default(app_client: TestClient) -> None:
    response = app_client.get("/boom-default-message")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["message"] == _DEFAULT_MESSAGE_BY_CODE[ErrorCode.CONFLICT]
    assert "Retry-After" not in response.headers


def test_unmatched_route_is_a_well_formed_404(app_client: TestClient) -> None:
    response = app_client.get("/this-route-does-not-exist")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == ErrorCode.RESOURCE_NOT_FOUND


def test_wrong_method_is_a_well_formed_405_with_allow_header(app_client: TestClient) -> None:
    response = app_client.post("/method-only-get")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert "GET" in response.headers["allow"]


def test_missing_required_query_param_is_validation_failed_with_sanitized_errors(
    app_client: TestClient,
) -> None:
    response = app_client.get("/requires-query")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert body["code"] == ErrorCode.VALIDATION_FAILED
    assert body["errors"] == [{"location": ["query", "name"], "code": "missing"}]
    # Never the raw Pydantic msg/input/ctx, which could otherwise echo submitted data back.
    assert "input" not in body["errors"][0]
    assert "msg" not in body["errors"][0]


def test_unhandled_exception_is_internal_error_and_never_leaks_exception_text(
    app_client: TestClient,
) -> None:
    response = app_client.get("/boom-unhandled")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert body["message"] == _DEFAULT_MESSAGE_BY_CODE[ErrorCode.INTERNAL_ERROR]
    assert _SECRET_FRAGMENT not in response.text
    assert "RuntimeError" not in response.text
    assert "admin" not in response.text


def test_every_error_response_carries_the_request_id_header(app_client: TestClient) -> None:
    response = app_client.get("/boom-unhandled", headers={REQUEST_ID_HEADER: "trace-errors-1"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-errors-1"
    assert response.json()["requestId"] == "trace-errors-1"

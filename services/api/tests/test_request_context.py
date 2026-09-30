from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from tapjob_api.http.request_context import REQUEST_ID_HEADER, RequestContextMiddleware

DOWNSTREAM_PATH = "/downstream-sets-request-id"


def _app_whose_handler_sets_request_ids() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get(DOWNSTREAM_PATH)
    def handler() -> Response:
        response = Response(headers={REQUEST_ID_HEADER: "downstream-1"})
        # A second, duplicate header proves every downstream value is dropped, not only the first.
        response.raw_headers.append((REQUEST_ID_HEADER.lower().encode(), b"downstream-2"))
        return response

    return app


def test_response_has_exactly_one_canonical_request_id() -> None:
    with TestClient(_app_whose_handler_sets_request_ids()) as client:
        response = client.get(DOWNSTREAM_PATH, headers={REQUEST_ID_HEADER: "canonical-123"})

    assert response.headers.get_list(REQUEST_ID_HEADER) == ["canonical-123"]


def test_generated_request_id_replaces_downstream_values() -> None:
    with TestClient(_app_whose_handler_sets_request_ids()) as client:
        response = client.get(DOWNSTREAM_PATH)

    values = response.headers.get_list(REQUEST_ID_HEADER)
    assert len(values) == 1
    assert values[0] not in {"downstream-1", "downstream-2"}

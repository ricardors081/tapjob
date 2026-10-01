"""Unit coverage for the structural rules themselves, via small constructed schemas -- independent
of whether any real business route exists yet (docs/IMPLEMENTATION_PLAN.md WP0.2 ships contracts,
not product endpoints). See test_openapi_export.py for the rules run against the real app schema.
"""

from typing import Any

import pytest

from tapjob_api.http.contract_rules import (
    ContractViolationError,
    validate_api_v1_scoping,
    validate_error_responses,
    validate_openapi_contract,
    validate_operation_ids,
)

_ERROR_RESPONSE_SCHEMA: dict[str, Any] = {"$ref": "#/components/schemas/ErrorResponse"}


def _operation(
    operation_id: str | None, *, responses: dict[str, Any] | None = None
) -> dict[str, Any]:
    operation: dict[str, Any] = {"responses": responses or {"200": {"description": "ok"}}}
    if operation_id is not None:
        operation["operationId"] = operation_id
    return operation


def test_valid_schema_has_no_violations() -> None:
    schema = {
        "paths": {
            "/health": {"get": _operation("getHealth")},
            "/api/v1/jobs": {
                "get": _operation(
                    "listJobs",
                    responses={
                        "200": {"description": "ok"},
                        "404": {
                            "content": {"application/json": {"schema": _ERROR_RESPONSE_SCHEMA}}
                        },
                    },
                )
            },
        }
    }
    validate_openapi_contract(schema)  # raises ContractViolationError on any violation


def test_non_method_keys_in_a_path_item_are_ignored() -> None:
    schema = {
        "paths": {
            "/api/v1/jobs": {
                "parameters": [{"name": "unrelated", "in": "query"}],
                "get": _operation("listJobs"),
            }
        }
    }
    assert validate_operation_ids(schema) == []


def test_missing_operation_id_is_a_violation() -> None:
    schema = {"paths": {"/api/v1/jobs": {"get": _operation(None)}}}
    assert validate_operation_ids(schema) == ["GET /api/v1/jobs: missing operationId"]


@pytest.mark.parametrize(
    "bad_id", ["list_jobs", "ListJobs", "list-jobs", "get_jobs_api_v1_jobs_get", "123jobs"]
)
def test_non_camel_case_operation_id_is_a_violation(bad_id: str) -> None:
    schema = {"paths": {"/api/v1/jobs": {"get": _operation(bad_id)}}}
    violations = validate_operation_ids(schema)
    assert len(violations) == 1
    assert bad_id in violations[0]


def test_duplicate_operation_id_is_a_violation() -> None:
    schema = {
        "paths": {
            "/api/v1/jobs": {"get": _operation("listJobs")},
            "/api/v1/jobs/archived": {"get": _operation("listJobs")},
        }
    }
    violations = validate_operation_ids(schema)
    assert len(violations) == 1
    assert "duplicates GET /api/v1/jobs" in violations[0]


def test_route_outside_v1_and_not_exempt_is_a_violation() -> None:
    schema = {"paths": {"/jobs": {"get": _operation("listJobs")}}}
    assert validate_api_v1_scoping(schema) == [
        "/jobs: not under /api/v1 and not an exempt operational path"
    ]


def test_a_path_merely_sharing_the_v1_string_prefix_is_still_a_violation() -> None:
    # "/api/v1beta".startswith("/api/v1") is True even though it is not actually under /api/v1;
    # the check must be boundary-aware (path == prefix or prefix + "/"), not a bare string prefix.
    schema = {"paths": {"/api/v1beta/jobs": {"get": _operation("listJobs")}}}
    assert validate_api_v1_scoping(schema) == [
        "/api/v1beta/jobs: not under /api/v1 and not an exempt operational path"
    ]


def test_the_v1_prefix_itself_with_nothing_after_it_is_allowed() -> None:
    schema = {"paths": {"/api/v1": {"get": _operation("listRoot")}}}
    assert validate_api_v1_scoping(schema) == []


def test_a_proper_v1_descendant_is_not_a_violation() -> None:
    schema = {"paths": {"/api/v1/jobs": {"get": _operation("listJobs")}}}
    assert validate_api_v1_scoping(schema) == []


def test_exempt_paths_are_allowed_outside_v1() -> None:
    schema = {
        "paths": {
            "/health": {"get": _operation("getHealth")},
            "/ready": {"get": _operation("getReadiness")},
        }
    }
    assert validate_api_v1_scoping(schema) == []


def test_non_2xx_response_without_error_response_schema_is_a_violation() -> None:
    schema = {
        "paths": {
            "/api/v1/jobs": {
                "get": _operation(
                    "listJobs",
                    responses={"200": {"description": "ok"}, "404": {"description": "not found"}},
                )
            }
        }
    }
    assert validate_error_responses(schema) == [
        "GET /api/v1/jobs 404: must reference #/components/schemas/ErrorResponse"
    ]


def test_2xx_and_default_responses_are_not_checked() -> None:
    schema = {
        "paths": {
            "/api/v1/jobs": {
                "get": _operation(
                    "listJobs",
                    responses={
                        "200": {"description": "ok"},
                        "201": {"description": "created"},
                        "default": {"description": "fallback"},
                    },
                )
            }
        }
    }
    assert validate_error_responses(schema) == []


def test_an_openapi_31_wildcard_status_range_is_not_misclassified_as_a_violation() -> None:
    # "2XX"/"4XX" range keys are valid OpenAPI 3.1 (unlike FastAPI's own generated output, which
    # always uses literal codes); `int("2XX")` would raise, so these must be skipped, not crash or
    # be wrongly treated as a non-2xx response missing ErrorResponse.
    schema = {
        "paths": {
            "/api/v1/jobs": {
                "get": _operation(
                    "listJobs",
                    responses={"2XX": {"description": "any success"}, "default": {}},
                )
            }
        }
    }
    assert validate_error_responses(schema) == []


def test_exempt_path_non_2xx_is_not_checked() -> None:
    schema = {
        "paths": {
            "/ready": {
                "get": _operation(
                    "getReadiness", responses={"200": {"description": "ok"}, "503": {}}
                )
            }
        }
    }
    assert validate_error_responses(schema) == []


def test_route_outside_v1_is_not_checked_for_error_response_shape() -> None:
    # validate_api_v1_scoping already flags this path; validate_error_responses does not also
    # pile on an unrelated complaint about it.
    schema = {
        "paths": {
            "/jobs": {
                "get": _operation(
                    "listJobs", responses={"200": {"description": "ok"}, "404": {"description": ""}}
                )
            }
        }
    }
    assert validate_error_responses(schema) == []


def test_invalid_schema_raises_with_every_violation_listed() -> None:
    schema = {
        "paths": {
            "/jobs": {
                "get": {"responses": {"200": {"description": "ok"}, "404": {"description": "nope"}}}
            }
        }
    }
    with pytest.raises(ContractViolationError) as exc_info:
        validate_openapi_contract(schema)
    message = str(exc_info.value)
    assert "missing operationId" in message
    assert "not under /api/v1" in message

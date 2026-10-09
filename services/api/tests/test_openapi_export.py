"""The export is offline (no DATABASE_URL/REDIS_URL from the environment, nothing connected),
deterministic (two runs produce byte-identical output) and satisfies the structural contract rules
(docs/adr/0003-api-contract-client-generation.md). CI compares its output against the committed
``packages/contracts/openapi.json`` with ``git diff --exit-code``.
"""

from pathlib import Path

from tapjob_api.http.contract_rules import validate_openapi_contract
from tapjob_api.openapi_export import (
    build_openapi_schema,
    export_openapi_schema,
    serialize_openapi_schema,
)


def test_build_schema_satisfies_the_contract_rules() -> None:
    # Raises ContractViolationError on any violation; a clean call is the assertion.
    validate_openapi_contract(build_openapi_schema())


def test_health_and_ready_have_explicit_camel_case_operation_ids() -> None:
    schema = build_openapi_schema()

    assert schema["paths"]["/health"]["get"]["operationId"] == "getHealth"
    assert schema["paths"]["/ready"]["get"]["operationId"] == "getReadiness"


def test_schema_exposes_the_business_routes_under_api_v1() -> None:
    # WP1.1 (identity), WP1.2 (career-profile), WP1.3 (resume-import), WP2.1-A (jobs),
    # and WP2.2 (matching).
    # Every business route lives under /api/v1 (validate_api_v1_scoping enforces structurally).
    schema = build_openapi_schema()

    assert set(schema["paths"]) == {
        "/health",
        "/ready",
        "/api/v1/auth/bootstrap",
        "/api/v1/me",
        "/api/v1/me/sessions/revoke-all",
        "/api/v1/me/devices",
        "/api/v1/me/devices/{device_id}",
        "/api/v1/me/consents",
        "/api/v1/me/deletion-request",
        "/api/v1/me/career-profile",
        "/api/v1/me/profile-facts",
        "/api/v1/me/profile-facts/{fact_id}",
        "/api/v1/me/profile-facts/{fact_id}/replace",
        "/api/v1/me/profile-facts/{fact_id}/retract",
        "/api/v1/me/truth-snapshot",
        "/api/v1/me/resume-imports",
        "/api/v1/me/resume-imports/{import_id}",
        "/api/v1/me/resume-imports/{import_id}/finalize",
        "/api/v1/me/resumes/master",
        "/api/v1/jobs/intake/paste",
        "/api/v1/jobs/intake/url",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/match",
    }


def test_error_response_is_a_documented_schema_component() -> None:
    schema = build_openapi_schema()

    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "ErrorCode" in schema["components"]["schemas"]


def test_serialization_is_deterministic_across_calls() -> None:
    schema = build_openapi_schema()

    first = serialize_openapi_schema(schema)
    second = serialize_openapi_schema(build_openapi_schema())

    assert first == second


def test_serialized_output_ends_with_exactly_one_trailing_newline() -> None:
    serialized = serialize_openapi_schema(build_openapi_schema())

    assert serialized.endswith("\n")
    assert not serialized.endswith("\n\n")


def test_export_writes_lf_only_regardless_of_platform(tmp_path: Path) -> None:
    output_path = tmp_path / "openapi.json"

    export_openapi_schema(output_path)

    raw = output_path.read_bytes()
    assert b"\r\n" not in raw


def test_export_is_reproducible_byte_for_byte(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    export_openapi_schema(first_path)
    export_openapi_schema(second_path)

    assert first_path.read_bytes() == second_path.read_bytes()


def test_committed_contract_matches_what_the_app_actually_defines() -> None:
    """Fails loudly (rather than via a confusing downstream codegen diff) if someone edits a route
    without re-running the export -- the same invariant CI's `git diff --exit-code` step checks."""
    committed_path = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"

    current = serialize_openapi_schema(build_openapi_schema())
    committed = committed_path.read_text(encoding="utf-8")

    assert current == committed, (
        "packages/contracts/openapi.json is stale: run "
        "`uv run python -m tapjob_api.openapi_export` and commit the result."
    )

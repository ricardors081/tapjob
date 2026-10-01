"""Structural rules the exported OpenAPI schema must satisfy
(docs/adr/0003-api-contract-client-generation.md).

Enforced at export time (tapjob_api.openapi_export) so a violation fails CI before it ever reaches
the generated TypeScript client, and unit-tested directly against small constructed schemas
(tests/test_contract_rules.py) so the rules themselves have real positive/negative coverage even
while no business route exists yet (docs/IMPLEMENTATION_PLAN.md WP0.2 ships contracts, not
product endpoints).
"""

import re
from http import HTTPStatus
from typing import Any

from tapjob_api.http.api_v1 import API_V1_PREFIX
from tapjob_api.http.health import HEALTH_PATH
from tapjob_api.http.readiness import READY_PATH

# An operationId FastAPI auto-generates (when a route does not set one explicitly) is built from
# the function name, path and HTTP method joined by underscores -- e.g. "get_health_health_get".
# Requiring strict camelCase (no underscores) therefore also enforces "explicit": an
# auto-generated id can never match this pattern.
_OPERATION_ID_PATTERN = re.compile(r"^[a-z][a-zA-Z0-9]*$")

_ERROR_RESPONSE_REF = "#/components/schemas/ErrorResponse"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})

# Pre-existing operational endpoints with their own, already-accepted response shapes
# (docs/adr/0002-mobile-foundation.md); not part of the versioned business contract this ADR
# governs. Imported from their own modules (not re-literaled here) so the two cannot drift.
UNVERSIONED_EXEMPT_PATHS = frozenset({HEALTH_PATH, READY_PATH})

Operation = tuple[str, str, dict[str, Any]]


class ContractViolationError(AssertionError):
    """Raised by validate_openapi_contract; the message lists every violation found, not just the
    first, so a single export failure surfaces everything that needs fixing at once."""


def _operations(schema: dict[str, Any]) -> list[Operation]:
    result: list[Operation] = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in _HTTP_METHODS:
                result.append((path, method.lower(), operation))
    return result


def _is_under_prefix(path: str, prefix: str) -> bool:
    """True only for the prefix itself or a `/`-bounded descendant: "/api/v1beta" is NOT under
    "/api/v1" even though it shares the string prefix."""
    return path == prefix or path.startswith(prefix + "/")


def validate_operation_ids(
    schema: dict[str, Any], *, operations: list[Operation] | None = None
) -> list[str]:
    """Every operation needs an explicit, unique, camelCase operationId: it becomes the generated
    SDK's function name, and renaming it later is a breaking change for every client."""
    violations: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    for path, method, operation in operations if operations is not None else _operations(schema):
        location = f"{method.upper()} {path}"
        operation_id = operation.get("operationId")
        if not operation_id:
            violations.append(f"{location}: missing operationId")
        elif not _OPERATION_ID_PATTERN.fullmatch(operation_id):
            violations.append(f"{location}: operationId {operation_id!r} is not explicit camelCase")
        elif operation_id in seen:
            other_path, other_method = seen[operation_id]
            violations.append(
                f"{location}: operationId {operation_id!r} duplicates "
                f"{other_method.upper()} {other_path}"
            )
        else:
            seen[operation_id] = (path, method)
    return violations


def validate_api_v1_scoping(
    schema: dict[str, Any], *, exempt_paths: frozenset[str] = UNVERSIONED_EXEMPT_PATHS
) -> list[str]:
    """Every business route lives under API_V1_PREFIX; only the explicitly exempted operational
    endpoints may sit outside it."""
    return [
        f"{path}: not under {API_V1_PREFIX} and not an exempt operational path"
        for path in schema.get("paths", {})
        if path not in exempt_paths and not _is_under_prefix(path, API_V1_PREFIX)
    ]


def validate_error_responses(
    schema: dict[str, Any],
    *,
    exempt_paths: frozenset[str] = UNVERSIONED_EXEMPT_PATHS,
    operations: list[Operation] | None = None,
) -> list[str]:
    """Every documented non-2xx response of a versioned business route must reference the
    canonical ErrorResponse schema, not an ad hoc per-endpoint shape."""
    violations: list[str] = []
    for path, method, operation in operations if operations is not None else _operations(schema):
        if path in exempt_paths or not _is_under_prefix(path, API_V1_PREFIX):
            continue
        for status_code, response in operation.get("responses", {}).items():
            # "default" and an OpenAPI 3.1 wildcard range ("2XX") are left to the route's own
            # judgment rather than misclassified by a literal int parse: only a response keyed by
            # an explicit, parseable status code is enforced here.
            if not status_code.isdigit():
                continue
            if HTTPStatus.OK <= int(status_code) < HTTPStatus.MULTIPLE_CHOICES:
                continue
            schema_ref = (
                response.get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref")
            )
            if schema_ref != _ERROR_RESPONSE_REF:
                violations.append(
                    f"{method.upper()} {path} {status_code}: must reference {_ERROR_RESPONSE_REF}"
                )
    return violations


def validate_openapi_contract(schema: dict[str, Any]) -> None:
    operations = _operations(schema)
    violations = [
        *validate_operation_ids(schema, operations=operations),
        *validate_api_v1_scoping(schema),
        *validate_error_responses(schema, operations=operations),
    ]
    if violations:
        detail = "\n".join(f"- {violation}" for violation in violations)
        raise ContractViolationError(f"OpenAPI contract violations:\n{detail}")

"""Deterministic, offline OpenAPI export (docs/adr/0003-api-contract-client-generation.md).

Builds the FastAPI app purely to read its schema. The database/Redis URLs below are synthetic
placeholders (same pattern as services/api/tests/conftest.py's `make_settings`): nothing is ever
connected, because generating ``app.openapi()`` never enters the app's lifespan. This also does not
depend on ``openapi_url`` being enabled -- that setting only controls whether FastAPI serves the
schema over HTTP, not whether ``app.openapi()`` can be called directly.

Run with ``uv run python -m tapjob_api.openapi_export`` from the repository root (writes
``packages/contracts/openapi.json`` by default, or an explicit path given as the first argument).
CI runs this and then ``git diff --exit-code`` to catch drift between the committed contract and
the code that actually defines it.
"""

import json
import sys
from pathlib import Path
from typing import Any

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.http.contract_rules import validate_openapi_contract
from tapjob_api.main import create_app

# services/api/src/tapjob_api/openapi_export.py -> repository root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_PATH = _REPO_ROOT / "packages" / "contracts" / "openapi.json"

# Never connected to: schema generation does not enter the app's lifespan. Mirrors the synthetic
# DSNs services/api/tests/conftest.py uses for the same reason.
_EXPORT_SETTINGS = ApiSettings(
    app_env=Environment.LOCAL,
    database_url="postgresql+psycopg://tapjob@localhost:5432/tapjob_openapi_export",  # type: ignore[arg-type]
    redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
)


def build_openapi_schema() -> dict[str, Any]:
    app = create_app(_EXPORT_SETTINGS)
    schema: dict[str, Any] = app.openapi()
    return schema


def serialize_openapi_schema(schema: dict[str, Any]) -> str:
    # sort_keys makes the output independent of dict insertion order; trailing newline matches
    # standard POSIX text-file convention and keeps `git diff` quiet about an EOF newline.
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_openapi_schema(output_path: Path = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    schema = build_openapi_schema()
    validate_openapi_contract(schema)
    # Explicit newline="\n": on Windows, text-mode writes otherwise translate "\n" to the
    # platform line separator, which would make a local export disagree with the LF the
    # repository's .gitattributes normalizes to (and with CI, which always runs on Linux).
    output_path.write_text(serialize_openapi_schema(schema), encoding="utf-8", newline="\n")
    return schema


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    output_path = Path(args[0]) if args else DEFAULT_OUTPUT_PATH
    export_openapi_schema(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

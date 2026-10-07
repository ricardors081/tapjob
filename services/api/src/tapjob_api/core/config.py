"""Typed, validated environment configuration (docs/ENGINEERING_STANDARDS.md §3-5).

This is the only place backend code reads process environment variables. Settings are validated
when the process starts, so a missing or malformed required value fails fast.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# Named default instead of a literal scattered at each call site (docs/ENGINEERING_STANDARDS.md
# §2). A readiness probe should fail fast rather than hang: orchestrators re-poll on their own
# interval, so a short per-dependency timeout is preferable to a long one.
DEFAULT_READINESS_TIMEOUT_SECONDS = 2.0
# Sentry is disabled unless a DSN is configured; CI and local development never set one
# (docs/FOUNDATION_DECISIONS.md "Technical observability").
DEFAULT_SENTRY_TRACES_SAMPLE_RATE = 0.0


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class RuntimeSettings(BaseSettings):
    """Settings every backend process needs.

    Values come from the process environment only: no implicit ``.env`` file loading, so there is
    no hidden local state. Docker Compose (or the deployment platform) injects the variables.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: Environment
    log_level: LogLevel = "INFO"

    # Optional and unset by default: Sentry stays off unless a DSN is explicitly configured, and a
    # DSN is not a secret (it is meant to be embedded in clients), but it is still an
    # environment-specific value rather than a product default.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(
        default=DEFAULT_SENTRY_TRACES_SAMPLE_RATE, ge=0.0, le=1.0
    )

    # Cloudflare R2 (docs/ARCHITECTURE.md §3 "Storage", WP1.3 docs/adr/0011-resume-import.md
    # "object storage"). All four unset is valid ONLY in `local`/`development`
    # (`tapjob_api.resume_import.storage.create_storage_adapter` fails fast otherwise -- round-1
    # independent review "Finding 1": staging/production must never silently fall back to a
    # process-local storage adapter). A PARTIAL set (some but not all four) is never valid in any
    # environment. `r2_secret_access_key` is a true secret (docs/ENGINEERING_STANDARDS.md §3.3):
    # server-side environment only, never `EXPO_PUBLIC_*`, never logged.
    r2_endpoint_url: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None

    # Local/development ONLY durable storage when R2 is unset (round-1 independent review
    # "Finding 1"): a shared filesystem directory BOTH the `api` and `worker` processes mount (a
    # Docker named volume in `infra/compose.yaml`), so an object the API process writes is actually
    # readable by a genuinely separate worker process -- never a process-local in-memory fake
    # pretending to be shared. Unset means `create_storage_adapter` FAILS FAST with
    # `ObjectStorageConfigError` in `local`/`development` too (round-2 independent review
    # "Finding 5") -- it no longer falls back to an in-memory fake implicitly. Every test process
    # leaves this unset AND injects `InMemoryObjectStorage` explicitly instead
    # (`tapjob_api.main.create_app`'s `resume_storage_override` parameter).
    resume_import_local_storage_dir: str | None = None


class ApiSettings(RuntimeSettings):
    """HTTP API and migration settings."""

    database_url: PostgresDsn
    redis_url: RedisDsn
    readiness_timeout_seconds: float = Field(default=DEFAULT_READINESS_TIMEOUT_SECONDS, gt=0.0)

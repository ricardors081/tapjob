"""Typed, validated environment configuration (docs/ENGINEERING_STANDARDS.md §3-5).

This is the only place backend code reads process environment variables. Settings are validated
when the process starts, so a missing or malformed required value fails fast.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
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
    # (`tapjob_api.main.create_app`'s `resume_storage_override` parameter).
    resume_import_local_storage_dir: str | None = None

    # Evolution API messaging gateway settings
    evolution_api_url: str = "http://localhost:8080"
    evolution_api_key: str = "test-key"
    evolution_instance_name: str = "tapjob"

    # AI Gateway provider settings
    ai_provider: str = "openai"
    ai_resume_extraction_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"


def validate_evolution_settings_production_like(
    *,
    app_env: Environment,
    evolution_api_url: str,
    evolution_api_key: str,
    evolution_instance_name: str,
) -> None:
    """Validates that Evolution API settings are non-placeholder in staging/production."""
    is_production_like = app_env in (Environment.STAGING, Environment.PRODUCTION)
    if not is_production_like:
        return

    if evolution_api_key.strip() in (
        "test-key",
        "dummy",
        "change-me",
        "placeholder",
        "secret",
        "",
    ):
        raise ValueError(
            "evolution_api_key cannot use default 'test-key' as an operational credential "
            "in staging/production"
        )
    if "localhost" in evolution_api_url or "127.0.0.1" in evolution_api_url:
        raise ValueError(
            "evolution_api_url cannot point to localhost/127.0.0.1 in staging/production"
        )
    if evolution_instance_name.strip() in ("tapjob", "default", ""):
        raise ValueError(
            "evolution_instance_name must be explicitly configured and not depend on the "
            "default in staging/production"
        )


class ApiSettings(RuntimeSettings):
    """HTTP API and migration settings."""

    database_url: PostgresDsn
    redis_url: RedisDsn
    readiness_timeout_seconds: float = Field(default=DEFAULT_READINESS_TIMEOUT_SECONDS, gt=0.0)
    whatsapp_webhook_enabled: bool = False
    whatsapp_webhook_secret: str | None = None

    @model_validator(mode="after")
    def validate_whatsapp_security_in_production(self) -> Self:
        is_production_like = self.app_env in (Environment.STAGING, Environment.PRODUCTION)
        if is_production_like and self.whatsapp_webhook_enabled:
            if not self.whatsapp_webhook_secret or self.whatsapp_webhook_secret.strip() in (
                "test-key",
                "secret",
                "change-me",
                "dummy",
            ):
                raise ValueError(
                    "whatsapp_webhook_secret must be set to a non-default secret in "
                    "staging/production when WhatsApp webhook is enabled"
                )
            validate_evolution_settings_production_like(
                app_env=self.app_env,
                evolution_api_url=self.evolution_api_url,
                evolution_api_key=self.evolution_api_key,
                evolution_instance_name=self.evolution_instance_name,
            )
        return self

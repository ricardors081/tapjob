from typing import Self

from pydantic import PostgresDsn, RedisDsn, model_validator

from tapjob_api.core.config import RuntimeSettings, validate_evolution_settings_production_like


class WorkerSettings(RuntimeSettings):
    """Worker settings. Redis is the Celery broker (D-034); it is not a durable source of truth.
    `database_url` is new in WP1.3 (docs/adr/0011-resume-import.md): the first checkpoint whose
    worker tasks need direct PostgreSQL access (`tapjob_api.resume_import.tasks`), via the exact
    same `tapjob_api.db.session` session-factory helper the API process uses -- never a second
    database access pattern."""

    redis_url: RedisDsn
    database_url: PostgresDsn

    # WP2.1-B hosted ATS job sources configuration JSON string (or unset for zero sources)
    hosted_ats_sources: str | None = None

    @model_validator(mode="after")
    def validate_worker_settings(self) -> Self:
        validate_evolution_settings_production_like(
            app_env=self.app_env,
            evolution_api_url=self.evolution_api_url,
            evolution_api_key=self.evolution_api_key,
            evolution_instance_name=self.evolution_instance_name,
        )
        return self


class BeatSettings(RuntimeSettings):
    """Settings required exclusively by the Celery Beat scheduler process.

    Only requires broker URL to schedule periodic tasks. Does not require database
    engine, object storage, or third-party messaging provider credentials.
    """

    redis_url: RedisDsn

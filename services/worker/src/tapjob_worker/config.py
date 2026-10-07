from pydantic import PostgresDsn, RedisDsn

from tapjob_api.core.config import RuntimeSettings


class WorkerSettings(RuntimeSettings):
    """Worker settings. Redis is the Celery broker (D-034); it is not a durable source of truth.
    `database_url` is new in WP1.3 (docs/adr/0011-resume-import.md): the first checkpoint whose
    worker tasks need direct PostgreSQL access (`tapjob_api.resume_import.tasks`), via the exact
    same `tapjob_api.db.session` session-factory helper the API process uses -- never a second
    database access pattern."""

    redis_url: RedisDsn
    database_url: PostgresDsn

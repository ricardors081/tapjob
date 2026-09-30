from pydantic import RedisDsn

from tapjob_api.core.config import RuntimeSettings


class WorkerSettings(RuntimeSettings):
    """Worker settings. Redis is the Celery broker (D-034); it is not a durable source of truth."""

    redis_url: RedisDsn

"""Celery application factory with delivery-safety defaults (docs/ENGINEERING_STANDARDS.md §24).

Tasks must still be idempotent: these settings favour redelivery over loss, so a task can run more
than once.
"""

from typing import Any

from celery import Celery, signals

from tapjob_api.core.logging import configure_logging
from tapjob_api.core.observability import init_sentry
from tapjob_worker.config import WorkerSettings

SERVICE_NAME = "worker"
TASK_SERIALIZER = "json"


def create_celery_app(settings: WorkerSettings) -> Celery:
    init_sentry(settings, service=SERVICE_NAME)
    app = Celery(SERVICE_NAME, broker=str(settings.redis_url))
    app.conf.update(
        # Only JSON crosses the broker: no pickle deserialization of queue payloads.
        task_serializer=TASK_SERIALIZER,
        accept_content=[TASK_SERIALIZER],
        result_serializer=TASK_SERIALIZER,
        # No result backend until a task needs one; business state lives in PostgreSQL.
        task_ignore_result=True,
        # Acknowledge after execution so a crashed worker does not silently drop a task.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # Long-running jobs should not be hoarded by one worker process.
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        enable_utc=True,
        timezone="UTC",
        # Logging is configured by the setup_logging hook below.
        worker_hijack_root_logger=False,
    )

    def _configure_worker_logging(**_: Any) -> None:
        configure_logging(
            level=settings.log_level, service=SERVICE_NAME, environment=settings.app_env
        )

    signals.setup_logging.connect(
        _configure_worker_logging, weak=False, dispatch_uid="tapjob_worker.setup_logging"
    )
    return app

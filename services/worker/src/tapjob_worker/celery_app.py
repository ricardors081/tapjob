"""Celery application factory with delivery-safety defaults (docs/ENGINEERING_STANDARDS.md §24).

Tasks must still be idempotent: these settings favour redelivery over loss, so a task can run more
than once.
"""

from typing import Any

import redis
from celery import Celery, signals

from tapjob_api.core.database import create_database_engine
from tapjob_api.core.logging import configure_logging
from tapjob_api.core.observability import init_sentry
from tapjob_api.db.session import create_session_factory
from tapjob_api.jobs.sources.registry import (
    JobSourceRegistry,
    load_job_source_registry,
)

# Importing these modules registers the shared tasks against this process's Celery app
from tapjob_api.jobs.tasks import (
    DEFAULT_ALERT_SWEEP_INTERVAL_SECONDS,
    DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS,
    EVALUATE_JOB_ALERTS_TASK_NAME,
    SWEEP_SCHEDULED_INGESTION_TASK_NAME,
    configure_jobs_worker,
)
from tapjob_api.messaging.evolution_adapter import EvolutionAdapter
from tapjob_api.messaging.tasks import (
    DISPATCH_DUE_REMINDERS_TASK_NAME,
    DISPATCH_OUTBOX_TASK_NAME,
    RECOVER_UNPROCESSED_INBOUND_ENVELOPES_TASK_NAME,
    configure_messaging_worker,
)
from tapjob_api.resume_import.limits import RESUME_IMPORT_SWEEP_INTERVAL_SECONDS
from tapjob_api.resume_import.storage import create_storage_adapter
from tapjob_api.resume_import.tasks import (
    RESUME_IMPORT_SWEEP_TASK_NAME,
    configure_resume_import_worker,
)
from tapjob_worker.config import BeatSettings, WorkerSettings
from tapjob_worker.correlation import connect_correlation_signals

SERVICE_NAME = "worker"
TASK_SERIALIZER = "json"

# Centralized periodic Celery Beat schedule (docs/IMPLEMENTATION_PLAN.md WP2.1-B)
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "jobs-scheduled-ingestion-sweep": {
        "task": SWEEP_SCHEDULED_INGESTION_TASK_NAME,
        "schedule": float(DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS),
        "args": [DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS],
    },
    "messaging-dispatch-outbox": {
        "task": DISPATCH_OUTBOX_TASK_NAME,
        "schedule": 5.0,
    },
    "messaging-dispatch-due-reminders": {
        "task": DISPATCH_DUE_REMINDERS_TASK_NAME,
        "schedule": 30.0,
    },
    "messaging-recover-unprocessed-inbound": {
        "task": RECOVER_UNPROCESSED_INBOUND_ENVELOPES_TASK_NAME,
        "schedule": 60.0,
    },
    "jobs-evaluate-job-alerts": {
        "task": EVALUATE_JOB_ALERTS_TASK_NAME,
        "schedule": float(DEFAULT_ALERT_SWEEP_INTERVAL_SECONDS),
    },
}


def create_celery_app(
    settings: WorkerSettings,
    *,
    registry: JobSourceRegistry | None = None,
) -> Celery:
    init_sentry(settings, service=SERVICE_NAME)
    app = Celery(SERVICE_NAME, broker=str(settings.redis_url))

    # One engine/session-factory per worker process (docs/ENGINEERING_STANDARDS.md §23),
    # the same "construct once, reuse" discipline tapjob_api.main applies to app.state.db_engine.
    engine = create_database_engine(str(settings.database_url), connect_timeout_seconds=5.0)
    session_factory = create_session_factory(engine)
    storage = create_storage_adapter(settings)
    configure_resume_import_worker(session_factory=session_factory, storage=storage)

    if registry is None:
        registry = load_job_source_registry(settings.hosted_ats_sources)
    configure_jobs_worker(session_factory=session_factory, registry=registry)

    redis_client = redis.Redis.from_url(str(settings.redis_url))
    channel_adapter = EvolutionAdapter(
        api_url=settings.evolution_api_url,
        api_key=settings.evolution_api_key,
        instance_name=settings.evolution_instance_name,
    )
    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=channel_adapter,
        storage=storage,
    )

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
        beat_schedule=BEAT_SCHEDULE,
    )

    def _configure_worker_logging(**_: Any) -> None:
        configure_logging(
            level=settings.log_level, service=SERVICE_NAME, environment=settings.app_env
        )

    signals.setup_logging.connect(
        _configure_worker_logging, weak=False, dispatch_uid="tapjob_worker.setup_logging"
    )
    connect_correlation_signals()

    def _kick_off_resume_import_sweep(**_: Any) -> None:
        # Round-2 independent review "Finding 4": kicked off exactly ONCE per worker process, when
        # the worker has finished booting and is ready to consume -- the self-rescheduling task
        # itself (`tasks.sweep_stale_resume_import_dispatches_task`) schedules every subsequent run,
        # never this signal handler again.
        app.send_task(RESUME_IMPORT_SWEEP_TASK_NAME, countdown=RESUME_IMPORT_SWEEP_INTERVAL_SECONDS)

    signals.worker_ready.connect(
        _kick_off_resume_import_sweep, weak=False, dispatch_uid="tapjob_worker.resume_import_sweep"
    )

    return app


def create_beat_app(settings: BeatSettings) -> Celery:
    """Creates a minimal Celery application instance specifically for the Beat scheduler.

    Scheduler-only runtime: broker + periodic schedules. Does not construct database
    engines, object storage adapters, or third-party messaging channels.
    """
    init_sentry(settings, service="beat")
    app = Celery("beat", broker=str(settings.redis_url))
    app.conf.update(
        task_serializer=TASK_SERIALIZER,
        accept_content=[TASK_SERIALIZER],
        result_serializer=TASK_SERIALIZER,
        task_ignore_result=True,
        enable_utc=True,
        timezone="UTC",
        worker_hijack_root_logger=False,
        beat_schedule=BEAT_SCHEDULE,
    )
    return app

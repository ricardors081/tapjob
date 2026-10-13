"""Celery task definitions for Job Ingestion and Scheduler (WP2.1-B).

Tasks are bound lazily via `@shared_task` and configured at worker startup via
`configure_jobs_worker()`. The dependency direction remains worker -> api.
Periodic execution is managed centrally via Celery Beat rather than worker self-rescheduling.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery import Task, shared_task
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.jobs.alert_service import evaluate_job_alerts_sweep
from tapjob_api.jobs.ingestion import IngestionSummary, ingest_job_source
from tapjob_api.jobs.sources.registry import (
    JobSourceRegistry,
    get_default_job_source_registry,
)

logger = logging.getLogger(__name__)

INGEST_JOB_SOURCE_TASK_NAME = "jobs.ingest_source"
SWEEP_SCHEDULED_INGESTION_TASK_NAME = "jobs.sweep_scheduled_ingestion"
EVALUATE_JOB_ALERTS_TASK_NAME = "jobs.evaluate_job_alerts"

DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS = 3600  # 1 hour
DEFAULT_ALERT_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes
MAX_INGESTION_ATTEMPTS = 3
INGESTION_SOFT_TIME_LIMIT_SECONDS = 600
INGESTION_HARD_TIME_LIMIT_SECONDS = 660

_session_factory: sessionmaker[Session] | None = None
_registry: JobSourceRegistry | None = None


def configure_jobs_worker(
    *,
    session_factory: sessionmaker[Session],
    registry: JobSourceRegistry | None = None,
) -> None:
    """Configure DB sessionmaker and registry for jobs worker process."""
    global _session_factory, _registry
    _session_factory = session_factory
    _registry = registry or get_default_job_source_registry()


def _require_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError(
            "jobs worker not configured -- call configure_jobs_worker() at process start"
        )
    return _session_factory


def _require_registry() -> JobSourceRegistry:
    if _registry is None:
        return get_default_job_source_registry()
    return _registry


def _resolve_task_cycle_id(task: Task[Any, Any], prefix: str) -> str:
    request_id = (
        getattr(task.request, "id", None) if hasattr(task, "request") and task.request else None
    )
    if request_id and str(request_id).strip():
        return f"{prefix}:{str(request_id).strip()}"
    return f"{prefix}:test-{uuid.uuid4().hex}"


@shared_task(  # type: ignore[untyped-decorator]
    name=INGEST_JOB_SOURCE_TASK_NAME,
    bind=True,
    acks_late=True,
    max_retries=MAX_INGESTION_ATTEMPTS,
    soft_time_limit=INGESTION_SOFT_TIME_LIMIT_SECONDS,
    time_limit=INGESTION_HARD_TIME_LIMIT_SECONDS,
)
def ingest_job_source_task(
    self: Task[Any, Any],
    source_id: str,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    """Ingest jobs from a configured JobSource by source_id within an ingestion cycle."""
    registry = _require_registry()
    config = registry.get(source_id)
    if config is None:
        logger.warning("Ingest task called for unregistered source: %s", source_id)
        return {
            "source_id": source_id,
            "status": "skipped",
            "reason": "unregistered_source",
        }

    if cycle_id and str(cycle_id).strip():
        effective_cycle_id = str(cycle_id).strip()
    else:
        effective_cycle_id = _resolve_task_cycle_id(self, "task")
    session_maker = _require_session_factory()
    with session_maker() as session:
        try:
            summary: IngestionSummary = asyncio.run(
                ingest_job_source(session, config, cycle_id=effective_cycle_id)
            )
            return {
                "source_id": summary.source_id,
                "provider": summary.provider,
                "cycle_id": effective_cycle_id,
                "status": "completed",
                "total_discovered": summary.total_discovered,
                "created_jobs": summary.created_jobs,
                "updated_origins": summary.updated_origins,
                "closed_jobs": summary.closed_jobs,
                "errors": summary.errors,
                "discovery_complete": summary.discovery_complete,
            }
        except Exception as exc:
            logger.error(
                "Ingestion failed for source %s (cycle %s): %s",
                source_id,
                effective_cycle_id,
                exc,
                exc_info=True,
            )
            raise self.retry(exc=exc, countdown=60) from exc


@shared_task(  # type: ignore[untyped-decorator]
    name=SWEEP_SCHEDULED_INGESTION_TASK_NAME,
    bind=True,
    acks_late=True,
    soft_time_limit=120,
    time_limit=180,
)
def sweep_scheduled_ingestion_task(
    self: Task[Any, Any],
    interval_seconds: int = DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Sweep all active JobSources, enqueueing individual ingestion tasks
    with a deterministic cycle_id.
    """
    registry = _require_registry()
    active_sources = registry.list_active()
    enqueued_sources: list[str] = []
    cycle_id = _resolve_task_cycle_id(self, "sweep")

    for cfg in active_sources:
        self.app.send_task(
            INGEST_JOB_SOURCE_TASK_NAME,
            args=[cfg.source_id],
            kwargs={"cycle_id": cycle_id},
        )
        enqueued_sources.append(cfg.source_id)

    return {
        "status": "dispatched",
        "cycle_id": cycle_id,
        "enqueued_count": len(enqueued_sources),
        "enqueued_sources": enqueued_sources,
    }


@shared_task(  # type: ignore[untyped-decorator]
    name=EVALUATE_JOB_ALERTS_TASK_NAME,
    bind=True,
    acks_late=True,
    soft_time_limit=180,
    time_limit=240,
)
def evaluate_job_alerts_task(
    self: Task[Any, Any],
) -> dict[str, Any]:
    """Sweep and evaluate active job alerts, queuing notifications and outbound messages."""
    session_maker = _require_session_factory()
    with session_maker() as session:
        try:
            result = evaluate_job_alerts_sweep(session)
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            logger.error("Failed to evaluate job alerts: %s", exc, exc_info=True)
            raise self.retry(exc=exc, countdown=30) from exc

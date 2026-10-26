"""Celery task definitions for WP1.3 (docs/adr/0011-resume-import.md "async processing"). Defined
here, in `tapjob_api` rather than `tapjob_worker`, using `celery.shared_task` -- tasks bound lazily
to whichever `Celery` app instance is "current" (`tapjob_worker.celery_app.create_celery_app`),
never forcing `tapjob_api` itself to own a Celery app/worker process. The dependency direction
remains worker -> api (docs/adr/0006-worker-correlation-deployment-cd-contract.md), never the
reverse: this module does not import anything from `tapjob_worker`.

`configure_resume_import_worker` is called exactly once per worker process, by
`tapjob_worker.celery_app.create_celery_app` -- the SAME "construct once per process, reuse"
discipline `tapjob_api.main`'s lifespan already applies to `app.state.db_engine`
(docs/ENGINEERING_STANDARDS.md §23). The HTTP API process never calls this: it only ENQUEUES tasks
by name (`tapjob_api.resume_import.router`), it never executes one.
"""

from __future__ import annotations  # `Task[Any, Any]` below is not subscriptable at runtime in

# this Celery version -- deferred (string) annotation evaluation avoids executing it at import time
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from celery import Task, shared_task
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.ports.resume_parser import ResumeParser
from tapjob_api.resume_import import service
from tapjob_api.resume_import.errors import ImportLeaseActiveError, TransientProcessingError
from tapjob_api.resume_import.limits import (
    LEASE_WAIT_MAX_RETRIES,
    LEASE_WAIT_MIN_COUNTDOWN_SECONDS,
    LEASE_WAIT_SAFETY_MARGIN_SECONDS,
    MAX_PROCESSING_ATTEMPTS,
    RESUME_IMPORT_SWEEP_INTERVAL_SECONDS,
    RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS,
    RESUME_PROCESS_SOFT_TIME_LIMIT_SECONDS,
    TRANSIENT_RETRY_COUNTDOWN_SECONDS,
)
from tapjob_api.resume_import.storage import ObjectStoragePort

logger = logging.getLogger(__name__)

RESUME_IMPORT_PROCESS_TASK_NAME = "resume_import.process"
RESUME_IMPORT_SWEEP_TASK_NAME = "resume_import.sweep_stale_dispatches"


_session_factory: sessionmaker[Session] | None = None
_storage: ObjectStoragePort | None = None
_parser: ResumeParser | None = None


def configure_resume_import_worker(
    *,
    session_factory: sessionmaker[Session],
    storage: ObjectStoragePort,
    parser: ResumeParser,
) -> None:
    global _session_factory, _storage, _parser
    _session_factory = session_factory
    _storage = storage
    _parser = parser


def _require_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError(
            "resume_import worker not configured -- "
            "call configure_resume_import_worker() at process start"
        )
    return _session_factory


def _require_storage() -> ObjectStoragePort:
    if _storage is None:
        raise RuntimeError(
            "resume_import worker not configured -- "
            "call configure_resume_import_worker() at process start"
        )
    return _storage


def _require_parser() -> ResumeParser:
    if _parser is None:
        raise RuntimeError(
            "resume_import worker not configured -- "
            "call configure_resume_import_worker() at process start"
        )
    return _parser


def lease_wait_retry_countdown(retry_at: datetime, *, now: datetime | None = None) -> float:
    """Round-2 independent review "Finding 1": a redelivery that finds the row under a STILL-
    ACTIVE lease must retry AFTER that lease's own real expiry, never on a fixed guess divorced
    from it -- a fixed countdown (e.g. a flat 60s) checked against a 300s lease, bounded by the
    SAME retry budget as genuine processing attempts, could exhaust that budget entirely before the
    lease ever actually expires, leaving the row stuck `PROCESSING` forever. Pure function, directly
    unit-testable without any real Celery/time-based plumbing."""
    current = now or datetime.now(UTC)
    return max(
        LEASE_WAIT_MIN_COUNTDOWN_SECONDS,
        (retry_at - current).total_seconds() + LEASE_WAIT_SAFETY_MARGIN_SECONDS,
    )


@shared_task(  # type: ignore[untyped-decorator]
    name=RESUME_IMPORT_PROCESS_TASK_NAME,
    bind=True,
    acks_late=True,
    max_retries=MAX_PROCESSING_ATTEMPTS,
    soft_time_limit=RESUME_PROCESS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS,
)
def process_resume_import_task(self: Task[Any, Any], import_id: str) -> None:
    """Thin Celery wrapper -- all real logic lives in `service.process_resume_import`, which owns
    the actual retry-safety/lease semantics (docs/adr/0011-...md "worker idempotency", round-1
    independent review "Finding 2", round-2 independent review "Finding 2"). This wrapper's own job
    is translating the service layer's two explicit "please retry" signals into a REAL, bounded
    Celery retry -- never letting either one escape as an unhandled exception that Celery's own
    default (unbounded, unmanaged) redelivery behavior would otherwise retry blindly:

    - `TransientProcessingError` (e.g. a storage read failure, or an integrity-check mismatch,
      presumed recoverable): the service layer already released the row back to `QUEUED` -- retry
      soon, using this TASK's own default `max_retries` (`MAX_PROCESSING_ATTEMPTS`), since each such
      retry corresponds to a real, DB-tracked processing attempt.
    - `ImportLeaseActiveError` (a different delivery/attempt is presumed still genuinely running):
      the row is untouched. Round-2 independent review "Finding 1": retried on a countdown computed
      from the lease's OWN real remaining lifetime (`error.retry_at`), using a SEPARATE, larger
      retry budget (`max_retries=LEASE_WAIT_MAX_RETRIES`) that this call explicitly overrides --
      merely waiting for someone else's active lease to resolve is not itself a processing attempt
      and must never consume the same bounded budget one does.

    `self.retry(...)` itself raises `Retry` (a `BaseException`, not caught by either `except`
    clause below), so Celery's own bounded `max_retries` is the final backstop even if the service
    layer's own DB-level `processing_attempts` bound ever had a bug.

    A Celery soft time limit firing (round-2 independent review "Finding 3") surfaces as a plain
    `SoftTimeLimitExceeded` from INSIDE `service.process_resume_import`'s own call stack -- a real
    `Exception` subclass in this pinned Celery version, so it is already caught by that function's
    own generic exception handler (terminal `FAILED`, correctly lease-fenced, then re-raised for
    observability) with no special handling needed here at all. A hard time limit firing kills the
    OS process outright; recovery then relies purely on the lease naturally expiring, which
    `limits.RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS` being strictly less than
    `limits.PROCESSING_LEASE_SECONDS` guarantees will always eventually happen."""
    with _require_session_factory()() as session:
        try:
            service.process_resume_import(
                session,
                import_id=uuid.UUID(import_id),
                storage=_require_storage(),
                parser=_require_parser(),
            )
        except TransientProcessingError as error:
            raise self.retry(exc=error, countdown=TRANSIENT_RETRY_COUNTDOWN_SECONDS) from error
        except ImportLeaseActiveError as error:
            countdown = lease_wait_retry_countdown(error.retry_at)
            raise self.retry(
                exc=error, countdown=countdown, max_retries=LEASE_WAIT_MAX_RETRIES
            ) from error


@shared_task(  # type: ignore[untyped-decorator]
    name=RESUME_IMPORT_SWEEP_TASK_NAME, bind=True, ignore_result=True
)
def sweep_stale_resume_import_dispatches_task(self: Task[Any, Any]) -> None:
    """Server-owned dispatch-recovery sweep (round-2 independent review "Finding 4") -- a self-
    RESCHEDULING Celery task, not Celery beat/a new scheduler process: the smallest correct
    mechanism that is still genuinely server-owned (never depends on a client's own `GET` poll),
    bounded (reschedules itself exactly once per run, on a fixed interval -- never a hot loop,
    never unbounded polling), and introduces no new infrastructure/framework. Kicked off once per
    worker process by `tapjob_worker.celery_app`'s own `worker_ready` signal handler; every
    subsequent run reschedules the next one itself.

    `service.sweep_stale_queued_imports` owns the actual DB query/bookkeeping and has no Celery
    dependency at all; this wrapper is the one place that actually calls `send_task`, using
    `self.app` directly (this task already runs inside the worker process, which owns a real
    `Celery` app instance -- no separate "task producer" dependency is needed here the way the HTTP
    API side needs one)."""
    with _require_session_factory()() as session:
        stale_ids = service.sweep_stale_queued_imports(session)
    for import_id in stale_ids:
        try:
            self.app.send_task(RESUME_IMPORT_PROCESS_TASK_NAME, args=[str(import_id)])
        except Exception:
            logger.warning(
                "resume import sweep redispatch failed -- durable row remains QUEUED for "
                "the next sweep cycle",
                extra={"import_id": str(import_id)},
                exc_info=True,
            )
    self.apply_async(countdown=RESUME_IMPORT_SWEEP_INTERVAL_SECONDS)

"""Propagates a TapJob correlation id across Celery message boundaries, and binds/clears worker
structured-log context around every task execution (docs/adr/0006-worker-correlation-deployment-cd-
contract.md).

Three identifiers must never be conflated:

- **HTTP request id** (`tapjob_api.http.request_context`) -- identifies one HTTP request.
- **TapJob correlation id** (this module) -- connects a request or other logical origin to
  whatever asynchronous work it causes, across any number of tasks, retries or fan-out. This is
  the id propagated here, and the one bound into `tapjob_api.core.logging.request_id_var` for the
  duration of a task, so a task's log lines carry the SAME `request_id` field as the HTTP request
  (if any) that caused it -- the two services' logs correlate on that one field.
- **Celery task id** (`Task.request.id`) -- identifies one specific execution attempt of one task.
  Celery's own built-in message `correlation_id` property is deliberately NOT reused to carry the
  above: per Celery's own documentation, it is "usually the same as the task id" already -- a
  different, lower-level AMQP/task-identity concept, not a cross-task-boundary application
  correlation id. TapJob's correlation id instead travels in its own custom message header,
  `CORRELATION_HEADER_NAME`.

## Propagation

`before_task_publish` reads the current `request_id_var` (set either by the HTTP middleware, or by
this module's own `task_prerun` hook for a task publishing a NESTED task from within another task's
body) and, if present, injects it into the outgoing message's headers -- unless the caller already
set that header explicitly, which is never overwritten. A task published with no TapJob context
active (e.g. Celery beat/a periodic task with no HTTP or parent-task origin) gets no header at all;
nothing invents a correlation id at publish time.

## Consumption

`task_prerun` resolves the correlation id with an explicit three-step precedence, validating EVERY
candidate with the same bounded policy HTTP already uses (`tapjob_api.core.correlation`) rather than
trusting any of them just because they came from `Task.request`: (1) the inbound TapJob header, if
present and valid; (2) otherwise the task's own `root_id` (its stable identity across an entire
workflow/retry chain, always present in normal operation, never freshly randomized), if valid; (3)
otherwise the task's own `task_id`, if valid; (4) otherwise a freshly generated id -- reached only
when every single identifier this task's message carried was absent or malformed, a degraded path,
never the normal one, but still safe (2026 review, Finding 3): `request.root_id`/`.parent_id` are
themselves derived from message properties a non-standard or malicious producer could set to an
arbitrary value, not guaranteed-safe just because Celery's own client library normally sets them
correctly. It binds both `request_id_var` (the resolved correlation id) and `task_context_var` (the
rest of the structured fields: Celery task id, root id, parent id, task name, retry count -- each
independently validated/normalized before being placed in a log field, never the raw value) for the
duration of the task.

Three signals reset that state, not just one: `task_postrun` (the normal case), and `task_failure`/
`task_retry` as a defensive backstop in case a given Celery version or pool does not reliably fire
`task_postrun` after every failure/retry path (Celery's own signal docs do not explicitly guarantee
this). All three call the same idempotent `_reset_context`, so there is no path left where a
finished task's context survives into whatever this worker process/thread executes next.

This module assumes the default prefork (or solo) execution model: one task body runs at a time per
OS thread/process, so `task_prerun`/`task_postrun` for a given task always fully bracket that task's
own execution on the thread that ran it, with no interleaving from a second, concurrently-executing
task's hooks. This is NOT re-verified for cooperative pools (eventlet/gevent) where multiple tasks
can interleave within one OS thread; TapJob does not use those pools today.
"""

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from celery import signals

from tapjob_api.core.correlation import is_valid_correlation_id
from tapjob_api.core.logging import request_id_var, task_context_var

CORRELATION_HEADER_NAME = "tapjob_correlation_id"


def _current_correlation_id() -> str | None:
    """The correlation id active on this thread right now, if any -- set either by the HTTP
    middleware (`tapjob_api.http.request_context`) or by `task_prerun` below for a task already in
    flight. Reading it here, at publish time, is what makes nested task publication (a task calling
    `.delay()`/`.apply_async()` on another task) preserve the same correlation id automatically."""
    return request_id_var.get()


def _before_task_publish(headers: dict[str, Any] | None = None, **_kwargs: Any) -> None:
    if headers is None:
        return
    if CORRELATION_HEADER_NAME in headers:
        # An explicit value some caller already set on this specific publish call is never
        # silently overwritten -- there is no ambiguity about which id should win.
        return
    correlation_id = _current_correlation_id()
    if correlation_id is not None:
        headers[CORRELATION_HEADER_NAME] = correlation_id


def _reset_context(**_kwargs: Any) -> None:
    request_id_var.set(None)
    task_context_var.set(None)


def _resolve_task_correlation_id(*candidates: object) -> str:
    """Returns the first `candidate` that `is_valid_correlation_id`, in the order given, or a
    freshly generated id if none is. Every candidate -- the inbound header, `root_id`, `task_id` --
    is validated identically; none is trusted just because of where it came from (2026 review,
    Finding 3)."""
    for candidate in candidates:
        if is_valid_correlation_id(candidate):
            return candidate
    return uuid4().hex


def _safe_metadata_string(value: object) -> str | None:
    """Returns `value` unchanged if it `is_valid_correlation_id` (the same bounded, log-injection-
    safe shape), else `None`. Applied to every string-ish piece of Celery message metadata this
    module places into a log field (`root_id`, `parent_id`, `task_name`) -- `request.root_id`/
    `.parent_id` are derived from message properties, not guaranteed safe just because they came
    from `Task.request` (2026 review, Finding 3); a malformed value is omitted, never logged raw."""
    return value if is_valid_correlation_id(value) else None


def _safe_retry_count(value: object) -> int | None:
    """Celery documents `request.retries` as a non-negative integer starting at 0.
    `isinstance(value, int)` alone is not a strict enough check (2026 review round 3): `bool` is a
    subclass of `int` in Python, so `isinstance(True, int)` is `True` -- `type(value) is int`
    excludes `bool` while still accepting any real `int`, and `value >= 0` additionally rejects a
    negative count, which Celery itself never produces but a malformed/forged message could still
    claim. Anything else (malformed message metadata) is omitted rather than logged raw or
    coerced."""
    if type(value) is int and value >= 0:
        return value
    return None


def _task_prerun(task_id: str = "", task: Any = None, **_kwargs: Any) -> None:
    request = getattr(task, "request", None)
    headers = getattr(request, "headers", None) if request else None
    header_candidate = (
        headers.get(CORRELATION_HEADER_NAME) if isinstance(headers, Mapping) else None
    )
    raw_root_id = getattr(request, "root_id", None) if request else None

    correlation_id = _resolve_task_correlation_id(header_candidate, raw_root_id, task_id)
    request_id_var.set(correlation_id)

    task_name = getattr(task, "name", None) if task is not None else None
    raw_parent_id = getattr(request, "parent_id", None) if request else None
    raw_retries = getattr(request, "retries", None) if request else None
    task_context_var.set(
        {
            "celery_task_id": _safe_metadata_string(task_id),
            "celery_root_id": _safe_metadata_string(raw_root_id),
            "celery_parent_id": _safe_metadata_string(raw_parent_id),
            "celery_task_name": _safe_metadata_string(task_name),
            "celery_retries": _safe_retry_count(raw_retries),
        }
    )


def connect_correlation_signals() -> None:
    """Idempotent: safe to call every time `create_celery_app` runs (tests call it repeatedly),
    the same way `celery_app.py`'s own `setup_logging` connection already relies on `dispatch_uid`
    to make a repeat `.connect()` call a no-op rather than a duplicate handler."""
    signals.before_task_publish.connect(
        _before_task_publish,
        weak=False,
        dispatch_uid="tapjob_worker.correlation.before_task_publish",
    )
    signals.task_prerun.connect(
        _task_prerun, weak=False, dispatch_uid="tapjob_worker.correlation.task_prerun"
    )
    signals.task_postrun.connect(
        _reset_context, weak=False, dispatch_uid="tapjob_worker.correlation.task_postrun"
    )
    signals.task_failure.connect(
        _reset_context, weak=False, dispatch_uid="tapjob_worker.correlation.task_failure"
    )
    signals.task_retry.connect(
        _reset_context, weak=False, dispatch_uid="tapjob_worker.correlation.task_retry"
    )

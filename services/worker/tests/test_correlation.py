"""Worker-side TapJob correlation propagation (docs/adr/0006-worker-correlation-deployment-cd-
contract.md). Pure-function tests exercise `_before_task_publish`/`_task_prerun`/`_reset_context`
directly (fast, deterministic, no broker); a few wiring tests prove `connect_correlation_signals`
itself is idempotent and that the real Celery signal dispatch reaches these handlers.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from celery import signals

from tapjob_api.core.config import Environment
from tapjob_api.core.correlation import CORRELATION_ID_PATTERN
from tapjob_api.core.logging import request_id_var, task_context_var
from tapjob_worker.celery_app import create_celery_app
from tapjob_worker.config import WorkerSettings
from tapjob_worker.correlation import (
    CORRELATION_HEADER_NAME,
    _before_task_publish,
    _reset_context,
    _safe_retry_count,
    _task_prerun,
    connect_correlation_signals,
)

REDIS_URL = "redis://localhost:6379/0"


def _settings() -> WorkerSettings:
    return WorkerSettings(app_env=Environment.LOCAL, redis_url=REDIS_URL)  # type: ignore[arg-type]


def _fake_request(
    *,
    task_id: str,
    root_id: str | None,
    parent_id: str | None = None,
    retries: int = 0,
    headers: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id, root_id=root_id, parent_id=parent_id, retries=retries, headers=headers
    )


def _fake_task(*, name: str, request: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(name=name, request=request)


@pytest.fixture(autouse=True)
def _clean_context() -> Any:
    """Every test starts and ends with both contextvars unset, regardless of what a given test
    does in between -- the same hygiene the HTTP middleware's own tests already rely on."""
    request_token = request_id_var.set(None)
    context_token = task_context_var.set(None)
    try:
        yield
    finally:
        request_id_var.reset(request_token)
        task_context_var.reset(context_token)


# -- publish-side propagation (_before_task_publish) ---------------------------------------------


def test_publish_within_context_adds_the_custom_header() -> None:
    request_id_var.set("corr-123")
    headers: dict[str, Any] = {}

    _before_task_publish(headers=headers)

    assert headers[CORRELATION_HEADER_NAME] == "corr-123"


def test_publish_without_context_does_not_invent_a_header() -> None:
    headers: dict[str, Any] = {}

    _before_task_publish(headers=headers)

    assert CORRELATION_HEADER_NAME not in headers


def test_publish_never_overwrites_an_explicit_header_already_set() -> None:
    request_id_var.set("corr-from-context")
    headers: dict[str, Any] = {CORRELATION_HEADER_NAME: "explicit-value"}

    _before_task_publish(headers=headers)

    assert headers[CORRELATION_HEADER_NAME] == "explicit-value"


def test_publish_with_no_headers_mapping_does_not_raise() -> None:
    request_id_var.set("corr-123")

    _before_task_publish(headers=None)  # must be a safe no-op, never a crash


def test_nested_publication_from_within_a_running_task_preserves_the_correlation_id() -> None:
    # Mirrors the real sequence: task_prerun binds the parent task's correlation id, and ANY
    # publish that happens while that task's body is running (a nested `.delay()`) must propagate
    # the same id, not the parent task's own Celery task id.
    parent_request = _fake_request(task_id="parent-task-id", root_id="root-abc")
    _task_prerun(task_id="parent-task-id", task=_fake_task(name="parent", request=parent_request))

    nested_headers: dict[str, Any] = {}
    _before_task_publish(headers=nested_headers)

    assert nested_headers[CORRELATION_HEADER_NAME] == "root-abc"
    assert nested_headers[CORRELATION_HEADER_NAME] != "parent-task-id"


# -- consumption / binding (_task_prerun) ---------------------------------------------------------


def test_valid_inbound_header_is_bound_unchanged() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-1", headers={CORRELATION_HEADER_NAME: "inbound-corr-id"}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "inbound-corr-id"


def test_missing_header_falls_back_to_root_id_deterministically() -> None:
    request = _fake_request(task_id="task-1", root_id="root-xyz", headers=None)

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-xyz"


def test_invalid_inbound_header_falls_back_and_never_enters_context() -> None:
    malicious = "has\nnewline\ninjection"
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: malicious}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"
    assert request_id_var.get() != malicious
    context = task_context_var.get()
    assert context is not None
    assert malicious not in context.values()


def test_task_id_is_never_confused_with_the_resolved_correlation_id() -> None:
    # A task's own Celery task id is a different concept from the cross-task-boundary TapJob
    # correlation id -- NOT because retry assigns a new task id (it does not: per Celery's own
    # docs, `Task.retry()` "send[s] a new message, using the same task-id"), but because task id
    # identifies one execution attempt while root_id is what stays stable across an entire
    # workflow/chain/fan-out, which is what a cross-task correlation id should track. With no
    # inbound header, resolution must prefer root_id over this task's own task_id.
    request = _fake_request(task_id="attempt-task-id", root_id="original-root-id", headers=None)

    _task_prerun(task_id="attempt-task-id", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "original-root-id"
    assert request_id_var.get() != "attempt-task-id"


# -- untrusted/malformed message metadata (2026 review, Finding 3) -------------------------------
# `headers.get(...)`, `request.root_id`, `request.task_id` are all message-derived values a
# non-standard or malicious producer could set to anything; none is trusted just because of
# where it came from. No candidate here may crash `_task_prerun`.


def test_header_integer_falls_back_without_raising() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: 123}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"


def test_header_list_falls_back_without_raising() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: ["a", "b"]}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"


def test_header_dict_falls_back_without_raising() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: {"a": 1}}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"


def test_header_empty_string_falls_back_without_raising() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: ""}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"


def test_overlong_header_falls_back_without_raising() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-safe", headers={CORRELATION_HEADER_NAME: "a" * 129}
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "root-safe"


def test_invalid_root_id_falls_back_to_task_id() -> None:
    request = _fake_request(task_id="task-1", root_id="root with spaces", headers=None)

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "task-1"


def test_non_string_root_id_falls_back_to_task_id_without_raising() -> None:
    request = _fake_request(task_id="task-1", root_id=12345, headers=None)  # type: ignore[arg-type]

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    assert request_id_var.get() == "task-1"


def test_invalid_task_id_falls_back_to_a_freshly_generated_id() -> None:
    request = _fake_request(task_id="bad\ntask\nid", root_id=None, headers=None)

    _task_prerun(task_id="bad\ntask\nid", task=_fake_task(name="t", request=request))

    resolved = request_id_var.get()
    assert resolved is not None
    assert resolved != "bad\ntask\nid"
    assert CORRELATION_ID_PATTERN.fullmatch(resolved) is not None


# -- retry-count metadata hardening (2026 review round 3) ----------------------------------------
# Celery documents `request.retries` as a non-negative integer starting at 0. `bool` is a subclass
# of `int` in Python, so a naive `isinstance(value, int)` would wrongly accept `True`/`False`; a
# negative value is never produced by Celery itself but must still be rejected defensively, since
# this field is still message-derived metadata, not a value this module computed itself.


def test_retry_count_zero_passes() -> None:
    assert _safe_retry_count(0) == 0


def test_retry_count_positive_passes() -> None:
    assert _safe_retry_count(2) == 2


def test_retry_count_negative_is_rejected() -> None:
    assert _safe_retry_count(-1) is None


def test_retry_count_true_is_rejected() -> None:
    assert _safe_retry_count(True) is None


def test_retry_count_false_is_rejected() -> None:
    assert _safe_retry_count(False) is None


def test_retry_count_numeric_string_is_rejected() -> None:
    assert _safe_retry_count("1") is None


def test_retry_count_float_is_rejected() -> None:
    assert _safe_retry_count(1.0) is None


def test_both_root_id_and_task_id_invalid_falls_back_to_a_freshly_generated_id() -> None:
    request = _fake_request(task_id="bad task id", root_id="also bad", headers=None)

    _task_prerun(task_id="bad task id", task=_fake_task(name="t", request=request))

    resolved = request_id_var.get()
    assert resolved is not None
    assert resolved not in ("bad task id", "also bad")
    assert CORRELATION_ID_PATTERN.fullmatch(resolved) is not None


def test_newline_metadata_is_omitted_from_logged_context_not_logged_raw() -> None:
    request = _fake_request(
        task_id="task-1", root_id="root-1", parent_id="parent\nwith\nnewline", retries=0
    )

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    context = task_context_var.get()
    assert context is not None
    assert context["celery_parent_id"] is None
    assert "parent\nwith\nnewline" not in context.values()


def test_root_id_and_parent_id_are_preserved_as_separate_metadata() -> None:
    request = _fake_request(task_id="task-1", root_id="root-1", parent_id="parent-1", retries=2)

    _task_prerun(task_id="task-1", task=_fake_task(name="my.task", request=request))

    context = task_context_var.get()
    assert context is not None
    assert context["celery_task_id"] == "task-1"
    assert context["celery_root_id"] == "root-1"
    assert context["celery_parent_id"] == "parent-1"
    assert context["celery_task_name"] == "my.task"
    assert context["celery_retries"] == 2


def test_bound_context_never_contains_task_args_or_kwargs() -> None:
    # The fake request below intentionally carries args/kwargs, the same shape a real Celery
    # Context does -- task_prerun must never copy them into the logged context.
    request = _fake_request(task_id="task-1", root_id="root-1")
    request.args = ("sensitive-positional-arg",)
    request.kwargs = {"resume_text": "should never be logged"}

    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    context = task_context_var.get()
    assert context is not None
    assert "args" not in context
    assert "kwargs" not in context
    assert "sensitive-positional-arg" not in context.values()


def test_task_with_no_request_object_does_not_raise() -> None:
    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=None))

    # No header to resolve and no root_id on the (absent) request -- falls back to the task id.
    assert request_id_var.get() == "task-1"


# -- context cleanup (_reset_context) --------------------------------------------------------------


def test_reset_clears_both_contextvars() -> None:
    request_id_var.set("corr-1")
    task_context_var.set({"celery_task_id": "task-1"})

    _reset_context()

    assert request_id_var.get() is None
    assert task_context_var.get() is None


def test_success_path_clears_context() -> None:
    request = _fake_request(task_id="task-1", root_id="root-1")
    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))
    assert request_id_var.get() is not None

    _reset_context(task_id="task-1", task=None, retval="ok", state="SUCCESS")

    assert request_id_var.get() is None
    assert task_context_var.get() is None


def test_failure_path_clears_context() -> None:
    request = _fake_request(task_id="task-1", root_id="root-1")
    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    _reset_context(task_id="task-1", exception=RuntimeError("boom"))

    assert request_id_var.get() is None
    assert task_context_var.get() is None


def test_retry_path_clears_context() -> None:
    request = _fake_request(task_id="task-1", root_id="root-1")
    _task_prerun(task_id="task-1", task=_fake_task(name="t", request=request))

    _reset_context(request=request, reason="transient failure")

    assert request_id_var.get() is None
    assert task_context_var.get() is None


def test_two_sequential_tasks_do_not_share_correlation_by_accident() -> None:
    request_a = _fake_request(task_id="task-a", root_id="root-a", headers=None)
    _task_prerun(task_id="task-a", task=_fake_task(name="a", request=request_a))
    assert request_id_var.get() == "root-a"
    _reset_context()

    request_b = _fake_request(task_id="task-b", root_id="root-b", headers=None)
    _task_prerun(task_id="task-b", task=_fake_task(name="b", request=request_b))

    assert request_id_var.get() == "root-b"
    assert request_id_var.get() != "root-a"


# -- signal wiring (connect_correlation_signals / create_celery_app) -----------------------------


def test_connect_correlation_signals_is_idempotent() -> None:
    # Relative, not absolute: this asserts a SECOND (and third) call adds zero new receivers
    # compared to right after the first, regardless of how many unrelated receivers this process's
    # signal registry already held before this test ran (dispatch_uid-based connections persist
    # for the lifetime of the process, across the whole test session, not just within one test).
    connect_correlation_signals()
    after_first = len(signals.before_task_publish.receivers)

    connect_correlation_signals()
    connect_correlation_signals()
    after_more_calls = len(signals.before_task_publish.receivers)

    assert after_more_calls == after_first


def test_create_celery_app_called_repeatedly_does_not_duplicate_handlers() -> None:
    create_celery_app(_settings())
    after_first = len(signals.task_prerun.receivers)

    create_celery_app(_settings())
    after_second = len(signals.task_prerun.receivers)

    assert after_second == after_first


def test_real_signal_dispatch_reaches_before_task_publish_exactly_once() -> None:
    connect_correlation_signals()
    request_id_var.set("corr-dispatch-test")
    headers: dict[str, Any] = {}

    signals.before_task_publish.send(sender="some.task", headers=headers)

    assert headers[CORRELATION_HEADER_NAME] == "corr-dispatch-test"

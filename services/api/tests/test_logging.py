import json
import logging
import sys

from tapjob_api.core.logging import JsonFormatter, request_id_var, task_context_var


def _format(record: logging.LogRecord) -> dict[str, object]:
    output = JsonFormatter(service="api", environment="local").format(record)
    parsed: dict[str, object] = json.loads(output)
    return parsed


def _record(message: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.getLogger("tapjob.test").makeRecord(
        "tapjob.test", level, __file__, 1, message, (), None
    )


def test_log_line_is_structured_json_with_context() -> None:
    record = _record("hello")
    record.http_status = 200
    token = request_id_var.set("req-1")
    try:
        payload = _format(record)
    finally:
        request_id_var.reset(token)

    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["service"] == "api"
    assert payload["request_id"] == "req-1"
    assert payload["http_status"] == 200
    assert str(payload["timestamp"]).endswith("+00:00")


def test_task_context_fields_are_merged_into_the_log_line() -> None:
    record = _record("task done")
    token = task_context_var.set({"celery_task_id": "task-1", "celery_root_id": "root-1"})
    try:
        payload = _format(record)
    finally:
        task_context_var.reset(token)

    assert payload["celery_task_id"] == "task-1"
    assert payload["celery_root_id"] == "root-1"


def test_task_context_absent_by_default() -> None:
    payload = _format(_record("no task"))

    assert "celery_task_id" not in payload


def test_exceptions_are_serialized_without_request_context() -> None:
    record = _record("failed", logging.ERROR)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record.exc_info = sys.exc_info()

    payload = _format(record)

    assert "RuntimeError: boom" in str(payload["exception"])
    assert "request_id" not in payload

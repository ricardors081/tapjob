"""Structured JSON logging with request correlation (docs/ENGINEERING_STANDARDS.md §18).

Callers attach structured fields through ``extra``; never pass secrets, tokens or document bodies.
"""

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# A small, generic place for a caller (the worker's Celery task hooks, for now) to attach a few
# extra structured fields to every log line emitted during some scope, the same way `request_id_var`
# already does for one field. Kept deliberately generic here (no Celery-specific field names) so
# this module stays a cross-cutting logging primitive rather than owning worker-specific concepts
# (docs/adr/0006-worker-correlation-deployment-cd-contract.md): the worker decides what goes in it.
task_context_var: ContextVar[Mapping[str, Any] | None] = ContextVar("task_context", default=None)

# Attributes every LogRecord carries; anything else on a record came from ``extra``.
_STANDARD_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

# Framework loggers that install their own handlers; they are routed through the root JSON handler.
_FRAMEWORK_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "celery")


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        task_context = task_context_var.get()
        if task_context is not None:
            payload.update(task_context)
        for key, value in vars(record).items():
            if key not in _STANDARD_RECORD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, level: str, service: str, environment: str) -> None:
    """Install a single JSON stdout handler on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _FRAMEWORK_LOGGERS:
        framework_logger = logging.getLogger(name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True

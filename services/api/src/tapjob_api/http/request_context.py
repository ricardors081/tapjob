"""Request correlation ID propagation and one structured log line per HTTP request."""

import logging
import re
import time
from http import HTTPStatus
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tapjob_api.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
# Inbound IDs are echoed into logs and responses, so they must be bounded and log-injection safe.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MILLISECONDS_PER_SECOND = 1000

logger = logging.getLogger(__name__)


def _resolve_request_id(scope: Scope) -> str:
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if incoming is not None and _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid4().hex


class RequestContextMiddleware:
    """Pure ASGI middleware: assigns a request ID, returns it in a header and logs the outcome."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        # Reported if the downstream app raises before sending a response.
        status_code = int(HTTPStatus.INTERNAL_SERVER_ERROR)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Assignment replaces any X-Request-ID set downstream: exactly one canonical value.
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "http request completed",
                extra={
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_status": status_code,
                    "duration_ms": round(
                        (time.perf_counter() - started) * _MILLISECONDS_PER_SECOND, 1
                    ),
                },
            )
            request_id_var.reset(token)

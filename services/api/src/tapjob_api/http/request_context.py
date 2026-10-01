"""Request correlation ID propagation and one structured log line per HTTP request."""

import logging
import re
import time
from http import HTTPStatus
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tapjob_api.core.logging import request_id_var
from tapjob_api.http.errors import handle_unhandled_exception

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
    """Pure ASGI middleware: assigns a request ID, returns it in a header and logs the outcome.

    Also the last point that can still attach X-Request-ID (and a populated ErrorResponse.
    requestId) to a truly unhandled exception's response. Starlette promotes a handler registered
    for the bare ``Exception`` class/status 500 to ``ServerErrorMiddleware``, which sits *outside*
    every ``add_middleware``-registered middleware -- including this one -- in the ASGI stack, so
    it never sees ``send_with_request_id`` below, and by the time it would run, this middleware's
    own ``finally`` block has already reset ``request_id_var`` back to ``None``. Catching here,
    while the context is still live, and sending the response ourselves closes that gap; the
    subsequent ``raise`` still propagates the exception to the ASGI server/Sentry for
    observability, exactly as it did before this middleware existed.
    """

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
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                # Assignment replaces any X-Request-ID set downstream: exactly one canonical value.
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            if not response_started:
                response = handle_unhandled_exception(Request(scope, receive), exc)
                await response(scope, receive, send_with_request_id)
            raise
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

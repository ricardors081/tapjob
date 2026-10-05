"""Canonical error envelope and exception handlers (docs/adr/0004-http-errors-pagination-
correlation-idempotency.md).

``message`` is safe to display but is never a contract for client logic: UI/business decisions
branch on ``code``. Nothing here ever echoes an exception message, stack trace, DSN, token or
request body back to the caller (AGENTS.md §8, docs/ENGINEERING_STANDARDS.md §8/§18).
"""

import logging
from collections.abc import Mapping
from enum import StrEnum
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from tapjob_api.core.logging import request_id_var

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Machine-readable error vocabulary. Clients localize/branch on this, never on ``message``."""

    INVALID_REQUEST = "INVALID_REQUEST"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UPSTREAM_FAILED = "UPSTREAM_FAILED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    INVALID_CURSOR = "INVALID_CURSOR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # WP1.1 — identity/auth (docs/adr/0009-identity-auth-user-account.md): a verified-token trust
    # boundary needs a finer vocabulary than AUTHENTICATION_REQUIRED alone, since a mobile client's
    # retry/reauth behavior differs for each of these cases.
    AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"  # noqa: S105 -- error code, not a credential
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"  # noqa: S105 -- error code, not a credential
    AUTH_TOKEN_REVOKED = "AUTH_TOKEN_REVOKED"  # noqa: S105 -- error code, not a credential
    ACCOUNT_NOT_BOOTSTRAPPED = "ACCOUNT_NOT_BOOTSTRAPPED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    REAUTHENTICATION_REQUIRED = "REAUTHENTICATION_REQUIRED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    # WP1.2 — career profile & professional truth store (docs/adr/0010-career-profile-truth-
    # store.md): a small, coherent vocabulary for the optimistic-concurrency and fact-lifecycle
    # invariants the mobile/WhatsApp client must branch on.
    PROFILE_VERSION_CONFLICT = "PROFILE_VERSION_CONFLICT"
    PROFILE_FACT_NOT_FOUND = "PROFILE_FACT_NOT_FOUND"
    PROFILE_FACT_ALREADY_RETRACTED = "PROFILE_FACT_ALREADY_RETRACTED"
    PROFILE_VERSION_NOT_FOUND = "PROFILE_VERSION_NOT_FOUND"


# A status not explicitly raised anywhere yet (e.g. by a future connector/port) still needs a
# sensible code; see _fallback_code_for_status. Every member here IS reachable today, either via a
# raised HTTPException/ApiError or via Starlette's own 404/405 defaults (see test_errors.py).
_DEFAULT_CODE_BY_STATUS: dict[int, ErrorCode] = {
    HTTPStatus.BAD_REQUEST: ErrorCode.INVALID_REQUEST,
    HTTPStatus.UNAUTHORIZED: ErrorCode.AUTHENTICATION_REQUIRED,
    HTTPStatus.FORBIDDEN: ErrorCode.PERMISSION_DENIED,
    HTTPStatus.NOT_FOUND: ErrorCode.RESOURCE_NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    HTTPStatus.CONFLICT: ErrorCode.CONFLICT,
    HTTPStatus.UNPROCESSABLE_ENTITY: ErrorCode.VALIDATION_FAILED,
    HTTPStatus.TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
    HTTPStatus.INTERNAL_SERVER_ERROR: ErrorCode.INTERNAL_ERROR,
    HTTPStatus.BAD_GATEWAY: ErrorCode.UPSTREAM_FAILED,
    HTTPStatus.SERVICE_UNAVAILABLE: ErrorCode.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT: ErrorCode.UPSTREAM_TIMEOUT,
}

# Safe, PT-BR default message per code (docs/ENGINEERING_STANDARDS.md §9: backend does not localize
# by Accept-Language at this stage; mobile/web localize their own UI from `code`). Every ErrorCode
# member must have one -- enforced by test_errors.py rather than relied on silently.
_DEFAULT_MESSAGE_BY_CODE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "A requisição não pôde ser processada.",
    ErrorCode.VALIDATION_FAILED: "Os dados enviados são inválidos.",
    ErrorCode.AUTHENTICATION_REQUIRED: "É necessário autenticar-se para continuar.",
    ErrorCode.PERMISSION_DENIED: "Você não tem permissão para executar esta ação.",
    ErrorCode.RESOURCE_NOT_FOUND: "Recurso não encontrado.",
    ErrorCode.METHOD_NOT_ALLOWED: "Método não permitido para este recurso.",
    ErrorCode.CONFLICT: "A operação não pôde ser concluída devido a um conflito de estado.",
    ErrorCode.RATE_LIMITED: "Muitas requisições. Tente novamente em instantes.",
    ErrorCode.SERVICE_UNAVAILABLE: "Serviço temporariamente indisponível.",
    ErrorCode.UPSTREAM_FAILED: "Uma integração externa falhou.",
    ErrorCode.UPSTREAM_TIMEOUT: "Uma integração externa não respondeu a tempo.",
    ErrorCode.INVALID_CURSOR: "O cursor de paginação informado é inválido.",
    ErrorCode.INTERNAL_ERROR: "Ocorreu um erro inesperado.",
    ErrorCode.AUTH_TOKEN_MISSING: "É necessário informar um token de autenticação.",
    ErrorCode.AUTH_TOKEN_INVALID: "O token de autenticação informado é inválido.",
    ErrorCode.AUTH_TOKEN_REVOKED: "A sessão foi revogada. Autentique-se novamente.",
    ErrorCode.ACCOUNT_NOT_BOOTSTRAPPED: "Esta conta ainda não foi inicializada.",
    ErrorCode.ACCOUNT_DISABLED: "Esta conta está desativada.",
    ErrorCode.REAUTHENTICATION_REQUIRED: "É necessário autenticar-se novamente para esta ação.",
    ErrorCode.DEVICE_NOT_FOUND: "Dispositivo não encontrado.",
    ErrorCode.IDENTITY_CONFLICT: "Esta identidade já está associada a outra conta.",
    ErrorCode.PROFILE_VERSION_CONFLICT: (
        "O perfil foi alterado por outra requisição. Atualize e tente novamente."
    ),
    ErrorCode.PROFILE_FACT_NOT_FOUND: "Fato de perfil não encontrado.",
    ErrorCode.PROFILE_FACT_ALREADY_RETRACTED: (
        "Este fato de perfil não está mais ativo (já substituído ou retratado)."
    ),
    ErrorCode.PROFILE_VERSION_NOT_FOUND: ("A versão de perfil solicitada ainda não existe."),
}


class ValidationErrorItem(BaseModel):
    """One field-level validation failure. Carries only a normalized ``code`` (Pydantic's error
    ``type``) plus ``location``, never the raw ``input``/``ctx``/``msg``: those can echo request
    data -- including personal data -- back into the error body."""

    location: list[str]
    code: str


class ErrorResponse(BaseModel):
    """Canonical envelope for every non-2xx response (docs/ENGINEERING_STANDARDS.md §8)."""

    model_config = ConfigDict(populate_by_name=True)

    code: ErrorCode
    message: str
    request_id: str = Field(alias="requestId")
    errors: list[ValidationErrorItem] | None = None
    details: dict[str, Any] | None = None


# Documents that any route can hit the catch-all handler below and get back ErrorResponse. Used on
# /health and /ready, which keep their own pre-existing 200/503 response shapes otherwise
# (docs/adr/0002-mobile-foundation.md); every future /api/v1 route additionally documents its own
# specific non-2xx cases (enforced by tapjob_api.http.contract_rules.validate_error_responses).
COMMON_OPERATIONAL_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    HTTPStatus.INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Erro interno inesperado.",
    },
}


class ApiError(Exception):
    """Raise for any application/domain error that must reach the client as ErrorResponse. Prefer
    this over a bare HTTPException whenever the code must differ from the status's default, or
    when details/Retry-After are needed."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        status_code: int,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details
        self.retry_after_seconds = retry_after_seconds


def _fallback_code_for_status(status_code: int) -> ErrorCode:
    return (
        ErrorCode.INTERNAL_ERROR
        if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        else ErrorCode.INVALID_REQUEST
    )


def _error_response(
    request: Request,
    *,
    code: ErrorCode,
    status_code: int,
    message: str | None = None,
    errors: list[ValidationErrorItem] | None = None,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message or _DEFAULT_MESSAGE_BY_CODE[code],
        request_id=request_id_var.get() or "",
        errors=errors,
        details=details,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(by_alias=True, exclude_none=True),
        headers=dict(headers) if headers else None,
    )


def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI/Starlette only ever dispatch to this handler for an exception matching the type it
    # was registered under (register_exception_handlers below); the broad `Exception` parameter
    # type is what Starlette's dispatch API itself requires (see the mypy note there).
    error = cast(ApiError, exc)
    headers = (
        {"Retry-After": str(error.retry_after_seconds)}
        if error.retry_after_seconds is not None
        else None
    )
    return _error_response(
        request,
        code=error.code,
        status_code=error.status_code,
        message=error.message,
        details=error.details,
        headers=headers,
    )


def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(RequestValidationError, exc)
    items = [
        ValidationErrorItem(location=[str(part) for part in item["loc"]], code=item["type"])
        for item in error.errors()
    ]
    return _error_response(
        request,
        code=ErrorCode.VALIDATION_FAILED,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        errors=items,
    )


def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    code = _DEFAULT_CODE_BY_STATUS.get(
        error.status_code, _fallback_code_for_status(error.status_code)
    )
    return _error_response(request, code=code, status_code=error.status_code, headers=error.headers)


def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log and build the INTERNAL_ERROR response for a truly unhandled exception.

    Public (not prefixed with ``_``) because tapjob_api.http.request_context.
    RequestContextMiddleware calls this directly as the sole handler of last resort, rather than
    this module registering it with FastAPI for the bare ``Exception`` class: Starlette promotes an
    ``Exception``/500 handler to ``ServerErrorMiddleware``, which sits *outside* every
    ``add_middleware``-registered middleware -- including ``RequestContextMiddleware`` -- in the
    ASGI stack. A response built there could never carry X-Request-ID/``requestId`` (it never sees
    RequestContextMiddleware's header-injecting ``send`` wrapper, and by the time it would run,
    that middleware's ``finally`` block has already reset ``request_id_var`` back to ``None``). See
    request_context.py for where this is actually invoked; every app this module's tests build
    includes RequestContextMiddleware, so this function has exactly one caller.
    """
    # Server-side only: the exception type/message/traceback never reach the client, only this
    # structured log line (and, when SENTRY_DSN is configured, the Sentry SDK's own ASGI
    # instrumentation, which observes the exception independently of this function).
    logger.exception(
        "unhandled exception",
        extra={"http_method": request.method, "http_path": request.url.path},
    )
    return _error_response(
        request, code=ErrorCode.INTERNAL_ERROR, status_code=HTTPStatus.INTERNAL_SERVER_ERROR
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler so ALL non-2xx responses -- including ones FastAPI/Starlette would
    otherwise produce by default (404, 405, its own 422 validation shape) -- use ErrorResponse.

    Deliberately does NOT register ``handle_unhandled_exception`` for the bare ``Exception`` class
    here; see that function's docstring for why ``RequestContextMiddleware`` must own that call
    instead. An app built without that middleware falls back to Starlette's own generic 500
    response for a truly unhandled exception -- every app this codebase builds includes it.
    """
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)

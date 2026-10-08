"""FastAPI endpoints for Jobs Domain and Manual Intake (WP2.1-A)."""

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.core.rate_limit import RedisFixedWindowRateLimiter
from tapjob_api.db.session import get_db_session, get_session_factory
from tapjob_api.http.errors import ApiError, ErrorCode, ErrorResponse
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity.models import User
from tapjob_api.jobs import service
from tapjob_api.jobs.dependencies import (
    JOB_PASTE_INTAKE_RATE_LIMIT,
    JOB_URL_INTAKE_RATE_LIMIT,
    get_safe_http_client,
)
from tapjob_api.jobs.errors import (
    ConcurrentRequestInProgressError,
    IdempotencyKeyPayloadMismatchError,
    IdempotencyOwnershipLostError,
    JobContentTypeUnsupportedError,
    JobNotFoundError,
    JobPageRequiresManualPasteError,
    JobTextTooLongError,
    JobTextTooShortError,
    JobUrlDestinationForbiddenError,
    JobUrlInvalidError,
    JobUrlTlsFailedError,
    JobUrlTooLargeError,
    JobUrlUnreachableError,
)
from tapjob_api.jobs.http_client import SafeHttpClient
from tapjob_api.jobs.models import Job
from tapjob_api.jobs.schemas import (
    CompanyDto,
    JobOriginDto,
    JobPasteIntakeRequest,
    JobProvenanceSummary,
    JobResponse,
    JobUrlIntakeRequest,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ERROR_TO_HTTP: dict[type[Exception], tuple[ErrorCode, int]] = {
    JobTextTooShortError: (ErrorCode.JOB_TEXT_TOO_SHORT, HTTPStatus.UNPROCESSABLE_ENTITY),
    JobTextTooLongError: (ErrorCode.JOB_TEXT_TOO_LONG, HTTPStatus.UNPROCESSABLE_ENTITY),
    JobUrlInvalidError: (ErrorCode.JOB_URL_INVALID, HTTPStatus.BAD_REQUEST),
    JobUrlDestinationForbiddenError: (
        ErrorCode.JOB_URL_DESTINATION_FORBIDDEN,
        HTTPStatus.FORBIDDEN,
    ),
    JobUrlUnreachableError: (ErrorCode.JOB_URL_UNREACHABLE, HTTPStatus.BAD_GATEWAY),
    JobUrlTlsFailedError: (ErrorCode.JOB_URL_TLS_FAILED, HTTPStatus.BAD_GATEWAY),
    JobUrlTooLargeError: (
        ErrorCode.JOB_URL_TOO_LARGE,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ),
    JobContentTypeUnsupportedError: (
        ErrorCode.JOB_CONTENT_TYPE_UNSUPPORTED,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ),
    JobPageRequiresManualPasteError: (
        ErrorCode.JOB_PAGE_REQUIRES_MANUAL_PASTE,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
    JobNotFoundError: (ErrorCode.JOB_NOT_FOUND, HTTPStatus.NOT_FOUND),
    IdempotencyKeyPayloadMismatchError: (
        ErrorCode.IDEMPOTENCY_KEY_PAYLOAD_MISMATCH,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
    ConcurrentRequestInProgressError: (
        ErrorCode.CONCURRENT_REQUEST_IN_PROGRESS,
        HTTPStatus.CONFLICT,
    ),
    IdempotencyOwnershipLostError: (
        ErrorCode.CONCURRENT_REQUEST_IN_PROGRESS,
        HTTPStatus.CONFLICT,
    ),
}


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_RATE_LIMITED_RESPONSES = _error(HTTPStatus.TOO_MANY_REQUESTS, "Limite de requisições excedido.")
_COMMON_RESPONSES = {
    **_AUTH_RESPONSES,
    **_RATE_LIMITED_RESPONSES,
    **_error(HTTPStatus.BAD_REQUEST, "Requisição inválida."),
    **_error(HTTPStatus.FORBIDDEN, "Acesso não autorizado ou destino proibido."),
    **_error(HTTPStatus.NOT_FOUND, "Recurso não encontrado."),
    **_error(HTTPStatus.CONFLICT, "Conflito de concorrência ou operação em andamento."),
    **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Validação de dados ou payload inválido."),
    **_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Conteúdo da resposta excede o limite máximo."),
    **_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Tipo de conteúdo não suportado."),
    **_error(HTTPStatus.BAD_GATEWAY, "Servidor de destino inalcançável ou falha TLS."),
}


def _to_job_response(job: Job) -> JobResponse:
    company_dto = CompanyDto(
        id=job.company.id,
        name=job.company.name,
        name_normalized=job.company.name_normalized,
        domain=job.company.domain,
        created_at=job.company.created_at,
        updated_at=job.company.updated_at,
    )
    origins_dto = [
        JobOriginDto(
            id=origin.id,
            job_id=origin.job_id,
            provider=origin.provider,
            external_id=origin.external_id,
            source_url=origin.source_url,
            canonical_url=origin.canonical_url,
            observation_status=origin.observation_status,
            consecutive_not_found_count=origin.consecutive_not_found_count,
            first_observed_at=origin.first_observed_at,
            last_observed_at=origin.last_observed_at,
            provenance_metadata=origin.provenance_metadata,
            created_at=origin.created_at,
            updated_at=origin.updated_at,
        )
        for origin in job.origins
    ]
    provenance_summary = JobProvenanceSummary.model_validate(job.provenance_summary)

    return JobResponse(
        id=job.id,
        company_id=job.company_id,
        company=company_dto,
        title=job.title,
        title_normalized=job.title_normalized,
        location_raw=job.location_raw,
        location_normalized=job.location_normalized,
        workplace_type=job.workplace_type,
        description_raw=job.description_raw,
        description_cleaned=job.description_cleaned,
        similarity_fingerprint=job.similarity_fingerprint,
        status=job.status,
        provenance_summary=provenance_summary,
        origins=origins_dto,
        expires_at=job.expires_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/intake/paste",
    operation_id="intakeJobPaste",
    response_model=JobResponse,
    status_code=HTTPStatus.CREATED,
    responses=_COMMON_RESPONSES,
)
def intake_job_paste(
    request: JobPasteIntakeRequest,
    user: User = Depends(identity_deps.get_current_user),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=255),
) -> Response:
    """Ingest a job description supplied via manual paste."""
    identity_deps.enforce_rate_limit(limiter, JOB_PASTE_INTAKE_RATE_LIMIT, str(user.id))

    reservation_token: uuid.UUID | None = None
    if idempotency_key:
        fingerprint = service.compute_request_fingerprint(
            request.model_dump_json(by_alias=True).encode("utf-8")
        )
        with session_factory() as idem_session:
            try:
                res = service.reserve_idempotency_key(
                    idem_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.paste",
                    request_fingerprint=fingerprint,
                )
                idem_session.commit()
            except tuple(_ERROR_TO_HTTP) as error:
                idem_session.rollback()
                code, status_code = _ERROR_TO_HTTP[type(error)]
                raise ApiError(code, status_code=status_code) from error

        if (
            not res.is_owner
            and res.stored_response is not None
            and res.stored_status_code is not None
        ):
            return JSONResponse(content=res.stored_response, status_code=res.stored_status_code)

        reservation_token = res.reservation_token

    try:
        with session_factory() as db_session:
            with db_session.begin():
                job = service.intake_job_paste(db_session, user=user, request=request)
                response_dto = _to_job_response(job)
                response_dict = response_dto.model_dump(by_alias=True, mode="json")

                if idempotency_key and reservation_token is not None:
                    service.complete_idempotency_key(
                        db_session,
                        user_id=user.id,
                        key=idempotency_key,
                        operation="jobs.intake.paste",
                        reservation_token=reservation_token,
                        response_status=HTTPStatus.CREATED,
                        response_body=response_dict,
                        resource_id=job.id,
                    )

            # with db_session.begin(): commits both Job and IdempotencyKey atomically!
            return JSONResponse(content=response_dict, status_code=HTTPStatus.CREATED)

    except tuple(_ERROR_TO_HTTP) as error:
        if idempotency_key and reservation_token is not None:
            with session_factory() as clean_session, clean_session.begin():
                service.release_pending_idempotency_key(
                    clean_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.paste",
                    reservation_token=reservation_token,
                )
        code, status_code = _ERROR_TO_HTTP[type(error)]
        raise ApiError(code, status_code=status_code) from error
    except Exception:
        if idempotency_key and reservation_token is not None:
            with session_factory() as clean_session, clean_session.begin():
                service.release_pending_idempotency_key(
                    clean_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.paste",
                    reservation_token=reservation_token,
                )
        raise


@router.post(
    "/intake/url",
    operation_id="intakeJobUrl",
    response_model=JobResponse,
    status_code=HTTPStatus.CREATED,
    responses=_COMMON_RESPONSES,
)
async def intake_job_url(
    request: JobUrlIntakeRequest,
    user: User = Depends(identity_deps.get_current_user),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
    http_client: SafeHttpClient = Depends(get_safe_http_client),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=255),
) -> Response:
    """Ingest a job by fetching and parsing an untrusted URL through SSRF safety boundaries."""
    identity_deps.enforce_rate_limit(limiter, JOB_URL_INTAKE_RATE_LIMIT, str(user.id))

    reservation_token: uuid.UUID | None = None
    if idempotency_key:
        fingerprint = service.compute_request_fingerprint(
            request.model_dump_json(by_alias=True).encode("utf-8")
        )
        with session_factory() as idem_session:
            try:
                res = service.reserve_idempotency_key(
                    idem_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.url",
                    request_fingerprint=fingerprint,
                )
                idem_session.commit()
            except tuple(_ERROR_TO_HTTP) as error:
                idem_session.rollback()
                code, status_code = _ERROR_TO_HTTP[type(error)]
                raise ApiError(code, status_code=status_code) from error

        if (
            not res.is_owner
            and res.stored_response is not None
            and res.stored_status_code is not None
        ):
            return JSONResponse(content=res.stored_response, status_code=res.stored_status_code)

        reservation_token = res.reservation_token

    # 1. Fetch & extract happens OUTSIDE any database transaction
    try:
        final_url, media_type, extracted = await service.fetch_and_extract_job_url(
            request=request, http_client=http_client
        )
    except Exception as error:
        if idempotency_key and reservation_token is not None:
            with session_factory() as clean_session, clean_session.begin():
                service.release_pending_idempotency_key(
                    clean_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.url",
                    reservation_token=reservation_token,
                )
        if isinstance(error, tuple(_ERROR_TO_HTTP)):
            code, status_code = _ERROR_TO_HTTP[type(error)]
            raise ApiError(code, status_code=status_code) from error
        raise

    # 2. Short persistence transaction: persists Job + Idempotency COMPLETED atomically
    try:
        with session_factory() as db_session:
            with db_session.begin():
                job = service.persist_job_url(
                    db_session,
                    user=user,
                    source_url=request.url,
                    final_url=final_url,
                    media_type=media_type,
                    extracted=extracted,
                )
                response_dto = _to_job_response(job)
                response_dict = response_dto.model_dump(by_alias=True, mode="json")

                if idempotency_key and reservation_token is not None:
                    service.complete_idempotency_key(
                        db_session,
                        user_id=user.id,
                        key=idempotency_key,
                        operation="jobs.intake.url",
                        reservation_token=reservation_token,
                        response_status=HTTPStatus.CREATED,
                        response_body=response_dict,
                        resource_id=job.id,
                    )

            # with db_session.begin(): commits both Job and IdempotencyKey atomically!
            return JSONResponse(content=response_dict, status_code=HTTPStatus.CREATED)

    except tuple(_ERROR_TO_HTTP) as error:
        if idempotency_key and reservation_token is not None:
            with session_factory() as clean_session, clean_session.begin():
                service.release_pending_idempotency_key(
                    clean_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.url",
                    reservation_token=reservation_token,
                )
        code, status_code = _ERROR_TO_HTTP[type(error)]
        raise ApiError(code, status_code=status_code) from error
    except Exception:
        if idempotency_key and reservation_token is not None:
            with session_factory() as clean_session, clean_session.begin():
                service.release_pending_idempotency_key(
                    clean_session,
                    user_id=user.id,
                    key=idempotency_key,
                    operation="jobs.intake.url",
                    reservation_token=reservation_token,
                )
        raise


@router.get(
    "/{job_id}",
    operation_id="getJobById",
    response_model=JobResponse,
    status_code=HTTPStatus.OK,
    responses=_COMMON_RESPONSES,
)
def get_job_by_id(
    job_id: uuid.UUID,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> JobResponse:
    """Retrieve canonical job posting by ID."""
    try:
        job = service.get_job_by_id(session, job_id=job_id, user_id=user.id)
        return _to_job_response(job)
    except tuple(_ERROR_TO_HTTP) as error:
        code, status_code = _ERROR_TO_HTTP[type(error)]
        raise ApiError(code, status_code=status_code) from error

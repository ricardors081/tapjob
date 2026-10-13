"""FastAPI endpoints for Company Watchlist (WP2.3)."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import ApiError, ErrorCode, ErrorResponse
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity.models import User
from tapjob_api.jobs import feed_service
from tapjob_api.jobs.errors import CompanyNotFoundError
from tapjob_api.jobs.schemas import CompanyDto, CompanyWatchResponse

router = APIRouter(prefix="/companies", tags=["companies"])

_ERROR_TO_HTTP: dict[type[Exception], tuple[ErrorCode, int]] = {
    CompanyNotFoundError: (ErrorCode.COMPANY_NOT_FOUND, HTTPStatus.NOT_FOUND),
}


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_COMMON_RESPONSES = {
    **_AUTH_RESPONSES,
    **_error(HTTPStatus.BAD_REQUEST, "Requisição inválida."),
    **_error(HTTPStatus.FORBIDDEN, "Acesso não autorizado."),
    **_error(HTTPStatus.NOT_FOUND, "Recurso não encontrado."),
    **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Validação de dados ou payload inválido."),
}


@router.post(
    "/{company_id}/watch",
    operation_id="watchCompany",
    response_model=CompanyWatchResponse,
    status_code=HTTPStatus.OK,
    responses=_COMMON_RESPONSES,
)
def watch_company(
    company_id: uuid.UUID,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> CompanyWatchResponse:
    """Idempotently watch a company."""
    try:
        watch = feed_service.watch_company(session, user=user, company_id=company_id)
        session.commit()
        return CompanyWatchResponse(
            id=watch.id,
            company_id=watch.company_id,
            company=CompanyDto(
                id=watch.company.id,
                name=watch.company.name,
                name_normalized=watch.company.name_normalized,
                domain=watch.company.domain,
                created_at=watch.company.created_at,
                updated_at=watch.company.updated_at,
            ),
            created_at=watch.created_at,
        )
    except tuple(_ERROR_TO_HTTP) as error:
        code, status_code = _ERROR_TO_HTTP[type(error)]
        raise ApiError(code, status_code=status_code) from error


@router.delete(
    "/{company_id}/watch",
    operation_id="unwatchCompany",
    status_code=HTTPStatus.NO_CONTENT,
    responses=_COMMON_RESPONSES,
)
def unwatch_company(
    company_id: uuid.UUID,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """Idempotently unwatch a company."""
    feed_service.unwatch_company(session, user=user, company_id=company_id)
    session.commit()
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get(
    "/watched",
    operation_id="listWatchedCompanies",
    response_model=list[CompanyDto],
    status_code=HTTPStatus.OK,
    responses=_COMMON_RESPONSES,
)
def list_watched_companies(
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> list[CompanyDto]:
    """List companies watched by the authenticated user."""
    companies = feed_service.list_watched_companies(session, user=user)
    return [
        CompanyDto(
            id=c.id,
            name=c.name,
            name_normalized=c.name_normalized,
            domain=c.domain,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in companies
    ]

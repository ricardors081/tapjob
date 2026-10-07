"""`/api/v1/me/resume-imports` and `/api/v1/me/resumes/master` routes
(docs/adr/0011-resume-import.md). One adapter over `tapjob_api.resume_import.service` -- a future
WhatsApp/mobile channel calls the exact same service functions (docs/adr/0011-...md "channel-first
design"), never a second copy.
"""

import contextlib
import logging
from http import HTTPStatus
from typing import Any
from uuid import UUID

from celery import Celery
from fastapi import APIRouter, Depends, UploadFile
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import ProfileVersionConflictError
from tapjob_api.core.logging import request_id_var
from tapjob_api.core.rate_limit import RedisFixedWindowRateLimiter
from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import ApiError, ErrorCode, ErrorResponse
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity import service as identity_service
from tapjob_api.identity.models import AuditEventType, AuditOutcome, User
from tapjob_api.resume_import import dependencies as deps
from tapjob_api.resume_import import schemas, service
from tapjob_api.resume_import.candidates import CandidateValue
from tapjob_api.resume_import.errors import (
    CandidateDecisionCoverageError,
    CandidateValueInvalidError,
    DocumentTooLargeError,
    EncryptedDocumentError,
    MalformedDocumentError,
    ResumeImportNotFoundError,
    ResumeImportNotReadyError,
    TextNotExtractableError,
    UnsupportedDocumentTypeError,
)
from tapjob_api.resume_import.limits import MAX_UPLOAD_BYTES
from tapjob_api.resume_import.models import ImportStatus, ResumeImport, ResumeImportCandidate
from tapjob_api.resume_import.storage import ObjectStorageError, ObjectStoragePort
from tapjob_api.resume_import.tasks import RESUME_IMPORT_PROCESS_TASK_NAME

logger = logging.getLogger(__name__)

router = APIRouter(tags=["resume-import"])

_CANDIDATE_VALUE_ADAPTER: TypeAdapter[CandidateValue] = TypeAdapter(CandidateValue)


def _dispatch_processing(task_producer: Celery, *, import_id: UUID) -> None:
    """Best-effort: the `ResumeImport` row (already durably committed at `QUEUED`) is the real,
    recoverable record of intent to process -- never only this Celery message (round-1
    independent review "Finding 3"). A broker failure here is logged and swallowed, never an
    unhandled 500 for what is fundamentally a "the row still exists and will be picked up a bit
    later" situation: `router.get_resume_import`'s own poll re-dispatches once
    `service.should_redispatch_processing` judges the original attempt lost, and the worker's own
    idempotent claim makes any eventual duplicate dispatch always safe."""
    try:
        task_producer.send_task(RESUME_IMPORT_PROCESS_TASK_NAME, args=[str(import_id)])
    except Exception:
        logger.warning(
            "resume import processing dispatch failed -- durable row remains QUEUED for "
            "later recovery via polling",
            extra={"import_id": str(import_id)},
            exc_info=True,
        )


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_ACCOUNT_RESPONSES = {**_AUTH_RESPONSES, **_error(HTTPStatus.FORBIDDEN, "Conta desativada.")}
_BOOTSTRAPPED_RESPONSES = {
    **_ACCOUNT_RESPONSES,
    **_error(HTTPStatus.NOT_FOUND, "Conta ainda não inicializada."),
}
_RATE_LIMITED_RESPONSES = _error(HTTPStatus.TOO_MANY_REQUESTS, "Muitas requisições.")
_IMPORT_NOT_FOUND_RESPONSES = _error(
    HTTPStatus.NOT_FOUND, "Importação de currículo não encontrada."
)
_VALIDATION_RESPONSES = _error(HTTPStatus.UNPROCESSABLE_ENTITY, "Dados inválidos.")
_UPLOAD_REJECTED_RESPONSES = _error(
    HTTPStatus.UNPROCESSABLE_ENTITY, "O arquivo enviado não pôde ser aceito."
)


async def _read_bounded(upload_file: UploadFile, *, max_bytes: int) -> bytes:
    """Streams the upload in bounded chunks (docs/ENGINEERING_STANDARDS.md §26) -- never
    `await upload_file.read()` unbounded, which would load an arbitrarily large body fully into
    memory before any size check ever ran."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload_file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DocumentTooLargeError("upload exceeds the maximum allowed size")
        chunks.append(chunk)
    return b"".join(chunks)


def _candidate_response(candidate: ResumeImportCandidate) -> schemas.ResumeImportCandidateResponse:
    return schemas.ResumeImportCandidateResponse(
        id=candidate.id,
        candidate_kind=candidate.candidate_kind,
        value=_CANDIDATE_VALUE_ADAPTER.validate_python(candidate.value_json),
        confidence=candidate.confidence,
        source_excerpt=candidate.source_excerpt,
        review_state=candidate.review_state,
        created_at=candidate.created_at,
    )


def _import_response(
    resume_import: ResumeImport, candidates: list[ResumeImportCandidate] | None
) -> schemas.ResumeImportResponse:
    return schemas.ResumeImportResponse(
        id=resume_import.id,
        status=resume_import.status,
        original_filename=resume_import.original_filename,
        media_type=resume_import.media_type,
        byte_size=resume_import.byte_size,
        failure_code=resume_import.failure_code,
        candidates=(
            [_candidate_response(candidate) for candidate in candidates]
            if candidates is not None
            else None
        ),
        finalized_resume_id=resume_import.finalized_resume_id,
        finalized_resume_version_id=resume_import.finalized_resume_version_id,
        created_at=resume_import.created_at,
        updated_at=resume_import.updated_at,
    )


def _audit(
    session: Session,
    *,
    user: User,
    event_type: AuditEventType,
    outcome: AuditOutcome,
    metadata: dict[str, object] | None = None,
) -> None:
    identity_service.record_audit_event(
        session,
        user=user,
        event_type=event_type.value,
        outcome=outcome,
        metadata=metadata,
        correlation_id=request_id_var.get(),
    )


_UPLOAD_ERROR_TO_CODE: dict[type[Exception], ErrorCode] = {
    UnsupportedDocumentTypeError: ErrorCode.UNSUPPORTED_DOCUMENT_TYPE,
    DocumentTooLargeError: ErrorCode.DOCUMENT_TOO_LARGE,
    MalformedDocumentError: ErrorCode.MALFORMED_DOCUMENT,
    EncryptedDocumentError: ErrorCode.ENCRYPTED_DOCUMENT,
    TextNotExtractableError: ErrorCode.TEXT_NOT_EXTRACTABLE,
}


@router.post(
    "/me/resume-imports",
    operation_id="createResumeImport",
    response_model=schemas.ResumeImportResponse,
    status_code=HTTPStatus.ACCEPTED,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_UPLOAD_REJECTED_RESPONSES,
    },
)
async def create_resume_import(
    file: UploadFile,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
    storage: ObjectStoragePort = Depends(deps.get_resume_storage),
    task_producer: Celery = Depends(deps.get_task_producer),
) -> schemas.ResumeImportResponse:
    """Validate + store synchronously; extraction/parsing is enqueued to the worker AFTER this
    request's own transaction commits (docs/adr/0011-...md "async processing") -- never before, so
    a fast worker can never look up a row this transaction has not actually persisted yet."""
    identity_deps.enforce_rate_limit(limiter, deps.RESUME_IMPORT_UPLOAD_RATE_LIMIT, str(user.id))
    try:
        content = await _read_bounded(file, max_bytes=MAX_UPLOAD_BYTES)
        resume_import = service.create_resume_import(
            session,
            user=user,
            content=content,
            original_filename=file.filename or "resume",
            declared_content_type=file.content_type or "application/octet-stream",
            storage=storage,
        )
    except tuple(_UPLOAD_ERROR_TO_CODE) as error:
        code = _UPLOAD_ERROR_TO_CODE[type(error)]
        raise ApiError(code, status_code=HTTPStatus.UNPROCESSABLE_ENTITY) from error
    # Captured as plain Python values BEFORE any further DB work (round-2 independent review
    # "Finding 6"): `session.rollback()` below expires ORM attributes by default too, same hazard
    # as the worker-side lease-fencing token capture -- reading `resume_import.storage_key` AFTER a
    # rollback of a row that was never actually committed would try to re-SELECT a row that does
    # not exist in the database at all, rather than returning the value this request itself already
    # knows.
    import_id, storage_key = resume_import.id, resume_import.storage_key
    try:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.RESUME_IMPORT_CREATED,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "import_id": str(import_id),
                "media_type": resume_import.media_type,
                "byte_size": resume_import.byte_size,
            },
        )
        session.commit()
    except Exception:
        # The object was already durably written to storage (`service.create_resume_import`'s own
        # internal flush already succeeded, or this function would never have reached this point
        # at all) -- but the `ResumeImport` row covering it never durably committed. Compensate the
        # SAME way `service.create_resume_import` already does for its own earlier, narrower
        # failure window (round-2 independent review "Finding 6": the FULL upload transaction,
        # storage write through final commit, needs exactly one compensation boundary, not just
        # the first flush). Best-effort only -- `ObjectStorageError` here means the compensating
        # delete itself failed; the ORIGINAL failure is what the caller sees either way, never
        # masked by a secondary cleanup error, but the leak is still logged for operator
        # visibility.
        session.rollback()
        with contextlib.suppress(ObjectStorageError):
            storage.delete(storage_key=storage_key)
        logger.error(
            "resume import commit/audit failed after a durable storage write -- a compensating "
            "delete was attempted",
            extra={"import_id": str(import_id)},
            exc_info=True,
        )
        raise
    _dispatch_processing(task_producer, import_id=import_id)
    return _import_response(resume_import, candidates=None)


@router.get(
    "/me/resume-imports/{import_id}",
    operation_id="getResumeImport",
    response_model=schemas.ResumeImportResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_IMPORT_NOT_FOUND_RESPONSES,
        **_VALIDATION_RESPONSES,
    },
)
def get_resume_import(
    import_id: UUID,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.ResumeImportResponse:
    """A pure read, with no dispatch side effect (round-2 independent review "Finding 4": a `GET`
    used to also re-enqueue a stale import as a side effect of being polled, which both made an
    ordinary read cause operational broker traffic and left a row with no recovery path at all if
    the client simply never polled again). Stale-dispatch recovery is now entirely server-owned --
    see `tasks.sweep_stale_resume_import_dispatches_task`."""
    resume_import = service.find_owned_import(session, user=user, import_id=import_id)
    if resume_import is None:
        raise ApiError(ErrorCode.RESUME_IMPORT_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND)
    candidates = (
        service.list_candidates(session, resume_import=resume_import)
        if resume_import.status
        in (ImportStatus.READY_FOR_REVIEW, ImportStatus.FINALIZED, ImportStatus.FAILED)
        else None
    )
    return _import_response(resume_import, candidates=candidates)


@router.post(
    "/me/resume-imports/{import_id}/finalize",
    operation_id="finalizeResumeImport",
    response_model=schemas.FinalizeResumeImportResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_IMPORT_NOT_FOUND_RESPONSES,
        **_error(HTTPStatus.CONFLICT, "O perfil mudou ou a importação não está pronta."),
        **_VALIDATION_RESPONSES,
    },
)
def finalize_resume_import(
    import_id: UUID,
    body: schemas.FinalizeResumeImportRequest,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> schemas.FinalizeResumeImportResponse:
    identity_deps.enforce_rate_limit(limiter, deps.RESUME_IMPORT_FINALIZE_RATE_LIMIT, str(user.id))
    try:
        result = service.finalize_resume_import(
            session, user=user, import_id=import_id, command=body
        )
    except ResumeImportNotFoundError as error:
        raise ApiError(
            ErrorCode.RESUME_IMPORT_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND
        ) from error
    except ResumeImportNotReadyError as error:
        raise ApiError(
            ErrorCode.RESUME_IMPORT_NOT_READY, status_code=HTTPStatus.CONFLICT
        ) from error
    except (CandidateDecisionCoverageError, CandidateValueInvalidError) as error:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, status_code=HTTPStatus.UNPROCESSABLE_ENTITY
        ) from error
    except ProfileVersionConflictError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_CONFLICT, status_code=HTTPStatus.CONFLICT
        ) from error

    if not result.is_replay:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.RESUME_IMPORT_FINALIZED,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "import_id": str(import_id),
                "resume_id": str(result.resume_version.resume_id),
                "resume_version": result.resume_version.version,
                "profile_version_after": result.resume_version.profile_version,
                "accepted_fact_count": len(
                    schemas.MasterResumeSnapshot.model_validate(
                        result.resume_version.content_json
                    ).fact_ids
                ),
            },
        )
        session.commit()
    snapshot = schemas.MasterResumeSnapshot.model_validate(result.resume_version.content_json)
    return schemas.FinalizeResumeImportResponse(
        resume_id=result.resume_version.resume_id,
        resume_version_id=result.resume_version.id,
        version=result.resume_version.version,
        profile_version=result.resume_version.profile_version,
        headline=snapshot.headline,
        summary=snapshot.summary,
        fact_ids=snapshot.fact_ids,
        created_at=result.resume_version.created_at,
    )


@router.get(
    "/me/resumes/master",
    operation_id="getMasterResume",
    response_model=schemas.MasterResumeResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_error(HTTPStatus.NOT_FOUND, "Nenhum currículo mestre."),
    },
)
def get_master_resume(
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.MasterResumeResponse:
    found = service.get_master_resume(session, user=user)
    if found is None:
        raise ApiError(ErrorCode.RESOURCE_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND)
    resume, resume_version = found
    snapshot = schemas.MasterResumeSnapshot.model_validate(resume_version.content_json)
    return schemas.MasterResumeResponse(
        resume_id=resume.id,
        resume_version_id=resume_version.id,
        version=resume_version.version,
        profile_version=resume_version.profile_version,
        headline=snapshot.headline,
        summary=snapshot.summary,
        fact_ids=snapshot.fact_ids,
        generated_by=resume_version.generated_by,
        created_at=resume_version.created_at,
    )

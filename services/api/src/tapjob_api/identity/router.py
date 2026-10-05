"""`/api/v1/auth` and `/api/v1/me` routes (docs/adr/0009-identity-auth-user-account.md). Mounted
into `tapjob_api.http.api_v1`'s router (see `tapjob_api/main.py`).
"""

from http import HTTPStatus
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tapjob_api.core.logging import request_id_var
from tapjob_api.core.rate_limit import RedisFixedWindowRateLimiter
from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import (
    COMMON_OPERATIONAL_ERROR_RESPONSES,
    ApiError,
    ErrorCode,
    ErrorResponse,
)
from tapjob_api.identity import dependencies as deps
from tapjob_api.identity import schemas, service
from tapjob_api.identity.firebase import (
    FirebaseTokenError,
    IdentityTokenVerifier,
    VerifiedIdentity,
)
from tapjob_api.identity.models import AuditEventType, AuditOutcome, DevicePlatform, User

router = APIRouter(tags=["identity"], responses=COMMON_OPERATIONAL_ERROR_RESPONSES)


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_ACCOUNT_RESPONSES = {**_AUTH_RESPONSES, **_error(HTTPStatus.FORBIDDEN, "Conta desativada.")}
_BOOTSTRAPPED_RESPONSES = {
    **_ACCOUNT_RESPONSES,
    **_error(HTTPStatus.NOT_FOUND, "Conta ainda não inicializada."),
}
_RATE_LIMITED_RESPONSES = _error(HTTPStatus.TOO_MANY_REQUESTS, "Muitas requisições.")
_REAUTH_RESPONSES = _error(
    HTTPStatus.UNAUTHORIZED, "Autenticação recente é necessária para esta ação."
)


def _user_response(user: User) -> schemas.UserResponse:
    return schemas.UserResponse(
        id=user.id,
        status=user.status,
        locale=user.locale,
        timezone=user.timezone,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _device_response(device: Any) -> schemas.DeviceResponse:
    return schemas.DeviceResponse(
        id=device.id,
        installation_id=device.installation_id,
        platform=device.platform,
        app_version=device.app_version,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        revoked_at=device.revoked_at,
    )


def _consent_response(record: Any) -> schemas.ConsentRecordResponse:
    return schemas.ConsentRecordResponse(
        id=record.id,
        purpose_key=record.purpose_key,
        policy_version=record.policy_version,
        decision=record.decision,
        recorded_at=record.recorded_at,
    )


def _deletion_response(request_row: Any) -> schemas.DeletionRequestResponse:
    return schemas.DeletionRequestResponse(
        id=request_row.id,
        status=request_row.status,
        requested_at=request_row.requested_at,
        cancelled_at=request_row.cancelled_at,
    )


def _audit(
    session: Session,
    *,
    user: User,
    event_type: AuditEventType,
    outcome: AuditOutcome,
    metadata: dict[str, object] | None = None,
) -> None:
    service.record_audit_event(
        session,
        user=user,
        event_type=event_type.value,
        outcome=outcome,
        metadata=metadata,
        correlation_id=request_id_var.get(),
    )


@router.post(
    "/auth/bootstrap",
    operation_id="bootstrapAccount",
    response_model=schemas.BootstrapResponse,
    responses=_AUTH_RESPONSES | _RATE_LIMITED_RESPONSES,
)
def bootstrap_account(
    identity: VerifiedIdentity = Depends(deps.get_verified_identity),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> schemas.BootstrapResponse:
    """Resolves the caller's existing TapJob `User` for this verified Firebase identity, or creates
    one (plus its `ExternalIdentity` link) if this is the first time this identity has ever
    authenticated. Idempotent and concurrency-safe (`service.bootstrap_user`); never auto-links to
    a different `User` based on email. The `ACCOUNT_CREATED` audit row (when `created`) commits in
    the SAME transaction as the new `User`/`ExternalIdentity` rows -- never a separate, later
    commit that could leave a `User` durably created with no corresponding audit row (2026
    independent-review "account-created audit transaction")."""
    deps.enforce_rate_limit(limiter, deps.BOOTSTRAP_RATE_LIMIT, identity.subject)
    user, created = service.bootstrap_user(session, identity=identity)
    if created:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.ACCOUNT_CREATED,
            outcome=AuditOutcome.SUCCESS,
        )
    session.commit()
    return schemas.BootstrapResponse(user=_user_response(user), created=created)


@router.get(
    "/me",
    operation_id="getCurrentUser",
    response_model=schemas.UserResponse,
    responses=_BOOTSTRAPPED_RESPONSES,
)
def get_current_user(user: User = Depends(deps.get_current_user)) -> schemas.UserResponse:
    return _user_response(user)


@router.patch(
    "/me",
    operation_id="updateCurrentUser",
    response_model=schemas.UserResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Dados inválidos."),
    },
)
def update_current_user(
    body: schemas.UpdateUserSettingsRequest,
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.UserResponse:
    service.update_user_settings(session, user=user, locale=body.locale, timezone=body.timezone)
    _audit(
        session,
        user=user,
        event_type=AuditEventType.ACCOUNT_SETTINGS_UPDATED,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "locale_changed": body.locale is not None,
            "timezone_changed": body.timezone is not None,
        },
    )
    session.commit()
    return _user_response(user)


@router.post(
    "/me/sessions/revoke-all",
    operation_id="revokeAllSessions",
    status_code=HTTPStatus.NO_CONTENT,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_REAUTH_RESPONSES,
        **_error(
            HTTPStatus.SERVICE_UNAVAILABLE, "O provedor de identidade não pôde ser contatado."
        ),
    },
)
def revoke_all_sessions(
    identity: VerifiedIdentity = Depends(deps.get_verified_identity),
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
    verifier: IdentityTokenVerifier = Depends(deps.get_identity_verifier),
) -> None:
    """Revokes every Firebase refresh token for this account's linked identity (global, not
    per-device -- docs/adr/0009-...md "per-device session revocation"). The audit row reflects the
    ACTUAL provider outcome: a success row is written only after Firebase confirms the revocation,
    and a failed provider call is recorded as a failure, never silently dropped
    (docs/adr/0009-...md "audit transaction semantics"). Because `IdentityTokenVerifier` always
    verifies with `check_revoked=True` (see `tapjob_api.identity.firebase`'s module docstring,
    corrected 2026 independent-review finding 5), this backend rejects an already-issued ID token
    on the very next request after this call, not merely after that token's own natural expiry --
    the only thing that can lag is the CLIENT noticing (its local Firebase SDK session keeps using
    its cached token until it next refreshes or itself observes revocation); this endpoint's own
    server-side enforcement is immediate."""
    deps.enforce_rate_limit(limiter, deps.SESSION_REVOKE_RATE_LIMIT, str(user.id))
    deps.require_recent_authentication(identity)

    subject = service.get_firebase_subject(session, user=user)
    if subject is None:
        # A (future) channel-only User with no Firebase identity yet has no provider session to
        # revoke; nothing to do, still audited as a success (there is genuinely nothing to fail).
        _audit(
            session,
            user=user,
            event_type=AuditEventType.SESSIONS_REVOKED,
            outcome=AuditOutcome.SUCCESS,
        )
        session.commit()
        return None

    try:
        verifier.revoke_refresh_tokens(subject)
    except FirebaseTokenError as error:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.SESSIONS_REVOKED,
            outcome=AuditOutcome.FAILURE,
            metadata={"reason": error.reason},
        )
        session.commit()
        raise ApiError(
            ErrorCode.SERVICE_UNAVAILABLE, status_code=HTTPStatus.SERVICE_UNAVAILABLE
        ) from error

    _audit(
        session, user=user, event_type=AuditEventType.SESSIONS_REVOKED, outcome=AuditOutcome.SUCCESS
    )
    session.commit()
    return None


@router.post(
    "/me/devices",
    operation_id="registerDevice",
    response_model=schemas.DeviceResponse,
    status_code=HTTPStatus.OK,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Dados inválidos."),
    },
)
def register_device(
    body: schemas.DeviceRegistrationRequest,
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> schemas.DeviceResponse:
    deps.enforce_rate_limit(limiter, deps.DEVICE_MUTATION_RATE_LIMIT, str(user.id))
    device, created = service.register_device(
        session,
        user=user,
        installation_id=body.installation_id,
        platform=DevicePlatform(body.platform),
        app_version=body.app_version,
        push_token=body.push_token,
    )
    if created:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.DEVICE_REGISTERED,
            outcome=AuditOutcome.SUCCESS,
            metadata={"platform": device.platform.value},
        )
    session.commit()
    return _device_response(device)


@router.delete(
    "/me/devices/{device_id}",
    operation_id="revokeDevice",
    status_code=HTTPStatus.NO_CONTENT,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_error(HTTPStatus.NOT_FOUND, "Dispositivo não encontrado."),
        **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Identificador de dispositivo inválido."),
    },
)
def revoke_device(
    device_id: UUID,
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> None:
    """Ownership-scoped (docs/adr/0009-...md "ownership / IDOR testing"): a device belonging to a
    different user is indistinguishable from a nonexistent one -- both resolve to
    `DEVICE_NOT_FOUND`."""
    deps.enforce_rate_limit(limiter, deps.DEVICE_MUTATION_RATE_LIMIT, str(user.id))
    device = service.find_owned_device(session, user=user, device_id=device_id)
    if device is None:
        raise ApiError(ErrorCode.DEVICE_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND)
    service.revoke_device(session, device=device)
    _audit(
        session, user=user, event_type=AuditEventType.DEVICE_REVOKED, outcome=AuditOutcome.SUCCESS
    )
    session.commit()
    return None


@router.get(
    "/me/consents",
    operation_id="listConsents",
    response_model=list[schemas.ConsentRecordResponse],
    responses=_BOOTSTRAPPED_RESPONSES,
)
def list_consents(
    user: User = Depends(deps.get_current_user), session: Session = Depends(get_db_session)
) -> list[schemas.ConsentRecordResponse]:
    return [_consent_response(record) for record in service.list_consents(session, user=user)]


@router.post(
    "/me/consents",
    operation_id="recordConsent",
    response_model=schemas.ConsentRecordResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_error(HTTPStatus.UNPROCESSABLE_ENTITY, "Dados inválidos."),
    },
)
def record_consent(
    body: schemas.ConsentRecordRequest,
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> schemas.ConsentRecordResponse:
    deps.enforce_rate_limit(limiter, deps.CONSENT_MUTATION_RATE_LIMIT, str(user.id))
    record = service.record_consent(
        session,
        user=user,
        purpose_key=body.purpose_key,
        policy_version=body.policy_version,
        decision=body.decision,
        source="mobile",
    )
    _audit(
        session,
        user=user,
        event_type=AuditEventType.CONSENT_RECORDED,
        outcome=AuditOutcome.SUCCESS,
        metadata={"purpose_key": body.purpose_key, "decision": body.decision.value},
    )
    session.commit()
    return _consent_response(record)


@router.post(
    "/me/deletion-request",
    operation_id="requestAccountDeletion",
    response_model=schemas.DeletionRequestResponse,
    status_code=HTTPStatus.ACCEPTED,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_REAUTH_RESPONSES,
    },
)
def request_account_deletion(
    identity: VerifiedIdentity = Depends(deps.get_verified_identity),
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> schemas.DeletionRequestResponse:
    """202 Accepted, not 200/204: this only SCHEDULES/records a request -- it never claims data was
    deleted (docs/adr/0009-...md "deletion endpoint"). Idempotent: a second call while one is
    already active returns the existing request rather than creating a duplicate."""
    deps.enforce_rate_limit(limiter, deps.DELETION_REQUEST_RATE_LIMIT, str(user.id))
    deps.require_recent_authentication(identity)
    request_row, created = service.request_deletion(session, user=user)
    if created:
        _audit(
            session,
            user=user,
            event_type=AuditEventType.DELETION_REQUESTED,
            outcome=AuditOutcome.SUCCESS,
        )
    session.commit()
    return _deletion_response(request_row)


@router.get(
    "/me/deletion-request",
    operation_id="getAccountDeletionRequest",
    response_model=schemas.DeletionRequestResponse | None,
    responses=_BOOTSTRAPPED_RESPONSES,
)
def get_account_deletion_request(
    user: User = Depends(deps.get_current_user), session: Session = Depends(get_db_session)
) -> schemas.DeletionRequestResponse | None:
    request_row = service.get_latest_deletion_request(session, user=user)
    return _deletion_response(request_row) if request_row is not None else None


@router.delete(
    "/me/deletion-request",
    operation_id="cancelAccountDeletion",
    response_model=schemas.DeletionRequestResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_REAUTH_RESPONSES,
        **_error(
            HTTPStatus.CONFLICT,
            "Não há solicitação de exclusão cancelável (inexistente ou já em processamento).",
        ),
    },
)
def cancel_account_deletion(
    identity: VerifiedIdentity = Depends(deps.get_verified_identity),
    user: User = Depends(deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(deps.get_rate_limiter),
) -> schemas.DeletionRequestResponse:
    """Security-sensitive in exactly the same way as requesting deletion -- recent authentication
    and rate limiting apply here too (docs/adr/0009-...md "sensitive action / recent auth",
    2026 independent-review finding 3). Only a REQUESTED request is cancellable: once
    PROCESSING/COMPLETED/FAILED, this is a 409, never a silent no-op or a false "cancelled"."""
    deps.enforce_rate_limit(limiter, deps.DELETION_REQUEST_RATE_LIMIT, str(user.id))
    deps.require_recent_authentication(identity)
    request_row = service.cancel_deletion_request(session, user=user)
    if request_row is None:
        raise ApiError(ErrorCode.CONFLICT, status_code=HTTPStatus.CONFLICT)
    _audit(
        session,
        user=user,
        event_type=AuditEventType.DELETION_CANCELLED,
        outcome=AuditOutcome.SUCCESS,
    )
    session.commit()
    return _deletion_response(request_row)

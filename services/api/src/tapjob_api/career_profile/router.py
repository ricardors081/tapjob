"""`/api/v1/me/career-profile`, `/api/v1/me/profile-facts` and `/api/v1/me/truth-snapshot` routes
(docs/adr/0010-career-profile-truth-store.md). Mounted into `tapjob_api.http.api_v1`'s router (see
`tapjob_api/main.py`). This is ONE adapter over `tapjob_api.career_profile.service` -- a future
`MessagingChannel`/WhatsApp adapter calls the exact same service functions
(docs/adr/0010-...md "channel-first design"), never a second copy of these use cases.
"""

from http import HTTPStatus
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from tapjob_api.career_profile import dependencies as deps
from tapjob_api.career_profile import schemas, service
from tapjob_api.career_profile.fact_values import FactValue
from tapjob_api.career_profile.models import CareerProfile, FactType, ProfileFact
from tapjob_api.core.logging import request_id_var
from tapjob_api.core.rate_limit import RedisFixedWindowRateLimiter
from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import ApiError, ErrorCode, ErrorResponse
from tapjob_api.identity import dependencies as identity_deps
from tapjob_api.identity import service as identity_service
from tapjob_api.identity.models import AuditEventType, AuditOutcome, User

router = APIRouter(tags=["career-profile"])


def _error(status_code: HTTPStatus, description: str) -> dict[int | str, dict[str, Any]]:
    return {status_code: {"model": ErrorResponse, "description": description}}


_AUTH_RESPONSES = _error(HTTPStatus.UNAUTHORIZED, "Token ausente, inválido, expirado ou revogado.")
_ACCOUNT_RESPONSES = {**_AUTH_RESPONSES, **_error(HTTPStatus.FORBIDDEN, "Conta desativada.")}
_BOOTSTRAPPED_RESPONSES = {
    **_ACCOUNT_RESPONSES,
    **_error(HTTPStatus.NOT_FOUND, "Conta ainda não inicializada."),
}
_RATE_LIMITED_RESPONSES = _error(HTTPStatus.TOO_MANY_REQUESTS, "Muitas requisições.")
_VERSION_CONFLICT_RESPONSES = _error(
    HTTPStatus.CONFLICT, "O perfil foi alterado por outra requisição."
)
_FACT_NOT_FOUND_RESPONSES = _error(HTTPStatus.NOT_FOUND, "Fato de perfil não encontrado.")
_VALIDATION_RESPONSES = _error(HTTPStatus.UNPROCESSABLE_ENTITY, "Dados inválidos.")

# Explicitly parses a fact's stored `value_json` dict into a real, typed `FactValue` instance
# (round-2 independent review "BLOCKER 2") before it ever reaches `ProfileFactResponse` -- a
# corrupted/incompatible DB row fails HERE (an unhandled 500), never silently passed through as a
# raw dict the way `dict[str, object]` used to allow.
_FACT_VALUE_ADAPTER: TypeAdapter[FactValue] = TypeAdapter(FactValue)


def _profile_response(profile: CareerProfile | None) -> schemas.CareerProfileResponse:
    if profile is None:
        # Virtual empty profile -- a User with no row yet is NOT a 404
        # (docs/adr/0010-...md "profile creation").
        return schemas.CareerProfileResponse(target_roles=[], profile_version=0)
    return schemas.CareerProfileResponse(
        headline=profile.headline,
        summary=profile.summary,
        target_roles=list(profile.target_roles),
        profile_version=profile.profile_version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _fact_response(fact: ProfileFact) -> schemas.ProfileFactResponse:
    return schemas.ProfileFactResponse(
        id=fact.id,
        fact_type=fact.fact_type,
        value=_FACT_VALUE_ADAPTER.validate_python(fact.value_json),
        source_kind=fact.source_kind.value,
        source_ref=fact.source_ref,
        evidence_ref=fact.evidence_ref,
        confidence=fact.confidence,
        user_confirmed=fact.user_confirmed,
        confirmed_at=fact.confirmed_at,
        status=fact.status,
        lineage_id=fact.lineage_id,
        supersedes_fact_id=fact.supersedes_fact_id,
        active_from_profile_version=fact.active_from_profile_version,
        inactive_from_profile_version=fact.inactive_from_profile_version,
        created_at=fact.created_at,
    )


def _audit(
    session: Session,
    *,
    user: User,
    event_type: AuditEventType,
    outcome: AuditOutcome,
    metadata: dict[str, object] | None = None,
) -> None:
    # Reuses WP1.1's append-only audit mechanism wholesale (docs/adr/0010-...md "audit") --
    # `metadata` here is field/fact-type/fact-id labels only, NEVER a fact's `value_json` content,
    # a summary body, or a work-authorization answer (docs/SECURITY_PRIVACY.md §10).
    identity_service.record_audit_event(
        session,
        user=user,
        event_type=event_type.value,
        outcome=outcome,
        metadata=metadata,
        correlation_id=request_id_var.get(),
    )


@router.get(
    "/me/career-profile",
    operation_id="getCareerProfile",
    response_model=schemas.CareerProfileResponse,
    responses=_BOOTSTRAPPED_RESPONSES,
)
def get_career_profile(
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.CareerProfileResponse:
    return _profile_response(service.get_career_profile(session, user=user))


@router.patch(
    "/me/career-profile",
    operation_id="updateCareerProfile",
    response_model=schemas.CareerProfileResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_VERSION_CONFLICT_RESPONSES,
        **_VALIDATION_RESPONSES,
    },
)
def update_career_profile(
    body: schemas.UpdateCareerProfileRequest,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> schemas.CareerProfileResponse:
    """`UpdateCareerProfileRequest` IS a `CareerProfilePatchCommand` (round-3 independent review
    "BLOCKER 1") -- passed straight through to `service.update_career_profile`, which owns every
    invariant itself (trim/non-blank/control-character/bounds/dedupe, absent-vs-explicit-null via
    `model_fields_set`, the virtual-profile/same-value no-op decision). This router call is just
    the HTTP entry point, never a second copy of that validation."""
    identity_deps.enforce_rate_limit(limiter, deps.PROFILE_MUTATION_RATE_LIMIT, str(user.id))
    try:
        result = service.update_career_profile(
            session, user=user, expected_version=body.expected_profile_version, command=body
        )
    except service.ProfileVersionConflictError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_CONFLICT, status_code=HTTPStatus.CONFLICT
        ) from error
    if result.fields_changed and result.profile is not None:
        # A genuine no-op (round-2 "ADDITIONAL REVIEW"/round-3 "BLOCKER 2" -- including the virtual-
        # profile case, where `result.profile` is `None`) never gets a success mutation audit --
        # nothing actually changed, despite a valid expected_version.
        _audit(
            session,
            user=user,
            event_type=AuditEventType.PROFILE_UPDATED,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "fields_changed": sorted(result.fields_changed),
                "profile_version_before": body.expected_profile_version,
                "profile_version_after": result.profile.profile_version,
            },
        )
    session.commit()
    return _profile_response(result.profile)


@router.get(
    "/me/profile-facts",
    operation_id="listProfileFacts",
    response_model=list[schemas.ProfileFactResponse],
    responses={**_BOOTSTRAPPED_RESPONSES, **_VALIDATION_RESPONSES},
)
def list_profile_facts(
    fact_type: FactType | None = Query(default=None, alias="factType"),
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> list[schemas.ProfileFactResponse]:
    """Active (current-truth) facts only -- superseded/retracted history is reachable one fact at a
    time via `GET /me/profile-facts/{factId}`, not this listing (docs/adr/0010-...md
    "retraction")."""
    facts = service.list_active_facts(session, user=user, fact_type=fact_type)
    return [_fact_response(fact) for fact in facts]


@router.post(
    "/me/profile-facts",
    operation_id="addProfileFact",
    response_model=schemas.ProfileFactResponse,
    status_code=HTTPStatus.CREATED,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_VERSION_CONFLICT_RESPONSES,
        **_VALIDATION_RESPONSES,
    },
)
def add_profile_fact(
    body: schemas.AddProfileFactRequest,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> schemas.ProfileFactResponse:
    identity_deps.enforce_rate_limit(limiter, deps.PROFILE_FACT_MUTATION_RATE_LIMIT, str(user.id))
    try:
        fact = service.add_profile_fact(
            session,
            user=user,
            expected_version=body.expected_profile_version,
            fact_type=body.fact_type,
            # The typed `FactValue` itself (round-2 independent review "BLOCKER 1") -- the service
            # validates value.kind against fact_type and serializes it, never trusting this router
            # to have already done so.
            value=body.value,
        )
    except service.ProfileVersionConflictError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_CONFLICT, status_code=HTTPStatus.CONFLICT
        ) from error
    except service.ProfileFactValueMismatchError as error:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, status_code=HTTPStatus.UNPROCESSABLE_ENTITY
        ) from error
    _audit(
        session,
        user=user,
        event_type=AuditEventType.PROFILE_FACT_ADDED,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "fact_type": fact.fact_type.value,
            "fact_id": str(fact.id),
            "source_kind": fact.source_kind.value,
            "profile_version_before": body.expected_profile_version,
            "profile_version_after": fact.active_from_profile_version,
        },
    )
    session.commit()
    return _fact_response(fact)


@router.get(
    "/me/profile-facts/{fact_id}",
    operation_id="getProfileFact",
    response_model=schemas.ProfileFactResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_FACT_NOT_FOUND_RESPONSES,
        **_VALIDATION_RESPONSES,
    },
)
def get_profile_fact(
    fact_id: UUID,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.ProfileFactResponse:
    """Ownership-scoped (docs/adr/0010-...md "ownership / IDOR testing"): a fact belonging to a
    different user is indistinguishable from a nonexistent one -- both resolve to
    `PROFILE_FACT_NOT_FOUND`. Deliberately reachable regardless of `status` -- this is how a
    superseded/retracted fact's history stays readable."""
    fact = service.find_owned_fact(session, user=user, fact_id=fact_id)
    if fact is None:
        raise ApiError(ErrorCode.PROFILE_FACT_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND)
    return _fact_response(fact)


@router.post(
    "/me/profile-facts/{fact_id}/replace",
    operation_id="replaceProfileFact",
    response_model=schemas.ProfileFactResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_VERSION_CONFLICT_RESPONSES,
        **_FACT_NOT_FOUND_RESPONSES,
        **_error(
            HTTPStatus.CONFLICT,
            "Este fato de perfil não está mais ativo (já substituído ou retratado).",
        ),
        **_VALIDATION_RESPONSES,
    },
)
def replace_profile_fact(
    fact_id: UUID,
    body: schemas.ReplaceProfileFactRequest,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> schemas.ProfileFactResponse:
    """The original fact's `value_json` is NEVER edited in place -- this creates a NEW fact in the
    same lineage and marks the old one `SUPERSEDED`, permanently readable via its own id
    (docs/adr/0010-...md "fact immutability"). The ownership-scoped lookup and the `value.kind`/
    `factType` cross-check both happen INSIDE `service.replace_profile_fact` now (round-2
    independent review "BLOCKER 1") -- this router no longer resolves the fact itself first."""
    identity_deps.enforce_rate_limit(limiter, deps.PROFILE_FACT_MUTATION_RATE_LIMIT, str(user.id))
    try:
        replacement = service.replace_profile_fact(
            session,
            user=user,
            fact_id=fact_id,
            expected_version=body.expected_profile_version,
            value=body.value,
        )
    except service.ProfileFactNotFoundError as error:
        raise ApiError(
            ErrorCode.PROFILE_FACT_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND
        ) from error
    except service.ProfileFactValueMismatchError as error:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, status_code=HTTPStatus.UNPROCESSABLE_ENTITY
        ) from error
    except service.ProfileVersionConflictError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_CONFLICT, status_code=HTTPStatus.CONFLICT
        ) from error
    except service.ProfileFactNotActiveError as error:
        raise ApiError(
            ErrorCode.PROFILE_FACT_ALREADY_RETRACTED, status_code=HTTPStatus.CONFLICT
        ) from error
    _audit(
        session,
        user=user,
        event_type=AuditEventType.PROFILE_FACT_REPLACED,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "fact_type": replacement.fact_type.value,
            "fact_id": str(replacement.id),
            "supersedes_fact_id": str(replacement.supersedes_fact_id),
            "source_kind": replacement.source_kind.value,
            "profile_version_before": body.expected_profile_version,
            "profile_version_after": replacement.active_from_profile_version,
        },
    )
    session.commit()
    return _fact_response(replacement)


@router.post(
    "/me/profile-facts/{fact_id}/retract",
    operation_id="retractProfileFact",
    response_model=schemas.ProfileFactResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_RATE_LIMITED_RESPONSES,
        **_VERSION_CONFLICT_RESPONSES,
        **_FACT_NOT_FOUND_RESPONSES,
        **_error(
            HTTPStatus.CONFLICT,
            "Este fato de perfil não está mais ativo (já substituído ou retratado).",
        ),
        **_VALIDATION_RESPONSES,
    },
)
def retract_profile_fact(
    fact_id: UUID,
    body: schemas.RetractProfileFactRequest,
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
    limiter: RedisFixedWindowRateLimiter = Depends(identity_deps.get_rate_limiter),
) -> schemas.ProfileFactResponse:
    """Logical retraction -- the row is never hard-deleted (docs/adr/0010-...md "retraction"). The
    ownership-scoped lookup happens INSIDE `service.retract_profile_fact` now (round-2 independent
    review "BLOCKER 1")."""
    identity_deps.enforce_rate_limit(limiter, deps.PROFILE_FACT_MUTATION_RATE_LIMIT, str(user.id))
    try:
        retracted = service.retract_profile_fact(
            session,
            user=user,
            fact_id=fact_id,
            expected_version=body.expected_profile_version,
        )
    except service.ProfileFactNotFoundError as error:
        raise ApiError(
            ErrorCode.PROFILE_FACT_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND
        ) from error
    except service.ProfileVersionConflictError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_CONFLICT, status_code=HTTPStatus.CONFLICT
        ) from error
    except service.ProfileFactNotActiveError as error:
        raise ApiError(
            ErrorCode.PROFILE_FACT_ALREADY_RETRACTED, status_code=HTTPStatus.CONFLICT
        ) from error
    _audit(
        session,
        user=user,
        event_type=AuditEventType.PROFILE_FACT_RETRACTED,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "fact_type": retracted.fact_type.value,
            "fact_id": str(retracted.id),
            "source_kind": retracted.source_kind.value,
            "profile_version_before": body.expected_profile_version,
            "profile_version_after": retracted.inactive_from_profile_version,
        },
    )
    session.commit()
    return _fact_response(retracted)


@router.get(
    "/me/truth-snapshot",
    operation_id="getTruthSnapshot",
    response_model=schemas.TruthSnapshotResponse,
    responses={
        **_BOOTSTRAPPED_RESPONSES,
        **_error(HTTPStatus.NOT_FOUND, "A versão de perfil solicitada ainda não existe."),
        **_VALIDATION_RESPONSES,
    },
)
def get_truth_snapshot(
    profile_version: int | None = Query(default=None, alias="profileVersion", ge=0),
    user: User = Depends(identity_deps.get_current_user),
    session: Session = Depends(get_db_session),
) -> schemas.TruthSnapshotResponse:
    """The ONLY shape any future consumer may use to ground generated external material
    (docs/AI_AGENT.md §6, AGENTS.md §6) -- confirmed, active facts only, never an unconfirmed
    candidate or another user's fact (docs/adr/0010-...md "truth snapshot filter"). A
    `profileVersion` greater than the profile's real current version is `404
    PROFILE_VERSION_NOT_FOUND` -- never a fabricated snapshot for a version that was never actually
    reached (round-1 independent review "future version error")."""
    try:
        snapshot = service.get_truth_snapshot(session, user=user, profile_version=profile_version)
    except service.ProfileVersionNotFoundError as error:
        raise ApiError(
            ErrorCode.PROFILE_VERSION_NOT_FOUND, status_code=HTTPStatus.NOT_FOUND
        ) from error
    return schemas.TruthSnapshotResponse(
        user_id=snapshot.user_id,
        profile_version=snapshot.profile_version,
        headline=snapshot.headline,
        summary=snapshot.summary,
        target_roles=snapshot.target_roles,
        facts=[_fact_response(fact) for fact in snapshot.facts],
    )

"""Inbound messaging conversation orchestration (Findings 1, 2, 3, 4, 8, 9).

Coordinates conversational flows for candidate interactions:
- Unknown identity -> User creation (Case A) -> link identity -> durable outbox greeting.
- First contact with document/URL/job -> User creation + executes content flow.
- Document attachment -> secure retrieval -> canonical Resume Import (WP1.3).
- Fact confirmation routing -> canonical Truth Store (ACCEPT, EDIT_ACCEPT, REJECT).
- Job URL / paste -> canonical intake (WP2.1) -> canonical WP2.2 matching.
- Match analysis & explanation -> user-scoped target job resolution in PostgreSQL.
- Application kit & tailoring -> grounded tailoring -> ApplicationKit (PREPARING).
- Application submission -> "já me candidatei" transitions to APPLIED.
- Follow-up reminders -> strictly requires real Application, zero fabricated UUIDs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import redis
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.models import Job
from tapjob_api.messaging.applications import Application, ApplicationStatus
from tapjob_api.messaging.career_agent_tools import (
    tool_build_application_kit,
    tool_explain_match,
    tool_get_profile_missing_information,
    tool_ingest_job_text,
    tool_ingest_job_url,
    tool_ingest_resume,
    tool_match_job,
    tool_recommend_jobs,
    tool_schedule_follow_up,
    tool_tailor_resume,
    tool_update_application_status,
)
from tapjob_api.messaging.document_intake import confirm_candidates_and_finalize
from tapjob_api.messaging.inbound import InboundEffectKind, InboundEffectState
from tapjob_api.messaging.inbound_service import (
    complete_inbound_effect,
    get_inbound_effect,
    is_transient_inbound_error,
    reserve_inbound_effect,
)
from tapjob_api.messaging.service import (
    get_user_profile_status,
    link_or_create_identity,
    queue_response_message,
    resolve_link_by_channel_identity,
    resolve_user_by_channel_identity,
)
from tapjob_api.ports._common import CallContext, PortError
from tapjob_api.ports.messaging_channel import InboundMessage, MessagingChannel
from tapjob_api.resume_import import service as resume_import_service
from tapjob_api.resume_import.commands import CandidateReviewDecision, ReviewDecisionKind
from tapjob_api.resume_import.errors import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    ImportLeaseActiveError,
    MalformedDocumentError,
    NoReviewableCandidatesError,
    ResumeImportError,
    TextNotExtractableError,
    TransientProcessingError,
    UnsupportedDocumentTypeError,
)
from tapjob_api.resume_import.models import (
    CandidateReviewDraft,
    CandidateReviewState,
    ImportFailureCode,
    ImportStatus,
    ResumeImport,
    ResumeVersion,
)
from tapjob_api.resume_import.service import _FAILURE_CODE_BY_ERROR
from tapjob_api.resume_import.storage import ObjectStorageError, ObjectStoragePort
from tapjob_product_config import get_product_config

logger = logging.getLogger(__name__)

# Test failure injection hooks for hard-crash recovery validation (Findings 1, 2)
_crash_hook_after_resume_import_commit: Callable[[], None] | None = None
_crash_hook_after_finalize_commit: Callable[[], None] | None = None


def format_resume_failure_response(failure_code: str | ImportFailureCode | None) -> str:
    """Produces normalized user-facing failure response for resume import failures (Finding 1).

    Ensures zero success wording on failure.
    """
    code_str = (
        failure_code.value
        if isinstance(failure_code, ImportFailureCode)
        else str(failure_code or "")
    )
    if code_str == ImportFailureCode.UNSUPPORTED_DOCUMENT_TYPE.value:
        return (
            "Recebemos seu documento, mas o tipo de arquivo não é suportado. "
            "Por favor, envie um currículo em formato PDF ou DOCX."
        )
    if code_str == ImportFailureCode.DOCUMENT_TOO_LARGE.value:
        return (
            "Recebemos seu documento, mas o arquivo é muito grande. "
            "Por favor, envie um arquivo menor."
        )
    if code_str == ImportFailureCode.MALFORMED_DOCUMENT.value:
        return (
            "Recebemos seu documento, mas o arquivo está corrompido ou mal formatado. "
            "Por favor, verifique o arquivo e envie novamente."
        )
    if code_str == ImportFailureCode.ENCRYPTED_DOCUMENT.value:
        return (
            "Recebemos seu documento, mas ele está protegido por senha. "
            "Por favor, envie um arquivo desprotegido."
        )
    if code_str == ImportFailureCode.TEXT_NOT_EXTRACTABLE.value:
        return (
            "Recebemos seu documento, mas não foi possível extrair o texto. "
            "Por favor, envie um currículo em formato de texto legível."
        )
    if code_str == ImportFailureCode.NO_REVIEWABLE_CANDIDATES.value:
        return (
            "Recebemos seu currículo, mas não identificamos competências ou experiências "
            "para inclusão no seu perfil profissional."
        )
    return (
        "Recebemos seu documento, mas não foi possível processá-lo no momento. "
        "Por favor, certifique-se de que o arquivo seja um PDF válido."
    )


def reconcile_reserved_resume_import(
    session: Session,
    *,
    user: User,
    envelope_id: int,
    target_import_uuid: uuid.UUID,
    storage: ObjectStoragePort,
    doc_filename: str,
) -> tuple[bool, str]:
    """Reconciles a PENDING RESUME_IMPORT effect against canonical domain state (Finding 1).

    Policy:
    - Inexistent import (None): Return (False, "") to let caller run intake with same UUID.
    - Ownership mismatch: Log warning and return (False, "").
    - PROCESSING with active lease: Raise ImportLeaseActiveError(retry_at) to wait for owner.
    - PROCESSING with expired lease: Reclaim via canonical process_resume_import.
    - QUEUED: Process via canonical process_resume_import, then refresh.
    - READY_FOR_REVIEW / FINALIZED: Canonical success. Complete effect and return success reply.
    - FAILED: Canonical failure. Complete effect with outcome=failed and return failure reply.
    """
    existing_import = session.get(ResumeImport, target_import_uuid)
    if existing_import is None:
        return False, ""

    if existing_import.user_id != user.id:
        logger.warning(
            "reconcile_reserved_resume_import.ownership_mismatch",
            extra={"target_id": str(target_import_uuid), "user_id": str(user.id)},
        )
        return False, ""

    now = datetime.now(UTC)

    if existing_import.status == ImportStatus.PROCESSING:
        active_lease_expiry = existing_import.processing_lease_expires_at
        if active_lease_expiry is not None and active_lease_expiry > now:
            # Active lease held by another owner - do not complete effect or treat as success
            raise ImportLeaseActiveError(retry_at=active_lease_expiry)
        # Lease expired - reclaim via canonical pipeline
        resume_import_service.process_resume_import(
            session, import_id=existing_import.id, storage=storage
        )
        session.refresh(existing_import)

    elif existing_import.status == ImportStatus.QUEUED:
        resume_import_service.process_resume_import(
            session, import_id=existing_import.id, storage=storage
        )
        session.refresh(existing_import)

    if existing_import.status in (ImportStatus.READY_FOR_REVIEW, ImportStatus.FINALIZED):
        facts_count = len(existing_import.candidates)
        complete_inbound_effect(
            session,
            envelope_id=envelope_id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
            expected_resource_id=str(existing_import.id),
            effect_payload={
                "outcome": "success",
                "facts_count": facts_count,
                "filename": doc_filename,
            },
        )
        session.commit()
        response_text = (
            f"Recebemos seu currículo ({doc_filename}) com sucesso!\n"
            f"Identificamos {facts_count} competências e experiências preliminares.\n"
            "Para confirmar e salvar essas informações no seu perfil, envie:\n"
            "• 'confirmar todos': aceitar todas as sugestões\n"
            "• 'rejeitar fato <número>': descartar um item específico\n"
            "• 'editar fato <número>: <novo valor>': corrigir um item"
        )
        return True, response_text

    if existing_import.status == ImportStatus.FAILED:
        failure_code = existing_import.failure_code
        failure_code_str = (
            failure_code.value
            if isinstance(failure_code, ImportFailureCode)
            else str(failure_code or "")
        )
        complete_inbound_effect(
            session,
            envelope_id=envelope_id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
            expected_resource_id=str(existing_import.id),
            effect_payload={
                "outcome": "failed",
                "failure_code": failure_code_str,
                "filename": doc_filename,
            },
        )
        session.commit()
        response_text = format_resume_failure_response(failure_code)
        return True, response_text

    raise TransientProcessingError(
        f"Unexpected non-terminal resume import status: {existing_import.status}"
    )


def is_pasted_job_text(text: str) -> bool:
    """Detects if inbound text represents a pasted job description."""
    lower = text.lower()
    return (
        lower.startswith("vaga:")
        or lower.startswith("oportunidade:")
        or (
            len(text) > 80
            and any(
                term in lower
                for term in (
                    "requisitos",
                    "responsabilidades",
                    "benefícios",
                    "senior",
                    "desenvolvedor",
                    "engenheiro",
                )
            )
        )
    )


def resolve_user_target_job(session: Session, *, user: User) -> Job | None:
    """Deterministically resolves latest target job scoped to user context (Finding 2).

    Queries PostgreSQL:
    1. Active Job explicitly created/ingested by this user:
       Job.created_by_user_id == user.id
    2. Active/Target Job associated through an Application for this user:
       Application.user_id == user.id

    If both exist, returns the most recently observed one.
    NEVER queries or leaks global jobs from other users.
    """
    stmt_user_jobs = (
        select(Job)
        .where(Job.created_by_user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job_by_intake = session.execute(stmt_user_jobs).scalar_one_or_none()

    stmt_app_jobs = (
        select(Job)
        .join(Application, Application.job_id == Job.id)
        .where(Application.user_id == user.id)
        .order_by(Application.created_at.desc())
        .limit(1)
    )
    job_by_app = session.execute(stmt_app_jobs).scalar_one_or_none()

    if job_by_intake is not None and job_by_app is not None:
        return job_by_intake if job_by_intake.created_at >= job_by_app.created_at else job_by_app
    return job_by_intake or job_by_app


class IncompleteCandidateReviewError(Exception):
    """Raised when review finalization is attempted without complete, explicit draft coverage."""


class InvalidCandidateReviewDraftError(Exception):
    """Raised when a candidate review draft is corrupted or missing required payload."""


def assemble_candidate_review_decisions(
    ordered_candidates: list[Any],
    drafts_by_cand_id: dict[uuid.UUID, CandidateReviewDraft],
) -> list[CandidateReviewDecision]:
    """Assembles and strictly validates CandidateReviewDecision list from explicit drafts.

    Enforces:
    - NO ACCEPTANCE BY OMISSION.
    - Exact coverage: candidate_ids == draft_ids (no missing drafts, no alien drafts).
    - Exactly one decision per candidate.
    - decision in (ACCEPT, REJECT, EDIT_ACCEPT).
    - EDIT_ACCEPT must have non-empty mapping edited_value.
    - ACCEPT and REJECT must not carry edited_value.
    """
    candidate_ids = {cf.id for cf in ordered_candidates}
    draft_ids = set(drafts_by_cand_id.keys())

    if candidate_ids != draft_ids:
        missing = candidate_ids - draft_ids
        excess = draft_ids - candidate_ids
        raise IncompleteCandidateReviewError(
            f"Candidate review coverage mismatch: missing={len(missing)}, excess={len(excess)}"
        )

    decisions: list[CandidateReviewDecision] = []
    for cf in ordered_candidates:
        draft = drafts_by_cand_id[cf.id]
        if draft.decision == ReviewDecisionKind.ACCEPT.value:
            if draft.edited_value is not None:
                raise InvalidCandidateReviewDraftError(
                    f"Candidate {cf.id} marked ACCEPT must not carry edited_value"
                )
            decisions.append(
                CandidateReviewDecision(candidate_id=cf.id, decision=ReviewDecisionKind.ACCEPT)
            )
        elif draft.decision == ReviewDecisionKind.REJECT.value:
            if draft.edited_value is not None:
                raise InvalidCandidateReviewDraftError(
                    f"Candidate {cf.id} marked REJECT must not carry edited_value"
                )
            decisions.append(
                CandidateReviewDecision(candidate_id=cf.id, decision=ReviewDecisionKind.REJECT)
            )
        elif draft.decision == ReviewDecisionKind.EDIT_ACCEPT.value:
            if not draft.edited_value or not isinstance(draft.edited_value, dict):
                raise InvalidCandidateReviewDraftError(
                    f"Candidate {cf.id} marked EDIT_ACCEPT but missing valid edited_value mapping"
                )
            decisions.append(
                CandidateReviewDecision(
                    candidate_id=cf.id,
                    decision=ReviewDecisionKind.EDIT_ACCEPT,
                    edited_value=draft.edited_value,
                )
            )
        else:
            raise InvalidCandidateReviewDraftError(
                f"Candidate {cf.id} has invalid decision kind {draft.decision}"
            )

    return decisions


def dispatch_inbound_message(
    session: Session,
    redis_client: redis.Redis | None,
    *,
    inbound: InboundMessage,
    provider_id: uuid.UUID,
    channel_adapter: MessagingChannel | None = None,
    storage: ObjectStoragePort | None = None,
    envelope_id: int | None = None,
) -> list[str]:
    """Dispatches a normalized inbound message to conversational workflows.

    Returns:
        List of queued outbound message idempotency keys.
    """
    external_id = inbound.sender.external_id
    text = (inbound.text or "").strip()
    lower_text = text.lower()

    # 1. Resolve candidate user
    user = resolve_user_by_channel_identity(
        session, provider_id=provider_id, external_id=external_id
    )

    queued_keys: list[str] = []
    link_id: uuid.UUID | None = None

    if user is None:
        # First contact workflow (Case A: new user created and linked)
        config = get_product_config()
        new_user = User(
            locale=config.default_locale,
            timezone="Etc/UTC",
            status=UserStatus.ACTIVE,
        )
        session.add(new_user)
        session.flush()

        link = link_or_create_identity(
            session,
            user_id=new_user.id,
            provider_id=provider_id,
            external_id=external_id,
            is_new_user=True,
        )

        user = new_user
        link_id = link.id

        has_document = bool(
            inbound.attachments and any(a.kind == "document" for a in inbound.attachments)
        )
        has_job_url = "http://" in text or "https://" in text
        has_pasted_job = is_pasted_job_text(text) and not any(
            cmd in lower_text for cmd in ("analisa", "candidatar", "lembra", "status", "confirmar")
        )

        if not (has_document or has_job_url or has_pasted_job):
            greeting_text = (
                f"Olá! Bem-vindo ao {config.product_name}. "
                "Sou seu assistente de carreira pessoal.\n"
                "Para começar, envie seu currículo em PDF ou compartilhe "
                "suas principais experiências e competências."
            )
            msg = queue_response_message(
                session,
                channel_identity_link_id=link.id,
                recipient_external_id=external_id,
                message_text=greeting_text,
                idempotency_key=f"first_contact_{inbound.provider_message_id}",
            )
            queued_keys.append(msg.idempotency_key)
            return queued_keys

        # First contact has content: send greeting first and continue same inbound (Finding 9)
        greeting_text = (
            f"Olá! Bem-vindo ao {config.product_name}. Sou seu assistente de carreira pessoal."
        )
        msg = queue_response_message(
            session,
            channel_identity_link_id=link.id,
            recipient_external_id=external_id,
            message_text=greeting_text,
            idempotency_key=f"first_contact_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
    else:
        existing_link = resolve_link_by_channel_identity(
            session, provider_id=provider_id, external_id=external_id
        )
        link_id = existing_link.id if existing_link is not None else None

    # 2. Inbound Document / Resume Attachment workflow (Finding 1)
    if inbound.attachments:
        doc_att = next((att for att in inbound.attachments if att.kind == "document"), None)
        if doc_att:
            # Runtime fail-fast checks: ObjectStoragePort and retrieve_media are mandatory
            if storage is None:
                raise RuntimeError("ObjectStoragePort is required for document ingestion runtime")
            if channel_adapter is None or not hasattr(channel_adapter, "retrieve_media"):
                raise RuntimeError(
                    "MessagingChannel capable of retrieve_media is required for document ingestion"
                )

            target_import_uuid: uuid.UUID | None = None
            if envelope_id is not None:
                # Check if domain effect was already committed on an earlier attempt (Finding 1)
                existing_import_effect = get_inbound_effect(
                    session,
                    envelope_id=envelope_id,
                    effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                )
                if (
                    existing_import_effect is not None
                    and existing_import_effect.operation_state == InboundEffectState.COMPLETED.value
                ):
                    outcome = existing_import_effect.effect_payload.get("outcome", "success")
                    if outcome == "failed":
                        failure_code = existing_import_effect.effect_payload.get("failure_code")
                        response_text = format_resume_failure_response(failure_code)
                    else:
                        facts_count = existing_import_effect.effect_payload.get("facts_count", 0)
                        filename = (
                            existing_import_effect.effect_payload.get("filename")
                            or doc_att.filename
                            or "documento"
                        )
                        response_text = (
                            f"Recebemos seu currículo ({filename}) com sucesso!\n"
                            f"Identificamos {facts_count} competências e experiências "
                            "preliminares.\n"
                            "Para confirmar e salvar essas informações no seu perfil, envie:\n"
                            "• 'confirmar todos': aceitar todas as sugestões\n"
                            "• 'rejeitar fato <número>': descartar um item específico\n"
                            "• 'editar fato <número>: <novo valor>': corrigir um item"
                        )
                    msg = queue_response_message(
                        session,
                        channel_identity_link_id=link_id,
                        recipient_external_id=external_id,
                        message_text=response_text,
                        idempotency_key=f"doc_{inbound.provider_message_id}",
                    )
                    queued_keys.append(msg.idempotency_key)
                    return queued_keys

                if (
                    existing_import_effect is not None
                    and existing_import_effect.operation_state == InboundEffectState.PENDING.value
                ):
                    target_import_uuid = uuid.UUID(existing_import_effect.resource_id)
                    doc_filename = doc_att.filename or "curriculo.pdf"
                    is_reconciled, reconciled_response = reconcile_reserved_resume_import(
                        session,
                        user=user,
                        envelope_id=envelope_id,
                        target_import_uuid=target_import_uuid,
                        storage=storage,
                        doc_filename=doc_filename,
                    )
                    if is_reconciled:
                        msg = queue_response_message(
                            session,
                            channel_identity_link_id=link_id,
                            recipient_external_id=external_id,
                            message_text=reconciled_response,
                            idempotency_key=f"doc_{inbound.provider_message_id}",
                        )
                        queued_keys.append(msg.idempotency_key)
                        return queued_keys
                else:
                    # Durably reserve intent with a preallocated UUID BEFORE domain operations run
                    target_import_uuid = uuid.uuid4()
                    reserve_inbound_effect(
                        session,
                        envelope_id=envelope_id,
                        inbound_message_id=inbound.provider_message_id,
                        effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                        resource_id=str(target_import_uuid),
                    )
                    session.commit()

            ctx = CallContext.with_timeout(
                correlation_id=inbound.provider_message_id, timeout_seconds=15.0
            )
            media_bytes: bytes | None = None
            try:
                media_bytes = asyncio.run(
                    channel_adapter.retrieve_media(
                        doc_att,
                        ctx,
                        message_id=inbound.provider_message_id,
                    )
                )
            except PortError as exc:
                if exc.transient:
                    logger.warning(
                        "orchestration.retrieve_media_transient_error",
                        extra={
                            "error": str(exc),
                            "code": exc.code.value,
                            "media_id": doc_att.provider_media_id,
                        },
                    )
                    raise
                logger.warning(
                    "orchestration.retrieve_media_terminal_error",
                    extra={
                        "error": str(exc),
                        "code": exc.code.value,
                        "media_id": doc_att.provider_media_id,
                    },
                )
                response_text = (
                    "Recebemos seu documento, mas o formato ou arquivo é inválido. "
                    "Por favor, envie um currículo válido em formato PDF."
                )
                msg = queue_response_message(
                    session,
                    channel_identity_link_id=link_id,
                    recipient_external_id=external_id,
                    message_text=response_text,
                    idempotency_key=f"doc_{inbound.provider_message_id}",
                )
                queued_keys.append(msg.idempotency_key)
                return queued_keys
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning(
                    "orchestration.retrieve_media_network_transient",
                    extra={"error": str(exc), "media_id": doc_att.provider_media_id},
                )
                raise

            if not media_bytes:
                raise RuntimeError("Provider returned empty media bytes for document attachment")

            import_res: dict[str, Any] | None = None
            try:
                import_res = tool_ingest_resume(
                    session,
                    user=user,
                    content=media_bytes,
                    filename=doc_att.filename or "curriculo.pdf",
                    content_type=doc_att.mime_type,
                    storage=storage,
                    import_id=target_import_uuid,
                )
            except SoftTimeLimitExceeded:
                raise
            except (
                ObjectStorageError,
                TransientProcessingError,
                ImportLeaseActiveError,
                httpx.TimeoutException,
                httpx.ConnectError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                logger.warning(
                    "orchestration.ingest_resume_transient_error",
                    extra={"error": str(exc), "doc_filename": doc_att.filename},
                )
                raise
            except (
                UnsupportedDocumentTypeError,
                DocumentTooLargeError,
                MalformedDocumentError,
                EncryptedDocumentError,
                TextNotExtractableError,
                NoReviewableCandidatesError,
            ) as exc:
                logger.info(
                    "orchestration.ingest_resume_terminal_document_error",
                    extra={"error": str(exc), "doc_filename": doc_att.filename},
                )
                failure_code = _FAILURE_CODE_BY_ERROR.get(
                    type(exc), ImportFailureCode.INTERNAL_ERROR
                )
                response_text = format_resume_failure_response(failure_code)
                if envelope_id is not None and target_import_uuid is not None:
                    complete_inbound_effect(
                        session,
                        envelope_id=envelope_id,
                        effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                        expected_resource_id=str(target_import_uuid),
                        effect_payload={
                            "outcome": "failed",
                            "failure_code": (
                                failure_code.value
                                if hasattr(failure_code, "value")
                                else str(failure_code)
                            ),
                            "filename": doc_att.filename or "curriculo.pdf",
                        },
                    )
                    session.commit()
            except ResumeImportError as exc:
                logger.info(
                    "orchestration.ingest_resume_terminal_domain_error",
                    extra={"error": str(exc), "doc_filename": doc_att.filename},
                )
                failure_code = _FAILURE_CODE_BY_ERROR.get(
                    type(exc), ImportFailureCode.INTERNAL_ERROR
                )
                response_text = format_resume_failure_response(failure_code)
                if envelope_id is not None and target_import_uuid is not None:
                    complete_inbound_effect(
                        session,
                        envelope_id=envelope_id,
                        effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                        expected_resource_id=str(target_import_uuid),
                        effect_payload={
                            "outcome": "failed",
                            "failure_code": (
                                failure_code.value
                                if hasattr(failure_code, "value")
                                else str(failure_code)
                            ),
                            "filename": doc_att.filename or "curriculo.pdf",
                        },
                    )
                    session.commit()
            except Exception as exc:
                if is_transient_inbound_error(exc):
                    logger.warning(
                        "orchestration.ingest_resume_transient_generic",
                        extra={"error": str(exc), "doc_filename": doc_att.filename},
                    )
                    raise
                logger.exception("orchestration.ingest_resume_failed", extra={"error": str(exc)})
                response_text = (
                    "Recebemos seu documento, mas não foi possível processá-lo no momento. "
                    "Por favor, certifique-se de que o arquivo seja um PDF válido."
                )

            if import_res is not None:
                if _crash_hook_after_resume_import_commit is not None:
                    _crash_hook_after_resume_import_commit()

                import_status = import_res.get("status")
                if import_status == ImportStatus.READY_FOR_REVIEW.value:
                    facts_count = import_res.get("candidate_count") or len(
                        import_res.get("candidate_facts", [])
                    )
                    if envelope_id is not None:
                        complete_inbound_effect(
                            session,
                            envelope_id=envelope_id,
                            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                            expected_resource_id=str(import_res["resume_import_id"]),
                            effect_payload={
                                "outcome": "success",
                                "facts_count": facts_count,
                                "filename": doc_att.filename or "curriculo.pdf",
                            },
                        )
                        session.commit()
                    doc_filename = doc_att.filename or "documento"
                    response_text = (
                        f"Recebemos seu currículo ({doc_filename}) com sucesso!\n"
                        f"Identificamos {facts_count} competências e experiências "
                        "preliminares.\n"
                        "Para confirmar e salvar essas informações no seu perfil, envie:\n"
                        "• 'confirmar todos': aceitar todas as sugestões\n"
                        "• 'rejeitar fato <número>': descartar um item específico\n"
                        "• 'editar fato <número>: <novo valor>': corrigir um item"
                    )
                elif import_status == ImportStatus.FAILED.value:
                    failure_code_str = import_res.get("failure_code")
                    if envelope_id is not None:
                        complete_inbound_effect(
                            session,
                            envelope_id=envelope_id,
                            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
                            expected_resource_id=str(import_res["resume_import_id"]),
                            effect_payload={
                                "outcome": "failed",
                                "failure_code": str(failure_code_str or ""),
                                "filename": doc_att.filename or "curriculo.pdf",
                            },
                        )
                        session.commit()
                    response_text = format_resume_failure_response(failure_code_str)
                else:
                    imp_rec = session.get(ResumeImport, uuid.UUID(import_res["resume_import_id"]))
                    if imp_rec and imp_rec.processing_lease_expires_at:
                        now = datetime.now(UTC)
                        if imp_rec.processing_lease_expires_at > now:
                            raise ImportLeaseActiveError(
                                retry_at=imp_rec.processing_lease_expires_at
                            )
                    raise TransientProcessingError(
                        "Resume import remains in non-terminal state after intake"
                    )

            msg = queue_response_message(
                session,
                channel_identity_link_id=link_id,
                recipient_external_id=external_id,
                message_text=response_text,
                idempotency_key=f"doc_{inbound.provider_message_id}",
            )
            queued_keys.append(msg.idempotency_key)
            return queued_keys

    # 3. Fact confirmation / edit / reject routing (Finding 8)
    is_confirm_all = any(
        k in lower_text
        for k in (
            "confirmar fatos",
            "confirmar todos",
            "aceitar todos",
            "confirmar fato",
            "confirmar",
        )
    ) and not any(
        k in lower_text for k in ("rejeitar", "ignorar", "descartar", "editar", "corrigir")
    )

    is_reject_all = any(
        k in lower_text
        for k in ("rejeitar todos", "ignorar todos", "descartar todos", "rejeitar fatos")
    )

    is_single_reject = any(
        k in lower_text for k in ("rejeitar fato", "ignorar fato", "descartar fato")
    )
    is_single_accept = any(k in lower_text for k in ("aceitar fato",))
    is_single_edit = any(k in lower_text for k in ("editar fato", "corrigir fato"))

    if is_confirm_all or is_reject_all or is_single_reject or is_single_accept or is_single_edit:
        existing_finalize_effect = None
        if envelope_id is not None:
            # Check if finalization was already completed or reserved (Finding 1)
            existing_finalize_effect = get_inbound_effect(
                session,
                envelope_id=envelope_id,
                effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
            )
            if (
                existing_finalize_effect is not None
                and existing_finalize_effect.operation_state == InboundEffectState.COMPLETED.value
            ):
                v_num = existing_finalize_effect.effect_payload.get("profile_version", 1)
                all_rejected = existing_finalize_effect.effect_payload.get("all_rejected", False)
                if all_rejected:
                    response_text = (
                        "Fatos do currículo rejeitados com sucesso.\n"
                        "Nenhuma informação foi adicionada ao seu perfil profissional.\n"
                        "Você pode nos enviar um novo documento quando desejar!"
                    )
                else:
                    response_text = (
                        f"Informações confirmadas com sucesso!\n"
                        f"Seu perfil profissional foi atualizado (Versão {v_num}).\n"
                        "Agora você pode nos enviar vagas (link ou texto) para analisarmos!"
                    )
                msg = queue_response_message(
                    session,
                    channel_identity_link_id=link_id,
                    recipient_external_id=external_id,
                    message_text=response_text,
                    idempotency_key=f"fact_conf_{inbound.provider_message_id}",
                )
                queued_keys.append(msg.idempotency_key)
                return queued_keys

        latest_import: ResumeImport | None = None
        if (
            existing_finalize_effect is not None
            and existing_finalize_effect.operation_state == InboundEffectState.PENDING.value
        ):
            target_import_id = uuid.UUID(existing_finalize_effect.resource_id)
            target_import = session.execute(
                select(ResumeImport).where(
                    ResumeImport.id == target_import_id,
                    ResumeImport.user_id == user.id,
                )
            ).scalar_one_or_none()
            if target_import is None:
                logger.warning(
                    "orchestration.finalize_pending_target_not_found",
                    extra={"target_id": str(target_import_id), "user_id": str(user.id)},
                )
                response_text = (
                    "Não encontramos um currículo pendente para confirmação. "
                    "Por favor, envie seu currículo novamente."
                )
                msg = queue_response_message(
                    session,
                    channel_identity_link_id=link_id,
                    recipient_external_id=external_id,
                    message_text=response_text,
                    idempotency_key=f"fact_conf_{inbound.provider_message_id}",
                )
                queued_keys.append(msg.idempotency_key)
                return queued_keys

            if target_import.status == ImportStatus.FINALIZED:
                resume_version = session.get(
                    ResumeVersion, target_import.finalized_resume_version_id
                )
                v_num = resume_version.profile_version if resume_version else 1
                all_rejected = all(
                    c.review_state == CandidateReviewState.REJECTED
                    for c in target_import.candidates
                )
                if envelope_id is not None:
                    complete_inbound_effect(
                        session,
                        envelope_id=envelope_id,
                        effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
                        expected_resource_id=str(target_import.id),
                        effect_payload={
                            "profile_version": v_num,
                            "all_rejected": all_rejected,
                        },
                    )
                session.commit()
                if all_rejected:
                    response_text = (
                        "Fatos do currículo rejeitados com sucesso.\n"
                        "Nenhuma informação foi adicionada ao seu perfil profissional.\n"
                        "Você pode nos enviar um novo documento quando desejar!"
                    )
                else:
                    response_text = (
                        f"Informações confirmadas com sucesso!\n"
                        f"Seu perfil profissional foi atualizado (Versão {v_num}).\n"
                        "Agora você pode nos enviar vagas (link ou texto) para analisarmos!"
                    )
                msg = queue_response_message(
                    session,
                    channel_identity_link_id=link_id,
                    recipient_external_id=external_id,
                    message_text=response_text,
                    idempotency_key=f"fact_conf_{inbound.provider_message_id}",
                )
                queued_keys.append(msg.idempotency_key)
                return queued_keys

            if target_import.status != ImportStatus.READY_FOR_REVIEW:
                logger.warning(
                    "orchestration.finalize_pending_target_invalid_status",
                    extra={"target_id": str(target_import_id), "status": target_import.status},
                )
                response_text = (
                    "O currículo selecionado não está mais disponível para confirmação. "
                    "Por favor, envie um novo documento."
                )
                msg = queue_response_message(
                    session,
                    channel_identity_link_id=link_id,
                    recipient_external_id=external_id,
                    message_text=response_text,
                    idempotency_key=f"fact_conf_{inbound.provider_message_id}",
                )
                queued_keys.append(msg.idempotency_key)
                return queued_keys

            latest_import = target_import
        else:
            latest_import = session.execute(
                select(ResumeImport)
                .where(
                    ResumeImport.user_id == user.id,
                    ResumeImport.status == ImportStatus.READY_FOR_REVIEW,
                )
                .order_by(ResumeImport.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        if latest_import and latest_import.candidates:
            ordered_candidates = sorted(
                list(latest_import.candidates), key=lambda c: (c.created_at, str(c.id))
            )
            # Load existing review drafts for this import
            existing_drafts = list(
                session.scalars(
                    select(CandidateReviewDraft).where(
                        CandidateReviewDraft.resume_import_id == latest_import.id
                    )
                ).all()
            )
            drafts_by_cand_id = {d.candidate_id: d for d in existing_drafts}

            should_finalize = False
            response_text = ""

            if is_confirm_all:
                for cf in ordered_candidates:
                    draft = drafts_by_cand_id.get(cf.id)
                    if draft is None:
                        draft = CandidateReviewDraft(
                            resume_import_id=latest_import.id,
                            candidate_id=cf.id,
                            user_id=user.id,
                            decision=ReviewDecisionKind.ACCEPT.value,
                        )
                        session.add(draft)
                        drafts_by_cand_id[cf.id] = draft
                    elif draft.decision == ReviewDecisionKind.REJECT.value:
                        draft.decision = ReviewDecisionKind.ACCEPT.value
                session.flush()
                should_finalize = True

            elif is_reject_all:
                for cf in ordered_candidates:
                    draft = drafts_by_cand_id.get(cf.id)
                    if draft is None:
                        draft = CandidateReviewDraft(
                            resume_import_id=latest_import.id,
                            candidate_id=cf.id,
                            user_id=user.id,
                            decision=ReviewDecisionKind.REJECT.value,
                        )
                        session.add(draft)
                        drafts_by_cand_id[cf.id] = draft
                    else:
                        draft.decision = ReviewDecisionKind.REJECT.value
                        draft.edited_value = None
                session.flush()
                should_finalize = True

            elif is_single_reject or is_single_accept:
                match_num = re.search(r"(?:fato\s+|fatos\s+)?(\d+)", lower_text)
                target_idx = int(match_num.group(1)) if match_num else None
                if target_idx is not None and 1 <= target_idx <= len(ordered_candidates):
                    target_cand = ordered_candidates[target_idx - 1]
                    dec_val = (
                        ReviewDecisionKind.REJECT.value
                        if is_single_reject
                        else ReviewDecisionKind.ACCEPT.value
                    )
                    draft = drafts_by_cand_id.get(target_cand.id)
                    if draft is None:
                        draft = CandidateReviewDraft(
                            resume_import_id=latest_import.id,
                            candidate_id=target_cand.id,
                            user_id=user.id,
                            decision=dec_val,
                        )
                        session.add(draft)
                        drafts_by_cand_id[target_cand.id] = draft
                    else:
                        draft.decision = dec_val
                        draft.edited_value = None
                    session.flush()

                    pending_cands = [
                        (i + 1)
                        for i, c in enumerate(ordered_candidates)
                        if c.id not in drafts_by_cand_id
                    ]
                    if not pending_cands:
                        should_finalize = True
                    else:
                        verb = "rejeitado" if is_single_reject else "aceito"
                        pending_str = ", ".join(f"#{idx}" for idx in pending_cands)
                        response_text = (
                            f"Fato {target_idx} {verb} com sucesso.\n"
                            f"Restam {len(pending_cands)} fato(s) pendente(s): {pending_str}.\n"
                            "Para continuar, envie:\n"
                            "• 'aceitar fato <número>'\n"
                            "• 'rejeitar fato <número>'\n"
                            "• 'editar fato <número>: <novo valor>'\n"
                            "• 'confirmar todos' para aceitar todos os fatos restantes."
                        )
                else:
                    total_cands = len(ordered_candidates)
                    response_text = (
                        f"Índice de fato inválido. O currículo possui {total_cands} "
                        f"fatos (1 a {total_cands})."
                    )

            elif is_single_edit:
                # Syntax: editar fato <N>: <novo_valor>
                match_edit = re.search(r"(?:fato\s+)?(\d+)\s*[:=]\s*(.+)$", text, re.IGNORECASE)
                if match_edit:
                    target_idx = int(match_edit.group(1))
                    new_val_str = match_edit.group(2).strip()
                    if 1 <= target_idx <= len(ordered_candidates):
                        target_cand = ordered_candidates[target_idx - 1]
                        if target_cand.candidate_kind == "skill":
                            edited_dict: dict[str, Any] = {"name": new_val_str}
                        elif target_cand.candidate_kind in ("headline", "summary"):
                            edited_dict = {"text": new_val_str}
                        elif target_cand.candidate_kind == "experience":
                            edited_dict = dict(target_cand.value_json)
                            edited_dict["title"] = new_val_str
                        else:
                            edited_dict = {"text": new_val_str}

                        draft = drafts_by_cand_id.get(target_cand.id)
                        if draft is None:
                            draft = CandidateReviewDraft(
                                resume_import_id=latest_import.id,
                                candidate_id=target_cand.id,
                                user_id=user.id,
                                decision=ReviewDecisionKind.EDIT_ACCEPT.value,
                                edited_value=edited_dict,
                            )
                            session.add(draft)
                            drafts_by_cand_id[target_cand.id] = draft
                        else:
                            draft.decision = ReviewDecisionKind.EDIT_ACCEPT.value
                            draft.edited_value = edited_dict
                        session.flush()

                        pending_cands = [
                            (i + 1)
                            for i, c in enumerate(ordered_candidates)
                            if c.id not in drafts_by_cand_id
                        ]
                        if not pending_cands:
                            should_finalize = True
                        else:
                            pending_str = ", ".join(f"#{idx}" for idx in pending_cands)
                            response_text = (
                                f"Fato {target_idx} atualizado para '{new_val_str}'.\n"
                                f"Restam {len(pending_cands)} fato(s) pendente(s): {pending_str}.\n"
                                "Para continuar, envie:\n"
                                "• 'aceitar fato <número>'\n"
                                "• 'rejeitar fato <número>'\n"
                                "• 'editar fato <número>: <novo valor>'\n"
                                "• 'confirmar todos' para aceitar todos os fatos restantes."
                            )
                    else:
                        response_text = (
                            f"Índice de fato inválido. Escolha entre 1 e {len(ordered_candidates)}."
                        )
                else:
                    response_text = (
                        "Formato de edição inválido. Exemplo: 'editar fato 1: Python Senior'."
                    )

            if should_finalize:
                try:
                    if envelope_id is not None and existing_finalize_effect is None:
                        # Durably reserve finalization intent BEFORE canonical finalize (Finding 1)
                        reserve_inbound_effect(
                            session,
                            envelope_id=envelope_id,
                            inbound_message_id=inbound.provider_message_id,
                            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
                            resource_id=str(latest_import.id),
                        )
                        session.commit()

                    decisions = assemble_candidate_review_decisions(
                        ordered_candidates, drafts_by_cand_id
                    )
                    current_truth = get_truth_snapshot(session, user=user)
                    finalize_res = confirm_candidates_and_finalize(
                        session,
                        user=user,
                        resume_import_id=latest_import.id,
                        decisions=decisions,
                        expected_profile_version=current_truth.profile_version,
                    )
                    session.commit()
                    if _crash_hook_after_finalize_commit is not None:
                        _crash_hook_after_finalize_commit()

                    v_num = finalize_res.resume_version.profile_version
                    all_rejected = all(d.decision == ReviewDecisionKind.REJECT for d in decisions)
                    if envelope_id is not None:
                        # Durably mark review finalization effect as COMPLETED (Finding 1)
                        complete_inbound_effect(
                            session,
                            envelope_id=envelope_id,
                            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
                            expected_resource_id=str(latest_import.id),
                            effect_payload={
                                "profile_version": v_num,
                                "all_rejected": all_rejected,
                            },
                        )
                        session.commit()
                    if is_reject_all or all_rejected:
                        response_text = (
                            "Fatos do currículo rejeitados com sucesso.\n"
                            "Nenhuma informação foi adicionada ao seu perfil profissional.\n"
                            "Você pode nos enviar um novo documento quando desejar!"
                        )
                    else:
                        response_text = (
                            f"Informações confirmadas com sucesso!\n"
                            f"Seu perfil profissional foi atualizado (Versão {v_num}).\n"
                            "Agora você pode nos enviar vagas (link ou texto) para analisarmos!"
                        )
                except SoftTimeLimitExceeded:
                    raise
                except (IncompleteCandidateReviewError, InvalidCandidateReviewDraftError) as err:
                    logger.warning(
                        "orchestration.review_finalize_refused", extra={"error": str(err)}
                    )
                    pending_cands = [
                        (i + 1)
                        for i, c in enumerate(ordered_candidates)
                        if c.id not in drafts_by_cand_id
                    ]
                    pending_str = ", ".join(f"#{idx}" for idx in pending_cands)
                    response_text = (
                        "Ainda restam fatos pendentes de decisão explícita no seu currículo.\n"
                        f"Fatos pendentes: {pending_str}.\n"
                        "Envie 'aceitar fato <N>', 'rejeitar fato <N>', ou 'confirmar todos'."
                    )
        else:
            response_text = (
                "Não encontramos fatos pendentes de confirmação no momento.\n"
                "Envie seu currículo em PDF para adicionar novas informações."
            )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"fact_conf_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 4. Job URL intake
    if "http://" in text or "https://" in text:
        words = text.split()
        job_url = next((w for w in words if w.startswith(("http://", "https://"))), None)
        if job_url:
            try:
                job_info = tool_ingest_job_url(session, user=user, url=job_url)
                job_id = uuid.UUID(job_info["job_id"])
                match_info = tool_match_job(session, redis_client, user=user, job_id=job_id)

                overall = match_info["overall_score"]
                eligibility = match_info["eligibility"]
                response_text = (
                    f"Vaga analisada: {job_info['title']} ({job_info['company']}).\n"
                    f"Match Score WP2.2: {overall}/100 ({eligibility}).\n\n"
                    "Deseja gerar o Application Kit completo para esta vaga? "
                    "Envie: 'quero me candidatar'."
                )
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                if is_transient_inbound_error(exc):
                    raise
                logger.warning(
                    "orchestration.job_url_failed",
                    extra={"user_id": str(user.id), "error": str(exc)},
                )
                response_text = (
                    "Recebemos seu link de vaga, mas não foi possível processá-lo no momento. "
                    "Verifique se o link está acessível."
                )

            msg = queue_response_message(
                session,
                channel_identity_link_id=link_id,
                recipient_external_id=external_id,
                message_text=response_text,
                idempotency_key=f"job_url_{inbound.provider_message_id}",
            )
            queued_keys.append(msg.idempotency_key)
            return queued_keys

    # 5. Pasted Job Description intake
    if is_pasted_job_text(text) and not any(
        cmd in lower_text for cmd in ("analisa", "candidatar", "lembra", "status", "confirmar")
    ):
        clean_text = text.split(":", 1)[1].strip() if ":" in text[:15] else text
        first_line = clean_text.splitlines()[0][:60]
        title = first_line if len(first_line) > 5 else "Oportunidade Profissional"

        job_info = tool_ingest_job_text(
            session,
            user=user,
            text=clean_text,
            title_hint=title,
            company_hint="Empresa",
        )
        job_id = uuid.UUID(job_info["job_id"])
        match_info = tool_match_job(session, redis_client, user=user, job_id=job_id)

        response_text = (
            f"Descrição da vaga registrada com sucesso: {job_info['title']}.\n"
            f"Match Score WP2.2: {match_info['overall_score']}/100 "
            f"({match_info['eligibility']}).\n\n"
            "Deseja preparar seu currículo e kit para esta oportunidade? "
            "Envie: 'quero me candidatar'."
        )
        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"job_paste_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 6. Match analysis & explanation request ("analisa essa vaga") (Finding 2)
    if any(q in lower_text for q in ("analisa", "analisar", "vale a pena", "explicar")):
        latest_job = resolve_user_target_job(session, user=user)

        if latest_job:
            match_info = tool_match_job(session, redis_client, user=user, job_id=latest_job.id)
            expl = tool_explain_match(session, redis_client, user=user, job_id=latest_job.id)

            def _format_item(item: Any) -> str:
                if isinstance(item, dict):
                    return str(
                        item.get("name")
                        or item.get("title")
                        or item.get("summary")
                        or item.get("skill")
                        or item.get("text")
                        or next(iter(item.values()), str(item))
                    )
                return str(item)

            strengths_str = (
                ", ".join(_format_item(s) for s in expl.get("strengths", []))
                or "Nenhum ponto forte específico"
            )
            gaps_str = (
                ", ".join(_format_item(g) for g in expl.get("missing_requirements", []))
                or "Nenhum gap crítico"
            )

            response_text = (
                f"Análise detalhada para {latest_job.title}:\n"
                f"• Match Score WP2.2: {match_info['overall_score']}/100 "
                f"({match_info['eligibility']})\n"
                f"• Pontos fortes: {strengths_str}\n"
                f"• Requisitos pendentes: {gaps_str}\n\n"
                "Para preparar seu Application Kit grounded, envie: 'quero me candidatar'."
            )
        else:
            response_text = (
                "Nenhuma vaga encontrada para análise no momento. "
                "Envie o link ou texto da vaga para começarmos!"
            )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"match_expl_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 7. Application Kit & Tailoring preparation ("quero me candidatar") (Findings 2, 3)
    if any(
        q in lower_text
        for q in ("quero me candidatar", "candidatar", "gerar kit", "preparar candidatura")
    ):
        target_job = resolve_user_target_job(session, user=user)

        if target_job:
            try:
                tool_tailor_resume(session, user=user, job_id=target_job.id)
                kit_info = tool_build_application_kit(
                    session, redis_client, user=user, job_id=target_job.id
                )
                app_status_display = kit_info.get("status", ApplicationStatus.PREPARING.value)
                if app_status_display == ApplicationStatus.APPLIED.value:
                    status_desc = "Candidatura já enviada (Status: applied)"
                    cta_text = (
                        "Sua candidatura já está registrada como enviada. Se desejar, envie: "
                        "'me lembra de acompanhar' para agendar um follow-up."
                    )
                elif app_status_display == ApplicationStatus.INTERVIEWING.value:
                    status_desc = "Candidatura em processo seletivo (Status: interviewing)"
                    cta_text = (
                        "Esta candidatura já avançou para a fase de entrevistas! "
                        "Conte comigo para praticar respostas e se preparar."
                    )
                elif app_status_display in (
                    ApplicationStatus.REJECTED.value,
                    ApplicationStatus.WITHDRAWN.value,
                ):
                    status_desc = f"Candidatura encerrada (Status: {app_status_display})"
                    cta_text = (
                        "Esta candidatura foi encerrada. "
                        "Envie uma nova vaga para prepararmos um novo Application Kit!"
                    )
                else:
                    status_desc = f"Candidatura em preparação (Status: {app_status_display})"
                    cta_text = (
                        "Assim que submeter sua candidatura, nos avise enviando: "
                        "'já me candidatei'."
                    )

                response_text = (
                    f"Application Kit preparado com sucesso para {target_job.title}!\n"
                    f"• ID do Kit: {kit_info['kit_id']}\n"
                    f"• Currículo adaptado com grounding 100% verificado no seu perfil.\n"
                    f"• {status_desc}.\n\n"
                    f"{cta_text}"
                )
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                if is_transient_inbound_error(exc):
                    raise
                logger.exception("orchestration.kit_generation_failed", extra={"error": str(exc)})
                response_text = (
                    "Não foi possível gerar o Application Kit no momento. "
                    "Verifique se o seu perfil profissional possui fatos confirmados."
                )
        else:
            response_text = (
                "Envie primeiro o link ou a descrição da vaga antes de gerar o Application Kit."
            )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"kit_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 8. Application Status update ("já me candidatei", "candidatura enviada") (Finding 3)
    if any(
        q in lower_text
        for q in ("já me candidatei", "me candidatei", "candidatura enviada", "marcar aplicada")
    ):
        latest_app = session.execute(
            select(Application)
            .where(Application.user_id == user.id)
            .order_by(Application.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if latest_app:
            tool_update_application_status(
                session,
                user=user,
                application_id=latest_app.id,
                new_status=ApplicationStatus.APPLIED,
            )
            response_text = (
                "Parabéns! Atualizamos o status da sua candidatura para "
                "'APPLIED' (Candidatura Enviada).\n\n"
                "Deseja que eu lembre você de fazer um follow-up? Envie: 'me lembra de acompanhar'."
            )
        else:
            response_text = (
                "Você ainda não possui nenhuma candidatura em andamento. "
                "Envie uma vaga para prepararmos seu Application Kit!"
            )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"status_upd_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 9. Reminder scheduling ("me lembra", "lembrete", "follow-up") (Finding 4)
    if any(q in lower_text for q in ("me lembra", "lembrete", "follow-up", "acompanhar")):
        latest_app = session.execute(
            select(Application)
            .where(Application.user_id == user.id)
            .order_by(Application.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if latest_app is None:
            response_text = (
                "Você ainda não possui nenhuma candidatura registrada para acompanhar. "
                "Envie uma vaga e prepare seu Application Kit antes de agendar o follow-up!"
            )
            msg = queue_response_message(
                session,
                channel_identity_link_id=link_id,
                recipient_external_id=external_id,
                message_text=response_text,
                idempotency_key=f"rem_sched_{inbound.provider_message_id}",
            )
            queued_keys.append(msg.idempotency_key)
            return queued_keys

        remind_at = datetime.now(UTC) + timedelta(days=3)
        rem_info = tool_schedule_follow_up(
            session,
            user=user,
            remind_at=remind_at,
            application_id=latest_app.id,
            note="Follow-up de candidatura",
            channel_identity_link_id=link_id,
        )
        response_text = (
            f"Lembrete agendado com sucesso (ID: {rem_info['reminder_id'][:8]}...)!\n"
            "Nós avisaremos você por aqui no WhatsApp quando for o momento "
            "de acompanhar o processo."
        )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"rem_sched_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 10. Recommendations list
    if any(cmd in lower_text for cmd in ("vaga", "vagas", "oportunidade", "recomendar")):
        recs = tool_recommend_jobs(session, redis_client, user=user, limit=3)
        if recs:
            lines = ["Oportunidades recomendadas para o seu perfil:"]
            for r in recs:
                lines.append(f"• {r['title']} na {r['company']} — Match: {r['overall_score']}/100")
            lines.append("\nEnvie o link de uma vaga para analisar ou gerar seu Application Kit.")
            response_text = "\n".join(lines)
        else:
            response_text = (
                "Não encontramos vagas ativas com match no momento. "
                "Você pode nos enviar o link direto de uma oportunidade para analisarmos!"
            )

        msg = queue_response_message(
            session,
            channel_identity_link_id=link_id,
            recipient_external_id=external_id,
            message_text=response_text,
            idempotency_key=f"recs_{inbound.provider_message_id}",
        )
        queued_keys.append(msg.idempotency_key)
        return queued_keys

    # 11. Profile readiness & Default Fallback
    status = get_user_profile_status(session, user_id=user.id)
    if not status["has_facts"]:
        missing = tool_get_profile_missing_information(session, user=user)
        response_text = (
            "Seu perfil profissional ainda está incompleto. "
            f"Campos pendentes: {', '.join(missing['missing_fields'])}.\n"
            "Envie seu currículo em PDF para importarmos suas informações automaticamente."
        )
    else:
        v_num = status["profile_version"]
        f_cnt = status["fact_count"]
        response_text = (
            f"Seu perfil está ativo (Versão {v_num}, {f_cnt} competências confirmadas).\n"
            "Comandos disponíveis:\n"
            "- 'Vagas': ver oportunidades recomendadas\n"
            "- Envie um link ou texto de vaga para analisar match\n"
            "- 'Quero me candidatar': gerar Application Kit\n"
            "- 'Já me candidatei': atualizar status da candidatura\n"
            "- 'Me lembra de acompanhar': agendar lembrete"
        )

    msg = queue_response_message(
        session,
        channel_identity_link_id=link_id,
        recipient_external_id=external_id,
        message_text=response_text,
        idempotency_key=f"status_{inbound.provider_message_id}",
    )
    queued_keys.append(msg.idempotency_key)
    return queued_keys

"""Service layer for Applications and Application Kits persistence (Finding 10).

Guarantees:
- Real database persistence in PostgreSQL for all application operations.
- Zero fake random UUIDs.
- Canonical state machine: SAVED -> PREPARING -> APPLIED -> INTERVIEWING -> REJECTED / WITHDRAWN.
- Immutable ApplicationKit package referencing grounded facts, match results, and tailored draft.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User
from tapjob_api.messaging.applications import (
    Application,
    ApplicationKit,
    ApplicationStatus,
    ApplyMode,
)
from tapjob_api.messaging.resume_tailoring import TailoredResumeDraft

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    """Raised when application operations fail."""


class ApplicationNotFoundError(ApplicationError):
    """Raised when an application is not found for the given user."""


class InvalidApplicationStateTransitionError(ApplicationError):
    """Raised when an invalid application status transition is attempted."""

    def __init__(
        self,
        message: str,
        *,
        current_status: ApplicationStatus | None = None,
        target_status: ApplicationStatus | None = None,
    ) -> None:
        super().__init__(message)
        self.current_status = current_status
        self.target_status = target_status


VALID_APPLICATION_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.SAVED: {
        ApplicationStatus.SAVED,
        ApplicationStatus.PREPARING,
        ApplicationStatus.APPLIED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.PREPARING: {
        ApplicationStatus.PREPARING,
        ApplicationStatus.APPLIED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.APPLIED: {
        ApplicationStatus.APPLIED,
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.INTERVIEWING: {
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.REJECTED: {
        ApplicationStatus.REJECTED,
    },
    ApplicationStatus.WITHDRAWN: {
        ApplicationStatus.WITHDRAWN,
    },
}


def can_transition_application_status(
    current_status: ApplicationStatus,
    new_status: ApplicationStatus,
) -> bool:
    """Checks whether the requested application status transition is allowed."""
    allowed = VALID_APPLICATION_TRANSITIONS.get(current_status, set())
    return new_status in allowed


def create_or_update_application(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
    status: ApplicationStatus = ApplicationStatus.SAVED,
    apply_mode: ApplyMode = ApplyMode.MANUAL,
    provider: str | None = None,
    provider_application_id: str | None = None,
    submitted_at: datetime | None = None,
) -> Application:
    """Creates or updates a real Application row for the user and job."""
    app_row = session.execute(
        select(Application).where(
            Application.user_id == user.id,
            Application.job_id == job_id,
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if app_row is None:
        app_row = Application(
            user_id=user.id,
            job_id=job_id,
            status=status,
            apply_mode=apply_mode,
            provider=provider,
            provider_application_id=provider_application_id,
            submitted_at=submitted_at or (now if status == ApplicationStatus.APPLIED else None),
            last_event_at=now,
        )
        session.add(app_row)
    else:
        if status != app_row.status:
            if not can_transition_application_status(app_row.status, status):
                raise InvalidApplicationStateTransitionError(
                    f"Cannot transition application from {app_row.status.value} to {status.value}",
                    current_status=app_row.status,
                    target_status=status,
                )
            app_row.status = status

        app_row.apply_mode = apply_mode
        if provider:
            app_row.provider = provider
        if provider_application_id:
            app_row.provider_application_id = provider_application_id
        if app_row.status == ApplicationStatus.APPLIED and app_row.submitted_at is None:
            app_row.submitted_at = submitted_at or now
        app_row.last_event_at = now

    session.flush()
    logger.info(
        "applications.persisted",
        extra={
            "application_id": str(app_row.id),
            "user_id": str(user.id),
            "job_id": str(job_id),
            "status": app_row.status.value,
        },
    )
    return app_row


def build_and_persist_application_kit(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
    match_result_id: uuid.UUID | None,
    profile_version: int,
    draft: TailoredResumeDraft,
    strengths: list[str],
    gaps: list[str],
) -> ApplicationKit:
    """Builds and persists an immutable ApplicationKit for a candidate application."""
    # 1. Check existing kit first (replay/idempotency guard)
    existing_kit = session.execute(
        select(ApplicationKit)
        .where(
            ApplicationKit.user_id == user.id,
            ApplicationKit.job_id == job_id,
        )
        .order_by(ApplicationKit.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    # 2. Check existing application row
    app_row = session.execute(
        select(Application).where(
            Application.user_id == user.id,
            Application.job_id == job_id,
        )
    ).scalar_one_or_none()

    if app_row is None:
        application = create_or_update_application(
            session,
            user=user,
            job_id=job_id,
            status=ApplicationStatus.PREPARING,
        )
    else:
        # If application is already APPLIED, INTERVIEWING, REJECTED, or WITHDRAWN,
        # we NEVER regress its status back to PREPARING!
        if app_row.status == ApplicationStatus.SAVED:
            application = create_or_update_application(
                session,
                user=user,
                job_id=job_id,
                status=ApplicationStatus.PREPARING,
            )
        else:
            application = app_row

    if existing_kit is not None:
        return existing_kit

    kit_snapshot = {
        "source_job_id": str(job_id),
        "target_title": draft.target_title,
        "fit_summary": draft.sections.get("summary", ""),
        "grounded_resume_draft": draft.to_dict(),
        "strengths": strengths,
        "gaps": gaps,
        "checklist": [
            "Revise suas informações de contato",
            "Verifique os destaques customizados para a oportunidade",
            "Acesse o link da vaga e complete a submissão",
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }

    kit = ApplicationKit(
        application_id=application.id,
        user_id=user.id,
        job_id=job_id,
        match_id=match_result_id,
        profile_version=profile_version,
        kit_snapshot=kit_snapshot,
    )
    session.add(kit)
    session.flush()

    logger.info(
        "application_kits.persisted",
        extra={
            "kit_id": str(kit.id),
            "application_id": str(application.id),
            "user_id": str(user.id),
            "job_id": str(job_id),
        },
    )
    return kit


def list_user_applications(
    session: Session,
    *,
    user: User,
    limit: int = 10,
) -> list[Application]:
    """Lists recent applications for the user."""
    stmt = (
        select(Application)
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def update_application_status(
    session: Session,
    *,
    user: User,
    application_id: uuid.UUID,
    new_status: ApplicationStatus,
) -> Application:
    """Updates status of an application scoped to authenticated user (IDOR protected)."""
    app_row = session.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user.id,
        )
    ).scalar_one_or_none()

    if app_row is None:
        raise ApplicationNotFoundError(f"Application {application_id} not found for user {user.id}")

    now = datetime.now(UTC)
    if new_status != app_row.status:
        if not can_transition_application_status(app_row.status, new_status):
            raise InvalidApplicationStateTransitionError(
                f"Cannot transition application from {app_row.status.value} to {new_status.value}",
                current_status=app_row.status,
                target_status=new_status,
            )
        app_row.status = new_status

    app_row.last_event_at = now
    if app_row.status == ApplicationStatus.APPLIED and app_row.submitted_at is None:
        app_row.submitted_at = now

    session.flush()
    logger.info(
        "applications.status_updated",
        extra={
            "application_id": str(app_row.id),
            "user_id": str(user.id),
            "new_status": new_status.value,
        },
    )
    return app_row

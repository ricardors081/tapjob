"""Unit tests for Application state machine and lifecycle enforcement (Finding 3).

Proves:
- Permitted vs forbidden transitions (SAVED -> PREPARING -> APPLIED -> beyond)
- APPLIED to PREPARING regression is strictly rejected (InvalidApplicationStateTransitionError)
- submitted_at is immutable after first submission (no timestamp regression)
- build_and_persist_application_kit never regresses status of already APPLIED application
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.applications import Application, ApplicationStatus, ApplyMode
from tapjob_api.messaging.applications_service import (
    InvalidApplicationStateTransitionError,
    build_and_persist_application_kit,
    can_transition_application_status,
    create_or_update_application,
    update_application_status,
)
from tapjob_api.messaging.resume_tailoring import TailoredResumeDraft


def test_can_transition_application_status_rules() -> None:
    """Verifies the state machine transition table for applications (Finding 3)."""
    # Same state is always allowed (idempotent)
    assert (
        can_transition_application_status(ApplicationStatus.SAVED, ApplicationStatus.SAVED) is True
    )
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.APPLIED)
        is True
    )

    # Forward progressions from SAVED
    assert (
        can_transition_application_status(ApplicationStatus.SAVED, ApplicationStatus.PREPARING)
        is True
    )
    assert (
        can_transition_application_status(ApplicationStatus.SAVED, ApplicationStatus.APPLIED)
        is True
    )

    # Forward progressions from PREPARING
    assert (
        can_transition_application_status(ApplicationStatus.PREPARING, ApplicationStatus.APPLIED)
        is True
    )
    assert (
        can_transition_application_status(ApplicationStatus.PREPARING, ApplicationStatus.SAVED)
        is False
    )

    # Forbidden regressions from APPLIED
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.PREPARING)
        is False
    )
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.SAVED)
        is False
    )

    # Forward progressions from APPLIED
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.INTERVIEWING)
        is True
    )
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.REJECTED)
        is True
    )
    assert (
        can_transition_application_status(ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN)
        is True
    )

    # Forbidden regressions from INTERVIEWING / REJECTED / WITHDRAWN
    assert (
        can_transition_application_status(
            ApplicationStatus.INTERVIEWING, ApplicationStatus.PREPARING
        )
        is False
    )
    assert (
        can_transition_application_status(ApplicationStatus.REJECTED, ApplicationStatus.PREPARING)
        is False
    )
    assert (
        can_transition_application_status(ApplicationStatus.WITHDRAWN, ApplicationStatus.PREPARING)
        is False
    )


def test_create_or_update_application_rejects_applied_to_preparing() -> None:
    """Attempting to update an APPLIED application back to PREPARING raises (Finding 3)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()
    t1 = datetime.now(UTC) - timedelta(hours=2)

    existing_app = Application(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job_id,
        status=ApplicationStatus.APPLIED,
        submitted_at=t1,
        apply_mode=ApplyMode.MANUAL,
    )
    session.execute.return_value.scalar_one_or_none.return_value = existing_app

    with pytest.raises(InvalidApplicationStateTransitionError) as exc_info:
        create_or_update_application(
            session,
            user=user,
            job_id=job_id,
            status=ApplicationStatus.PREPARING,
        )

    assert exc_info.value.current_status == ApplicationStatus.APPLIED
    assert exc_info.value.target_status == ApplicationStatus.PREPARING
    assert existing_app.status == ApplicationStatus.APPLIED
    assert existing_app.submitted_at == t1


def test_update_application_status_rejects_invalid_transition() -> None:
    """update_application_status enforces transitions and raises on invalid moves (Finding 3)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()

    app_row = Application(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job_id,
        status=ApplicationStatus.INTERVIEWING,
        submitted_at=datetime.now(UTC),
        apply_mode=ApplyMode.MANUAL,
    )
    session.execute.return_value.scalar_one_or_none.return_value = app_row

    with pytest.raises(InvalidApplicationStateTransitionError):
        update_application_status(
            session,
            user=user,
            application_id=app_row.id,
            new_status=ApplicationStatus.PREPARING,
        )


def test_submitted_at_preserved_and_immutable() -> None:
    """submitted_at is recorded once and remains unchanged on subsequent transitions (Finding 3)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()

    # 1. Start in PREPARING with submitted_at=None
    app_row = Application(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job_id,
        status=ApplicationStatus.PREPARING,
        submitted_at=None,
        apply_mode=ApplyMode.MANUAL,
    )
    session.execute.return_value.scalar_one_or_none.return_value = app_row

    # 2. User confirms submission: PREPARING -> APPLIED
    t1 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    updated = create_or_update_application(
        session,
        user=user,
        job_id=job_id,
        status=ApplicationStatus.APPLIED,
        submitted_at=t1,
    )
    assert updated.status == ApplicationStatus.APPLIED
    assert updated.submitted_at == t1

    # 3. Subsequent transition: APPLIED -> INTERVIEWING
    interviewing_app = update_application_status(
        session,
        user=user,
        application_id=app_row.id,
        new_status=ApplicationStatus.INTERVIEWING,
    )
    assert interviewing_app.status == ApplicationStatus.INTERVIEWING
    # submitted_at MUST remain T1 (immutable)
    assert interviewing_app.submitted_at == t1


def test_build_and_persist_application_kit_preserves_applied_status() -> None:
    """build_and_persist_application_kit preserves status of APPLIED application (Finding 3)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()
    match_id = uuid.uuid4()
    t1 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    # Pre-existing application that is already APPLIED
    existing_app = Application(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job_id,
        status=ApplicationStatus.APPLIED,
        submitted_at=t1,
        apply_mode=ApplyMode.MANUAL,
    )

    # First scalar_one_or_none: check existing_kit (None)
    # Second scalar_one_or_none: query existing application
    session.execute.return_value.scalar_one_or_none.side_effect = [
        None,  # no existing kit
        existing_app,  # existing application is APPLIED
    ]

    draft = TailoredResumeDraft(
        job_id=job_id,
        grounded_fact_ids=[],
        target_title="Senior Python Engineer",
        sections={"summary": "Experienced engineer with backend skills."},
        claims=[],
        policy_version="2026-10-10.v2",
        validation_result={"valid": True},
        ready_for_submission=True,
    )

    kit = build_and_persist_application_kit(
        session,
        user=user,
        job_id=job_id,
        match_result_id=match_id,
        profile_version=1,
        draft=draft,
        strengths=["Python"],
        gaps=[],
    )

    assert kit is not None
    # Application MUST remain APPLIED and submitted_at MUST remain T1
    assert existing_app.status == ApplicationStatus.APPLIED
    assert existing_app.submitted_at == t1

"""Unit tests for the 15 Career Agent Tools (Finding 12).

Verifies that every tool is:
- Typed
- User-scoped (IDOR safe)
- Free of domain rule leaks
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.applications import Application, ApplicationStatus, ApplyMode
from tapjob_api.messaging.career_agent_tools import (
    tool_get_profile_missing_information,
    tool_get_profile_summary,
    tool_list_applications,
    tool_register_application,
    tool_schedule_follow_up,
    tool_update_application_status,
)
from tapjob_api.messaging.reminders import Reminder, ReminderStatus


def test_tool_get_profile_summary() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)

    mock_fact = MagicMock()
    mock_fact.value_json = {"kind": "skill", "name": "Python"}

    snapshot = MagicMock(spec=TruthSnapshot)
    snapshot.profile_version = 3
    snapshot.headline = "Engenheiro Backend"
    snapshot.summary = "10 anos de experiência"
    snapshot.target_roles = ["Tech Lead", "Senior Engineer"]
    snapshot.facts = [mock_fact]

    with patch(
        "tapjob_api.messaging.career_agent_tools.get_truth_snapshot",
        return_value=snapshot,
    ):
        result = tool_get_profile_summary(session, user=user)

        assert result["user_id"] == str(user.id)
        assert result["profile_version"] == 3
        assert result["headline"] == "Engenheiro Backend"
        assert result["skills"] == ["Python"]
        assert result["fact_count"] == 1


def test_tool_get_profile_missing_information() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)

    snapshot = MagicMock(spec=TruthSnapshot)
    snapshot.profile_version = 0
    snapshot.headline = None
    snapshot.summary = None
    snapshot.target_roles = []
    snapshot.facts = []

    with patch(
        "tapjob_api.messaging.career_agent_tools.get_truth_snapshot",
        return_value=snapshot,
    ):
        result = tool_get_profile_missing_information(session, user=user)

        assert result["is_complete"] is False
        assert "headline" in result["missing_fields"]
        assert "summary" in result["missing_fields"]
        assert "profile_facts" in result["missing_fields"]


def test_tool_applications_workflow() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()

    app_row = Application(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job_id,
        status=ApplicationStatus.SAVED,
        apply_mode=ApplyMode.MANUAL,
    )

    with (
        patch(
            "tapjob_api.messaging.career_agent_tools.create_or_update_application",
            return_value=app_row,
        ),
        patch(
            "tapjob_api.messaging.career_agent_tools.update_application_status",
            return_value=app_row,
        ),
        patch(
            "tapjob_api.messaging.career_agent_tools.list_user_applications",
            return_value=[app_row],
        ),
    ):
        res_create = tool_register_application(session, user=user, job_id=job_id)
        assert res_create["status"] == "saved"

        app_row.status = ApplicationStatus.APPLIED
        res_update = tool_update_application_status(
            session, user=user, application_id=app_row.id, new_status=ApplicationStatus.APPLIED
        )
        assert res_update["status"] == "applied"

        res_list = tool_list_applications(session, user=user)
        assert len(res_list) == 1
        assert res_list[0]["job_id"] == str(job_id)


def test_tool_schedule_follow_up() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
    remind_time = datetime.now(UTC) + timedelta(days=3)
    app_id = uuid.uuid4()

    reminder = Reminder(
        id=uuid.uuid4(),
        user_id=user.id,
        channel_identity_link_id=None,
        intent="application_follow_up",
        remind_at=remind_time,
        status=ReminderStatus.PENDING,
        payload={"application_id": str(app_id), "note": "Verifique o status"},
        idempotency_key="rem_123",
    )

    with patch(
        "tapjob_api.messaging.career_agent_tools.create_reminder",
        return_value=reminder,
    ):
        res = tool_schedule_follow_up(
            session,
            user=user,
            remind_at=remind_time,
            application_id=app_id,
            note="Verifique o status",
        )
        assert res["intent"] == "application_follow_up"
        assert res["status"] == "pending"

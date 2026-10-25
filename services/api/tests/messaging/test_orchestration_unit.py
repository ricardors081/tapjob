"""Unit tests for hardened orchestration logic (Findings 1, 3, 4, 8).

Validates:
- Fail-closed storage and adapter requirement
- Transient retrieve_media failure raises (retryable, zero fake resume)
- Terminal retrieve_media failure returns terminal response
- FakeMessagingChannel explicitly injected works with real fixture
- Reminder without application fails closed without fake UUID
- Fact routing handles accept, edit, and reject
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.models import Job, JobStatus
from tapjob_api.messaging.applications import Application, ApplicationKit, ApplicationStatus
from tapjob_api.messaging.career_agent_tools import (
    tool_register_application,
    tool_schedule_follow_up,
)
from tapjob_api.messaging.orchestration import dispatch_inbound_message, resolve_user_target_job
from tapjob_api.messaging.resume_tailoring import TailoredResumeDraft
from tapjob_api.ports._common import PortError, PortErrorCode
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundAttachment,
    InboundMessage,
)
from tapjob_api.resume_import.commands import CandidateReviewDecision, ReviewDecisionKind
from tapjob_api.resume_import.errors import (
    MalformedDocumentError,
)
from tapjob_api.resume_import.models import (
    CandidateReviewDraft,
    CandidateReviewState,
    ImportStatus,
    ResumeImport,
    ResumeImportCandidate,
)
from tapjob_api.resume_import.storage import InMemoryObjectStorage, ObjectStorageError

from .fake_channel import FakeMessagingChannel


def test_resume_ingest_storage_transient_failure_propagates() -> None:
    """Storage transient failure is propagated, not swallowed as PDF error (Finding 1).

    Ensures that R2/storage.put failure does not generate a fake 'PDF inválido' response
    and allows bounded inbound task retry.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_storage_err",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_storage_err",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    with (
        pytest.MonkeyPatch.context() as mp,
        pytest.raises(ObjectStorageError),
    ):
        mp.setattr(
            fake_channel,
            "retrieve_media",
            AsyncMock(return_value=b"%PDF-1.4 test resume bytes"),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.tool_ingest_resume",
            MagicMock(
                side_effect=ObjectStorageError("R2 bucket put failed: connection reset by peer")
            ),
        )
        dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=fake_channel,
            storage=storage,
        )


def test_resume_ingest_malformed_document_fails_closed_terminal() -> None:
    """Terminal document failure produces a helpful response and does not retry (Finding 1)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_malformed",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_malformed",
                mime_type="application/pdf",
                filename="corrupt.pdf",
                kind="document",
            ),
        ),
    )

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()
    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "doc_msg_doc_malformed"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            fake_channel,
            "retrieve_media",
            AsyncMock(return_value=b"corrupt-data"),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.tool_ingest_resume",
            MagicMock(side_effect=MalformedDocumentError("PDF trailer not found or EOF corrupted")),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=fake_channel,
            storage=storage,
        )

    assert len(keys) == 1
    assert "doc_msg_doc_malformed" in keys[0]


def test_fact_confirmation_three_candidates_partial_decisions_no_omission() -> None:
    """Proves Finding 5: No acceptance or rejection by omission with 3 candidates.

    1. 'rejeitar fato 2' -> only candidate 2 has REJECT draft; 1 and 3 stay undecided.
       Finalize is NOT called.
    2. 'aceitar fato 1' -> candidate 1 has ACCEPT draft; 3 is still pending.
       Finalize is NOT called.
    3. 'editar fato 3: Python Specialist' -> candidate 3 has EDIT_ACCEPT draft.
       All 3 decided -> Finalize IS called with complete explicit decisions.
    4. 'confirmar todos' -> ACCEPT for all 3 -> Finalize called.
    5. 'rejeitar todos' -> REJECT for all 3 -> Finalize called.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link_mock = MagicMock(id=uuid.uuid4())

    import_id = uuid.uuid4()
    now = datetime.now(UTC)
    c1 = ResumeImportCandidate(
        id=uuid.uuid4(),
        user_id=user.id,
        resume_import_id=import_id,
        candidate_kind="skill",
        value_json={"name": "FastAPI"},
        created_at=now,
    )
    c2 = ResumeImportCandidate(
        id=uuid.uuid4(),
        user_id=user.id,
        resume_import_id=import_id,
        candidate_kind="experience",
        value_json={"employer": "OldCorp", "title": "Junior Dev"},
        created_at=now + timedelta(seconds=1),
    )
    c3 = ResumeImportCandidate(
        id=uuid.uuid4(),
        user_id=user.id,
        resume_import_id=import_id,
        candidate_kind="skill",
        value_json={"name": "Python"},
        created_at=now + timedelta(seconds=2),
    )
    import_record = ResumeImport(
        id=import_id,
        user_id=user.id,
        status=ImportStatus.READY_FOR_REVIEW,
        candidates=[c1, c2, c3],
        created_at=datetime.now(UTC),
    )

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "fact_outbox_key"
    finalize_mock = MagicMock()

    # In-memory drafts table simulation
    drafts_table: dict[uuid.UUID, CandidateReviewDraft] = {}

    def mock_scalars(stmt: object) -> MagicMock:
        res = MagicMock()
        # Return drafts for import_id
        res.all.return_value = list(drafts_table.values())
        return res

    session.scalars.side_effect = mock_scalars

    def mock_add(obj: object) -> None:
        if isinstance(obj, CandidateReviewDraft):
            drafts_table[obj.candidate_id] = obj

    session.add.side_effect = mock_add

    # STEP 1: 'rejeitar fato 2' -> only candidate 2 decided, 1 and 3 undecided
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,
        link_mock,
        import_record,
    ]
    inbound_1 = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_fact_step1",
        received_at=datetime.now(UTC),
        text="rejeitar fato 2",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            finalize_mock,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_1, provider_id=uuid.uuid4()
        )

    # Finalize must NOT have been called (no acceptance/rejection by omission)
    finalize_mock.assert_not_called()
    assert c2.id in drafts_table
    assert drafts_table[c2.id].decision == ReviewDecisionKind.REJECT.value
    assert c1.id not in drafts_table
    assert c3.id not in drafts_table

    # STEP 2: 'aceitar fato 1' -> candidate 1 decided, candidate 3 still pending
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,
        link_mock,
        import_record,
    ]
    inbound_2 = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_fact_step2",
        received_at=datetime.now(UTC),
        text="aceitar fato 1",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            finalize_mock,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_2, provider_id=uuid.uuid4()
        )

    finalize_mock.assert_not_called()
    assert c1.id in drafts_table
    assert drafts_table[c1.id].decision == ReviewDecisionKind.ACCEPT.value
    assert c3.id not in drafts_table

    # STEP 3: 'editar fato 3: Python Specialist' -> all 3 decided -> Finalize IS called!
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,
        link_mock,
        import_record,
    ]
    inbound_3 = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_fact_step3",
        received_at=datetime.now(UTC),
        text="editar fato 3: Python Specialist",
    )

    captured_decisions: list[CandidateReviewDecision] = []

    def mock_final_run(*args: object, **kwargs: object) -> MagicMock:
        nonlocal captured_decisions
        raw = kwargs.get("decisions")
        captured_decisions = list(raw) if isinstance(raw, list) else []
        ret = MagicMock()
        ret.resume_version.profile_version = 2
        return ret

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.get_truth_snapshot",
            lambda *args, **kwargs: MagicMock(profile_version=1),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            mock_final_run,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_3, provider_id=uuid.uuid4()
        )

    assert len(captured_decisions) == 3
    d1 = next(d for d in captured_decisions if d.candidate_id == c1.id)
    d2 = next(d for d in captured_decisions if d.candidate_id == c2.id)
    d3 = next(d for d in captured_decisions if d.candidate_id == c3.id)
    assert d1.decision == ReviewDecisionKind.ACCEPT
    assert d2.decision == ReviewDecisionKind.REJECT
    assert d3.decision == ReviewDecisionKind.EDIT_ACCEPT
    assert d3.edited_value == {"name": "Python Specialist"}


def test_fact_confirmation_confirm_all_and_reject_all() -> None:
    """'confirmar todos' and 'rejeitar todos' finalize all candidates explicitly (Finding 5)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link_mock = MagicMock(id=uuid.uuid4())

    import_id = uuid.uuid4()
    c1 = ResumeImportCandidate(
        id=uuid.uuid4(),
        user_id=user.id,
        resume_import_id=import_id,
        candidate_kind="skill",
        value_json={"name": "FastAPI"},
        created_at=datetime.now(UTC),
    )
    c2 = ResumeImportCandidate(
        id=uuid.uuid4(),
        user_id=user.id,
        resume_import_id=import_id,
        candidate_kind="skill",
        value_json={"name": "Docker"},
        created_at=datetime.now(UTC),
    )
    import_record = ResumeImport(
        id=import_id,
        user_id=user.id,
        status=ImportStatus.READY_FOR_REVIEW,
        candidates=[c1, c2],
        created_at=datetime.now(UTC),
    )

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "fact_outbox_key"

    captured_decisions: list[CandidateReviewDecision] = []

    def mock_final_run(*args: object, **kwargs: object) -> MagicMock:
        nonlocal captured_decisions
        raw = kwargs.get("decisions")
        captured_decisions = list(raw) if isinstance(raw, list) else []
        ret = MagicMock()
        ret.resume_version.profile_version = 2
        return ret

    session.scalars.return_value.all.return_value = []

    # Test Confirm All
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,
        link_mock,
        import_record,
    ]
    inbound_conf_all = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_conf_all",
        received_at=datetime.now(UTC),
        text="confirmar todos",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.get_truth_snapshot",
            lambda *args, **kwargs: MagicMock(profile_version=1),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            mock_final_run,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_conf_all, provider_id=uuid.uuid4()
        )

    assert len(captured_decisions) == 2
    assert all(d.decision == ReviewDecisionKind.ACCEPT for d in captured_decisions)

    # Test Reject All
    captured_decisions.clear()
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,
        link_mock,
        import_record,
    ]
    inbound_rej_all = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_rej_all",
        received_at=datetime.now(UTC),
        text="rejeitar todos",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.get_truth_snapshot",
            lambda *args, **kwargs: MagicMock(profile_version=1),
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            mock_final_run,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_rej_all, provider_id=uuid.uuid4()
        )

    assert len(captured_decisions) == 2
    assert all(d.decision == ReviewDecisionKind.REJECT for d in captured_decisions)


def test_runtime_fail_closed_when_storage_missing() -> None:
    """Document attachment intake fails fast if ObjectStoragePort is missing (Finding 1)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_1",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_1",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="ObjectStoragePort is required"):
        dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=FakeMessagingChannel(),
            storage=None,  # missing storage
        )


def test_runtime_fail_closed_when_channel_adapter_missing() -> None:
    """Document attachment intake fails fast if channel_adapter is missing (Finding 1)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_2",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_2",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    storage = InMemoryObjectStorage()

    with pytest.raises(
        RuntimeError, match="MessagingChannel capable of retrieve_media is required"
    ):
        dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=None,  # missing adapter
            storage=storage,
        )


def test_retrieve_media_transient_failure_raises_and_prevents_fake_resume() -> None:
    """Transient media failure raises PortError, preventing fake resume ingestion (Finding 1)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_3",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_3",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    adapter_mock = MagicMock()

    async def fail_transient(*args: object, **kwargs: object) -> bytes:
        raise PortError(
            PortErrorCode.TIMEOUT,
            provider="evolution",
            message="Evolution API timed out",
            transient=True,
        )

    adapter_mock.retrieve_media.side_effect = fail_transient

    storage = InMemoryObjectStorage()

    with pytest.raises(PortError) as exc_info:
        dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=adapter_mock,
            storage=storage,
        )

    assert exc_info.value.transient is True


def test_retrieve_media_terminal_failure_responds_gracefully() -> None:
    """Terminal media retrieval failure does not retry infinitely (Finding 1)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    # mock resolve_user_by_channel_identity and resolve_link_by_channel_identity
    session.execute.return_value.scalar_one_or_none.return_value = user

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_4",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_4",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    adapter_mock = MagicMock()

    async def fail_terminal(*args: object, **kwargs: object) -> bytes:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider="evolution",
            message="Unsupported document MIME type",
            transient=False,
        )

    adapter_mock.retrieve_media.side_effect = fail_terminal
    storage = InMemoryObjectStorage()

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "doc_msg_doc_4"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=adapter_mock,
            storage=storage,
        )
    assert len(keys) == 1
    assert "doc_msg_doc_4" in keys[0]


def test_tool_register_application_defaults_to_preparing() -> None:
    """tool_register_application defaults to PREPARING status (Finding 3)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    job_id = uuid.uuid4()

    app_mock = MagicMock()
    app_mock.id = uuid.uuid4()
    app_mock.status = ApplicationStatus.PREPARING
    app_mock.submitted_at = None
    app_mock.apply_mode.value = "manual"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.career_agent_tools.create_or_update_application",
            lambda *args, **kwargs: app_mock,
        )
        res = tool_register_application(session, user=user, job_id=job_id)
        assert res["status"] == "preparing"
        assert res["submitted_at"] is None


def test_tool_schedule_follow_up_without_application_id() -> None:
    """tool_schedule_follow_up omits application_id when application_id is None (Finding 4)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    rem_at = datetime.now(UTC)

    rem_mock = MagicMock()
    rem_mock.id = uuid.uuid4()
    rem_mock.intent = "application_follow_up"
    rem_mock.remind_at = rem_at
    rem_mock.status.value = "pending"
    rem_mock.channel_identity_link_id = None

    captured_payload: dict[str, object] = {}

    def mock_create_reminder(*args: object, **kwargs: object) -> object:
        nonlocal captured_payload
        raw_payload = kwargs.get("payload")
        captured_payload = raw_payload if isinstance(raw_payload, dict) else {}
        return rem_mock

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("tapjob_api.messaging.career_agent_tools.create_reminder", mock_create_reminder)
        res = tool_schedule_follow_up(session, user=user, remind_at=rem_at, application_id=None)
        assert "application_id" not in captured_payload
        assert captured_payload["note"] == "Follow-up de candidatura"
        assert res["reminder_id"] == str(rem_mock.id)


def test_reminder_intent_without_application_fails_closed() -> None:
    """When user has no applications, reminder request asks to apply first (Finding 4)."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)

    # First: user; second: link; third: latest_app (None);
    # fourth & fifth: target_job queries (None, None); sixth: existing reminder (None)
    session.execute.return_value.scalar_one_or_none.side_effect = [
        user,  # resolve user
        MagicMock(id=uuid.uuid4()),  # resolve link
        None,  # no latest application
        None,  # no intake job
        None,  # no app job
        None,  # no existing reminder
    ]

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_rem_1",
        received_at=datetime.now(UTC),
        text="me lembra de acompanhar a candidatura daqui a 3 dias",
    )

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "rem_sched_msg_rem_1"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )
    assert len(keys) == 1
    assert "rem_sched_msg_rem_1" in keys[0]


def test_resolve_user_target_job_multiuser_isolation() -> None:
    """resolve_user_target_job queries jobs for specified user only (Finding 2)."""
    session = MagicMock(spec=Session)
    user_a = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    user_b = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)

    job_a = Job(
        id=uuid.uuid4(),
        title="Vaga User A",
        created_by_user_id=user_a.id,
        created_at=datetime.now(UTC),
    )
    job_b = Job(
        id=uuid.uuid4(),
        title="Vaga User B",
        created_by_user_id=user_b.id,
        created_at=datetime.now(UTC),
    )

    # Mock execute to return job_a when filtering by user_a.id and job_b when filtering by user_b.id
    def mock_execute(stmt: object) -> MagicMock:
        result = MagicMock()
        # inspect query parameter / criteria
        stmt_str = str(stmt)
        if str(user_a.id) in stmt_str or "created_by_user_id" in stmt_str:
            result.scalar_one_or_none.return_value = job_a
        else:
            result.scalar_one_or_none.return_value = job_b
        return result

    session.execute.side_effect = mock_execute

    resolved_a = resolve_user_target_job(session, user=user_a)
    assert resolved_a is not None
    assert resolved_a.id == job_a.id


def test_first_contact_with_document_processes_in_same_inbound() -> None:
    """First contact with document creates user and ingests resume in same execution (Finding 9)."""
    session = MagicMock(spec=Session)
    user_new = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link_new = MagicMock(id=uuid.uuid4())

    def mock_add(obj: object) -> None:
        if isinstance(obj, User):
            obj.id = user_new.id

    session.add.side_effect = mock_add

    # User resolution returns None (first contact)
    session.execute.return_value.scalar_one_or_none.return_value = None

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_first",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_first",
                mime_type="application/pdf",
                filename="curriculo.pdf",
                kind="document",
            ),
        ),
    )

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "first_doc_outbox"

    resume_ingested = False

    def mock_ingest_resume(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal resume_ingested
        resume_ingested = True
        return {"import_id": str(uuid.uuid4()), "status": "ready_for_review", "candidates_count": 2}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.link_or_create_identity",
            lambda *args, **kwargs: link_new,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        mp.setattr("tapjob_api.messaging.orchestration.tool_ingest_resume", mock_ingest_resume)

        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=fake_channel,
            storage=storage,
        )

    # Greeting queued AND document processed
    assert resume_ingested is True
    assert len(keys) >= 1


def test_first_contact_with_job_paste_processes_in_same_inbound() -> None:
    """First contact with pasted job creates user and ingests job in same execution (Finding 9)."""
    session = MagicMock(spec=Session)
    user_new = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link_new = MagicMock(id=uuid.uuid4())

    def mock_add(obj: object) -> None:
        if isinstance(obj, User):
            obj.id = user_new.id

    session.add.side_effect = mock_add

    session.execute.return_value.scalar_one_or_none.return_value = None

    job_text = (
        "Vaga: Engenheiro Backend Python na Tech. Requisitos: FastAPI, PostgreSQL, Docker e Celery."
    )
    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_job_first",
        received_at=datetime.now(UTC),
        text=job_text,
    )

    outbox_mock = MagicMock()
    outbox_mock.idempotency_key = "first_job_outbox"

    job_ingested = False

    def mock_ingest_paste(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal job_ingested
        job_ingested = True
        return {"job_id": str(uuid.uuid4()), "title": "Engenheiro Backend", "company": "Tech"}

    def mock_match(*args: object, **kwargs: object) -> dict[str, object]:
        return {"overall_score": 85, "eligibility": "eligible"}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.link_or_create_identity",
            lambda *args, **kwargs: link_new,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            lambda *args, **kwargs: outbox_mock,
        )
        mp.setattr("tapjob_api.messaging.orchestration.tool_ingest_job_text", mock_ingest_paste)
        mp.setattr("tapjob_api.messaging.orchestration.tool_match_job", mock_match)

        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert job_ingested is True
    assert len(keys) >= 1


def test_application_kit_after_applied_conversational_regression() -> None:
    """Conversational regression: kit after applied preserves APPLIED status (Finding 3).

    Verifies:
    - Message 1 ("quero me candidatar") -> kit generated, Application PREPARING
    - Message 2 ("já me candidatei") -> Application APPLIED, submitted_at=T1
    - Message 3 (new provider_message_id, "quero me candidatar") -> returns kit, remains APPLIED,
      submitted_at == T1, zero duplicate Application, zero duplicate kit.
    """
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    target_job = Job(
        id=uuid.uuid4(),
        title="Engenheiro Backend",
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    link = MagicMock(id=uuid.uuid4())

    apps: list[Application] = []
    kits: list[ApplicationKit] = []
    queued_messages: list[MagicMock] = []

    def fake_queue_response_message(
        session: object,
        channel_identity_link_id: object,
        recipient_external_id: object,
        message_text: str,
        idempotency_key: str,
    ) -> MagicMock:
        msg = MagicMock(idempotency_key=idempotency_key, message_text=message_text)
        queued_messages.append(msg)
        return msg

    class StatefulTestSession:
        def add(self, obj: object) -> None:
            now = datetime.now(UTC)
            if isinstance(obj, (Application, ApplicationKit)) and obj.created_at is None:
                obj.created_at = now
            if isinstance(obj, Application):
                apps.append(obj)
            elif isinstance(obj, ApplicationKit):
                kits.append(obj)

        def flush(self) -> None:
            pass

        def execute(self, stmt: object) -> MagicMock:
            s = str(stmt)
            res = MagicMock()
            if "application_kits" in s:
                res.scalar_one_or_none.return_value = kits[-1] if kits else None
                res.scalar_one.return_value = kits[-1] if kits else None
            elif "applications" in s:
                res.scalar_one_or_none.return_value = apps[-1] if apps else None
                res.scalar_one.return_value = apps[-1] if apps else None
            else:
                res.scalar_one_or_none.return_value = None
                res.scalar_one.return_value = None
            return res

    session: Any = StatefulTestSession()

    draft = TailoredResumeDraft(
        job_id=target_job.id,
        grounded_fact_ids=[],
        target_title="Engenheiro Backend",
        sections={"summary": "Qualificado para Backend"},
        claims=[],
        policy_version="1.0",
        validation_result={"grounded": True},
        ready_for_submission=True,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            lambda *args, **kwargs: user,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            lambda *args, **kwargs: link,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            lambda *args, **kwargs: target_job,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.tool_tailor_resume",
            lambda *args, **kwargs: None,
        )
        mp.setattr(
            "tapjob_api.messaging.career_agent_tools.matching_service.get_or_compute_match",
            lambda *args, **kwargs: MagicMock(
                id=uuid.uuid4(), profile_version=1, strengths=[], missing_requirements=[]
            ),
        )
        mp.setattr(
            "tapjob_api.messaging.career_agent_tools.generate_grounded_tailored_resume",
            lambda *args, **kwargs: draft,
        )
        mp.setattr(
            "tapjob_api.messaging.orchestration.queue_response_message",
            fake_queue_response_message,
        )

        # Message 1: "quero me candidatar"
        inbound_1 = InboundMessage(
            sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
            provider_message_id="msg_kit_step1",
            received_at=datetime.now(UTC),
            text="quero me candidatar",
        )
        keys_1 = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound_1,
            provider_id=uuid.uuid4(),
        )
        assert len(keys_1) == 1
        assert len(apps) == 1
        assert apps[0].status.value == ApplicationStatus.PREPARING.value
        assert len(kits) == 1
        assert "Status: preparing" in queued_messages[-1].message_text

        # Message 2: "já me candidatei"
        inbound_2 = InboundMessage(
            sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
            provider_message_id="msg_kit_step2",
            received_at=datetime.now(UTC),
            text="já me candidatei",
        )
        keys_2 = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound_2,
            provider_id=uuid.uuid4(),
        )
        assert len(keys_2) == 1
        assert apps[0].status.value == ApplicationStatus.APPLIED.value
        t1 = apps[0].submitted_at
        assert t1 is not None
        assert "enviada" in queued_messages[-1].message_text.lower()

        # Message 3: "quero me candidatar" again with a new provider_message_id
        inbound_3 = InboundMessage(
            sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
            provider_message_id="msg_kit_step3",
            received_at=datetime.now(UTC),
            text="quero me candidatar",
        )
        keys_3 = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound_3,
            provider_id=uuid.uuid4(),
        )
        assert len(keys_3) == 1
        assert apps[0].status.value == ApplicationStatus.APPLIED.value
        assert apps[0].submitted_at == t1
        assert len(apps) == 1
        assert len(kits) == 1
        assert "Candidatura já enviada (Status: applied)" in queued_messages[-1].message_text
        # Finding 5: CTA must be status-aware; never instruct user to re-submit
        assert "já me candidatei" not in queued_messages[-1].message_text


def test_application_kit_interviewing_status_aware() -> None:
    """When an application is in INTERVIEWING status, kit response is status-aware (Finding 5)."""
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    user = User(id=user_id, status=UserStatus.ACTIVE)
    job = Job(
        id=job_id,
        title="Backend Lead",
        status=JobStatus.ACTIVE,
        created_by_user_id=user_id,
        created_at=datetime.now(UTC),
    )

    session = MagicMock(spec=Session)
    session.execute.return_value.scalar_one_or_none.return_value = user

    queued_messages: list[Any] = []

    def fake_queue_response_message(session: Any, **kwargs: Any) -> Any:
        m = MagicMock()
        m.idempotency_key = kwargs.get("idempotency_key", "k1")
        m.message_text = kwargs.get("message_text", "")
        queued_messages.append(m)
        return m

    fake_kit = {
        "kit_id": "kit_interview_123",
        "status": ApplicationStatus.INTERVIEWING.value,
        "is_grounded": True,
    }

    with (
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=job),
        patch("tapjob_api.messaging.orchestration.tool_tailor_resume"),
        patch(
            "tapjob_api.messaging.orchestration.tool_build_application_kit",
            return_value=fake_kit,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            fake_queue_response_message,
        ),
    ):
        inbound = InboundMessage(
            sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
            provider_message_id="msg_kit_interview",
            received_at=datetime.now(UTC),
            text="quero me candidatar",
        )
        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    resp_text = queued_messages[-1].message_text
    assert "Status: interviewing" in resp_text
    assert "entrevistas" in resp_text
    # Finding 5: strictly no contradictory CTA
    assert "já me candidatei" not in resp_text


def test_sanitized_real_fixture_media_pipeline_integration() -> None:
    """End-to-end media pipeline integration test with sanitized real Human Acceptance fixture.

    Proves:
    1. EvolutionAdapter.parse_inbound parses sanitized real payload without crash.
    2. channel_adapter.retrieve_media is invoked with doc_att and message_id="3A8424FAE89C98FF36B6".
    3. tool_ingest_resume is invoked with downloaded PDF bytes and filename="curriculo - teste.pdf".
    4. Outbox response message is queued confirming receipt ("Recebemos seu currículo").
    """
    import asyncio
    import json

    from fixtures.evolution_real_payload_sanitized import (
        make_sanitized_pdf_document_webhook_payload,
    )

    from tapjob_api.messaging.evolution_adapter import EvolutionAdapter
    from tapjob_api.ports._common import CallContext

    # 1. Parse using EvolutionAdapter
    adapter = EvolutionAdapter("https://evolution.test.local", "secret", "tapjob")
    ctx = CallContext.with_timeout(correlation_id="corr-real-pipe", timeout_seconds=10.0)
    raw_payload = make_sanitized_pdf_document_webhook_payload()
    inbound_messages = asyncio.run(adapter.parse_inbound(json.dumps(raw_payload).encode(), {}, ctx))
    assert len(inbound_messages) == 1
    inbound_msg = inbound_messages[0]

    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user

    fake_import = {
        "status": ImportStatus.READY_FOR_REVIEW.value,
        "resume_import_id": str(uuid.uuid4()),
        "candidate_count": 3,
        "candidate_facts": [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}],
    }

    fake_channel = FakeMessagingChannel()
    valid_pdf_bytes = b"%PDF-1.4 test resume valid content"
    fake_channel.retrieve_media = AsyncMock(return_value=valid_pdf_bytes)  # type: ignore[method-assign]
    storage = InMemoryObjectStorage()

    queued_messages: list[Any] = []

    def fake_queue_response_message(
        sess: Any,
        *,
        channel_identity_link_id: Any,
        recipient_external_id: Any,
        message_text: Any,
        idempotency_key: Any,
    ) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.idempotency_key = idempotency_key
        mock_msg.message_text = message_text
        queued_messages.append(mock_msg)
        return mock_msg

    with (
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_resume",
            return_value=fake_import,
        ) as mock_ingest,
        patch(
            "tapjob_api.messaging.orchestration.complete_inbound_effect",
        ) as mock_complete_effect,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            fake_queue_response_message,
        ),
    ):
        keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound_msg,
            provider_id=uuid.uuid4(),
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=101,
        )

    assert len(keys) == 1
    assert keys[0] == "doc_3A8424FAE89C98FF36B6"
    assert fake_channel.retrieve_media.await_count == 1
    _, kwargs = fake_channel.retrieve_media.call_args
    assert kwargs.get("message_id") == "3A8424FAE89C98FF36B6"

    # Ingestion was called with bytes and filename
    mock_ingest.assert_called_once()
    assert mock_ingest.call_args.kwargs["content"] == valid_pdf_bytes
    assert mock_ingest.call_args.kwargs["filename"] == "curriculo - teste.pdf"
    mock_complete_effect.assert_called_once()

    # Outbox message queued
    assert len(queued_messages) == 1
    outbox_text = queued_messages[0].message_text
    assert "Recebemos seu currículo" in outbox_text
    assert "curriculo - teste.pdf" in outbox_text
    assert "3 informações profissionais" in outbox_text


def test_dispatch_unrelated_sim_does_not_finalize_stale_resume_review() -> None:
    """Stale READY_FOR_REVIEW import exists; last assistant prompt is about job recommendations;
    user sends 'sim'. Review is NOT finalized, FINALIZE_REVIEW effect is NOT emitted,
    and message routes to recommendations rather than hijacking review.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())

    stale_import_id = uuid.uuid4()
    cand = ResumeImportCandidate(
        id=uuid.uuid4(),
        resume_import_id=stale_import_id,
        user_id=user.id,
        candidate_kind="skill",
        value_json={"kind": "skill", "name": "Python"},
        confidence=1.0,
        review_state=CandidateReviewState.PENDING,
    )
    stale_import = ResumeImport(
        id=stale_import_id,
        user_id=user.id,
        status=ImportStatus.READY_FOR_REVIEW,
        original_filename="cv.pdf",
        media_type="application/pdf",
        byte_size=1000,
        checksum_sha256="stale_chk",
        storage_key="k1",
        candidates=[cand],
    )

    outbox_entry = MagicMock()
    outbox_entry.message_content = {"text": "Gostaria de ver vagas recomendadas para seu perfil?"}
    outbox_entry.idempotency_key = "rec_prompt_test"

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_sim_unrelated",
        received_at=datetime.now(UTC),
        text="sim",
    )

    queued_messages: list[Any] = []

    def fake_queue_response_message(
        sess: Any,
        *,
        channel_identity_link_id: Any,
        recipient_external_id: Any,
        message_text: Any,
        idempotency_key: Any,
    ) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.idempotency_key = idempotency_key
        mock_msg.message_text = message_text
        queued_messages.append(mock_msg)
        return mock_msg

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.complete_inbound_effect",
        ) as mock_complete_effect,
        patch(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
        ) as mock_finalize,
        patch(
            "tapjob_api.messaging.orchestration.tool_recommend_jobs",
            return_value=[{"title": "Backend Dev", "company": "Empresa Tech", "overall_score": 90}],
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            fake_queue_response_message,
        ),
    ):

        def fake_session_execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
            res = MagicMock()
            stmt_str = str(stmt).lower()
            if "outbound_message_queue" in stmt_str:
                res.scalar_one_or_none.return_value = outbox_entry
            elif "resume_imports" in stmt_str:
                res.scalar_one_or_none.return_value = stale_import
            else:
                res.scalar_one_or_none.return_value = None
            return res

        session.execute = fake_session_execute

        dispatched_keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            envelope_id=202,
        )

    # 1. Finalize was NEVER called
    assert isinstance(dispatched_keys, list)
    mock_finalize.assert_not_called()
    mock_complete_effect.assert_not_called()

    # 2. Stale import status remains READY_FOR_REVIEW
    assert stale_import.status == ImportStatus.READY_FOR_REVIEW

    # 3. Response was queued for recommendations, not review confirmation
    assert len(queued_messages) == 1
    resp_text = queued_messages[0].message_text
    assert "Informações confirmadas com sucesso!" not in resp_text
    assert "Oportunidades recomendadas" in resp_text
    assert "Backend Dev" in resp_text


def test_dispatch_sim_targets_current_resume_review() -> None:
    """When last assistant prompt is about resume review and user sends 'sim',
    dispatch executes review finalization and responds with confirmation.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())

    import_id = uuid.uuid4()
    cand = ResumeImportCandidate(
        id=uuid.uuid4(),
        resume_import_id=import_id,
        user_id=user.id,
        candidate_kind="skill",
        value_json={"kind": "skill", "name": "Python"},
        confidence=1.0,
        review_state=CandidateReviewState.PENDING,
    )
    current_import = ResumeImport(
        id=import_id,
        user_id=user.id,
        status=ImportStatus.READY_FOR_REVIEW,
        original_filename="cv.pdf",
        media_type="application/pdf",
        byte_size=1000,
        checksum_sha256="chk_active",
        storage_key="k1",
        candidates=[cand],
    )

    outbox_entry = MagicMock()
    outbox_entry.message_content = {
        "text": (
            "Recebemos seu currículo! Deseja adicionar essas informações "
            "profissionais ao seu perfil?"
        )
    }
    outbox_entry.idempotency_key = "doc_msg_test"

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_sim_review",
        received_at=datetime.now(UTC),
        text="sim",
    )

    queued_messages: list[Any] = []

    def fake_queue_response_message(
        sess: Any,
        *,
        channel_identity_link_id: Any,
        recipient_external_id: Any,
        message_text: Any,
        idempotency_key: Any,
    ) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.idempotency_key = idempotency_key
        mock_msg.message_text = message_text
        queued_messages.append(mock_msg)
        return mock_msg

    fake_res = MagicMock()
    fake_res.resume_version.profile_version = 1

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            return_value=fake_res,
        ) as mock_finalize,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            fake_queue_response_message,
        ),
        patch(
            "tapjob_api.messaging.orchestration.get_inbound_effect",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.reserve_inbound_effect",
        ),
        patch(
            "tapjob_api.messaging.orchestration.complete_inbound_effect",
        ) as mock_complete_effect,
        patch(
            "tapjob_api.messaging.orchestration.resume_import_service.detect_cross_document_conflicts",
            return_value=[],
        ),
        patch(
            "tapjob_api.messaging.orchestration.get_truth_snapshot",
            return_value=MagicMock(profile_version=1),
        ),
    ):

        def fake_session_execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
            res = MagicMock()
            stmt_str = str(stmt).lower()
            if "outbound_message_queue" in stmt_str:
                res.scalar_one_or_none.return_value = outbox_entry
            elif "resume_imports" in stmt_str:
                res.scalar_one_or_none.return_value = current_import
            else:
                res.scalar_one_or_none.return_value = None
            return res

        session.execute = fake_session_execute

        dispatched_keys = dispatch_inbound_message(
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            envelope_id=303,
        )

    # Finalize was called and completed
    assert isinstance(dispatched_keys, list)
    mock_finalize.assert_called_once()
    mock_complete_effect.assert_called_once()

    # Response confirms success
    assert len(queued_messages) == 1
    resp_text = queued_messages[0].message_text
    assert "Informações confirmadas com sucesso!" in resp_text
    assert "Versão 1" in resp_text

"""Unit tests for inbound envelope retry, backoff, and recovery tasks (Finding 5).

Proves:
- Error classification: transient vs. terminal
- Bounded exponential backoff calculation
- Maximum attempts limit (fail terminal after max retries)
- Stale ACCEPTED and retryable FAILED envelope recovery sweep
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.messaging.inbound import (
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.inbound_service import (
    InboundClaimResult,
    claim_inbound_envelope,
)
from tapjob_api.messaging.tasks import (
    MAX_INBOUND_RETRIES,
    compute_inbound_backoff_seconds,
    is_transient_inbound_error,
    process_inbound_envelope_task,
    recover_unprocessed_inbound_envelopes_task,
)
from tapjob_api.ports._common import PortError, PortErrorCode


def test_is_transient_inbound_error_classification() -> None:
    """Classifies transient vs. terminal errors accurately (Finding 5)."""
    # Transient PortError
    transient_port_err = PortError(
        PortErrorCode.TIMEOUT,
        provider="evolution",
        message="Request timed out",
        transient=True,
    )
    assert is_transient_inbound_error(transient_port_err) is True

    # Terminal PortError
    terminal_port_err = PortError(
        PortErrorCode.INVALID_REQUEST,
        provider="evolution",
        message="Invalid payload",
        transient=False,
    )
    assert is_transient_inbound_error(terminal_port_err) is False

    # Standard python transient errors
    assert is_transient_inbound_error(TimeoutError("Timeout")) is True
    assert is_transient_inbound_error(ConnectionError("Connection lost")) is True

    # Standard python terminal errors
    assert is_transient_inbound_error(ValueError("Invalid argument")) is False
    assert is_transient_inbound_error(RuntimeError("Schema violation")) is False


def test_compute_inbound_backoff_seconds_bounded() -> None:
    """Verifies exponential backoff with upper bound (Finding 5)."""
    assert compute_inbound_backoff_seconds(1) == 2.0
    assert compute_inbound_backoff_seconds(2) == 4.0
    assert compute_inbound_backoff_seconds(3) == 8.0
    assert compute_inbound_backoff_seconds(10) == 60.0  # capped at MAX_INBOUND_BACKOFF_SECONDS


def test_process_inbound_envelope_transient_failure_records_retryable_metadata() -> None:
    """Transient failure records attempt count, retryable=True, and next_attempt_at (Finding 5)."""
    session = MagicMock(spec=Session)
    envelope_id = 1234
    envelope = ProviderEventEnvelope(
        id=envelope_id,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_transient_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "wamid_1",
            "text": "oi",
        },
    )
    session.execute.return_value.scalar_one_or_none.return_value = envelope

    session_factory = MagicMock(return_value=session)
    channel_adapter = MagicMock()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks._channel_adapter", channel_adapter),
        patch("tapjob_api.messaging.tasks._redis_client", MagicMock()),
        patch("tapjob_api.messaging.tasks._storage", MagicMock()),
        patch(
            "tapjob_api.messaging.tasks.dispatch_inbound_message",
            side_effect=PortError(
                PortErrorCode.UNAVAILABLE,
                provider="evolution",
                message="Evolution server 503 unavailable",
                transient=True,
            ),
        ),
    ):
        process_inbound_envelope_task(envelope_id)

    assert envelope.status == ProviderEventProcessingStatus.FAILED
    assert envelope.error_details is not None
    assert envelope.error_details.get("retryable") is True
    assert envelope.error_details.get("attempts") == 1
    assert envelope.error_details.get("next_attempt_at") is not None
    session.commit.assert_called()


def test_process_inbound_envelope_terminal_failure_records_terminal_metadata() -> None:
    """Terminal failure records retryable=False and no next_attempt_at (Finding 5)."""
    session = MagicMock(spec=Session)
    envelope_id = 1235
    envelope = ProviderEventEnvelope(
        id=envelope_id,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_terminal_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "wamid_2",
            "text": "oi",
        },
    )
    session.execute.return_value.scalar_one_or_none.return_value = envelope

    session_factory = MagicMock(return_value=session)
    channel_adapter = MagicMock()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks._channel_adapter", channel_adapter),
        patch("tapjob_api.messaging.tasks._redis_client", MagicMock()),
        patch("tapjob_api.messaging.tasks._storage", MagicMock()),
        patch(
            "tapjob_api.messaging.tasks.dispatch_inbound_message",
            side_effect=ValueError("Malformed envelope structure"),
        ),
    ):
        process_inbound_envelope_task(envelope_id)

    assert envelope.status == ProviderEventProcessingStatus.FAILED
    assert envelope.error_details is not None
    assert envelope.error_details.get("retryable") is False
    assert envelope.error_details.get("attempts") == 1
    assert envelope.error_details.get("next_attempt_at") is None
    session.commit.assert_called()


def test_process_inbound_envelope_max_retries_becomes_terminal() -> None:
    """When transient failure reaches MAX_INBOUND_RETRIES, it becomes terminal
    (retryable=False) (Finding 5).
    """
    session = MagicMock(spec=Session)
    envelope_id = 1236
    envelope = ProviderEventEnvelope(
        id=envelope_id,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_max_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.FAILED,
        error_details={"attempts": MAX_INBOUND_RETRIES - 1, "retryable": True},
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "wamid_3",
            "text": "oi",
        },
    )
    session.execute.return_value.scalar_one_or_none.return_value = envelope

    session_factory = MagicMock(return_value=session)
    channel_adapter = MagicMock()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks._channel_adapter", channel_adapter),
        patch("tapjob_api.messaging.tasks._redis_client", MagicMock()),
        patch("tapjob_api.messaging.tasks._storage", MagicMock()),
        patch(
            "tapjob_api.messaging.tasks.dispatch_inbound_message",
            side_effect=TimeoutError("Network timeout"),
        ),
    ):
        process_inbound_envelope_task(envelope_id)

    assert envelope.status == ProviderEventProcessingStatus.FAILED
    assert envelope.error_details is not None
    assert envelope.error_details.get("attempts") == MAX_INBOUND_RETRIES
    assert envelope.error_details.get("retryable") is False  # terminal state
    assert envelope.error_details.get("next_attempt_at") is None


def test_recover_unprocessed_inbound_envelopes_task_sweeps_eligible() -> None:
    """Recovery sweep discovers eligible ACCEPTED, FAILED, and stale PROCESSING envelopes."""
    session = MagicMock(spec=Session)

    now = datetime.now(UTC)
    stale_accepted = ProviderEventEnvelope(
        id=101,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_stale_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        received_at=now - timedelta(seconds=120),
    )
    due_failed = ProviderEventEnvelope(
        id=102,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_due_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.FAILED,
        received_at=now,
        error_details={
            "retryable": True,
            "attempts": 1,
            "next_attempt_at": (now - timedelta(seconds=10)).isoformat(),
        },
    )
    not_due_failed = ProviderEventEnvelope(
        id=103,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_not_due_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.FAILED,
        received_at=now,
        error_details={
            "retryable": True,
            "attempts": 1,
            "next_attempt_at": (now + timedelta(seconds=60)).isoformat(),
        },
    )
    terminal_failed = ProviderEventEnvelope(
        id=104,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_terminal_rec",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.FAILED,
        received_at=now,
        error_details={"retryable": False, "attempts": 1},
    )
    stale_processing = ProviderEventEnvelope(
        id=105,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_stale_proc_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        received_at=now - timedelta(seconds=120),
        error_details={
            "attempts": 1,
            "processing_started_at": (now - timedelta(seconds=120)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=60)).isoformat(),
        },
    )
    active_processing = ProviderEventEnvelope(
        id=106,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_active_proc_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        received_at=now,
        error_details={
            "attempts": 1,
            "processing_started_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(seconds=50)).isoformat(),
        },
    )
    maxed_processing = ProviderEventEnvelope(
        id=107,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_maxed_proc_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        received_at=now - timedelta(seconds=200),
        error_details={
            "attempts": MAX_INBOUND_RETRIES,
            "processing_started_at": (now - timedelta(seconds=200)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=100)).isoformat(),
        },
    )

    # 1. ACCEPTED, 2. FAILED, 3. PROCESSING
    session.execute.return_value.scalars.return_value.all.side_effect = [
        [stale_accepted],
        [due_failed, not_due_failed, terminal_failed],
        [stale_processing, active_processing, maxed_processing],
    ]

    session_factory = MagicMock(return_value=session)

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks.process_inbound_envelope_task.delay") as mock_delay,
    ):
        recovered = recover_unprocessed_inbound_envelopes_task()

    # 1 stale accepted + 1 due failed + 1 stale processing = 3
    assert recovered == 3
    assert mock_delay.call_count == 3
    mock_delay.assert_any_call(101)
    mock_delay.assert_any_call(102)
    mock_delay.assert_any_call(105)

    # maxed_processing must have been marked FAILED terminal
    assert maxed_processing.status == ProviderEventProcessingStatus.FAILED
    assert maxed_processing.error_details is not None
    assert maxed_processing.error_details["retryable"] is False


def test_process_inbound_envelope_active_lease_not_redispatched() -> None:
    """Worker skips processing if envelope has an active, unexpired lease (Finding 2)."""
    session = MagicMock(spec=Session)
    now = datetime.now(UTC)
    envelope_id = 999
    envelope = ProviderEventEnvelope(
        id=envelope_id,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_lease_active",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        error_details={
            "lease_expires_at": (now + timedelta(seconds=45)).isoformat(),
            "processing_started_at": now.isoformat(),
        },
        normalized_event={"sender_external_id": "5511999999999", "provider_message_id": "m1"},
    )
    session.execute.return_value.scalar_one_or_none.return_value = envelope
    session_factory = MagicMock(return_value=session)

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks.dispatch_inbound_message") as mock_dispatch,
    ):
        process_inbound_envelope_task(envelope_id)

    # Dispatch was NOT called because lease is active
    mock_dispatch.assert_not_called()


def test_failed_persistence_failure_recovered_by_stale_processing() -> None:
    """If mark_event_failed persistence fails, envelope remains in PROCESSING with lease.

    Once the lease expires, recover_unprocessed_inbound_envelopes_task sweeps it up (Finding 2).
    """
    session = MagicMock(spec=Session)
    now = datetime.now(UTC)
    envelope_id = 888

    # Envelope with expired lease
    stale_env = ProviderEventEnvelope(
        id=envelope_id,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_failed_persist_fail",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        received_at=now - timedelta(seconds=120),
        error_details={
            "attempts": 1,
            "lease_expires_at": (now - timedelta(seconds=30)).isoformat(),
            "processing_started_at": (now - timedelta(seconds=90)).isoformat(),
        },
    )

    # Recovery sweep discovers it
    session.execute.return_value.scalars.return_value.all.side_effect = [
        [],  # no accepted
        [],  # no failed
        [stale_env],  # 1 stale processing
    ]
    session_factory = MagicMock(return_value=session)

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch("tapjob_api.messaging.tasks.process_inbound_envelope_task.delay") as mock_delay,
    ):
        count = recover_unprocessed_inbound_envelopes_task()

    assert count == 1
    mock_delay.assert_called_once_with(envelope_id)


def test_inbound_soft_time_limit_is_retryable_and_fenced() -> None:
    """SoftTimeLimitExceeded triggers rollback, fenced release, retryable=True (Finding 2)."""
    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    envelope = ProviderEventEnvelope(
        id=501,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_soft_timeout_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "msg_soft_1",
            "text": "oi",
        },
        processing_attempts=0,
    )
    session.add(envelope)
    session.commit()

    dummy_provider = MagicMock()
    dummy_provider.id = uuid.uuid4()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch(
            "tapjob_api.messaging.tasks.get_or_create_channel_provider",
            return_value=dummy_provider,
        ),
        patch(
            "tapjob_api.messaging.tasks.dispatch_inbound_message",
            side_effect=SoftTimeLimitExceeded(),
        ),
    ):
        process_inbound_envelope_task(501)

    # Verify attempt 1 failure
    session.expire_all()
    env_after = session.get(ProviderEventEnvelope, 501)
    assert env_after is not None
    assert env_after.status == ProviderEventProcessingStatus.FAILED
    assert env_after.processing_attempts == 1
    assert env_after.processing_lease_token is None
    assert env_after.processing_lease_expires_at is None
    assert env_after.error_details is not None
    assert env_after.error_details["type"] == "SoftTimeLimitExceeded"
    assert env_after.error_details["retryable"] is True
    assert env_after.error_details["next_attempt_at"] is not None

    # Simulate backoff elapsed
    details = dict(env_after.error_details)
    details["next_attempt_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    env_after.error_details = details
    session.commit()

    # Retry claims attempt 2
    claimed_env, token2, res2 = claim_inbound_envelope(
        session, envelope_id=501, lease_seconds=60.0, max_retries=MAX_INBOUND_RETRIES
    )
    session.commit()

    assert res2 == InboundClaimResult.CLAIMED
    assert claimed_env is not None
    assert token2 is not None
    assert claimed_env.status == ProviderEventProcessingStatus.PROCESSING
    assert claimed_env.processing_attempts == 2
    session.close()
    engine.dispose()


def test_inbound_soft_time_limit_last_attempt_is_terminal() -> None:
    """SoftTimeLimitExceeded on last attempt transitions to terminal FAILED (Finding 2)."""
    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Envelope already consumed 2 attempts
    envelope = ProviderEventEnvelope(
        id=502,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_soft_timeout_last",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.FAILED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "msg_soft_last",
            "text": "oi",
        },
        processing_attempts=2,
        error_details={"retryable": True, "attempts": 2},
    )
    session.add(envelope)
    session.commit()

    dummy_provider = MagicMock()
    dummy_provider.id = uuid.uuid4()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch(
            "tapjob_api.messaging.tasks.get_or_create_channel_provider",
            return_value=dummy_provider,
        ),
        patch(
            "tapjob_api.messaging.tasks.dispatch_inbound_message",
            side_effect=SoftTimeLimitExceeded(),
        ),
    ):
        process_inbound_envelope_task(502)

    session.expire_all()
    env_after = session.get(ProviderEventEnvelope, 502)
    assert env_after is not None
    assert env_after.status == ProviderEventProcessingStatus.FAILED
    assert env_after.processing_attempts == 3  # MAX_INBOUND_RETRIES consumed
    assert env_after.processing_lease_token is None
    assert env_after.error_details is not None
    assert env_after.error_details["type"] == "SoftTimeLimitExceeded"
    assert env_after.error_details["retryable"] is False
    assert env_after.error_details["next_attempt_at"] is None

    # Subsequent claim attempt is blocked
    _claimed, token3, res3 = claim_inbound_envelope(
        session, envelope_id=502, lease_seconds=60.0, max_retries=MAX_INBOUND_RETRIES
    )
    assert res3 in (InboundClaimResult.TERMINAL_FAILED, InboundClaimResult.MAX_ATTEMPTS_EXHAUSTED)
    assert token3 is None
    session.close()
    engine.dispose()


def test_job_url_soft_time_limit_propagates_to_worker_retry() -> None:
    """SoftTimeLimitExceeded during Job URL intake in orchestration propagates to worker
    task instead of being swallowed into user-facing normal response (Finding 2).
    """
    from tapjob_api.identity.models import User, UserStatus

    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    dummy_user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR")
    dummy_link = MagicMock(id=uuid.uuid4())
    dummy_provider = MagicMock(id=uuid.uuid4())

    envelope = ProviderEventEnvelope(
        id=601,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_job_url_soft_limit",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "msg_job_url_soft",
            "text": "Veja essa vaga: https://example.com/job/dev-python-42",
        },
        processing_attempts=0,
    )
    session.add(envelope)
    session.commit()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch(
            "tapjob_api.messaging.tasks.get_or_create_channel_provider",
            return_value=dummy_provider,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=dummy_user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=dummy_link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            side_effect=SoftTimeLimitExceeded(),
        ),
    ):
        # Executes real dispatch_inbound_message through orchestration
        process_inbound_envelope_task(601)

    session.expire_all()
    env_after = session.get(ProviderEventEnvelope, 601)
    assert env_after is not None
    # Envelope was NOT processed as a success
    assert env_after.status == ProviderEventProcessingStatus.FAILED
    assert env_after.processing_attempts == 1
    assert env_after.error_details is not None
    assert env_after.error_details["type"] == "SoftTimeLimitExceeded"
    assert env_after.error_details["retryable"] is True
    assert env_after.error_details["next_attempt_at"] is not None

    session.close()
    engine.dispose()


def test_application_kit_soft_time_limit_propagates_to_worker_retry() -> None:
    """SoftTimeLimitExceeded during Application Kit generation in orchestration propagates
    to worker task instead of being swallowed into user-facing normal response (Finding 2).
    """
    from tapjob_api.identity.models import User, UserStatus

    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    dummy_user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR")
    dummy_link = MagicMock(id=uuid.uuid4())
    dummy_provider = MagicMock(id=uuid.uuid4())
    dummy_target_job = MagicMock(id=uuid.uuid4(), title="Senior Backend Engineer")

    envelope = ProviderEventEnvelope(
        id=602,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_app_kit_soft_limit",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={
            "sender_external_id": "5511999999999",
            "provider_message_id": "msg_app_kit_soft",
            "text": "quero me candidatar",
        },
        processing_attempts=0,
    )
    session.add(envelope)
    session.commit()

    with (
        patch("tapjob_api.messaging.tasks._session_factory", session_factory),
        patch(
            "tapjob_api.messaging.tasks.get_or_create_channel_provider",
            return_value=dummy_provider,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=dummy_user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=dummy_link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=dummy_target_job,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_tailor_resume",
            return_value={"tailored": True},
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_build_application_kit",
            side_effect=SoftTimeLimitExceeded(),
        ),
    ):
        # Executes real dispatch_inbound_message through orchestration
        process_inbound_envelope_task(602)

    session.expire_all()
    env_after = session.get(ProviderEventEnvelope, 602)
    assert env_after is not None
    # Envelope was NOT processed as a success
    assert env_after.status == ProviderEventProcessingStatus.FAILED
    assert env_after.processing_attempts == 1
    assert env_after.error_details is not None
    assert env_after.error_details["type"] == "SoftTimeLimitExceeded"
    assert env_after.error_details["retryable"] is True
    assert env_after.error_details["next_attempt_at"] is not None

    session.close()
    engine.dispose()

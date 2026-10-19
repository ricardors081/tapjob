"""Tests for outbox immediate dispatch latency, post-commit trigger, and fallback (Finding 2).

Verifies:
1. Immediate outbox dispatch triggered after database commit via after_commit hook
2. Broker outage fallback: broker connection failure does not roll back transaction or crash caller;
   the 5s Celery Beat sweep reliably claims and delivers pending messages.
3. Concurrent dispatch idempotency: multiple simultaneous executions do not duplicate sends.
4. Non-blocking error handling of trigger_immediate_outbox_dispatch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy as sa
from sqlalchemy import Table
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.messaging.outbox_service import (
    claim_and_dispatch_outbox_batch,
    queue_outbound_message,
    trigger_immediate_outbox_dispatch,
)
from tapjob_api.ports.messaging_channel import MessagingChannel, SendReceipt


def _create_in_memory_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine("sqlite:///:memory:")
    table = cast(Table, OutboundMessageQueue.__table__)
    table.c.id.type = sa.BigInteger().with_variant(sa.Integer, "sqlite")
    table.create(engine)
    return sessionmaker(bind=engine)


def test_immediate_outbox_dispatch_triggered_after_commit() -> None:
    """Proves that queue_outbound_message registers an after_commit hook that triggers
    dispatch_outbox_task.delay() only after the database transaction successfully commits.
    """
    factory = _create_in_memory_session_factory()
    session = factory()

    with patch("tapjob_api.messaging.tasks.dispatch_outbox_task.delay") as mock_delay:
        msg = queue_outbound_message(
            session,
            channel_identity_link_id="link-42",
            message_content={"recipient_external_id": "5511999990001", "text": "Immediate test"},
            idempotency_key=f"idem-latency-{uuid.uuid4()}",
        )
        assert msg is not None
        assert msg.status == OutboundMessageStatus.PENDING

        # Before commit, the background dispatch task must NOT have been queued yet
        mock_delay.assert_not_called()

        # Commit transaction
        session.commit()

        # After commit, immediate dispatch MUST have been invoked exactly once
        mock_delay.assert_called_once()

    session.close()


def test_broker_down_falls_back_to_polling_without_failing_commit() -> None:
    """Proves that if the Celery broker is unavailable at commit time, the commit succeeds
    cleanly without error, and subsequent polling (Celery Beat fallback) delivers the message.
    """
    factory = _create_in_memory_session_factory()
    session = factory()

    # Simulate Celery broker connection failure
    with patch(
        "tapjob_api.messaging.tasks.dispatch_outbox_task.delay",
        side_effect=ConnectionError("Redis broker is unreachable: [Errno 111] Connection refused"),
    ):
        msg = queue_outbound_message(
            session,
            channel_identity_link_id="link-broker-down",
            message_content={"recipient_external_id": "5511999990002", "text": "Broker fallback"},
            idempotency_key=f"idem-broker-fail-{uuid.uuid4()}",
        )
        # Commit MUST NOT raise despite broker failure
        session.commit()
        assert msg.status == OutboundMessageStatus.PENDING

    # Now verify the 5s Celery Beat sweep fallback:
    # A background worker runs claim_and_dispatch_outbox_batch and processes the pending message
    adapter = MagicMock(spec=MessagingChannel)
    now_utc = datetime.now(UTC)
    adapter.send = AsyncMock(
        return_value=SendReceipt(
            accepted_at=now_utc,
            provider_message_id="wamid.fallback.beat.123",
        )
    )

    dispatched = claim_and_dispatch_outbox_batch(
        session,
        provider_adapter=adapter,
        limit=10,
    )
    session.commit()

    assert len(dispatched) == 1
    assert dispatched[0].id == msg.id
    assert dispatched[0].status == OutboundMessageStatus.SENT
    assert dispatched[0].provider_message_id == "wamid.fallback.beat.123"

    session.close()


def test_trigger_immediate_outbox_dispatch_returns_true_on_success() -> None:
    """Proves trigger_immediate_outbox_dispatch returns True when Celery accepts the task."""
    with patch("tapjob_api.messaging.tasks.dispatch_outbox_task.delay") as mock_delay:
        res = trigger_immediate_outbox_dispatch()
        assert res is True
        mock_delay.assert_called_once()


def test_trigger_immediate_outbox_dispatch_catches_all_exceptions() -> None:
    """Proves trigger_immediate_outbox_dispatch safely catches any exception and returns False."""
    with patch(
        "tapjob_api.messaging.tasks.dispatch_outbox_task.delay",
        side_effect=RuntimeError("Unexpected Celery task queuing failure"),
    ):
        res = trigger_immediate_outbox_dispatch()
        assert res is False


def test_rollback_does_not_trigger_immediate_dispatch() -> None:
    """Proves that rolling back a transaction cancels the post-commit outbox dispatch."""
    factory = _create_in_memory_session_factory()
    session = factory()

    with patch("tapjob_api.messaging.tasks.dispatch_outbox_task.delay") as mock_delay:
        queue_outbound_message(
            session,
            channel_identity_link_id="link-rollback",
            message_content={"recipient_external_id": "5511999990003", "text": "Do not send"},
            idempotency_key=f"idem-rollback-{uuid.uuid4()}",
        )
        session.rollback()
        mock_delay.assert_not_called()

    session.close()


def test_nested_savepoint_does_not_trigger_dispatch_early() -> None:
    """Proves that committing a nested transaction/savepoint does NOT trigger outbox dispatch early;
    dispatch is triggered ONLY when the outer/root transaction commits.
    """
    factory = _create_in_memory_session_factory()
    session = factory()

    with patch("tapjob_api.messaging.tasks.dispatch_outbox_task.delay") as mock_delay:
        # Outer transaction is open
        with session.begin_nested():
            queue_outbound_message(
                session,
                channel_identity_link_id="link-savepoint",
                message_content={
                    "recipient_external_id": "5511999990004",
                    "text": "Savepoint test",
                },
                idempotency_key=f"idem-savepoint-{uuid.uuid4()}",
            )
        # Nested transaction committed, but outer root transaction is NOT committed yet
        mock_delay.assert_not_called()

        # Commit outer transaction
        session.commit()
        # Now immediate dispatch MUST be triggered exactly once
        mock_delay.assert_called_once()

    session.close()


def test_multiple_queued_messages_emit_exactly_one_dispatch_signal() -> None:
    """Proves that multiple messages queued within a single database transaction emit
    exactly one dispatch_outbox_task.delay() signal on commit (Finding 5).
    """
    factory = _create_in_memory_session_factory()
    session = factory()

    with patch("tapjob_api.messaging.tasks.dispatch_outbox_task.delay") as mock_delay:
        for i in range(3):
            queue_outbound_message(
                session,
                channel_identity_link_id=f"link-multi-{i}",
                message_content={
                    "recipient_external_id": f"551199999000{i}",
                    "text": f"Msg {i}",
                },
                idempotency_key=f"idem-multi-{i}-{uuid.uuid4()}",
            )

        mock_delay.assert_not_called()
        session.commit()
        # Exactly one acceleration signal for the entire transaction batch
        mock_delay.assert_called_once()

    session.close()


def test_duplicate_dispatch_execution_cannot_duplicate_provider_send() -> None:
    """Proves that duplicate execution of claim_and_dispatch_outbox_batch
    does not duplicate sends.
    """
    factory = _create_in_memory_session_factory()
    session = factory()

    queue_outbound_message(
        session,
        channel_identity_link_id="link-dup-dispatch",
        message_content={
            "recipient_external_id": "5511999990005",
            "text": "Idempotent dispatch",
        },
        idempotency_key=f"idem-dup-{uuid.uuid4()}",
    )
    session.commit()

    adapter = MagicMock(spec=MessagingChannel)
    now_utc = datetime.now(UTC)
    adapter.send = AsyncMock(
        return_value=SendReceipt(
            accepted_at=now_utc,
            provider_message_id="wamid.dup.123",
        )
    )

    # First dispatch run: claims and sends
    batch1 = claim_and_dispatch_outbox_batch(session, provider_adapter=adapter, limit=10)
    session.commit()
    assert len(batch1) == 1
    assert batch1[0].status == OutboundMessageStatus.SENT
    assert adapter.send.call_count == 1

    # Second concurrent / duplicate dispatch run: no messages should be claimed or sent
    batch2 = claim_and_dispatch_outbox_batch(session, provider_adapter=adapter, limit=10)
    session.commit()
    assert len(batch2) == 0
    # Provider adapter send MUST NOT be called again
    assert adapter.send.call_count == 1

    session.close()


def test_inbound_tasks_does_not_call_trigger_immediate_outbox_dispatch_directly() -> None:
    """Finding 5 regression guard: verifies process_inbound_envelope_task does NOT call
    trigger_immediate_outbox_dispatch directly, preserving the after_commit hook as the
    single canonical acceleration signal.
    """
    import inspect

    from tapjob_api.messaging import tasks

    source = inspect.getsource(tasks.process_inbound_envelope_task)
    assert "trigger_immediate_outbox_dispatch" not in source

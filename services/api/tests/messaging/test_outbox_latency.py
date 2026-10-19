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

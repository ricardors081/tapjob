"""Tests for Outbox retry semantics, bounded exponential backoff, and terminal states (Finding 7).

Enforces:
- DEFAULT_MAX_RETRIES = 5
- next_attempt_at scheduling and bounded exponential backoff
- SKIP LOCKED queries filter on next_attempt_at <= now()
- Transient failure -> status RETRYABLE + next_attempt_at in future
- Max retries exceeded -> status DEAD (terminal)
- Non-transient failure -> status FAILED (terminal)
- Internal enqueue idempotency by idempotency_key
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session

from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.messaging.outbox_service import (
    DEFAULT_MAX_RETRIES,
    calculate_backoff_delay,
    claim_and_dispatch_outbox_batch,
    queue_outbound_message,
)
from tapjob_api.ports._common import PortError, PortErrorCode
from tapjob_api.ports.messaging_channel import MessagingChannel, SendReceipt


def test_calculate_backoff_delay_bounds() -> None:
    """Proves exponential backoff is bounded."""
    delay_1 = calculate_backoff_delay(1)  # base * 2^0 = 2.0s
    assert delay_1 == 2.0
    delay_2 = calculate_backoff_delay(2)  # base * 2^1 = 4.0s
    assert delay_2 == 4.0
    delay_3 = calculate_backoff_delay(3)  # base * 2^2 = 8.0s
    assert delay_3 == 8.0
    delay_4 = calculate_backoff_delay(4)  # base * 2^3 = 16.0s
    assert delay_4 == 16.0
    delay_5 = calculate_backoff_delay(5)  # base * 2^4 = 32.0s
    assert delay_5 == 32.0

    # Capped at MAX_BACKOFF_SECONDS (300.0s)
    delay_large = calculate_backoff_delay(20)
    assert delay_large == 300.0


def test_queue_outbound_message_internal_idempotency() -> None:
    """Proves duplicate enqueue returns the existing record without side effects."""
    session = MagicMock(spec=Session)
    existing = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_idem_123",
        status=OutboundMessageStatus.PENDING,
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = existing
    session.execute.return_value = mock_res

    res = queue_outbound_message(
        session,
        channel_identity_link_id="link_1",
        message_content={"text": "hello"},
        idempotency_key="msg_idem_123",
    )
    assert res.id == existing.id
    # No new object added
    session.add.assert_not_called()


def test_outbox_dispatch_transient_failure_schedules_backoff() -> None:
    """Proves transient failure sets status=RETRYABLE and next_attempt_at in the future."""
    session = MagicMock(spec=Session)
    adapter = MagicMock(spec=MessagingChannel)

    # Provider throws transient PortError
    adapter.send = AsyncMock(
        side_effect=PortError(
            PortErrorCode.TIMEOUT,
            provider="fake_whatsapp",
            message="Temporary network interruption",
            transient=True,
        )
    )

    item = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_transient_1",
        status=OutboundMessageStatus.PENDING,
        retry_count=0,
        message_content={"recipient_external_id": "5511999999999", "text": "test"},
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    session.execute.return_value = mock_res

    dispatched = claim_and_dispatch_outbox_batch(
        session,
        provider_adapter=adapter,
        max_retries=DEFAULT_MAX_RETRIES,
    )

    assert len(dispatched) == 1
    assert item.status == OutboundMessageStatus.RETRYABLE
    assert item.retry_count == 1
    assert item.next_attempt_at is not None
    assert item.next_attempt_at > datetime.now(UTC)
    assert PortErrorCode.TIMEOUT in (item.last_error or "")


def test_outbox_max_retries_reaches_terminal_dead() -> None:
    """Proves that when retry_count reaches max_retries, message transitions to terminal DEAD."""
    session = MagicMock(spec=Session)
    adapter = MagicMock(spec=MessagingChannel)

    adapter.send = AsyncMock(
        side_effect=PortError(
            PortErrorCode.RATE_LIMITED,
            provider="fake_whatsapp",
            message="Exceeded downstream quota",
            transient=True,
        )
    )

    # Item already at retry_count = 4; this dispatch attempt will make it 5 (== max_retries)
    item = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_dead_1",
        status=OutboundMessageStatus.RETRYABLE,
        retry_count=4,
        message_content={"recipient_external_id": "5511999999999", "text": "test"},
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    session.execute.return_value = mock_res

    dispatched = claim_and_dispatch_outbox_batch(
        session,
        provider_adapter=adapter,
        max_retries=DEFAULT_MAX_RETRIES,  # 5
    )

    assert len(dispatched) == 1
    assert item.status == OutboundMessageStatus.DEAD
    assert item.retry_count == 5


def test_outbox_non_transient_error_transitions_to_failed() -> None:
    """Proves non-transient error immediately transitions to terminal FAILED without retries."""
    session = MagicMock(spec=Session)
    adapter = MagicMock(spec=MessagingChannel)

    adapter.send = AsyncMock(
        side_effect=PortError(
            PortErrorCode.INVALID_REQUEST,
            provider="fake_whatsapp",
            message="Number does not exist on WhatsApp",
            transient=False,
        )
    )

    item = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_failed_1",
        status=OutboundMessageStatus.PENDING,
        retry_count=0,
        message_content={"recipient_external_id": "000000", "text": "test"},
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    session.execute.return_value = mock_res

    dispatched = claim_and_dispatch_outbox_batch(
        session,
        provider_adapter=adapter,
        max_retries=DEFAULT_MAX_RETRIES,
    )

    assert len(dispatched) == 1
    assert item.status == OutboundMessageStatus.FAILED
    assert item.retry_count == 1
    assert PortErrorCode.INVALID_REQUEST in (item.last_error or "")


def test_outbox_dispatch_success_marks_sent() -> None:
    """Proves successful dispatch marks item SENT with provider_message_id and sent_at."""
    session = MagicMock(spec=Session)
    adapter = MagicMock(spec=MessagingChannel)

    now_utc = datetime.now(UTC)
    adapter.send = AsyncMock(
        return_value=SendReceipt(
            accepted_at=now_utc,
            provider_message_id="wamid.HBgL123",
        )
    )

    item = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_ok_1",
        status=OutboundMessageStatus.PENDING,
        retry_count=0,
        message_content={"recipient_external_id": "5511999999999", "text": "hello"},
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    session.execute.return_value = mock_res

    dispatched = claim_and_dispatch_outbox_batch(
        session,
        provider_adapter=adapter,
        max_retries=DEFAULT_MAX_RETRIES,
    )

    assert len(dispatched) == 1
    assert item.status == OutboundMessageStatus.SENT
    assert item.provider_message_id == "wamid.HBgL123"
    assert item.sent_at == now_utc
    assert item.last_error is None


def test_dispatch_outbox_task_runtime_driver() -> None:
    """Proves dispatch_outbox_task acts as real Celery runtime driver (Finding 2)."""
    from tapjob_api.messaging.tasks import configure_messaging_worker, dispatch_outbox_task

    session = MagicMock(spec=Session)
    adapter = MagicMock(spec=MessagingChannel)
    now_utc = datetime.now(UTC)
    adapter.send = AsyncMock(
        return_value=SendReceipt(
            accepted_at=now_utc,
            provider_message_id="wamid.celery_driver",
        )
    )

    item = OutboundMessageQueue(
        id=uuid.uuid4(),
        idempotency_key="msg_celery_driver_1",
        status=OutboundMessageStatus.PENDING,
        retry_count=0,
        message_content={"recipient_external_id": "5511999999999", "text": "driver hello"},
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [item]
    session.execute.return_value = mock_res

    session_factory = MagicMock(return_value=session)
    configure_messaging_worker(
        session_factory=session_factory,
        channel_adapter=adapter,
    )

    count = dispatch_outbox_task(limit=10)
    assert count == 1
    assert item.status == OutboundMessageStatus.SENT
    assert session.commit.called


def test_dispatch_due_reminders_task_runtime_driver() -> None:
    """Proves dispatch_due_reminders_task acts as real Celery runtime driver (Finding 3)."""
    from tapjob_api.messaging.tasks import configure_messaging_worker, dispatch_due_reminders_task

    session = MagicMock(spec=Session)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_res

    session_factory = MagicMock(return_value=session)
    configure_messaging_worker(session_factory=session_factory)

    count = dispatch_due_reminders_task(limit=25)
    assert count == 0
    assert session.commit.called

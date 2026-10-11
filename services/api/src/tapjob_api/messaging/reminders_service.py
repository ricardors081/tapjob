"""Channel-neutral Reminder service (Finding 11).

Provides persistent, idempotent reminder intents for follow-up and notification.
Dispatches due reminders directly into the durable outbox for delivery.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User
from tapjob_api.messaging.outbox_service import queue_outbound_message
from tapjob_api.messaging.reminders import Reminder, ReminderStatus

logger = logging.getLogger(__name__)


class ReminderError(Exception):
    """Raised when reminder operations fail."""


def create_reminder(
    session: Session,
    *,
    user: User,
    remind_at: datetime,
    intent: str,
    payload: dict[str, Any],
    channel_identity_link_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> Reminder:
    """Creates a persistent, channel-neutral reminder intent idempotently (Finding 3)."""
    # Auto-resolve active channel identity link if not provided
    if channel_identity_link_id is None:
        from tapjob_api.messaging.models import ChannelIdentityLink, ChannelIdentityLinkStatus

        link_stmt = (
            select(ChannelIdentityLink)
            .where(
                ChannelIdentityLink.user_id == user.id,
                ChannelIdentityLink.status != ChannelIdentityLinkStatus.BROKEN,
            )
            .order_by(
                (ChannelIdentityLink.status == ChannelIdentityLinkStatus.VERIFIED).desc(),
                ChannelIdentityLink.created_at.desc(),
            )
            .limit(1)
        )
        active_link = session.execute(link_stmt).scalar_one_or_none()
        if active_link is not None:
            channel_identity_link_id = active_link.id

    key = idempotency_key or f"rem_{user.id}_{intent}_{int(remind_at.timestamp())}"

    existing = session.execute(
        select(Reminder).where(Reminder.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    try:
        with session.begin_nested():
            reminder = Reminder(
                user_id=user.id,
                channel_identity_link_id=channel_identity_link_id,
                remind_at=remind_at,
                intent=intent,
                payload=payload,
                status=ReminderStatus.PENDING,
                idempotency_key=key,
            )
            session.add(reminder)
            session.flush()
            logger.info(
                "reminders.created",
                extra={
                    "reminder_id": str(reminder.id),
                    "user_id": str(user.id),
                    "intent": intent,
                    "idempotency_key": key,
                    "channel_identity_link_id": (
                        str(channel_identity_link_id) if channel_identity_link_id else None
                    ),
                },
            )
            return reminder
    except IntegrityError:
        found = session.execute(
            select(Reminder).where(Reminder.idempotency_key == key)
        ).scalar_one_or_none()
        if found is None:
            raise ReminderError(f"Failed to create reminder: {key}") from None
        return found


def dispatch_due_reminders(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> list[Reminder]:
    """Sweeps due reminders using SKIP LOCKED and enqueues notification messages into the outbox.

    Guarantees (Finding 3):
    - Derives recipient_external_id strictly from ChannelIdentityLink -> ChannelIdentity
    - FAIL CLOSED: If no usable identity exists, logs warning and does NOT enqueue empty recipient.
    """
    from tapjob_api.messaging.models import ChannelIdentityLink, ChannelIdentityLinkStatus

    as_of = now or datetime.now(UTC)

    stmt = (
        select(Reminder)
        .where(
            Reminder.status == ReminderStatus.PENDING,
            Reminder.remind_at <= as_of,
        )
        .order_by(Reminder.remind_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    due_reminders = list(session.execute(stmt).scalars().all())
    if not due_reminders:
        return []

    dispatched_reminders: list[Reminder] = []
    for item in due_reminders:
        # Resolve ChannelIdentityLink
        link: ChannelIdentityLink | None = None
        if item.channel_identity_link_id:
            link = session.get(ChannelIdentityLink, item.channel_identity_link_id)
        if link is None:
            # Fallback search for active link on user
            link_stmt = (
                select(ChannelIdentityLink)
                .where(
                    ChannelIdentityLink.user_id == item.user_id,
                    ChannelIdentityLink.status != ChannelIdentityLinkStatus.BROKEN,
                )
                .order_by(
                    (ChannelIdentityLink.status == ChannelIdentityLinkStatus.VERIFIED).desc(),
                    ChannelIdentityLink.created_at.desc(),
                )
                .limit(1)
            )
            link = session.execute(link_stmt).scalar_one_or_none()

        recipient_external = ""
        if link is not None and link.channel_identity is not None:
            recipient_external = link.channel_identity.external_id

        # FAIL CLOSED: Never enqueue empty recipient (Finding 3)
        if not recipient_external or link is None:
            logger.warning(
                "reminders.dispatch_failed_no_usable_identity",
                extra={
                    "reminder_id": str(item.id),
                    "user_id": str(item.user_id),
                    "intent": item.intent,
                },
            )
            continue

        msg_text = item.payload.get("message") or f"Lembrete TapJob: {item.intent}"
        outbox_key = f"out_rem_{item.idempotency_key}"
        link_id_str = str(link.id)

        queue_outbound_message(
            session,
            channel_identity_link_id=link_id_str,
            message_content={
                "text": msg_text,
                "recipient_external_id": recipient_external,
                "reminder_intent": item.intent,
            },
            idempotency_key=outbox_key,
        )

        item.status = ReminderStatus.DISPATCHED
        item.dispatched_at = as_of
        session.flush()

        logger.info(
            "reminders.dispatched",
            extra={
                "reminder_id": str(item.id),
                "user_id": str(item.user_id),
                "intent": item.intent,
                "channel_identity_link_id": link_id_str,
            },
        )
        dispatched_reminders.append(item)

    return dispatched_reminders

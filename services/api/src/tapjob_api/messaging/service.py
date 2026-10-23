"""Messaging service layer for channel identity management and linking (Finding 5).

Bridges the MessagingChannel port with business logic, enforcing:
- Case A: First-contact user creation -> VERIFIED link.
- Case B: Existing user linking -> PENDING link (requires verified proof).
- Account takeover guard: external identity cannot silently bind to multiple conflicting users.
- Concurrency safety with transactional savepoints; never leaking raw IntegrityError.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import CareerProfile, FactStatus, ProfileFact
from tapjob_api.identity.models import User
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.messaging.outbox import OutboundMessageQueue
from tapjob_api.messaging.outbox_service import queue_outbound_message

logger = logging.getLogger(__name__)


class MessagingServiceError(Exception):
    """Base error for messaging service operations."""


class IdentityAlreadyBoundError(MessagingServiceError):
    """Raised when an external identity is already bound to a conflicting user."""


def get_or_create_channel_provider(
    session: Session,
    *,
    channel: ChannelType,
    provider_name: str,
    provider_config_key: str,
) -> ChannelProvider:
    """Retrieves or creates a channel provider idempotently."""
    existing = session.execute(
        select(ChannelProvider).where(ChannelProvider.provider_config_key == provider_config_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    try:
        with session.begin_nested():
            provider = ChannelProvider(
                channel=channel,
                provider_name=provider_name,
                provider_config_key=provider_config_key,
                enabled=True,
            )
            session.add(provider)
            session.flush()
            return provider
    except IntegrityError:
        # Concurrent insert won race
        found = session.execute(
            select(ChannelProvider).where(
                ChannelProvider.provider_config_key == provider_config_key
            )
        ).scalar_one_or_none()
        if found is None:
            raise MessagingServiceError(
                f"Failed to resolve channel provider: {provider_config_key}"
            ) from None
        return found


def resolve_user_by_channel_identity(
    session: Session,
    *,
    provider_id: uuid.UUID,
    external_id: str,
) -> User | None:
    """Resolves an authenticated User from an active, verified channel identity.

    Only VERIFIED links are trusted for identity resolution.
    """
    stmt = (
        select(User)
        .join(ChannelIdentityLink, User.id == ChannelIdentityLink.user_id)
        .join(ChannelIdentity, ChannelIdentity.id == ChannelIdentityLink.channel_identity_id)
        .where(
            ChannelIdentity.provider_id == provider_id,
            ChannelIdentity.external_id == external_id,
            ChannelIdentityLink.status == ChannelIdentityLinkStatus.VERIFIED,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def resolve_link_by_channel_identity(
    session: Session,
    *,
    provider_id: uuid.UUID,
    external_id: str,
) -> ChannelIdentityLink | None:
    """Resolves the active channel identity link for an external identity."""
    stmt = (
        select(ChannelIdentityLink)
        .join(ChannelIdentity, ChannelIdentity.id == ChannelIdentityLink.channel_identity_id)
        .where(
            ChannelIdentity.provider_id == provider_id,
            ChannelIdentity.external_id == external_id,
            ChannelIdentityLink.status != ChannelIdentityLinkStatus.BROKEN,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def link_or_create_identity(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider_id: uuid.UUID,
    external_id: str,
    is_new_user: bool = False,
    verified_proof: bool = False,
) -> ChannelIdentityLink:
    """Links an external identity to a TapJob User with concurrency and takeover guards.

    Rules (Finding 4, Finding 5):
    - Case A: First-contact creates a brand new user (is_new_user=True) -> status=VERIFIED.
    - Case B: Existing user (is_new_user=False) -> status=PENDING unless verified_proof=True.
    - Invariant: A ChannelIdentity can have at most one active (non-broken) link.
      If linked to another user (PENDING or VERIFIED), reject with IdentityAlreadyBoundError.
    """
    # 1. Resolve or create ChannelIdentity row
    identity = session.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.provider_id == provider_id,
            ChannelIdentity.external_id == external_id,
        )
    ).scalar_one_or_none()

    if identity is None:
        try:
            with session.begin_nested():
                identity = ChannelIdentity(
                    provider_id=provider_id,
                    external_id=external_id,
                )
                session.add(identity)
                session.flush()
        except IntegrityError:
            identity = session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.provider_id == provider_id,
                    ChannelIdentity.external_id == external_id,
                )
            ).scalar_one_or_none()
            if identity is None:
                raise MessagingServiceError(
                    "Failed to resolve or create channel identity"
                ) from None

    # 2. Check if identity has an existing active (non-broken) link
    existing_link = session.execute(
        select(ChannelIdentityLink).where(
            ChannelIdentityLink.channel_identity_id == identity.id,
            ChannelIdentityLink.status != ChannelIdentityLinkStatus.BROKEN,
        )
    ).scalar_one_or_none()

    if existing_link is not None:
        if existing_link.user_id == user_id:
            # Same user: upgrade to verified if proof provided
            if verified_proof and existing_link.status != ChannelIdentityLinkStatus.VERIFIED:
                existing_link.status = ChannelIdentityLinkStatus.VERIFIED
                existing_link.verified_at = datetime.now(UTC)
                session.flush()
            return existing_link

        # Different user: account conflict guard
        logger.warning(
            "messaging.identity_conflict_rejected",
            extra={
                "channel_identity_id": str(identity.id),
                "target_user_id": str(user_id),
                "bound_user_id": str(existing_link.user_id),
                "status": existing_link.status.value,
            },
        )
        raise IdentityAlreadyBoundError(
            f"External identity is already bound to another account "
            f"(status: {existing_link.status.value})"
        )

    # 3. Create new link
    target_status = (
        ChannelIdentityLinkStatus.VERIFIED
        if (is_new_user or verified_proof)
        else ChannelIdentityLinkStatus.PENDING
    )
    verified_at = datetime.now(UTC) if target_status == ChannelIdentityLinkStatus.VERIFIED else None

    try:
        with session.begin_nested():
            link = ChannelIdentityLink(
                channel_identity_id=identity.id,
                user_id=user_id,
                status=target_status,
                verified_at=verified_at,
            )
            session.add(link)
            session.flush()
            return link
    except IntegrityError:
        # Concurrent link race - catch unique constraint violation
        found_link = session.execute(
            select(ChannelIdentityLink).where(
                ChannelIdentityLink.channel_identity_id == identity.id,
                ChannelIdentityLink.status != ChannelIdentityLinkStatus.BROKEN,
            )
        ).scalar_one_or_none()
        if found_link is not None:
            if found_link.user_id == user_id:
                return found_link
            raise IdentityAlreadyBoundError(
                "External identity was concurrently bound to another account"
            ) from None
        raise MessagingServiceError("Concurrent conflict while linking channel identity") from None


def get_user_profile_status(
    session: Session,
    *,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieves canonical profile existence and active fact count."""
    profile = session.execute(
        select(CareerProfile).where(CareerProfile.user_id == user_id)
    ).scalar_one_or_none()

    if profile is None:
        return {
            "has_profile": False,
            "profile_version": 0,
            "has_facts": False,
            "fact_count": 0,
            "headline": None,
            "summary": None,
            "target_roles": None,
        }

    fact_count = (
        session.execute(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        .scalars()
        .all()
    )

    active_count = len(fact_count)
    return {
        "has_profile": True,
        "profile_version": profile.profile_version,
        "has_facts": active_count > 0,
        "fact_count": active_count,
        "headline": profile.headline,
        "summary": profile.summary,
        "target_roles": profile.target_roles,
    }


def queue_response_message(
    session: Session,
    *,
    channel_identity_link_id: uuid.UUID | None,
    recipient_external_id: str,
    message_text: str,
    idempotency_key: str | None = None,
) -> OutboundMessageQueue:
    """Queues an outbound response message into the durable outbox."""
    key = idempotency_key or f"resp_{uuid.uuid4()}"
    content = {
        "text": message_text,
        "recipient_external_id": recipient_external_id,
    }
    link_id_str = str(channel_identity_link_id) if channel_identity_link_id else None
    return queue_outbound_message(
        session,
        channel_identity_link_id=link_id_str,
        message_content=content,
        idempotency_key=key,
    )

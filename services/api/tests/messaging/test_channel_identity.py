"""Tests for ChannelIdentity single active owner invariant (Finding 4).

Enforces:
- A ChannelIdentity can only have at most ONE non-broken link at any time
- Database partial unique index uq_channel_identity_links_single_active
- Service operations:
  - same user -> idempotent
  - same user + verified_proof -> status updated to VERIFIED
  - other user PENDING -> raises IdentityAlreadyBoundError (conflict)
  - other user VERIFIED -> raises IdentityAlreadyBoundError (conflict)
  - BROKEN link -> allows relinking
- Concurrent link attempts for different users serialize or fail cleanly
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.messaging.service import (
    IdentityAlreadyBoundError,
    link_or_create_identity,
)


def test_link_or_create_identity_same_user_idempotent() -> None:
    """Proves linking the same user twice is idempotent."""
    session = MagicMock(spec=Session)
    user_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    external_id = "5511999999999"

    identity = ChannelIdentity(
        id=identity_id,
        provider_id=provider_id,
        external_id=external_id,
    )
    existing_link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=identity_id,
        user_id=user_id,
        status=ChannelIdentityLinkStatus.PENDING,
    )

    # Mock DB query results
    mock_identity_res = MagicMock()
    mock_identity_res.scalar_one_or_none.return_value = identity

    mock_link_res = MagicMock()
    mock_link_res.scalar_one_or_none.return_value = existing_link

    session.execute.side_effect = [mock_identity_res, mock_link_res]

    returned_link = link_or_create_identity(
        session,
        user_id=user_id,
        provider_id=provider_id,
        external_id=external_id,
        verified_proof=False,
    )
    assert returned_link.id == existing_link.id
    assert returned_link.status == ChannelIdentityLinkStatus.PENDING


def test_link_or_create_identity_same_user_verified_upgrade() -> None:
    """Proves linking the same user with verified_proof upgrades PENDING link to VERIFIED."""
    session = MagicMock(spec=Session)
    user_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    external_id = "5511999999999"

    identity = ChannelIdentity(
        id=identity_id,
        provider_id=provider_id,
        external_id=external_id,
    )
    existing_link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=identity_id,
        user_id=user_id,
        status=ChannelIdentityLinkStatus.PENDING,
    )

    mock_identity_res = MagicMock()
    mock_identity_res.scalar_one_or_none.return_value = identity

    mock_link_res = MagicMock()
    mock_link_res.scalar_one_or_none.return_value = existing_link

    session.execute.side_effect = [mock_identity_res, mock_link_res]

    returned_link = link_or_create_identity(
        session,
        user_id=user_id,
        provider_id=provider_id,
        external_id=external_id,
        verified_proof=True,
    )
    assert returned_link.id == existing_link.id
    assert returned_link.status == ChannelIdentityLinkStatus.VERIFIED
    assert returned_link.verified_at is not None


def test_link_or_create_identity_other_user_pending_conflict() -> None:
    """Proves attempting to link an identity with an active PENDING link of another user
    raises conflict.
    """
    session = MagicMock(spec=Session)
    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    external_id = "5511999999999"

    identity = ChannelIdentity(
        id=identity_id,
        provider_id=provider_id,
        external_id=external_id,
    )
    user_a_link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=identity_id,
        user_id=user_a_id,
        status=ChannelIdentityLinkStatus.PENDING,
    )

    mock_identity_res = MagicMock()
    mock_identity_res.scalar_one_or_none.return_value = identity

    mock_link_res = MagicMock()
    mock_link_res.scalar_one_or_none.return_value = user_a_link

    session.execute.side_effect = [mock_identity_res, mock_link_res]

    with pytest.raises(IdentityAlreadyBoundError) as exc_info:
        link_or_create_identity(
            session,
            user_id=user_b_id,
            provider_id=provider_id,
            external_id=external_id,
            verified_proof=False,
        )
    assert "already bound" in str(exc_info.value)


def test_link_or_create_identity_other_user_verified_conflict() -> None:
    """Proves attempting to link an identity with an active VERIFIED link of another user
    raises conflict.
    """
    session = MagicMock(spec=Session)
    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    external_id = "5511999999999"

    identity = ChannelIdentity(
        id=identity_id,
        provider_id=provider_id,
        external_id=external_id,
    )
    user_a_link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=identity_id,
        user_id=user_a_id,
        status=ChannelIdentityLinkStatus.VERIFIED,
    )

    mock_identity_res = MagicMock()
    mock_identity_res.scalar_one_or_none.return_value = identity

    mock_link_res = MagicMock()
    mock_link_res.scalar_one_or_none.return_value = user_a_link

    session.execute.side_effect = [mock_identity_res, mock_link_res]

    with pytest.raises(IdentityAlreadyBoundError) as exc_info:
        link_or_create_identity(
            session,
            user_id=user_b_id,
            provider_id=provider_id,
            external_id=external_id,
            verified_proof=True,
        )
    assert "already bound" in str(exc_info.value)


@pytest.mark.integration
def test_channel_identity_single_active_owner_postgres_invariant(
    _identity_schema_ready: None,
) -> None:
    """Proves at the PostgreSQL level:
    - Partial unique index enforces single active link
    - Inserting second active link violates uq_channel_identity_links_single_active
    - Broken link allows new link creation
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        user_a = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        user_b = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add_all([user_a, user_b])

        provider = ChannelProvider(
            id=uuid.uuid4(),
            channel=ChannelType.WHATSAPP,
            provider_name="evolution_test",
            provider_config_key=f"cfg_{uuid.uuid4().hex[:12]}",
            enabled=True,
        )
        session.add(provider)
        session.commit()

        external_id = f"551199{uuid.uuid4().hex[:7]}"
        identity = ChannelIdentity(
            id=uuid.uuid4(),
            provider_id=provider.id,
            external_id=external_id,
        )
        session.add(identity)
        session.commit()

        # Link 1 for User A (PENDING)
        link_a = ChannelIdentityLink(
            id=uuid.uuid4(),
            channel_identity_id=identity.id,
            user_id=user_a.id,
            status=ChannelIdentityLinkStatus.PENDING,
        )
        session.add(link_a)
        session.commit()

        # Service level: user B attempting to link raises IdentityAlreadyBoundError
        with pytest.raises(IdentityAlreadyBoundError):
            link_or_create_identity(
                session,
                user_id=user_b.id,
                provider_id=provider.id,
                external_id=external_id,
            )

        # Database level: attempting to insert a second active link fails partial unique constraint
        link_b_conflict = ChannelIdentityLink(
            id=uuid.uuid4(),
            channel_identity_id=identity.id,
            user_id=user_b.id,
            status=ChannelIdentityLinkStatus.PENDING,
        )
        session.add(link_b_conflict)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # Now mark Link A as BROKEN
        link_a_row = session.get(ChannelIdentityLink, link_a.id)
        assert link_a_row is not None
        link_a_row.status = ChannelIdentityLinkStatus.BROKEN
        session.commit()

        # Now User B can link successfully (Link A is broken, so no active link exists)
        link_b = link_or_create_identity(
            session,
            user_id=user_b.id,
            provider_id=provider.id,
            external_id=external_id,
            is_new_user=False,
            verified_proof=True,
        )
        session.commit()
        assert link_b.user_id == user_b.id
        assert link_b.status == ChannelIdentityLinkStatus.VERIFIED
    finally:
        session.close()
        engine.dispose()

"""Tests for webhook endpoint (Finding 3).

Verifies:
- Route exists
- Unauthorized rejected (401)
- Malformed rejected (400)
- Oversized rejected (413)
- Unsupported content-type rejected (415)
- Valid accepted (202)
- Idempotency / deduplication is safe no-op
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import CareerProfile
from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.db.session import get_db_session
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.main import create_app
from tapjob_api.messaging.models import (
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.resume_import.storage import InMemoryObjectStorage


@pytest.fixture
def mock_db_session() -> MagicMock:
    session = MagicMock(spec=Session)
    provider = ChannelProvider(
        id=uuid.uuid4(),
        channel=ChannelType.WHATSAPP,
        provider_name="evolution",
        provider_config_key="evolution_tapjob",
    )
    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=uuid.uuid4(),
        user_id=user.id,
        status=ChannelIdentityLinkStatus.VERIFIED,
    )
    profile = CareerProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        profile_version=1,
        headline="Desenvolvedor",
        summary="Resumo profissional",
        target_roles=["Engenheiro de Software"],
    )

    def execute_handler(statement: Any, *args: Any, **kwargs: Any) -> MagicMock:
        stmt_str = str(statement)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        if "channel_providers" in stmt_str:
            mock_result.scalar_one_or_none.return_value = provider
        elif "channel_identity_links" in stmt_str:
            mock_result.scalar_one_or_none.return_value = link
        elif "users" in stmt_str:
            mock_result.scalar_one_or_none.return_value = user
        elif "career_profiles" in stmt_str:
            mock_result.scalar_one_or_none.return_value = profile
            mock_result.scalar.return_value = 1
        elif "profile_facts" in stmt_str:
            mock_result.scalars.return_value.all.return_value = []
        elif "provider_event_envelopes" in stmt_str:
            mock_result.scalar_one_or_none.return_value = None
        else:
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    session.execute.side_effect = execute_handler
    session.scalar.side_effect = lambda statement, *args, **kwargs: (
        profile if "career_profiles" in str(statement) else None
    )
    return session


@pytest.fixture
def client(database_url: str, redis_url: str, mock_db_session: MagicMock) -> Iterator[TestClient]:
    settings = ApiSettings(
        app_env=Environment.LOCAL,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
        whatsapp_webhook_secret="test-webhook-secret",
        whatsapp_webhook_enabled=True,
    )
    app = create_app(settings, resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(
    database_url: str, redis_url: str, mock_db_session: MagicMock
) -> Iterator[TestClient]:
    settings = ApiSettings(
        app_env=Environment.LOCAL,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
        whatsapp_webhook_secret="super-secret-token",
        whatsapp_webhook_enabled=True,
    )
    app = create_app(settings, resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    with TestClient(app) as test_client:
        yield test_client


def test_whatsapp_webhook_disabled_fails_closed_404(
    database_url: str, redis_url: str, mock_db_session: MagicMock
) -> None:
    """When whatsapp_webhook_enabled is False, endpoint returns 404 fail-closed even with secret
    (Finding 7).
    """
    settings = ApiSettings(
        app_env=Environment.LOCAL,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
        whatsapp_webhook_secret="super-secret-token",
        whatsapp_webhook_enabled=False,
    )
    app = create_app(settings, resume_storage_override=InMemoryObjectStorage())
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    with TestClient(app) as test_client:
        response = test_client.post(
            "/webhooks/whatsapp",
            headers={"X-Webhook-Secret": "super-secret-token"},
            json={"messages": []},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_whatsapp_webhook_route_exists(client: TestClient) -> None:
    """Webhook route exists and does not return 404 when enabled."""
    response = client.post(
        "/webhooks/whatsapp",
        json={"messages": []},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert response.status_code != 404


def test_whatsapp_webhook_accepts_valid_json(client: TestClient) -> None:
    """Valid JSON webhook returns 202 Accepted."""
    payload = {
        "messages": [
            {
                "from": "5511999999999",
                "id": "msg_id_123",
                "timestamp": 1729518000,
                "text": {"body": "Hello"},
            }
        ]
    }
    response = client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_whatsapp_webhook_rejects_invalid_content_type(client: TestClient) -> None:
    """Non-JSON content-type returns 415."""
    response = client.post(
        "/webhooks/whatsapp",
        content=b"not json",
        headers={
            "content-type": "text/plain",
            "X-Webhook-Secret": "test-webhook-secret",
        },
    )
    assert response.status_code == 415


def test_whatsapp_webhook_rejects_oversized_payload(client: TestClient) -> None:
    """Payload > 1 MiB returns 413."""
    oversized = b"x" * (1024 * 1024 + 1)
    response = client.post(
        "/webhooks/whatsapp",
        content=oversized,
        headers={
            "content-type": "application/json",
            "X-Webhook-Secret": "test-webhook-secret",
        },
    )
    assert response.status_code == 413


def test_whatsapp_webhook_rejects_invalid_json(client: TestClient) -> None:
    """Malformed JSON returns 400."""
    response = client.post(
        "/webhooks/whatsapp",
        content=b"{not valid json}",
        headers={
            "content-type": "application/json",
            "X-Webhook-Secret": "test-webhook-secret",
        },
    )
    assert response.status_code == 400


def test_whatsapp_webhook_rejects_unauthorized(auth_client: TestClient) -> None:
    """When webhook secret is configured, unauthorized requests return 401."""
    payload: dict[str, Any] = {"messages": []}

    # No secret header
    response = auth_client.post("/webhooks/whatsapp", json=payload)
    assert response.status_code == 401

    # Wrong secret
    response = auth_client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Webhook-Secret": "wrong-secret"},
    )
    assert response.status_code == 401

    # Secret in query parameter must be REJECTED (query auth removed)
    response = auth_client.post(
        "/webhooks/whatsapp?secret=super-secret-token",
        json=payload,
    )
    assert response.status_code == 401

    # Correct secret in header
    response = auth_client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Webhook-Secret": "super-secret-token"},
    )
    assert response.status_code == 202


def test_whatsapp_webhook_does_not_execute_orchestration_inline(client: TestClient) -> None:
    """Finding 2: Ingress route must be fast-ack and NEVER execute orchestration inline."""
    from unittest.mock import patch

    payload = {
        "messages": [
            {
                "from": "5511999998888",
                "id": "msg_async_001",
                "timestamp": 1729518000,
                "text": {"body": "Teste async"},
            }
        ]
    }

    with (
        patch("tapjob_api.messaging.orchestration.dispatch_inbound_message") as mock_dispatch,
        patch("tapjob_api.http.webhooks.process_inbound_envelope_task") as mock_task,
    ):
        response = client.post(
            "/webhooks/whatsapp",
            json=payload,
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert response.status_code == 202
        # Orchestration MUST NOT be called inline during the request
        assert mock_dispatch.call_count == 0
        # Background task MUST be enqueued
        assert mock_task.delay.call_count == 1


def test_whatsapp_webhook_deduplication_safe_noop(client: TestClient) -> None:
    """Duplicate webhook payload is acknowledged 202 safely without error."""
    payload = {
        "messages": [
            {
                "from": "5511999998888",
                "id": "msg_dedup_001",
                "timestamp": 1729518000,
                "text": {"body": "Primeiro envio"},
            }
        ]
    }
    resp1 = client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert resp1.status_code == 202

    resp2 = client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert resp2.status_code == 202
    assert resp2.json()["status"] == "accepted"


def test_whatsapp_webhook_commits_before_enqueue(
    client: TestClient, mock_db_session: MagicMock
) -> None:
    """Finding 1: Database transaction MUST commit before Celery task is enqueued."""
    from unittest.mock import patch

    payload = {
        "messages": [
            {
                "from": "5511999998888",
                "id": "msg_order_001",
                "timestamp": 1729518000,
                "text": {"body": "Test commit order"},
            }
        ]
    }

    call_order: list[str] = []
    mock_db_session.commit.side_effect = lambda: call_order.append("commit")

    with patch("tapjob_api.http.webhooks.process_inbound_envelope_task") as mock_task:
        mock_task.delay.side_effect = lambda *args: call_order.append("delay")

        response = client.post(
            "/webhooks/whatsapp",
            json=payload,
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert response.status_code == 202
        assert call_order == ["commit", "delay"]


def test_whatsapp_webhook_broker_failure_retains_envelope(
    client: TestClient, mock_db_session: MagicMock
) -> None:
    """Finding 1: Broker enqueue failure does NOT rollback committed ACCEPTED envelope."""
    from unittest.mock import patch

    payload = {
        "messages": [
            {
                "from": "5511999998888",
                "id": "msg_broker_fail_001",
                "timestamp": 1729518000,
                "text": {"body": "Broker down"},
            }
        ]
    }

    with patch("tapjob_api.http.webhooks.process_inbound_envelope_task") as mock_task:
        mock_task.delay.side_effect = RuntimeError("Broker connection timeout")

        response = client.post(
            "/webhooks/whatsapp",
            json=payload,
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        # Fast 202 still returned to provider
        assert response.status_code == 202
        # Database commit happened
        assert mock_db_session.commit.called


def test_recover_unprocessed_inbound_envelopes_sweep() -> None:
    """Finding 1: Sweep task finds stale ACCEPTED envelopes and re-dispatches them."""
    from unittest.mock import MagicMock, patch

    from tapjob_api.messaging.inbound import ProviderEventEnvelope, ProviderEventProcessingStatus
    from tapjob_api.messaging.tasks import (
        configure_messaging_worker,
        recover_unprocessed_inbound_envelopes_task,
    )

    envelope = ProviderEventEnvelope(
        id=99,
        provider="evolution",
        provider_instance="tapjob",
        external_event_id="stale_001",
        status=ProviderEventProcessingStatus.ACCEPTED,
    )
    session = MagicMock(spec=Session)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [envelope]
    session.execute.return_value = mock_res
    session_factory = MagicMock(return_value=session)

    configure_messaging_worker(session_factory=session_factory)

    with patch("tapjob_api.messaging.tasks.process_inbound_envelope_task") as mock_task:
        dispatched = recover_unprocessed_inbound_envelopes_task(stale_seconds=0.0)
        assert dispatched == 1
        mock_task.delay.assert_called_once_with(99)

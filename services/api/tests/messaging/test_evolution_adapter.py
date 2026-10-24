"""Tests for Evolution API adapter (Finding 4)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tapjob_api.messaging.evolution_adapter import (
    EvolutionAdapter,
    normalize_provider_int64,
    resolve_canonical_sender,
)
from tapjob_api.ports._common import (
    AdapterHealthStatus,
    CallContext,
    Deadline,
    PortError,
    PortErrorCode,
)
from tapjob_api.ports.capabilities import CapabilityStatus
from tapjob_api.ports.messaging_channel import ChannelIdentity, MessagingCapability, OutboundMessage


@pytest.fixture
def test_ctx() -> CallContext:
    """Create a test call context."""
    return CallContext(
        correlation_id="test-correlation-123",
        deadline=Deadline.after(30),
    )


def test_parse_inbound_text_message(test_ctx: CallContext) -> None:
    """Parse a simple text message from Evolution webhook."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "messages": [
                {
                    "from": "5511987654321",
                    "id": "msg_waid_123abc",
                    "timestamp": 1729520000,
                    "text": {"body": "oi"},
                }
            ]
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert messages[0].sender.external_id == "5511987654321"
        assert messages[0].provider_message_id == "msg_waid_123abc"
        assert messages[0].text == "oi"
        assert messages[0].interactive_reply_id is None

    asyncio.run(scenario())


def test_parse_inbound_interactive_button_reply(test_ctx: CallContext) -> None:
    """Parse an interactive button reply."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "messages": [
                {
                    "from": "5511987654321",
                    "id": "msg_waid_456def",
                    "timestamp": 1729520100,
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": "btn_confirm", "title": "Confirm"},
                    },
                }
            ]
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert messages[0].interactive_reply_id == "btn_confirm"
        assert messages[0].text is None

    asyncio.run(scenario())


def test_parse_inbound_empty_messages(test_ctx: CallContext) -> None:
    """Handle empty messages array."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload: dict[str, Any] = {"messages": []}
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 0

    asyncio.run(scenario())


def test_parse_inbound_invalid_json(test_ctx: CallContext) -> None:
    """Reject invalid JSON payload."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        raw_body = b"not valid json"
        with pytest.raises(PortError) as exc_info:
            await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_parse_inbound_missing_required_fields(test_ctx: CallContext) -> None:
    """Skip messages with missing required fields."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "messages": [
                {
                    "from": "5511987654321",
                    "text": {"body": "test"},
                },
                {
                    "from": "5511987654322",
                    "id": "msg_waid_valid",
                    "timestamp": 1729520000,
                    "text": {"body": "valid"},
                },
            ]
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert messages[0].sender.external_id == "5511987654322"

    asyncio.run(scenario())


def test_send_message_success(test_ctx: CallContext) -> None:
    """Send an outbound message successfully using mocked HTTP transport."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["apikey"] == "test_key_123"
            return httpx.Response(200, json={"key": {"id": "msg_evolution_999"}})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
            http_client=mock_client,
        )
        message = OutboundMessage(
            recipient=ChannelIdentity(channel="whatsapp", external_id="5511987654321"),
            idempotency_key="idem_send_123",
            text="Hello, this is a test message",
        )

        receipt = await adapter.send(message, test_ctx)

        assert receipt.provider_message_id == "msg_evolution_999"
        assert receipt.accepted_at is not None
        assert (datetime.now(tz=UTC) - receipt.accepted_at).total_seconds() < 5

    asyncio.run(scenario())


def test_send_message_auth_failed(test_ctx: CallContext) -> None:
    """Send an outbound message rejected due to invalid api key."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Unauthorized"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="bad_key",
            instance_name="test_instance",
            http_client=mock_client,
        )
        message = OutboundMessage(
            recipient=ChannelIdentity(channel="whatsapp", external_id="5511987654321"),
            idempotency_key="idem_send_bad",
            text="Test message",
        )

        with pytest.raises(PortError) as exc_info:
            await adapter.send(message, test_ctx)

        assert exc_info.value.code == PortErrorCode.AUTH_FAILED
        assert exc_info.value.transient is False

    asyncio.run(scenario())


def test_capabilities(test_ctx: CallContext) -> None:
    """Report adapter capabilities."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        manifest = await adapter.capabilities(test_ctx)

        assert manifest.status_for(MessagingCapability.TEXT) == CapabilityStatus.SUPPORTED
        assert manifest.status_for(MessagingCapability.DOCUMENT) == CapabilityStatus.UNSUPPORTED
        assert (
            manifest.status_for(MessagingCapability.INTERACTIVE_BUTTONS)
            == CapabilityStatus.UNSUPPORTED
        )
        assert (
            manifest.status_for(MessagingCapability.FREEFORM_OUTSIDE_SESSION)
            == CapabilityStatus.SUPPORTED
        )

    asyncio.run(scenario())


def test_health_healthy(test_ctx: CallContext) -> None:
    """Check adapter health returns HEALTHY when instance state is open."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"instance": {"state": "open"}})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
            http_client=mock_client,
        )
        health = await adapter.health(test_ctx)

        assert health.status == AdapterHealthStatus.HEALTHY
        assert health.checked_at is not None

    asyncio.run(scenario())


def test_parse_inbound_document_message(test_ctx: CallContext) -> None:
    """Parse document message from Evolution webhook into InboundAttachment (Finding 4)."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "messages": [
                {
                    "from": "5511987654321",
                    "id": "msg_doc_123abc",
                    "timestamp": 1729520200,
                    "message": {
                        "documentMessage": {
                            "fileName": "curriculo_candidato.pdf",
                            "mimetype": "application/pdf",
                            "fileLength": 10240,
                            "mediaKey": "media_key_abc123",
                            "caption": "Segue meu currículo atualizado",
                        }
                    },
                }
            ]
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        msg = messages[0]
        assert msg.sender.external_id == "5511987654321"
        assert msg.provider_message_id == "msg_doc_123abc"
        assert msg.text == "Segue meu currículo atualizado"
        assert len(msg.attachments) == 1
        att = msg.attachments[0]
        assert att.kind == "document"
        assert att.filename == "curriculo_candidato.pdf"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 10240
        # Prompt Req 3: provider_media_id must be the message ID, not the cryptographic mediaKey
        assert att.provider_media_id == "msg_doc_123abc"

    asyncio.run(scenario())


def test_retrieve_media_success(test_ctx: CallContext) -> None:
    """Retrieve media bytes securely via authenticated Evolution endpoint (Finding 4)."""
    import base64

    from tapjob_api.ports.messaging_channel import InboundAttachment

    async def scenario() -> None:
        raw_pdf = b"%PDF-1.4 test resume bytes"
        b64_pdf = base64.b64encode(raw_pdf).decode("ascii")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("apikey") == "test_key_123"
            assert "/chat/getBase64FromMediaMessage/test_instance" in str(request.url)
            return httpx.Response(200, json={"base64": b64_pdf})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
            http_client=mock_client,
        )
        attachment = InboundAttachment(
            provider_media_id="media_123",
            mime_type="application/pdf",
            filename="curriculo.pdf",
        )
        media_bytes = await adapter.retrieve_media(attachment, test_ctx, message_id="msg_123")
        assert media_bytes == raw_pdf

    asyncio.run(scenario())


def test_retrieve_media_unsupported_mime_rejected(test_ctx: CallContext) -> None:
    """Reject document media retrieval for unapproved MIME types."""
    from tapjob_api.ports.messaging_channel import InboundAttachment

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        attachment = InboundAttachment(
            provider_media_id="media_123",
            mime_type="image/jpeg",
        )
        with pytest.raises(PortError) as exc_info:
            await adapter.retrieve_media(attachment, test_ctx)

        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "Unsupported document MIME type" in exc_info.value.message

    asyncio.run(scenario())


def test_parse_inbound_from_me_true_ignored(test_ctx: CallContext) -> None:
    """Outbound echoes with key.fromMe=True are ignored (Finding 7)."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": "5511987654321@s.whatsapp.net",
                    "fromMe": True,
                    "id": "echo_msg_id_123",
                },
                "message": {"conversation": "Mensagem enviada pelo bot"},
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 0

    asyncio.run(scenario())


def test_parse_inbound_from_me_false_processed(test_ctx: CallContext) -> None:
    """Candidate messages with key.fromMe=False are normalized normally (Finding 7)."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": "5511987654321@s.whatsapp.net",
                    "fromMe": False,
                    "id": "candidate_msg_id_123",
                },
                "message": {"conversation": "olá, quero me candidatar"},
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert messages[0].sender.external_id == "5511987654321"
        assert messages[0].provider_message_id == "candidate_msg_id_123"
        assert messages[0].text == "olá, quero me candidatar"

    asyncio.run(scenario())


def test_parse_inbound_missing_from_me_processed(test_ctx: CallContext) -> None:
    """Inbound messages without explicit fromMe field default to normal candidate processing."""

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": "5511987654321@s.whatsapp.net",
                    "id": "legacy_msg_id_123",
                },
                "message": {"conversation": "mensagem sem campo fromMe"},
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert messages[0].sender.external_id == "5511987654321"
        assert messages[0].provider_message_id == "legacy_msg_id_123"
        assert messages[0].text == "mensagem sem campo fromMe"

    asyncio.run(scenario())


# ==============================================================================
# PDF Live Failure Forensic Tests: Protobuf Long, LID Addressing, Ingress Hardening
# ==============================================================================


def test_normalize_provider_int64_valid() -> None:
    """Verifies normalize_provider_int64 accepts int, str, and protobuf Long representations."""
    # Plain integers
    assert normalize_provider_int64(245434) == 245434
    assert normalize_provider_int64(0) == 0

    # Numeric strings
    assert normalize_provider_int64("245434") == 245434
    assert normalize_provider_int64("0") == 0

    # Protobuf Long from real Human Acceptance payload: {"low": 245434, "high": 0, "unsigned": True}
    real_long = {"low": 245434, "high": 0, "unsigned": True}
    assert normalize_provider_int64(real_long) == 245434

    # Protobuf Long signed positive
    assert normalize_provider_int64({"low": 245434, "high": 0}) == 245434

    # None returns None
    assert normalize_provider_int64(None) is None

    # Low signed negative with unsigned=True (unsigned 32-bit wrap)
    # e.g., low=-10 -> (-10 & 0xFFFFFFFF) == 4294967286
    assert normalize_provider_int64({"low": -10, "high": 0, "unsigned": True}) == 4294967286

    # High non-zero (64-bit value)
    assert normalize_provider_int64({"low": 100, "high": 2, "unsigned": True}) == (2 << 32) + 100


def test_normalize_provider_int64_invalid() -> None:
    """Verifies normalize_provider_int64 rejects invalid representations with ValueError."""
    # Booleans are not valid ints
    with pytest.raises(ValueError, match="boolean is not a valid provider int64"):
        normalize_provider_int64(True)
    with pytest.raises(ValueError, match="boolean is not a valid provider int64"):
        normalize_provider_int64(False)

    # Negative integer
    with pytest.raises(ValueError, match="negative media size"):
        normalize_provider_int64(-1)

    # Negative string
    with pytest.raises(ValueError, match="negative media size"):
        normalize_provider_int64("-500")

    # Non-numeric string
    with pytest.raises(ValueError, match="invalid numeric string"):
        normalize_provider_int64("not_a_number")

    # Protobuf Long signed negative result
    with pytest.raises(ValueError, match="negative media size"):
        normalize_provider_int64({"low": -1, "high": -1, "unsigned": False})

    # Missing low or high in dict
    with pytest.raises(ValueError, match="invalid protobuf Long representation"):
        normalize_provider_int64({"low": 100})
    with pytest.raises(ValueError, match="invalid protobuf Long representation"):
        normalize_provider_int64({"high": 100})

    # Non-int component in protobuf Long
    with pytest.raises(ValueError, match="boolean is not a valid protobuf Long component"):
        normalize_provider_int64({"low": True, "high": 0})
    with pytest.raises(ValueError, match="invalid integer in protobuf Long component"):
        normalize_provider_int64({"low": "abc", "high": 0})

    # Unsupported data structure
    with pytest.raises(ValueError, match="unsupported provider integer representation"):
        normalize_provider_int64([10, 20])


def test_resolve_canonical_sender() -> None:
    """Verifies WhatsApp sender resolution under standard, LID, and fallback conditions."""
    # 1. Standard remoteJid with @s.whatsapp.net
    msg_std = {"key": {"remoteJid": "5511999999999@s.whatsapp.net"}}
    assert resolve_canonical_sender(msg_std) == "5511999999999"

    # 2. WhatsApp LID addressing mode: remoteJid=@lid and remoteJidAlt=@s.whatsapp.net
    # Real Human Acceptance pattern: phone number must be recovered from remoteJidAlt
    msg_lid = {
        "key": {
            "remoteJid": "53378004578377@lid",
            "remoteJidAlt": "5511988887777@s.whatsapp.net",
            "addressingMode": "lid",
        }
    }
    assert resolve_canonical_sender(msg_lid) == "5511988887777"

    # 3. LID addressing mode with absent remoteJidAlt (falls back gracefully to LID)
    msg_lid_no_alt = {"key": {"remoteJid": "53378004578377@lid"}}
    assert resolve_canonical_sender(msg_lid_no_alt) == "53378004578377"

    # 4. LID addressing mode with malformed remoteJidAlt (not ending in @s.whatsapp.net)
    msg_lid_malformed_alt = {
        "key": {
            "remoteJid": "53378004578377@lid",
            "remoteJidAlt": "malformed_jid_without_domain",
        }
    }
    assert resolve_canonical_sender(msg_lid_malformed_alt) == "53378004578377"

    # 5. Explicit "from" field
    msg_from = {"from": "5511977776666@s.whatsapp.net"}
    assert resolve_canonical_sender(msg_from) == "5511977776666"

    # 6. Completely empty message
    assert resolve_canonical_sender({}) is None


def test_real_payload_sanitized_regression(test_ctx: CallContext) -> None:
    """Regression test: Exact shape of the real Human Acceptance failure parses successfully."""
    from fixtures.evolution_real_payload_sanitized import (
        make_sanitized_pdf_document_webhook_payload,
    )

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        payload = make_sanitized_pdf_document_webhook_payload()
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        msg = messages[0]

        # Sender canonical identity derived from remoteJidAlt (not LID)
        assert msg.sender.channel == "whatsapp"
        assert msg.sender.external_id == "5511988887777"
        assert msg.provider_message_id == "3A8424FAE89C98FF36B6"

        # Document attachment properly parsed from protobuf Long
        assert len(msg.attachments) == 1
        att = msg.attachments[0]
        assert att.kind == "document"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 245434
        assert att.filename == "curriculo - teste.pdf"
        assert att.provider_media_id == "3A8424FAE89C98FF36B6"

    asyncio.run(scenario())


def test_parse_inbound_invalid_file_length_handled_gracefully(test_ctx: CallContext) -> None:
    """Verifies malformed fileLength does not raise TypeError and sets size_bytes=None safely."""
    from fixtures.evolution_real_payload_sanitized import (
        make_sanitized_pdf_document_webhook_payload,
    )

    async def scenario() -> None:
        adapter = EvolutionAdapter(
            api_url="https://evolution.test.local",
            api_key="test_key_123",
            instance_name="test_instance",
        )
        # Pass a negative media size representation
        payload = make_sanitized_pdf_document_webhook_payload(
            file_length={"low": -1, "high": -1, "unsigned": False}
        )
        raw_body = json.dumps(payload).encode("utf-8")
        messages = await adapter.parse_inbound(raw_body, {}, test_ctx)

        assert len(messages) == 1
        assert len(messages[0].attachments) == 1
        # size_bytes defaults to None safely without crashing
        assert messages[0].attachments[0].size_bytes is None

    asyncio.run(scenario())

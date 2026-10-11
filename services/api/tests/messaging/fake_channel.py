"""Fake MessagingChannel test double for E2E canary and offline testing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from tapjob_api.ports._common import AdapterHealth, AdapterHealthStatus, CallContext
from tapjob_api.ports.capabilities import (
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
)
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundMessage,
    MessagingCapability,
    OutboundMessage,
    SendReceipt,
)

_VALID_TEST_PDF_BYTES: bytes = (
    b"%PDF-1.4\n"
    b"1 0 obj\n"
    b"<< /Type /Catalog /Pages 2 0 R >>\n"
    b"endobj\n"
    b"2 0 obj\n"
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
    b"endobj\n"
    b"3 0 obj\n"
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
    b"/Resources << /Font << /F1 5 0 R >> >> >>\n"
    b"endobj\n"
    b"4 0 obj\n"
    b"<< /Length 120 >>\n"
    b"stream\n"
    b"BT\n"
    b"/F1 12 Tf\n"
    b"100 700 Td\n"
    b"(Jane Doe) Tj\n"
    b"0 -20 Td\n"
    b"(Summary) Tj\n"
    b"0 -20 Td\n"
    b"(Senior Python Developer with 5 years experience) Tj\n"
    b"0 -20 Td\n"
    b"(Skills) Tj\n"
    b"0 -20 Td\n"
    b"(Python, SQL, FastApi) Tj\n"
    b"ET\n"
    b"endstream\n"
    b"endobj\n"
    b"5 0 obj\n"
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
    b"endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000244 00000 n \n"
    b"0000000416 00000 n \n"
    b"trailer\n"
    b"<< /Size 6 /Root 1 0 R >>\n"
    b"startxref\n"
    b"485\n"
    b"%%EOF\n"
)


class FakeMessagingChannel:
    """In-memory test double for MessagingChannel port."""

    provider: str = "fake_whatsapp"

    def __init__(self) -> None:
        self.sent_messages: list[OutboundMessage] = []

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[MessagingCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(MessagingCapability.TEXT, CapabilityStatus.SUPPORTED),
                CapabilityReport(MessagingCapability.DOCUMENT, CapabilityStatus.SUPPORTED),
                CapabilityReport(
                    MessagingCapability.INTERACTIVE_BUTTONS, CapabilityStatus.SUPPORTED
                ),
                CapabilityReport(
                    MessagingCapability.FREEFORM_OUTSIDE_SESSION, CapabilityStatus.SUPPORTED
                ),
            ]
        )

    async def send(self, message: OutboundMessage, ctx: CallContext) -> SendReceipt:
        self.sent_messages.append(message)
        return SendReceipt(
            provider_message_id=f"fake_msg_{len(self.sent_messages)}",
            accepted_at=datetime.now(UTC),
        )

    async def parse_inbound(
        self, raw_body: bytes, headers: Mapping[str, str], ctx: CallContext
    ) -> Sequence[InboundMessage]:
        data = json.loads(raw_body.decode("utf-8"))
        messages: list[InboundMessage] = []
        for m in data.get("messages", []):
            messages.append(
                InboundMessage(
                    sender=ChannelIdentity(channel="whatsapp", external_id=m["from"]),
                    provider_message_id=m["id"],
                    received_at=datetime.now(UTC),
                    text=m.get("text", {}).get("body"),
                    interactive_reply_id=m.get("interactive", {}).get("button_reply", {}).get("id"),
                )
            )
        return messages

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return AdapterHealth(
            status=AdapterHealthStatus.HEALTHY,
            checked_at=datetime.now(UTC),
        )

    async def retrieve_media(
        self,
        attachment: Any,
        ctx: CallContext,
        *,
        message_id: str | None = None,
        base64_data: str | None = None,
    ) -> bytes:
        return _VALID_TEST_PDF_BYTES

"""MessagingChannel: provider-neutral messaging boundary
(docs/adr/0005-internal-ports-capability-model.md).

Evolution API is a future adapter (docs/INTEGRATIONS.md §11); nothing here encodes a Baileys JID,
an Evolution-specific webhook shape, or any WhatsApp Cloud API type. `FREEFORM_OUTSIDE_SESSION`
exists specifically so a future migration from Evolution/Baileys to the official WhatsApp Cloud
API (docs/DELIVERY_ORDER.md §7) can be expressed as a capability difference, without the Career
Core changing at all.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext, normalize_utc_field
from tapjob_api.ports.capabilities import CapabilityManifest


class MessagingCapability(StrEnum):
    TEXT = "text"
    INTERACTIVE_BUTTONS = "interactive_buttons"
    DOCUMENT = "document"
    TEMPLATE = "template"
    DELIVERY_RECEIPTS = "delivery_receipts"
    # Models the WhatsApp Cloud API's 24h customer-service-window restriction, which a
    # Baileys-based transport does not have (docs/INTEGRATIONS.md §11).
    FREEFORM_OUTSIDE_SESSION = "freeform_outside_session"


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    """`channel` is a channel KIND (e.g. "whatsapp"), never a provider SDK type. `external_id` is
    opaque to this port -- e.g. a phone number -- never a Baileys JID shape."""

    channel: str
    external_id: str


@dataclass(frozen=True, slots=True)
class InboundAttachment:
    """Provider-neutral inbound media/attachment (Finding 4)."""

    provider_media_id: str
    mime_type: str
    filename: str | None = None
    size_bytes: int | None = None
    kind: str = "document"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    sender: ChannelIdentity
    provider_message_id: str
    received_at: datetime
    text: str | None
    interactive_reply_id: str | None = None
    attachments: tuple[InboundAttachment, ...] = ()

    def __post_init__(self) -> None:
        normalize_utc_field(self, "received_at")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    recipient: ChannelIdentity
    idempotency_key: str
    text: str | None = None
    template_key: str | None = None


@dataclass(frozen=True, slots=True)
class SendReceipt:
    provider_message_id: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        normalize_utc_field(self, "accepted_at")


class MessagingChannel(Protocol):
    provider: str

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[MessagingCapability]: ...

    async def send(self, message: OutboundMessage, ctx: CallContext) -> SendReceipt: ...

    async def parse_inbound(
        self, raw_body: bytes, headers: Mapping[str, str], ctx: CallContext
    ) -> Sequence[InboundMessage]: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...

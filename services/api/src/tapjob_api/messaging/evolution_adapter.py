"""Evolution API adapter implementing MessagingChannel Protocol (Finding 4, Finding 15).

Translates between Evolution API HTTP boundary and provider-neutral MessagingChannel port.
- Authenticated HTTP client with deadline/timeout handling.
- Outbound text and capabilities reporting.
- Real health check against Evolution instance connection state.
- Inbound webhook parsing for message and interactive events.
- Strict PII redaction: no raw phones, body payloads, or secrets in logs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from tapjob_api.ports._common import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    PortError,
    PortErrorCode,
)
from tapjob_api.ports.capabilities import (
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
)
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundAttachment,
    InboundMessage,
    MessagingCapability,
    MessagingChannel,
    OutboundMessage,
    SendReceipt,
)

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MiB bounded limit
ALLOWED_DOCUMENT_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}


def _find_document_in_message(m: dict[str, Any]) -> dict[str, Any] | None:
    """Recursively extracts a documentMessage from Evolution/Baileys nested structures."""
    if not isinstance(m, dict):
        return None
    if "documentMessage" in m and isinstance(m["documentMessage"], dict):
        return m["documentMessage"]
    for wrapper in (
        "documentWithCaptionMessage",
        "ephemeralMessage",
        "viewOnceMessage",
        "viewOnceMessageV2",
    ):
        if wrapper in m and isinstance(m[wrapper], dict):
            inner = m[wrapper].get("message")
            if isinstance(inner, dict):
                doc = _find_document_in_message(inner)
                if doc:
                    return doc
    return None


def normalize_provider_int64(value: object) -> int | None:
    """Normalizes provider int64 representations (int, numeric str, protobuf Long dict).

    Protobuf Long objects in Baileys/Evolution (long.js) are serialized as:
    {"low": 245434, "high": 0, "unsigned": true}
    where low and high are 32-bit signed or unsigned integers.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("boolean is not a valid provider int64")

    if isinstance(value, int):
        result = value

    elif isinstance(value, str):
        try:
            result = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid numeric string: {value}") from exc

    elif isinstance(value, dict):
        low_raw = value.get("low")
        high_raw = value.get("high")

        if low_raw is None or high_raw is None:
            raise ValueError("invalid protobuf Long representation: missing low or high")

        if isinstance(low_raw, bool) or isinstance(high_raw, bool):
            raise ValueError("boolean is not a valid protobuf Long component")

        try:
            low = int(low_raw) & 0xFFFFFFFF
            high = int(high_raw) & 0xFFFFFFFF
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid integer in protobuf Long component") from exc

        result = (high << 32) | low

        if not bool(value.get("unsigned", False)) and (high & 0x80000000):
            result -= 1 << 64

    else:
        raise ValueError("unsupported provider integer representation")

    if result < 0:
        raise ValueError("negative media size")

    return result


def resolve_canonical_sender(msg: dict[str, Any]) -> str | None:
    """Resolves canonical sender phone/identifier from Evolution message structure.

    Handles WhatsApp Privacy Addressing Mode (LID):
    When remoteJid ends in @lid and remoteJidAlt ends in @s.whatsapp.net,
    the canonical phone number from remoteJidAlt is used so that candidate
    identities and outbound text dispatches remain tied to real phones.
    """
    key_val = msg.get("key")
    key_dict: dict[str, Any] = key_val if isinstance(key_val, dict) else {}
    remote_jid = str(key_dict.get("remoteJid") or "")
    remote_jid_alt = str(key_dict.get("remoteJidAlt") or "")

    # 1. WhatsApp LID addressing mode: prefer remoteJidAlt if it has @s.whatsapp.net
    if remote_jid.endswith("@lid") and remote_jid_alt.endswith("@s.whatsapp.net"):
        cand = remote_jid_alt.split("@")[0].strip()
        if cand:
            return cand

    # 2. Explicit provider "from" field if present
    sender_raw = msg.get("from")
    if sender_raw:
        cand = str(sender_raw).strip()
        if "@" in cand:
            cand = cand.split("@")[0].strip()
        if cand:
            return cand

    # 3. Fallback: remoteJidAlt if remoteJid has no @s.whatsapp.net but remoteJidAlt does
    if not remote_jid.endswith("@s.whatsapp.net") and remote_jid_alt.endswith("@s.whatsapp.net"):
        cand = remote_jid_alt.split("@")[0].strip()
        if cand:
            return cand

    # 4. Standard remoteJid
    if remote_jid:
        cand = remote_jid.split("@")[0].strip() if "@" in remote_jid else remote_jid.strip()
        if cand:
            return cand

    return None


class EvolutionAdapter:
    """Evolution API HTTP adapter implementing MessagingChannel protocol."""

    provider: str = "evolution"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        instance_name: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.instance_name = instance_name
        self._http_client = http_client

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[MessagingCapability]:
        """Reports Evolution messaging capabilities using typed CapabilityReports.

        Capabilities are reported truthfully (Finding 8):
        - TEXT: SUPPORTED
        - FREEFORM_OUTSIDE_SESSION: SUPPORTED
        - DOCUMENT: UNSUPPORTED (adapter send() currently implements text only)
        - INTERACTIVE_BUTTONS: UNSUPPORTED (outbound interactive buttons dispatch not implemented)
        """
        reports = [
            CapabilityReport(MessagingCapability.TEXT, CapabilityStatus.SUPPORTED),
            CapabilityReport(
                MessagingCapability.DOCUMENT,
                CapabilityStatus.UNSUPPORTED,
                reason_code="OUTBOUND_DOCUMENT_NOT_IMPLEMENTED",
            ),
            CapabilityReport(
                MessagingCapability.INTERACTIVE_BUTTONS,
                CapabilityStatus.UNSUPPORTED,
                reason_code="OUTBOUND_BUTTONS_NOT_IMPLEMENTED",
            ),
            CapabilityReport(
                MessagingCapability.FREEFORM_OUTSIDE_SESSION,
                CapabilityStatus.SUPPORTED,
            ),
        ]
        return CapabilityManifest(reports)

    async def send(self, message: OutboundMessage, ctx: CallContext) -> SendReceipt:
        """Sends an outbound message to Evolution API within ctx deadline."""
        timeout_budget = ctx.deadline.remaining_seconds()
        if timeout_budget <= 0:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=self.provider,
                message="Call deadline already expired before dispatch",
                transient=True,
            )

        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        url = f"{self.api_url}/message/sendText/{self.instance_name}"
        payload = {
            "number": message.recipient.external_id,
            "text": message.text or "",
        }

        try:
            if self._http_client is not None:
                resp = await self._http_client.post(
                    url, headers=headers, json=payload, timeout=timeout_budget
                )
            else:
                async with httpx.AsyncClient(timeout=timeout_budget) as client:
                    resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=self.provider,
                message="Evolution API request timed out",
                transient=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                provider=self.provider,
                message="Evolution API endpoint unreachable",
                transient=True,
            ) from exc
        except Exception as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message=f"Evolution HTTP client error: {exc}",
                transient=True,
            ) from exc

        if resp.status_code in (401, 403):
            raise PortError(
                PortErrorCode.AUTH_FAILED,
                provider=self.provider,
                message="Evolution API key rejected",
                transient=False,
            )
        if resp.status_code == 429:
            raise PortError(
                PortErrorCode.RATE_LIMITED,
                provider=self.provider,
                message="Evolution API rate limit exceeded",
                transient=True,
            )
        if 400 <= resp.status_code < 500:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message=f"Evolution API rejected request with HTTP {resp.status_code}",
                transient=False,
            )
        if resp.status_code >= 500:
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                provider=self.provider,
                message=f"Evolution API server error HTTP {resp.status_code}",
                transient=True,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Evolution response not valid JSON",
                transient=False,
            ) from exc

        # Extract provider message id
        msg_id: str | None = None
        if isinstance(body, dict):
            key_data = body.get("key")
            if isinstance(key_data, dict):
                msg_id = key_data.get("id")
            if not msg_id:
                msg_id = body.get("id") or body.get("messageId")

        if not msg_id:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Evolution response missing message ID",
                transient=False,
            )

        logger.info(
            "evolution.send.success",
            extra={
                "correlation_id": ctx.correlation_id,
                "provider_message_id": msg_id,
            },
        )

        return SendReceipt(
            provider_message_id=str(msg_id),
            accepted_at=datetime.now(UTC),
        )

    async def parse_inbound(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        ctx: CallContext,
    ) -> Sequence[InboundMessage]:
        """Parses Evolution webhook payloads into normalized InboundMessage DTOs."""
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "evolution.parse_inbound.invalid_json",
                extra={"correlation_id": ctx.correlation_id},
            )
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message="Malformed JSON in webhook body",
                transient=False,
            ) from exc

        messages: list[Any] = []
        if isinstance(payload, dict):
            if "messages" in payload and isinstance(payload["messages"], list):
                messages = payload["messages"]
            elif "data" in payload:
                data = payload["data"]
                if isinstance(data, list):
                    messages = data
                elif isinstance(data, dict):
                    messages = [data]
            else:
                messages = [payload]

        inbound_messages: list[InboundMessage] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue

            # Explicit provider-boundary fromMe filter (Finding 7):
            # Messages sent by the platform/bot (fromMe=True) are outbound echoes and must
            # never be normalized into inbound candidate messages, preventing loops.
            key_dict = msg.get("key") if isinstance(msg.get("key"), dict) else None
            is_from_me = key_dict.get("fromMe") if key_dict is not None else msg.get("fromMe")
            if is_from_me is True:
                logger.info(
                    "evolution.parse_inbound.skipped_outbound_echo",
                    extra={"correlation_id": ctx.correlation_id},
                )
                continue

            # Resolve sender phone using canonical resolver (supports WhatsApp LID + remoteJidAlt)
            sender_raw = resolve_canonical_sender(msg)

            # Resolve message id (prefer WhatsApp key.id over internal provider record id)
            msg_id = None
            if isinstance(msg.get("key"), dict):
                msg_id = msg["key"].get("id")
            if not msg_id:
                msg_id = msg.get("id")

            # Resolve timestamp
            ts = msg.get("timestamp") or msg.get("messageTimestamp")
            received_at: datetime
            if isinstance(ts, (int, float)):
                received_at = datetime.fromtimestamp(ts, tz=UTC)
            else:
                received_at = datetime.now(UTC)

            if not sender_raw or not msg_id:
                continue

            # Extract body text or interactive reply
            text: str | None = None
            interactive_reply_id: str | None = None
            attachments: list[InboundAttachment] = []

            # Check for document attachment in Evolution v2 / Baileys format
            doc_data: dict[str, Any] | None = None
            if "message" in msg and isinstance(msg["message"], dict):
                doc_data = _find_document_in_message(msg["message"])
            elif "document" in msg and isinstance(msg["document"], dict):
                doc_data = msg["document"]

            if doc_data is not None:
                filename = doc_data.get("fileName") or doc_data.get("filename") or "document.pdf"
                mimetype = doc_data.get("mimetype") or doc_data.get("mimeType") or "application/pdf"
                if str(filename).lower().endswith(".pdf") and mimetype in (
                    "application/octet-stream",
                    "binary/octet-stream",
                ):
                    mimetype = "application/pdf"
                size_raw = doc_data.get("fileLength") or doc_data.get("size")
                size_bytes: int | None = None
                if size_raw is not None:
                    try:
                        size_bytes = normalize_provider_int64(size_raw)
                    except ValueError as exc:
                        logger.warning(
                            "evolution.parse_inbound.invalid_file_length",
                            extra={
                                "correlation_id": ctx.correlation_id,
                                "provider": self.provider,
                                "provider_message_id": str(msg_id) if msg_id else None,
                                "error_class": type(exc).__name__,
                                "error_message": str(exc),
                            },
                        )
                        size_bytes = None

                # Evolution v2 /chat/getBase64FromMediaMessage requires the message ID (msg_id),
                # NOT the cryptographic mediaKey (Finding 3).
                media_id = str(msg_id or doc_data.get("id") or "")
                attachments.append(
                    InboundAttachment(
                        provider_media_id=media_id,
                        mime_type=mimetype,
                        filename=filename,
                        size_bytes=size_bytes,
                        kind="document",
                    )
                )
                if doc_data.get("caption") and not text:
                    text = str(doc_data["caption"])

            if "text" in msg and isinstance(msg["text"], dict):
                text = msg["text"].get("body")
            elif "message" in msg and isinstance(msg["message"], dict):
                m = msg["message"]
                ext = (
                    m.get("extendedTextMessage", {}).get("text")
                    if isinstance(m.get("extendedTextMessage"), dict)
                    else None
                )
                if not text:
                    text = m.get("conversation") or ext

            if "interactive" in msg and isinstance(msg["interactive"], dict):
                inter = msg["interactive"]
                if inter.get("type") == "button_reply":
                    interactive_reply_id = inter.get("button_reply", {}).get("id")
                elif inter.get("type") == "list_reply":
                    interactive_reply_id = inter.get("list_reply", {}).get("id")

            inbound_messages.append(
                InboundMessage(
                    sender=ChannelIdentity(channel="whatsapp", external_id=str(sender_raw)),
                    provider_message_id=str(msg_id),
                    received_at=received_at,
                    text=text,
                    interactive_reply_id=interactive_reply_id,
                    attachments=tuple(attachments),
                )
            )

        logger.info(
            "evolution.parse_inbound.success",
            extra={
                "correlation_id": ctx.correlation_id,
                "message_count": len(inbound_messages),
            },
        )
        return inbound_messages

    async def retrieve_media(
        self,
        attachment: InboundAttachment,
        ctx: CallContext,
        *,
        message_id: str | None = None,
        base64_data: str | None = None,
    ) -> bytes:
        """Securely retrieves media bytes from Evolution API boundary (Finding 4).

        Guarantees:
        - Authenticated request with timeout budget
        - MIME validation (strictly PDF / DOCX)
        - Max size bounds (10 MiB limit)
        - No arbitrary URL fetching
        - PII-safe logging (no media content logged)
        """
        import base64

        if attachment.mime_type not in ALLOWED_DOCUMENT_MIMES:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message=f"Unsupported document MIME type: {attachment.mime_type}",
                transient=False,
            )

        if base64_data:
            try:
                b64_clean = base64_data.split(",", 1)[1] if "," in base64_data else base64_data
                content = base64.b64decode(b64_clean)
                if len(content) > MAX_MEDIA_BYTES:
                    raise PortError(
                        PortErrorCode.INVALID_REQUEST,
                        provider=self.provider,
                        message=f"Media size exceeds {MAX_MEDIA_BYTES} bytes",
                        transient=False,
                    )
                return content
            except Exception as exc:
                if isinstance(exc, PortError):
                    raise
                raise PortError(
                    PortErrorCode.INVALID_REQUEST,
                    provider=self.provider,
                    message=f"Failed to decode base64 media: {exc}",
                    transient=False,
                ) from exc

        timeout_budget = ctx.deadline.remaining_seconds()
        if timeout_budget <= 0:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=self.provider,
                message="Call deadline expired before media retrieval",
                transient=True,
            )

        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        url = f"{self.api_url}/chat/getBase64FromMediaMessage/{self.instance_name}"
        payload = {
            "message": {
                "key": {
                    "id": message_id or attachment.provider_media_id,
                }
            },
            "convertToMp4": False,
        }

        try:
            if self._http_client is not None:
                resp = await self._http_client.post(
                    url, headers=headers, json=payload, timeout=timeout_budget
                )
            else:
                async with httpx.AsyncClient(timeout=timeout_budget) as client:
                    resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=self.provider,
                message="Evolution media retrieval timed out",
                transient=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                provider=self.provider,
                message="Evolution API endpoint unreachable",
                transient=True,
            ) from exc
        except Exception as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message=f"Evolution media retrieval HTTP error: {exc}",
                transient=True,
            ) from exc

        if resp.status_code in (401, 403):
            raise PortError(
                PortErrorCode.AUTH_FAILED,
                provider=self.provider,
                message="Evolution API key rejected during media retrieval",
                transient=False,
            )
        if resp.status_code == 404:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message="Media not found on Evolution API",
                transient=False,
            )
        if resp.status_code >= 400:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message=f"Evolution media retrieval failed with HTTP {resp.status_code}",
                transient=resp.status_code >= 500,
            )

        try:
            body = resp.json()
            b64_str = body.get("base64") if isinstance(body, dict) else None
            if not b64_str:
                raise ValueError("Missing base64 in Evolution media response")
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            content = base64.b64decode(b64_str)
        except Exception as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message=f"Failed to decode Evolution media response: {exc}",
                transient=False,
            ) from exc

        if len(content) > MAX_MEDIA_BYTES:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message=f"Retrieved media size exceeds maximum allowed {MAX_MEDIA_BYTES} bytes",
                transient=False,
            )

        logger.info(
            "evolution.retrieve_media.success",
            extra={
                "correlation_id": ctx.correlation_id,
                "mime_type": attachment.mime_type,
                "size_bytes": len(content),
            },
        )
        return content

    async def health(self, ctx: CallContext) -> AdapterHealth:
        """Probes Evolution API instance connectivity status."""
        timeout_budget = ctx.deadline.remaining_seconds()
        if timeout_budget <= 0:
            return AdapterHealth(
                status=AdapterHealthStatus.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                reason_code="deadline_expired",
            )

        headers = {"apikey": self.api_key}
        url = f"{self.api_url}/instance/connectionState/{self.instance_name}"

        try:
            if self._http_client is not None:
                resp = await self._http_client.get(url, headers=headers, timeout=timeout_budget)
            else:
                async with httpx.AsyncClient(timeout=timeout_budget) as client:
                    resp = await client.get(url, headers=headers)
        except Exception:
            return AdapterHealth(
                status=AdapterHealthStatus.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                reason_code="network_error",
            )

        if resp.status_code == 200:
            try:
                data = resp.json()
                inst = data.get("instance") if isinstance(data, dict) else None
                if isinstance(inst, dict):
                    raw_state = inst.get("state")
                elif isinstance(data, dict):
                    raw_state = data.get("state")
                else:
                    raw_state = ""
                state = str(raw_state or "")
                if state in ("open", "connected"):
                    return AdapterHealth(
                        status=AdapterHealthStatus.HEALTHY,
                        checked_at=datetime.now(UTC),
                    )
                return AdapterHealth(
                    status=AdapterHealthStatus.DEGRADED,
                    checked_at=datetime.now(UTC),
                    reason_code=f"instance_state_{state or 'unknown'}",
                )
            except ValueError:
                return AdapterHealth(
                    status=AdapterHealthStatus.DEGRADED,
                    checked_at=datetime.now(UTC),
                    reason_code="non_json_response",
                )

        return AdapterHealth(
            status=AdapterHealthStatus.UNAVAILABLE,
            checked_at=datetime.now(UTC),
            reason_code=f"http_{resp.status_code}",
        )


# Static protocol conformance check
_conformance_check: MessagingChannel = EvolutionAdapter(
    api_url="http://localhost", api_key="secret", instance_name="test"
)

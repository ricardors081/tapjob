"""Webhook endpoint for inbound messaging events (Finding 3).

Responsibilities:
- Content-type validation (application/json -> 415)
- Payload size bounding (< 1 MiB -> 413)
- Provider authentication verification (x-webhook-secret / secret query -> 401)
- Transport translation via EvolutionAdapter.parse_inbound (malformed JSON -> 400)
- Atomic deduplication via ProviderEventEnvelope in database
- Safe no-op for retransmissions/duplicates
- Normalized processing delegation to orchestration
- FAST 202 Accepted acknowledgment
"""

from __future__ import annotations

import hmac
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.core.correlation import resolve_correlation_id
from tapjob_api.db.session import get_db_session
from tapjob_api.messaging.evolution_adapter import EvolutionAdapter
from tapjob_api.messaging.inbound import ProviderEventType
from tapjob_api.messaging.inbound_service import record_inbound_event
from tapjob_api.messaging.tasks import process_inbound_envelope_task
from tapjob_api.ports._common import CallContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)

# Webhook payload size limit: 1 MiB max
MAX_WEBHOOK_SIZE_BYTES = 1024 * 1024


@router.post(
    "/whatsapp",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    include_in_schema=False,
)
async def whatsapp_webhook(
    request: Request,
    db_session: Session = Depends(get_db_session),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> Response:
    """Receives inbound webhooks from Evolution API and orchestrates normalized processing."""
    settings: ApiSettings = getattr(request.app.state, "settings", None) or ApiSettings()
    incoming = request.headers.get("x-request-id")
    correlation_id = resolve_correlation_id(incoming, fallback=uuid4().hex)
    ctx = CallContext.with_timeout(correlation_id=correlation_id, timeout_seconds=15.0)

    # 0. Check enabled flag (fail-closed if webhook is not enabled)
    if not settings.whatsapp_webhook_enabled:
        logger.warning(
            "webhook.disabled",
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook endpoint is disabled",
        )

    # 1. Validate content-type
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("application/json"):
        logger.warning(
            "webhook.invalid_content_type",
            extra={"correlation_id": correlation_id, "content_type": content_type},
        )
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )

    # 2. Check payload size
    body_bytes = await request.body()
    if len(body_bytes) > MAX_WEBHOOK_SIZE_BYTES:
        logger.warning(
            "webhook.oversized_payload",
            extra={
                "correlation_id": correlation_id,
                "size_bytes": len(body_bytes),
                "max_bytes": MAX_WEBHOOK_SIZE_BYTES,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Webhook payload exceeds {MAX_WEBHOOK_SIZE_BYTES} bytes",
        )

    # 3. Provider authentication check (fail-closed, constant-time compare)
    configured_secret = settings.whatsapp_webhook_secret
    if (
        not configured_secret
        or not x_webhook_secret
        or not hmac.compare_digest(
            x_webhook_secret.encode("utf-8"), configured_secret.encode("utf-8")
        )
    ):
        logger.warning(
            "webhook.unauthorized",
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook authentication credentials",
        )

    # 4. Validate JSON payload structure
    try:
        json.loads(body_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            "webhook.malformed_json",
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON in webhook payload",
        ) from exc

    # 5. Parse via EvolutionAdapter
    instance_name = settings.evolution_instance_name or "tapjob"
    adapter = EvolutionAdapter(
        api_url=settings.evolution_api_url or "http://localhost:8080",
        api_key=settings.evolution_api_key or "secret",
        instance_name=instance_name,
    )
    inbound_messages = await adapter.parse_inbound(body_bytes, dict(request.headers), ctx)

    # 6. Atomic deduplication & commit accepted envelopes BEFORE async dispatch
    new_envelope_ids: list[int] = []
    for msg in inbound_messages:
        attachments_data = [
            {
                "provider_media_id": att.provider_media_id,
                "mime_type": att.mime_type,
                "filename": att.filename,
                "size_bytes": att.size_bytes,
                "kind": att.kind,
            }
            for att in msg.attachments
        ]

        envelope, is_new = record_inbound_event(
            db_session,
            provider="evolution",
            provider_instance=instance_name,
            external_event_id=msg.provider_message_id,
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "provider_message_id": msg.provider_message_id,
                "sender_external_id": msg.sender.external_id,
                "channel": msg.sender.channel,
                "received_at": msg.received_at.isoformat(),
                "text": msg.text,
                "interactive_reply_id": msg.interactive_reply_id,
                "attachments": attachments_data,
            },
        )

        if is_new:
            new_envelope_ids.append(envelope.id)
        else:
            logger.info(
                "webhook.duplicate_skipped",
                extra={
                    "correlation_id": correlation_id,
                    "envelope_id": envelope.id,
                },
            )

    # 7. COMMIT ACCEPTED envelopes FIRST (Finding 1)
    db_session.commit()

    # 8. Async publication to Celery ONLY AFTER COMMIT (Finding 1)
    for env_id in new_envelope_ids:
        try:
            process_inbound_envelope_task.delay(env_id)
        except Exception:
            # Envelope is safely committed as ACCEPTED in PostgreSQL;
            # Celery periodic recovery sweep will claim and process it.
            logger.warning(
                "webhook.celery_enqueue_fallback_retained_accepted",
                extra={"envelope_id": env_id, "correlation_id": correlation_id},
            )

    return Response(
        status_code=status.HTTP_202_ACCEPTED,
        content=b'{"status":"accepted"}',
        media_type="application/json",
    )

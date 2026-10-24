"""Sanitized Evolution API payload fixtures modeled after real Human Acceptance events."""

from __future__ import annotations

from typing import Any


def make_sanitized_pdf_document_webhook_payload(
    *,
    message_id: str = "3A8424FAE89C98FF36B6",
    file_length: Any = None,
    remote_jid: str = "53378004578377@lid",
    remote_jid_alt: str = "5511988887777@s.whatsapp.net",
    file_name: str = "curriculo - teste.pdf",
    mime_type: str = "application/pdf",
    from_me: bool = False,
) -> dict[str, Any]:
    """Generates a sanitized webhook payload matching the real Human Acceptance shape.

    All PII, media keys, hashes, URLs, and document content are replaced with low-entropy
    placeholders while preserving the provider field structure relevant to parser behavior.
    """
    if file_length is None:
        file_length = {"low": 245434, "high": 0, "unsigned": True}

    return {
        "event": "messages.upsert",
        "instance": "TapJob",
        "data": {
            "id": "sanitized-provider-record-id",
            "key": {
                "id": message_id,
                "fromMe": from_me,
                "remoteJid": remote_jid,
                "participant": "",
                "remoteJidAlt": remote_jid_alt,
                "addressingMode": "lid",
            },
            "pushName": "Candidato Teste",
            "messageType": "documentMessage",
            "message": {
                "documentMessage": {
                    "url": "https://example.invalid/sanitized-media",
                    "title": "curriculo - teste",
                    "fileName": file_name,
                    "mediaKey": "sanitized-media-key",
                    "mimetype": mime_type,
                    "pageCount": 3,
                    "directPath": "/sanitized-media-path",
                    "fileLength": file_length,
                    "fileSha256": "sanitized-file-sha256",
                    "contactVcard": False,
                    "fileEncSha256": "sanitized-file-enc-sha256",
                }
            },
            "messageTimestamp": 1792876044,
            "instanceId": "00000000-0000-4000-8000-000000000001",
            "source": "ios",
        },
    }

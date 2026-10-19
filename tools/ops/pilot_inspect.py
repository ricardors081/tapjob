"""TapJob WhatsApp Pilot Operator Inspection CLI (tools/ops/pilot_inspect.py).

Read-only inspection and monitoring utility for WhatsApp pilot operations.
Enforces strict LGPD / PII protection:
- Phone numbers: masked as 55119****1234
- WhatsApp JIDs: masked as 55119****1234@s.whatsapp.net
- Emails: masked as c***o@domain.com
- Candidate names: masked as F*** L***
- Message bodies and resumes: truncated and sanitized

Provides:
- --summary: High-level health snapshot of inbound envelopes, outbox queues, and participant count
- --recent-envelopes [limit]: Recent inbound envelopes with processing status and attempts
- --recent-outbox [limit]: Recent outbound messages with delivery status and retries
- --phone <phone_or_jid>: Detailed event timeline for a specific pilot participant
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("tapjob.ops.pilot_inspect")

# Inline PII regex patterns for free-text error/event sanitization
EMAIL_INLINE_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
JID_INLINE_RE = re.compile(r"\b(?:\+?\d{8,15})@(s\.whatsapp\.net|g\.us|c\.us)\b")
PHONE_INLINE_RE = re.compile(
    r"(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?(?:9\d{4}|\d{4})[-\s]?\d{4}\b|\b\d{10,13}\b"
)
SECRET_INLINE_RE = re.compile(
    r"(?i)\b(bearer\s+[a-zA-Z0-9_\-\.]+)|((?:token|api[-_]?key|secret|password|senha)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-\.]+['\"]?)"
)
PHONE_CLEAN_RE = re.compile(r"[^\d]")
JID_SUFFIX_RE = re.compile(r"(@[a-zA-Z0-9_\.\-]+)$")


def mask_inline_pii(text: str) -> str:
    """Masks all embedded PII (JIDs, emails, phone numbers, credentials) in free-text."""
    if not text:
        return text

    # 1. Mask tokens / secrets / credentials
    res = SECRET_INLINE_RE.sub("[REDACTED_CREDENTIAL]", text)

    # 2. Mask WhatsApp JIDs
    def _jid_sub(m: re.Match[str]) -> str:
        return mask_phone(m.group(0))

    res = JID_INLINE_RE.sub(_jid_sub, res)

    # 3. Mask Email addresses (excluding WhatsApp JID domains)
    def _email_sub(m: re.Match[str]) -> str:
        matched = m.group(0)
        jid_domains = ("@s.whatsapp.net", "@c.us", "@g.us", "@broadcast")
        if any(matched.endswith(suffix) for suffix in jid_domains):
            return matched
        return mask_email(matched)

    res = EMAIL_INLINE_RE.sub(_email_sub, res)

    # 4. Mask Standalone phone numbers
    def _phone_sub(m: re.Match[str]) -> str:
        matched = m.group(0)
        digits = PHONE_CLEAN_RE.sub("", matched)
        if 8 <= len(digits) <= 15 and "*" not in matched:
            return mask_phone(matched)
        return matched

    res = PHONE_INLINE_RE.sub(_phone_sub, res)
    return res


def mask_provider_message_id(msg_id: str | None) -> str | None:
    """Masks provider message ID if present and ensures no embedded JID/phone leaks."""
    if not msg_id or not str(msg_id).strip():
        return None
    raw = str(msg_id).strip()
    masked = mask_inline_pii(raw)
    if len(masked) > 36:
        return f"{masked[:14]}...{masked[-6:]}"
    return masked


def mask_phone(phone: str | None) -> str:
    """Masks a phone number or WhatsApp JID, preserving prefix and last 4 digits.

    Examples:
    - "5511999991234" -> "55119****1234"
    - "5511999991234@s.whatsapp.net" -> "55119****1234@s.whatsapp.net"
    - "+55 11 99999-1234" -> "55119****1234"
    """
    if not phone or not phone.strip():
        return "[EMPTY]"

    raw = phone.strip()
    jid_suffix = ""
    match_jid = JID_SUFFIX_RE.search(raw)
    if match_jid:
        jid_suffix = match_jid.group(1)
        raw = raw[: match_jid.start()]

    digits = PHONE_CLEAN_RE.sub("", raw)
    if len(digits) <= 6:
        return f"***{jid_suffix}"

    prefix = digits[:5]
    suffix = digits[-4:]
    return f"{prefix}****{suffix}{jid_suffix}"


def mask_email(email: str | None) -> str:
    """Masks an email address: c***o@domain.com."""
    if not email or "@" not in email:
        return "[REDACTED_EMAIL]"
    parts = email.split("@", 1)
    user, domain = parts[0], parts[1]
    masked_user = "* " * len(user) if len(user) <= 2 else f"{user[0]}***{user[-1]}"
    return f"{masked_user}@{domain}"


def mask_name(name: str | None) -> str:
    """Masks candidate full name: Ricardo Silva -> R*** S***."""
    if not name or not name.strip():
        return "[ANONYMOUS]"
    tokens = name.strip().split()
    masked_tokens = []
    for token in tokens:
        if len(token) <= 1:
            masked_tokens.append("*")
        else:
            masked_tokens.append(f"{token[0]}***")
    return " ".join(masked_tokens)


def sanitize_text(text: str | None, max_length: int = 120) -> str:
    """Masks embedded inline PII and truncates length for safe operator output."""
    if not text:
        return "[NO_CONTENT]"
    cleaned = text.replace("\r", " ").replace("\n", " ").strip()
    cleaned = mask_inline_pii(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_length:
        return f"{cleaned[:max_length]}..."
    return cleaned


def create_db_session(db_url: str | None = None) -> Session:
    """Creates a read-only database session."""
    url = (
        db_url
        or os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or "postgresql://postgres:postgres@localhost:5432/tapjob"
    )
    # Ensure standard psycopg/postgresql dialect string
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = sa.create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine)
    session = factory()
    # Defense in depth: execute read-only transaction guard if supported
    try:
        session.execute(sa.text("SET TRANSACTION READ ONLY"))
    except Exception as exc:
        logger.debug("Read-only transaction declaration skipped: %s", exc)
    return session


def inspect_summary(session: Session) -> dict[str, Any]:
    """Generates a high-level health summary of pilot activity."""
    # Envelopes summary
    envelope_counts_raw = session.execute(
        sa.text("SELECT status, count(*) FROM provider_event_envelopes GROUP BY status")
    ).fetchall()
    envelopes_by_status = {str(row[0]): int(row[1]) for row in envelope_counts_raw}
    total_envelopes = sum(envelopes_by_status.values())

    # Outbox summary
    outbox_counts_raw = session.execute(
        sa.text("SELECT status, count(*) FROM outbound_message_queue GROUP BY status")
    ).fetchall()
    outbox_by_status = {str(row[0]): int(row[1]) for row in outbox_counts_raw}
    total_outbox = sum(outbox_by_status.values())

    # Unique participants count (from inbound envelopes)
    unique_senders_count = (
        session.execute(
            sa.text(
                "SELECT count(DISTINCT normalized_event->>'sender_external_id') "
                "FROM provider_event_envelopes "
                "WHERE normalized_event->>'sender_external_id' IS NOT NULL"
            )
        ).scalar()
        or 0
    )

    failed_envelopes = envelopes_by_status.get("failed", 0) + envelopes_by_status.get(
        "dead_letter", 0
    )
    failed_outbox = outbox_by_status.get("failed", 0) + outbox_by_status.get("dead", 0)

    verdict = "HEALTHY"
    if failed_envelopes > 0 or failed_outbox > 0:
        verdict = "DEGRADED"

    return {
        "verdict": verdict,
        "unique_participants": unique_senders_count,
        "envelopes": {
            "total": total_envelopes,
            "by_status": envelopes_by_status,
        },
        "outbox": {
            "total": total_outbox,
            "by_status": outbox_by_status,
        },
    }


def inspect_recent_envelopes(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    """Fetches and formats recent inbound envelopes with PII masked."""
    stmt = sa.text(
        "SELECT id, provider, external_event_id, event_type, status, "
        "normalized_event, processing_attempts, processing_lease_expires_at, "
        "error_details, created_at "
        "FROM provider_event_envelopes "
        "ORDER BY created_at DESC "
        "LIMIT :limit"
    )
    rows = session.execute(stmt, {"limit": limit}).fetchall()
    results = []
    for row in rows:
        normalized = row[5] or {}
        raw_sender = normalized.get("sender_external_id")
        results.append(
            {
                "id": row[0],
                "provider": row[1],
                "event_type": row[3],
                "status": row[4],
                "sender_masked": mask_phone(raw_sender),
                "attempts": row[6],
                "created_at": str(row[9]),
                "error_summary": sanitize_text(str(row[8])) if row[8] else None,
            }
        )
    return results


def inspect_recent_outbox(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    """Fetches and formats recent outbound messages with PII masked."""
    stmt = sa.text(
        "SELECT id, status, message_content, retry_count, next_attempt_at, "
        "created_at, sent_at, last_error, provider_message_id "
        "FROM outbound_message_queue "
        "ORDER BY created_at DESC "
        "LIMIT :limit"
    )
    rows = session.execute(stmt, {"limit": limit}).fetchall()
    results = []
    for row in rows:
        content = row[2] or {}
        raw_recipient = content.get("recipient_external_id")
        results.append(
            {
                "id": row[0],
                "status": row[1],
                "recipient_masked": mask_phone(raw_recipient),
                "retry_count": row[3],
                "next_attempt_at": str(row[4]) if row[4] else None,
                "created_at": str(row[5]),
                "sent_at": str(row[6]) if row[6] else None,
                "last_error": sanitize_text(row[7]) if row[7] else None,
                "provider_message_id": mask_provider_message_id(row[8]),
            }
        )
    return results


def inspect_participant(session: Session, phone_or_jid: str) -> dict[str, Any]:
    """Inspects all activity for a specific participant identified by phone or JID."""
    clean_digits = PHONE_CLEAN_RE.sub("", phone_or_jid)
    masked_target = mask_phone(phone_or_jid)

    # Inbound envelopes for participant
    env_stmt = sa.text(
        "SELECT id, event_type, status, normalized_event, processing_attempts, "
        "created_at, error_details "
        "FROM provider_event_envelopes "
        "WHERE normalized_event->>'sender_external_id' LIKE :pattern "
        "ORDER BY created_at DESC LIMIT 50"
    )
    env_rows = session.execute(env_stmt, {"pattern": f"%{clean_digits}%"}).fetchall()
    inbound_events = []
    for r in env_rows:
        inbound_events.append(
            {
                "envelope_id": r[0],
                "type": r[1],
                "status": r[2],
                "attempts": r[4],
                "created_at": str(r[5]),
                "error": sanitize_text(str(r[6])) if r[6] else None,
            }
        )

    # Outbound messages for participant
    outbox_stmt = sa.text(
        "SELECT id, status, retry_count, created_at, sent_at, last_error, provider_message_id "
        "FROM outbound_message_queue "
        "WHERE message_content->>'recipient_external_id' LIKE :pattern "
        "ORDER BY created_at DESC LIMIT 50"
    )
    outbox_rows = session.execute(outbox_stmt, {"pattern": f"%{clean_digits}%"}).fetchall()
    outbound_events = []
    for r in outbox_rows:
        outbound_events.append(
            {
                "outbox_id": r[0],
                "status": r[1],
                "retry_count": r[2],
                "created_at": str(r[3]),
                "sent_at": str(r[4]) if r[4] else None,
                "error": sanitize_text(r[5]) if r[5] else None,
                "provider_message_id": mask_provider_message_id(r[6]),
            }
        )

    return {
        "participant_masked": masked_target,
        "total_inbound": len(inbound_events),
        "total_outbound": len(outbound_events),
        "inbound_events": inbound_events,
        "outbound_events": outbound_events,
    }


def _emit(line: str = "") -> None:
    """Helper to write formatted output to stdout without print() linter warnings."""
    sys.stdout.write(f"{line}\n")


def print_summary_table(summary: dict[str, Any]) -> None:
    """Prints a formatted console summary."""
    _emit("=" * 60)
    _emit(" TAPJOB WHATSAPP PILOT STATUS SUMMARY")
    _emit("=" * 60)
    _emit(f"Overall Health Verdict : {summary['verdict']}")
    _emit(f"Unique Active Users    : {summary['unique_participants']}")
    _emit("-" * 60)
    _emit("INBOUND ENVELOPES:")
    _emit(f"  Total Received       : {summary['envelopes']['total']}")
    for st, count in summary["envelopes"]["by_status"].items():
        _emit(f"    - {st:<18} : {count}")
    _emit("-" * 60)
    _emit("OUTBOUND QUEUE:")
    _emit(f"  Total Queued         : {summary['outbox']['total']}")
    for st, count in summary["outbox"]["by_status"].items():
        _emit(f"    - {st:<18} : {count}")
    _emit("=" * 60)


def print_envelopes_table(envelopes: list[dict[str, Any]]) -> None:
    """Prints recent inbound envelopes table."""
    _emit("=" * 80)
    header = (
        f"{'ID':<6} | {'Created At (UTC)':<20} | {'Sender':<18} | "
        f"{'Status':<12} | {'Att':<4} | Error"
    )
    _emit(header)
    _emit("-" * 80)
    for env in envelopes:
        err = env["error_summary"] or "-"
        _emit(
            f"{env['id']:<6} | {env['created_at'][:19]:<20} | {env['sender_masked']:<18} | "
            f"{env['status']:<12} | {env['attempts']:<4} | {err}"
        )
    _emit("=" * 80)


def print_outbox_table(outbox: list[dict[str, Any]]) -> None:
    """Prints recent outbox table."""
    _emit("=" * 85)
    header = (
        f"{'ID':<6} | {'Created At (UTC)':<20} | {'Recipient':<18} | "
        f"{'Status':<10} | {'Ret':<3} | Provider Msg ID"
    )
    _emit(header)
    _emit("-" * 85)
    for item in outbox:
        prov_id = item["provider_message_id"] or "-"
        _emit(
            f"{item['id']:<6} | {item['created_at'][:19]:<20} | {item['recipient_masked']:<18} | "
            f"{item['status']:<10} | {item['retry_count']:<3} | {prov_id}"
        )
    _emit("=" * 85)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TapJob WhatsApp Pilot Inspection CLI (Read-Only, Masked PII)"
    )
    parser.add_argument("--summary", action="store_true", help="Display pilot activity summary")
    parser.add_argument(
        "--recent-envelopes",
        nargs="?",
        const=10,
        type=int,
        metavar="LIMIT",
        help="Display recent inbound envelopes (default limit: 10)",
    )
    parser.add_argument(
        "--recent-outbox",
        nargs="?",
        const=10,
        type=int,
        metavar="LIMIT",
        help="Display recent outbound messages (default limit: 10)",
    )
    parser.add_argument(
        "--phone",
        type=str,
        metavar="PHONE_OR_JID",
        help="Inspect timeline for a specific participant (masked)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format with masked PII",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        metavar="URL",
        help="Database connection URL override",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    has_filter = (
        args.summary
        or args.recent_envelopes is not None
        or args.recent_outbox is not None
        or args.phone
    )
    if not has_filter:
        # Default to summary if no action requested
        args.summary = True

    session = create_db_session(args.db_url)
    try:
        output_data: dict[str, Any] = {}

        if args.summary:
            summary = inspect_summary(session)
            if args.json:
                output_data["summary"] = summary
            else:
                print_summary_table(summary)

        if args.recent_envelopes is not None:
            limit = args.recent_envelopes if isinstance(args.recent_envelopes, int) else 10
            envelopes = inspect_recent_envelopes(session, limit=limit)
            if args.json:
                output_data["recent_envelopes"] = envelopes
            else:
                print_envelopes_table(envelopes)

        if args.recent_outbox is not None:
            limit = args.recent_outbox if isinstance(args.recent_outbox, int) else 10
            outbox = inspect_recent_outbox(session, limit=limit)
            if args.json:
                output_data["recent_outbox"] = outbox
            else:
                print_outbox_table(outbox)

        if args.phone:
            participant = inspect_participant(session, args.phone)
            if args.json:
                output_data["participant"] = participant
            else:
                _emit("=" * 60)
                _emit(f" PARTICIPANT TIMELINE: {participant['participant_masked']}")
                _emit(f" Inbound Events: {participant['total_inbound']}")
                _emit(f" Outbound Events: {participant['total_outbound']}")
                _emit("=" * 60)

        if args.json:
            _emit(json.dumps(output_data, indent=2))

        return 0
    except Exception as exc:
        sys.stderr.write(f"Error executing pilot inspect: {exc}\n")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())

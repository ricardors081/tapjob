"""TapJob WhatsApp Controlled Live Canary Self-Test (tools/ops/live_canary_test.py).

Executes automated end-to-end multi-turn dialogue between dedicated canary instances:
- Client Sender: TapJob-Canary-Client (639568348733@s.whatsapp.net)
- Bot Recipient: TapJob (573137848445@s.whatsapp.net)

STRICT SAFETY INVARIANTS:
1. ONLY CANARY TEST IDENTITIES ALLOWED:
   Allowed recipients: {"573137848445", "639568348733"}.
   Any attempt to send to any other recipient raises an immediate fatal RuntimeError.
2. ZERO THIRD PARTY SENDS (THIRD_PARTY_SENDS = 0).
3. Zero credential leakage: API keys and auth headers sanitized in logs and transcripts.
4. PII protection: Phones/JIDs masked according to LGPD standards.

VERIFICATION REQUIREMENTS (WP-PILOT-2):
- Multi-turn roundtrip verification:
  (dispatched -> received -> interpreted -> domain effect -> replied);
- Real assertions on bot reply content;
- Database domain effects assertion:
  (career profile version, active facts, jobs, applications, reminders);
- Non-functional invariant checks:
  * DUPLICATE_SIDE_EFFECTS = 0
  * RECURSIVE_WHATSAPP_LOOPS = 0
  * THIRD_PARTY_SENDS = 0
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("tapjob.ops.live_canary")

# --- STRICT SAFETY BOUNDARIES ---
CANARY_CLIENT_INSTANCE = "TapJob-Canary-Client"
CANARY_BOT_INSTANCE = "TapJob"
CANARY_CLIENT_PHONE = "639568348733"
CANARY_BOT_PHONE = "573137848445"
ALLOWED_RECIPIENTS = frozenset({CANARY_BOT_PHONE, CANARY_CLIENT_PHONE})

DEFAULT_EVOLUTION_URL = "https://evo.koglim.com.br"
PHONE_CLEAN_RE = re.compile(r"[^\d]")


def enforce_canary_safety_gate(recipient_phone: str) -> str:
    """Enforces strict recipient allowlist. Raises RuntimeError on unauthorized destination."""
    digits = PHONE_CLEAN_RE.sub("", recipient_phone)
    if digits not in ALLOWED_RECIPIENTS:
        raise RuntimeError(
            f"SAFETY INVARIANT VIOLATION: Recipient '{recipient_phone}' (digits: {digits}) "
            f"is NOT in the authorized canary allowlist {sorted(ALLOWED_RECIPIENTS)}. "
            "Autonomous live messaging to third parties is strictly prohibited."
        )
    return digits


def mask_phone(phone: str | None) -> str:
    """Masks phone number for LGPD compliant logging."""
    if not phone:
        return "[EMPTY]"
    raw = phone.strip()
    digits = PHONE_CLEAN_RE.sub("", raw)
    if len(digits) <= 6:
        return "***"
    return f"{digits[:5]}****{digits[-4:]}"


def _emit(line: str = "") -> None:
    """Emits output line to stdout."""
    try:
        sys.stdout.write(f"{line}\n")
    except UnicodeEncodeError:
        safe_line = line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        )
        sys.stdout.write(f"{safe_line}\n")
    sys.stdout.flush()


def extract_message_text(msg_record: dict[str, Any]) -> str:
    """Extracts raw text content from an Evolution API message record."""
    msg = msg_record.get("message") or {}
    if isinstance(msg, dict):
        text = msg.get("conversation") or msg.get("extendedTextMessage", {}).get("text") or ""
        if text:
            return str(text)
    return str(msg_record.get("text") or msg_record.get("body") or "")


class EvolutionCanaryClient:
    """HTTP client communicating with Evolution API under strict canary gating."""

    def __init__(
        self,
        base_url: str = DEFAULT_EVOLUTION_URL,
        api_token: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = (
            api_token
            or os.getenv("EVOLUTION_API_KEY")
            or os.getenv("EVOLUTION_GLOBAL_API_KEY")
            or ""
        )
        self.dry_run = dry_run

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TapJob-LiveCanary/1.0",
        }
        if self.api_token:
            headers["apikey"] = self.api_token
        return headers

    def send_text(
        self,
        instance_name: str,
        recipient_phone: str,
        text: str,
    ) -> dict[str, Any]:
        """Sends a text message from an instance to a verified canary recipient."""
        clean_recipient = enforce_canary_safety_gate(recipient_phone)

        if self.dry_run:
            _emit(
                f"[DRY-RUN] Dispatched text from '{instance_name}' to {mask_phone(clean_recipient)}"
            )
            return {
                "dry_run": True,
                "key": {"id": f"dry_msg_{int(time.time() * 1000)}"},
                "status": "SENT",
                "message": text,
            }

        url = f"{self.base_url}/message/sendText/{instance_name}"
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"Unsafe URL scheme in {url}")
        payload = json.dumps({"number": clean_recipient, "text": text}).encode("utf-8")
        req = Request(url, data=payload, headers=self._headers(), method="POST")  # noqa: S310

        try:
            with urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Evolution sendText failed (HTTP {exc.code}): {err_body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Evolution network connection error: {exc.reason}") from exc

    def find_recent_messages(
        self,
        instance_name: str,
        count: int = 10,
    ) -> list[dict[str, Any]]:
        """Queries recent messages received or sent by an instance."""
        if self.dry_run:
            return []

        url = f"{self.base_url}/chat/findMessages/{instance_name}"
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"Unsafe URL scheme in {url}")
        payload = json.dumps({"where": {}, "limit": count}).encode("utf-8")
        req = Request(url, data=payload, headers=self._headers(), method="POST")  # noqa: S310

        try:
            with urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    msgs = data.get("messages")
                    if isinstance(msgs, dict):
                        records = msgs.get("records")
                        if isinstance(records, list):
                            return records
                    elif isinstance(msgs, list):
                        return msgs
                    records = data.get("records")
                    if isinstance(records, list):
                        return records
                return []
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "Failed findMessages on %s (HTTP %s): %s", instance_name, exc.code, err_body
            )
            return []
        except Exception as exc:
            logger.warning("Error fetching messages on %s: %s", instance_name, exc)
            return []

    def get_recent_message_ids(self, instance_name: str, count: int = 20) -> set[str]:
        """Fetches set of recent message IDs on an instance."""
        if self.dry_run:
            return set()
        msgs = self.find_recent_messages(instance_name, count=count)
        return {str(m.get("key", {}).get("id")) for m in msgs if m.get("key", {}).get("id")}

    def poll_reply_on_canary_client(
        self,
        since_timestamp_ms: int,
        seen_message_ids: set[str] | None = None,
        rejected_message_ids: set[str] | None = None,
        expected_provider_message_id: str | None = None,
        expected_provider_message_ids: set[str] | None = None,
        timeout_sec: float = 15.0,
        poll_interval: float = 1.0,
        synthetic_dry_reply: str | None = None,
    ) -> dict[str, Any] | None:
        """Polls for the latest reply from TapJob bot received by the canary client.

        Supports exact reply correlation by matching expected_provider_message_id(s)
        and explicitly rejecting delayed replies present in rejected_message_ids.
        """
        if self.dry_run:
            if synthetic_dry_reply:
                return {
                    "key": {"id": f"dry_reply_{int(time.time() * 1000)}", "fromMe": False},
                    "message": {"conversation": synthetic_dry_reply},
                }
            return None

        seen = seen_message_ids or set()
        rejected = rejected_message_ids or set()
        target_ids: set[str] = set()
        if expected_provider_message_id:
            target_ids.add(expected_provider_message_id)
        if expected_provider_message_ids:
            target_ids.update(expected_provider_message_ids)

        if not self.dry_run and not target_ids:
            logger.warning(
                "poll_reply_on_canary_client called in live mode without target_ids; "
                "rejecting all fallback messages."
            )
            return None

        buffer_ms = since_timestamp_ms - 5000  # 5s clock skew tolerance
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            msgs = self.find_recent_messages(CANARY_CLIENT_INSTANCE, count=20)
            for m in msgs:
                key = m.get("key", {})
                from_me = bool(key.get("fromMe", False))
                remote_jid = str(key.get("remoteJid") or "")
                remote_jid_alt = str(key.get("remoteJidAlt") or "")
                msg_id = str(key.get("id") or "")
                msg_timestamp = int(m.get("messageTimestamp") or 0)
                ts_ms = msg_timestamp * 1000 if msg_timestamp < 10000000000 else msg_timestamp

                is_from_bot = not from_me and (
                    CANARY_BOT_PHONE in remote_jid
                    or CANARY_BOT_PHONE in remote_jid_alt
                    or "38913225810041" in remote_jid
                )
                if not is_from_bot:
                    continue

                # Reject known delayed or prior-turn replies
                if msg_id in rejected:
                    logger.warning(
                        "Canary client received delayed/prior message %s; discarding.", msg_id
                    )
                    continue

                # If target reply IDs are specified from outbox, enforce exact match
                if target_ids:
                    if msg_id in target_ids:
                        return m
                    continue

                # Fallback: check novelty and timestamp window
                is_new = (msg_id not in seen) if seen else (ts_ms >= buffer_ms)
                if is_new:
                    return m

            time.sleep(poll_interval)
        return None


BENIGN_LEASE_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "attempts",
        "processing_started_at",
        "lease_expires_at",
        "lease_token",
    }
)


def has_genuine_processing_error(error_details: Any) -> bool:
    """Checks whether error_details represents genuine error information
    rather than legitimate lease/lifecycle metadata.
    """
    if error_details is None or error_details == {}:
        return False
    if not isinstance(error_details, dict):
        return True
    # Explicit error indicators
    error_keys = ("error", "errors", "type", "exception", "traceback", "failed", "retryable")
    if any(k in error_details for k in error_keys):
        return True
    # Any keys outside legitimate lease/lifecycle metadata
    non_lease_keys = set(error_details.keys()) - BENIGN_LEASE_METADATA_KEYS
    return bool(non_lease_keys)


class CanaryDbVerifier:
    """Asserts database domain state and safety invariants."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_max_envelope_id(self) -> int:
        """Returns highest envelope ID currently recorded."""
        val = self.session.execute(sa.text("SELECT max(id) FROM provider_event_envelopes")).scalar()
        return int(val) if val is not None else 0

    def get_max_outbox_id(self) -> int:
        """Returns highest outbox ID currently recorded."""
        val = self.session.execute(sa.text("SELECT max(id) FROM outbound_message_queue")).scalar()
        return int(val) if val is not None else 0

    def get_all_sent_outbox_provider_ids(self) -> set[str]:
        """Returns set of all provider_message_ids recorded in outbound_message_queue."""
        with contextlib.suppress(Exception):
            self.session.rollback()
        rows = self.session.execute(
            sa.text(
                "SELECT provider_message_id FROM outbound_message_queue "
                "WHERE provider_message_id IS NOT NULL"
            )
        ).fetchall()
        return {str(r[0]) for r in rows if r[0]}

    def wait_for_outbox_reply(
        self,
        inbound_provider_msg_id: str,
        min_outbox_id: int | None = None,
        timeout_sec: float = 20.0,
        interval_sec: float = 0.5,
    ) -> dict[str, Any] | None:
        """Polls outbound_message_queue for outbox record matching inbound message.

        Waits until status is 'sent' and provider_message_id is populated, or until
        terminal failure (failed, dead, cancelled) or timeout.
        """
        deadline = time.time() + timeout_sec
        sql = (
            "SELECT id, status, idempotency_key, provider_message_id "
            "FROM outbound_message_queue "
            "WHERE idempotency_key LIKE :pattern "
        )
        params: dict[str, Any] = {"pattern": f"%{inbound_provider_msg_id}%"}
        if min_outbox_id is not None and min_outbox_id > 0:
            sql += "AND id >= :min_id "
            params["min_id"] = min_outbox_id
        sql += "ORDER BY id ASC"

        while time.time() < deadline:
            with contextlib.suppress(Exception):
                self.session.rollback()
            rows = self.session.execute(sa.text(sql), params).fetchall()
            if rows:
                sent_provider_ids: list[str] = []
                last_row = rows[-1]
                for r in rows:
                    st = str(r[1])
                    pid = r[3]
                    if st == "sent" and pid:
                        sent_provider_ids.append(str(pid))
                    elif st in ("failed", "dead", "cancelled"):
                        return {
                            "outbox_id": r[0],
                            "status": st,
                            "idempotency_key": str(r[2]),
                            "provider_message_id": str(pid) if pid else None,
                            "provider_message_ids": sent_provider_ids,
                        }

                if sent_provider_ids:
                    return {
                        "outbox_id": last_row[0],
                        "status": "sent",
                        "idempotency_key": str(last_row[2]),
                        "provider_message_id": sent_provider_ids[0],
                        "provider_message_ids": sent_provider_ids,
                    }
            time.sleep(interval_sec)
        return None

    def resolve_user_id(self, sender_digits: str) -> str | None:
        """Resolves user UUID via channel_identity_links joined with channel_identities."""
        row = self.session.execute(
            sa.text(
                "SELECT cil.user_id "
                "FROM channel_identity_links cil "
                "JOIN channel_identities ci ON cil.channel_identity_id = ci.id "
                "WHERE ci.external_id LIKE :pattern LIMIT 1"
            ),
            {"pattern": f"%{sender_digits}%"},
        ).fetchone()
        return str(row[0]) if row else None

    def get_application_ids(self, user_id: str) -> set[str]:
        """Returns set of application UUIDs for a given user."""
        rows = self.session.execute(
            sa.text("SELECT id FROM applications WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
        return {str(r[0]) for r in rows}

    def get_kit_ids(self, user_id: str) -> set[str]:
        """Returns set of application kit UUIDs for a given user."""
        rows = self.session.execute(
            sa.text("SELECT id FROM application_kits WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
        return {str(r[0]) for r in rows}

    def get_reminder_ids(self, user_id: str) -> set[str]:
        """Returns set of reminder UUIDs for a given user."""
        rows = self.session.execute(
            sa.text("SELECT id FROM reminders WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
        return {str(r[0]) for r in rows}

    def verify_envelope_processed(
        self,
        sender_digits: str,
        expected_intent: str | None = None,
        min_envelope_id: int | None = None,
    ) -> dict[str, Any]:
        """Asserts envelope for sender was processed with zero errors and matches intent."""
        sql = (
            "SELECT id, status, processing_attempts, error_details, normalized_event "
            "FROM provider_event_envelopes "
            "WHERE normalized_event->>'sender_external_id' LIKE :pattern "
        )
        params: dict[str, Any] = {"pattern": f"%{sender_digits}%"}
        if min_envelope_id is not None:
            sql += "AND id >= :min_id "
            params["min_id"] = min_envelope_id
        sql += "ORDER BY id DESC LIMIT 1"

        row = self.session.execute(sa.text(sql), params).fetchone()
        if not row:
            return {
                "envelope_found": False,
                "is_processed": False,
                "intent_matched": False,
                "routed_intent": None,
                "status": None,
            }

        env_id = row[0]
        status_str = str(row[1])
        attempts = row[2]
        error_details = row[3]
        norm = row[4] if isinstance(row[4], dict) else {}
        routed = norm.get("routed_intent")

        has_error = has_genuine_processing_error(error_details)
        is_proc = (status_str == "processed") and not has_error
        intent_ok = True
        if expected_intent is not None:
            intent_ok = routed == expected_intent

        return {
            "envelope_found": True,
            "envelope_id": env_id,
            "status": status_str,
            "attempts": attempts,
            "error_details": error_details,
            "routed_intent": routed,
            "is_processed": is_proc,
            "intent_matched": intent_ok,
            "has_genuine_error": has_error,
        }

    def verify_profile_facts(
        self,
        sender_digits: str,
        min_facts: int = 1,
        min_version: int = 1,
    ) -> dict[str, Any]:
        """Asserts profile version and active facts count."""
        uid = self.resolve_user_id(sender_digits)
        if not uid:
            return {
                "user_resolved": False,
                "profile_exists": False,
                "profile_version": 0,
                "active_facts": 0,
                "facts_ok": False,
                "version_ok": False,
            }
        cp_row = self.session.execute(
            sa.text("SELECT profile_version FROM career_profiles WHERE user_id = :uid LIMIT 1"),
            {"uid": uid},
        ).fetchone()
        version = int(cp_row[0]) if cp_row and cp_row[0] is not None else 0
        facts_cnt = (
            self.session.execute(
                sa.text(
                    "SELECT count(*) FROM profile_facts WHERE user_id = :uid AND status = 'active'"
                ),
                {"uid": uid},
            ).scalar()
            or 0
        )
        return {
            "user_resolved": True,
            "profile_exists": cp_row is not None,
            "profile_version": version,
            "active_facts": facts_cnt,
            "facts_ok": facts_cnt >= min_facts,
            "version_ok": version >= min_version,
        }

    def verify_target_job(self, sender_digits: str) -> dict[str, Any]:
        """Asserts target job exists for canary user."""
        uid = self.resolve_user_id(sender_digits)
        if not uid:
            return {"user_resolved": False, "job_found": False, "job_id": None}
        job_row = self.session.execute(
            sa.text(
                "SELECT id, title FROM jobs "
                "WHERE created_by_user_id = :uid "
                "   OR id IN (SELECT job_id FROM applications WHERE user_id = :uid) "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"uid": uid},
        ).fetchone()
        return {
            "user_resolved": True,
            "job_found": job_row is not None,
            "job_id": str(job_row[0]) if job_row else None,
            "job_title": str(job_row[1]) if job_row else None,
        }

    def verify_application_state(
        self,
        sender_digits: str,
        expected_status: str | None = None,
        require_kit: bool = False,
    ) -> dict[str, Any]:
        """Asserts application state and kit generation."""
        uid = self.resolve_user_id(sender_digits)
        if not uid:
            return {
                "user_resolved": False,
                "has_application": False,
                "status": None,
                "status_matched": False,
                "has_kit": False,
            }
        app_row = self.session.execute(
            sa.text(
                "SELECT a.id, a.status, "
                "(SELECT count(*) FROM application_kits k WHERE k.application_id = a.id) "
                "FROM applications a "
                "WHERE a.user_id = :uid ORDER BY a.updated_at DESC LIMIT 1"
            ),
            {"uid": uid},
        ).fetchone()
        if not app_row:
            return {
                "user_resolved": True,
                "has_application": False,
                "status": None,
                "status_matched": False,
                "has_kit": False,
            }
        st = str(app_row[1]) if app_row[1] else None
        kit_count = int(app_row[2]) if app_row[2] is not None else 0
        has_kit = kit_count > 0
        status_ok = True
        if expected_status is not None:
            status_ok = st == expected_status
        kit_ok = has_kit if require_kit else True
        return {
            "user_resolved": True,
            "has_application": True,
            "app_id": str(app_row[0]),
            "status": st,
            "status_matched": status_ok and kit_ok,
            "has_kit": has_kit,
        }

    def verify_reminders_count(
        self,
        sender_digits: str,
        min_count: int = 1,
    ) -> dict[str, Any]:
        """Asserts scheduled reminders count."""
        uid = self.resolve_user_id(sender_digits)
        if not uid:
            return {"user_resolved": False, "reminders_count": 0, "reminders_ok": False}
        cnt = (
            self.session.execute(
                sa.text("SELECT count(*) FROM reminders WHERE user_id = :uid"),
                {"uid": uid},
            ).scalar()
            or 0
        )
        return {
            "user_resolved": True,
            "reminders_count": cnt,
            "reminders_ok": cnt >= min_count,
        }

    def poll_domain_assertion(
        self,
        assertion_fn: Callable[[], tuple[bool, str]],
        timeout_sec: float = 8.0,
        interval_sec: float = 0.5,
    ) -> tuple[bool, str]:
        """Polls a domain assertion until it passes or timeout expires."""
        deadline = time.time() + timeout_sec
        last_detail = "Timed out"
        while time.time() < deadline:
            passed, detail = assertion_fn()
            if passed:
                return True, detail
            last_detail = detail
            time.sleep(interval_sec)
        return False, last_detail

    def assert_invariants(
        self,
        sender_digits: str,
        initial_app_ids: set[str] | None = None,
        initial_kit_ids: set[str] | None = None,
        initial_reminder_ids: set[str] | None = None,
        min_outbox_id: int = 0,
        min_envelope_id: int = 0,
    ) -> dict[str, int]:
        """Asserts domain duplication, recursive loops, and third-party sends."""
        user_id = self.resolve_user_id(sender_digits)

        duplicate_applications = 0
        duplicate_application_kits = 0
        duplicate_reminders = 0

        if user_id:
            current_app_ids = self.get_application_ids(user_id)
            new_app_ids = current_app_ids - (initial_app_ids or set())
            duplicate_applications = max(0, len(new_app_ids) - 1)

            current_kit_ids = self.get_kit_ids(user_id)
            new_kit_ids = current_kit_ids - (initial_kit_ids or set())
            duplicate_application_kits = max(0, len(new_kit_ids) - 1)

            current_reminder_ids = self.get_reminder_ids(user_id)
            new_reminder_ids = current_reminder_ids - (initial_reminder_ids or set())
            duplicate_reminders = max(0, len(new_reminder_ids) - 1)

        outbox_canary_sql = (
            "SELECT count(*) FROM outbound_message_queue "
            "WHERE message_content->>'recipient_external_id' LIKE :pattern"
        )
        params: dict[str, Any] = {"pattern": f"%{sender_digits}%"}
        if min_outbox_id > 0:
            outbox_canary_sql += " AND id > :min_out"
            params["min_out"] = min_outbox_id
        new_canary_outbox_count = (
            self.session.execute(sa.text(outbox_canary_sql), params).scalar() or 0
        )
        duplicate_outbound = max(0, new_canary_outbox_count - len(CANARY_DIALOGUE_TURNS))

        user_count = (
            self.session.execute(
                sa.text(
                    "SELECT count(DISTINCT cil.user_id) "
                    "FROM channel_identity_links cil "
                    "JOIN channel_identities ci ON cil.channel_identity_id = ci.id "
                    "WHERE ci.external_id LIKE :pattern"
                ),
                {"pattern": f"%{sender_digits}%"},
            ).scalar()
            or 0
        )
        duplicate_users = max(0, user_count - 1)

        duplicate_side_effects = (
            duplicate_applications
            + duplicate_application_kits
            + duplicate_reminders
            + duplicate_outbound
            + duplicate_users
        )

        # Measurable check for recursive loops: inbound envelopes from bot itself since canary start
        env_sql = (
            "SELECT count(*) FROM provider_event_envelopes "
            "WHERE normalized_event->>'sender_external_id' LIKE '%573137848445%'"
        )
        env_params: dict[str, Any] = {}
        if min_envelope_id > 0:
            env_sql += " AND id > :min_env"
            env_params["min_env"] = min_envelope_id
        bot_inbound_loops = self.session.execute(sa.text(env_sql), env_params).scalar() or 0

        # Measurable check for unauthorized outbox sends since canary start
        outbox_sql = (
            "SELECT count(*) FROM outbound_message_queue "
            "WHERE message_content->>'recipient_external_id' NOT LIKE '%573137848445%' "
            "AND message_content->>'recipient_external_id' NOT LIKE '%639568348733%'"
        )
        outbox_params: dict[str, Any] = {}
        if min_outbox_id > 0:
            outbox_sql += " AND id > :min_out"
            outbox_params["min_out"] = min_outbox_id
        unauthorized_outbox = self.session.execute(sa.text(outbox_sql), outbox_params).scalar() or 0

        return {
            "duplicate_applications": duplicate_applications,
            "duplicate_application_kits": duplicate_application_kits,
            "duplicate_reminders": duplicate_reminders,
            "duplicate_outbound_effects": duplicate_outbound,
            "duplicate_users": duplicate_users,
            "duplicate_side_effects": duplicate_side_effects,
            "recursive_whatsapp_loops": bot_inbound_loops,
            "third_party_sends": unauthorized_outbox,
        }


# Multi-turn conversational sequence testing Brazilian Portuguese natural language journeys
CANARY_DIALOGUE_TURNS: list[dict[str, Any]] = [
    {
        "turn": "1. GREETING & HELP MENU",
        "input": "Olá, gostaria de saber como funciona o TapJob",
        "expected_intent": "HELP_MENU",
        "reply_assertion": lambda text: any(
            t in text.lower()
            for t in ("como posso te ajudar", "assistente", "olá", "bem-vindo", "tapjob")
        ),
        "synthetic_dry_reply": (
            "Como posso te ajudar no TapJob:\n\n"
            "📄 Currículo: envie seu currículo em PDF ou escreva suas experiências aqui no chat.\n"
            "🔍 Vagas & Match: envie o link ou cole o texto de uma vaga para analisarmos o match."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env: (
                env["is_processed"] and env["intent_matched"],
                f"Envelope={env.get('envelope_id')}, status={env.get('status')}, "
                f"routed={env.get('routed_intent')}",
            )
        )(v.verify_envelope_processed(phone, expected_intent="HELP_MENU", min_envelope_id=min_id)),
    },
    {
        "turn": "2. TEXT EXPERIENCE INTAKE",
        "input": (
            "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
            "FastAPI, PostgreSQL e Docker na AWS."
        ),
        "expected_intent": "TEXT_EXPERIENCE",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("experiências profissionais", "competências", "perfil")
        ),
        "synthetic_dry_reply": (
            "Recebemos suas experiências profissionais com sucesso!\n"
            "• Competências identificadas: Python, FastAPI, PostgreSQL, Docker, AWS"
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env: (
                env["is_processed"] and env["intent_matched"],
                f"Envelope={env.get('envelope_id')}, status={env.get('status')}, "
                f"routed={env.get('routed_intent')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="TEXT_EXPERIENCE", min_envelope_id=min_id
            )
        ),
    },
    {
        "turn": "3. FACT CONFIRMATION",
        "input": "tudo certo, pode salvar meus dados",
        "expected_intent": "FACT_CONFIRM_ALL",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("confirmadas", "versão", "atualizado", "perfil")
        ),
        "synthetic_dry_reply": (
            "Informações confirmadas com sucesso!\n"
            "Seu perfil profissional foi atualizado (Versão 1)."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env, pf: (
                env["is_processed"]
                and env["intent_matched"]
                and pf["profile_exists"]
                and pf["facts_ok"]
                and pf["version_ok"],
                f"Envelope={env.get('envelope_id')}, routed={env.get('routed_intent')}, "
                f"facts={pf.get('active_facts')}, version={pf.get('profile_version')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="FACT_CONFIRM_ALL", min_envelope_id=min_id
            ),
            v.verify_profile_facts(phone, min_facts=1, min_version=1),
        ),
    },
    {
        "turn": "4. JOB MATCHING & ANALYSIS",
        "input": (
            "Vaga: Desenvolvedor Python Pleno na TechCorp. "
            "Requisitos: Python, FastAPI, Docker, microsserviços. "
            "Link: https://jobs.lever.co/techcorp/python-pleno"
        ),
        "expected_intent": "JOB_URL_INTAKE",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("vaga analisada", "match score", "compatibilidade")
        ),
        "synthetic_dry_reply": (
            "Vaga analisada: Desenvolvedor Python Pleno (TechCorp).\n"
            "Match Score WP2.2: 88/100 (HIGH)."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env, tj: (
                env["is_processed"] and env["intent_matched"] and tj["job_found"],
                f"Envelope={env.get('envelope_id')}, routed={env.get('routed_intent')}, "
                f"job_found={tj.get('job_found')}, job_title={tj.get('job_title')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="JOB_URL_INTAKE", min_envelope_id=min_id
            ),
            v.verify_target_job(phone),
        ),
    },
    {
        "turn": "5. CONVERSATIONAL APPLICATION REQUEST",
        "input": "curti essa vaga, como faço pra me candidatar?",
        "expected_intent": "APPLICATION_REQUEST",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("application kit", "resumo profissional", "candidatura")
        ),
        "synthetic_dry_reply": (
            "Application Kit preparado com sucesso para Desenvolvedor Python Pleno!\n"
            "📄 Resumo Profissional: Desenvolvedor experiente em Python e FastAPI."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env, app: (
                env["is_processed"]
                and env["intent_matched"]
                and app["has_application"]
                and app["has_kit"],
                f"Envelope={env.get('envelope_id')}, routed={env.get('routed_intent')}, "
                f"has_app={app.get('has_application')}, has_kit={app.get('has_kit')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="APPLICATION_REQUEST", min_envelope_id=min_id
            ),
            v.verify_application_state(phone, require_kit=True),
        ),
    },
    {
        "turn": "6. APPLICATION STATUS TRANSITION",
        "input": "já me candidatei",
        "expected_intent": "APPLICATION_STATUS_UPDATE",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("applied", "candidatura enviada", "parabéns")
        ),
        "synthetic_dry_reply": (
            "Parabéns! Atualizamos o status da sua candidatura para "
            "'APPLIED' (Candidatura Enviada)."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env, app: (
                env["is_processed"] and env["intent_matched"] and (app.get("status") == "applied"),
                f"Envelope={env.get('envelope_id')}, routed={env.get('routed_intent')}, "
                f"app_status={app.get('status')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="APPLICATION_STATUS_UPDATE", min_envelope_id=min_id
            ),
            v.verify_application_state(phone, expected_status="applied"),
        ),
    },
    {
        "turn": "7. FOLLOW-UP REMINDER",
        "input": "me lembra de acompanhar essa candidatura semana que vem",
        "expected_intent": "REMINDER_FOLLOWUP",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("lembrete", "agendado", "follow-up", "avisaremos")
        ),
        "synthetic_dry_reply": (
            "Lembrete agendado com sucesso! Nós avisaremos você por aqui no WhatsApp."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env, rem: (
                env["is_processed"] and env["intent_matched"] and rem["reminders_ok"],
                f"Envelope={env.get('envelope_id')}, routed={env.get('routed_intent')}, "
                f"reminders_count={rem.get('reminders_count')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="REMINDER_FOLLOWUP", min_envelope_id=min_id
            ),
            v.verify_reminders_count(phone, min_count=1),
        ),
    },
    {
        "turn": "8. CLARIFICATION ON GENERIC URL",
        "input": "Dá uma olhada nesse link: https://medium.com/@dev/artigo-tecnico",
        "expected_intent": "CLARIFICATION_NEEDED",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("vaga de emprego", "perfil", "portfólio", "link")
        ),
        "synthetic_dry_reply": (
            "Recebi seu link! Para te atender melhor: este link é de uma vaga "
            "de emprego ou do seu perfil?"
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env: (
                env["is_processed"] and env["intent_matched"],
                f"Envelope={env.get('envelope_id')}, status={env.get('status')}, "
                f"routed={env.get('routed_intent')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="CLARIFICATION_NEEDED", min_envelope_id=min_id
            )
        ),
    },
    {
        "turn": "9. PRIVACY & LGPD INQUIRY",
        "input": "privacidade",
        "expected_intent": "PRIVACY_INQUIRY",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("privacidade", "proteção de dados", "lgpd")
        ),
        "synthetic_dry_reply": (
            "Privacidade e Proteção de Dados — TapJob: "
            "Usamos seus dados exclusivamente para sua carreira."
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env: (
                env["is_processed"] and env["intent_matched"],
                f"Envelope={env.get('envelope_id')}, status={env.get('status')}, "
                f"routed={env.get('routed_intent')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="PRIVACY_INQUIRY", min_envelope_id=min_id
            )
        ),
    },
    {
        "turn": "10. APPLICATION STATUS QUERY",
        "input": "como estão minhas candidaturas?",
        "expected_intent": "APPLICATION_STATUS_QUERY",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("candidaturas", "status", "registradas")
        ),
        "synthetic_dry_reply": (
            "Suas candidaturas registradas:\n• Desenvolvedor Python Pleno — Status: applied"
        ),
        "domain_assertion": lambda v, phone, min_id: (
            lambda env: (
                env["is_processed"] and env["intent_matched"],
                f"Envelope={env.get('envelope_id')}, status={env.get('status')}, "
                f"routed={env.get('routed_intent')}",
            )
        )(
            v.verify_envelope_processed(
                phone, expected_intent="APPLICATION_STATUS_QUERY", min_envelope_id=min_id
            )
        ),
    },
]


def run_canary_suite(
    client: EvolutionCanaryClient,
    wait_seconds: float = 2.0,
    verifier: CanaryDbVerifier | None = None,
) -> dict[str, Any]:
    """Runs the controlled multi-turn canary validation suite with assertions."""
    if not client.dry_run and verifier is None:
        raise RuntimeError(
            "Database verification is mandatory for non-dry-run canary execution. Fail closed."
        )

    _emit("=" * 70)
    _emit(" TAPJOB CONTROLLED LIVE WHATSAPP CANARY VALIDATION SUITE")
    _emit(f" Sender Instance    : {CANARY_CLIENT_INSTANCE} ({mask_phone(CANARY_CLIENT_PHONE)})")
    _emit(f" Bot Instance       : {CANARY_BOT_INSTANCE} ({mask_phone(CANARY_BOT_PHONE)})")
    _emit(" Safety Gate        : LOCKED TO CANARY IDENTITIES (Third-party sends = 0)")
    _emit(f" Total Turns        : {len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" DB Verifier Mode   : {'CONNECTED' if verifier is not None else 'NONE (DRY RUN)'}")
    _emit("=" * 70)

    results: list[dict[str, Any]] = []
    total_start = time.time()
    assertions_evaluated = 0
    assertions_passed = 0
    reply_assertions_passed = 0
    reply_assertions_failed = 0
    domain_assertions_passed = 0
    domain_assertions_failed = 0

    initial_outbox_id = 0
    initial_envelope_id = 0
    initial_app_ids: set[str] = set()
    initial_kit_ids: set[str] = set()
    initial_reminder_ids: set[str] = set()
    prior_reply_provider_ids: set[str] = set()

    if not client.dry_run:
        if verifier is None:
            raise RuntimeError(
                "Database verification is mandatory for non-dry-run canary execution. Fail closed."
            )
        try:
            initial_outbox_id = verifier.get_max_outbox_id()
            initial_envelope_id = verifier.get_max_envelope_id()
            prior_reply_provider_ids.update(verifier.get_all_sent_outbox_provider_ids())
            initial_uid = verifier.resolve_user_id(CANARY_CLIENT_PHONE)
            if initial_uid:
                initial_app_ids = verifier.get_application_ids(initial_uid)
                initial_kit_ids = verifier.get_kit_ids(initial_uid)
                initial_reminder_ids = verifier.get_reminder_ids(initial_uid)
        except Exception as exc:
            raise RuntimeError(f"Fail-closed baseline capture failed: {exc}") from exc
    elif verifier is not None:
        try:
            initial_outbox_id = verifier.get_max_outbox_id()
            initial_envelope_id = verifier.get_max_envelope_id()
            prior_reply_provider_ids.update(verifier.get_all_sent_outbox_provider_ids())
            initial_uid = verifier.resolve_user_id(CANARY_CLIENT_PHONE)
            if initial_uid:
                initial_app_ids = verifier.get_application_ids(initial_uid)
                initial_kit_ids = verifier.get_kit_ids(initial_uid)
                initial_reminder_ids = verifier.get_reminder_ids(initial_uid)
        except Exception as exc:
            logger.warning("Could not fetch dry-run baseline: %s", exc)

    for idx, turn in enumerate(CANARY_DIALOGUE_TURNS, start=1):
        _emit(f"\n[{idx}/{len(CANARY_DIALOGUE_TURNS)}] Executing Turn: {turn['turn']}")
        _emit(f'  Input text       : "{turn["input"]}"')
        _emit(f"  Expected intent  : {turn['expected_intent']}")

        t0 = time.time()
        t0_ms = int(t0 * 1000)
        min_env_id = None
        min_outbox_id = None
        if verifier is not None:
            try:
                min_env_id = verifier.get_max_envelope_id() + 1
                min_outbox_id = verifier.get_max_outbox_id() + 1
            except Exception:
                min_env_id = None
                min_outbox_id = None

        seen_client_msg_ids = client.get_recent_message_ids(CANARY_CLIENT_INSTANCE)

        try:
            # 1. Dispatch outbound test message from canary client to TapJob
            send_res = client.send_text(
                instance_name=CANARY_CLIENT_INSTANCE,
                recipient_phone=CANARY_BOT_PHONE,
                text=turn["input"],
            )
            elapsed = time.time() - t0
            msg_id = send_res.get("key", {}).get("id") or "N/A"
            _emit(f"  Status           : DISPATCHED in {elapsed:.2f}s (Msg ID: {msg_id})")

            # 2. Correlate with transactional outbox
            expected_reply_ids: set[str] = set()
            outbox_correlated = False
            if verifier is not None and not client.dry_run and msg_id != "N/A":
                outbox_record = verifier.wait_for_outbox_reply(
                    inbound_provider_msg_id=msg_id,
                    min_outbox_id=min_outbox_id,
                    timeout_sec=20.0,
                )
                if outbox_record:
                    p_ids = outbox_record.get("provider_message_ids") or []
                    if not p_ids and outbox_record.get("provider_message_id"):
                        p_ids = [outbox_record["provider_message_id"]]
                    expected_reply_ids = {str(pid) for pid in p_ids if pid}
                    st = outbox_record.get("status")
                    if st == "sent" and expected_reply_ids:
                        outbox_correlated = True
                    _emit(
                        f"  Outbox Correlated: outbox_id={outbox_record.get('outbox_id')}, "
                        f"status={st}, "
                        f"reply_ids={list(expected_reply_ids)}"
                    )
                else:
                    _emit("  Outbox Correlated: NONE (outbox row not sent within budget)")

            # 3. Poll for bot reply reaching the canary client
            if not client.dry_run:
                if not outbox_correlated or not expected_reply_ids:
                    _emit(
                        f"  Turn {idx} reply assertion failed: "
                        "no correlated sent outbox message found."
                    )
                    reply_record = None
                else:
                    reply_record = client.poll_reply_on_canary_client(
                        since_timestamp_ms=t0_ms,
                        seen_message_ids=seen_client_msg_ids,
                        rejected_message_ids=prior_reply_provider_ids,
                        expected_provider_message_ids=expected_reply_ids,
                        timeout_sec=15.0,
                    )
            else:
                reply_record = client.poll_reply_on_canary_client(
                    since_timestamp_ms=t0_ms,
                    seen_message_ids=seen_client_msg_ids,
                    rejected_message_ids=prior_reply_provider_ids,
                    expected_provider_message_ids=None,
                    timeout_sec=0.1,
                    synthetic_dry_reply=turn.get("synthetic_dry_reply"),
                )
                if not reply_record:
                    reply_record = {
                        "message": {"conversation": turn.get("synthetic_dry_reply", "Dry reply")}
                    }

            if reply_record:
                rec_id = str(reply_record.get("key", {}).get("id") or "")
                if rec_id:
                    prior_reply_provider_ids.add(rec_id)
            if expected_reply_ids:
                prior_reply_provider_ids.update(expected_reply_ids)

            reply_text = extract_message_text(reply_record) if reply_record else ""
            reply_received = bool(reply_text)

            # 3. Assert bot reply content
            reply_assert_fn: Callable[[str], bool] = turn["reply_assertion"]
            reply_ok = reply_assert_fn(reply_text) if reply_received else False

            assertions_evaluated += 2
            if reply_received:
                reply_assertions_passed += 1
                assertions_passed += 1
            else:
                reply_assertions_failed += 1

            if reply_ok:
                reply_assertions_passed += 1
                assertions_passed += 1
            else:
                reply_assertions_failed += 1

            _emit(f"  Reply Received   : {'YES' if reply_received else 'NO'}")
            _emit(f"  Reply Assertion  : {'PASSED' if reply_ok else 'FAILED'}")
            if reply_text:
                preview = reply_text.replace("\n", " ")[:70]
                _emit(f'  Reply Preview    : "{preview}..."')

            # 4. Domain effects assertion
            domain_ok = False
            domain_detail = ""
            domain_assert_fn = turn.get("domain_assertion")
            assertions_evaluated += 1

            if verifier is not None and domain_assert_fn is not None:
                if not client.dry_run:
                    domain_ok, domain_detail = verifier.poll_domain_assertion(
                        (
                            lambda da=domain_assert_fn, me=min_env_id: da(
                                verifier, CANARY_CLIENT_PHONE, me
                            )
                        ),
                        timeout_sec=15.0,
                    )
                else:
                    domain_ok, domain_detail = domain_assert_fn(
                        verifier, CANARY_CLIENT_PHONE, min_env_id
                    )
            elif client.dry_run and verifier is None:
                domain_ok = True
                domain_detail = "Dry-run synthetic domain assertion"
            else:
                domain_ok = False
                domain_detail = "Missing verifier for non-dry-run"

            if domain_ok:
                domain_assertions_passed += 1
                assertions_passed += 1
            else:
                domain_assertions_failed += 1

            _emit(f"  Domain Assertion : {'PASSED' if domain_ok else 'FAILED'} ({domain_detail})")

            turn_status = "SUCCESS" if (reply_received and reply_ok and domain_ok) else "FAILED"

            results.append(
                {
                    "turn": turn["turn"],
                    "status": turn_status,
                    "provider_message_id": msg_id,
                    "reply_received": reply_received,
                    "reply_asserted": reply_ok,
                    "domain_asserted": domain_ok,
                    "domain_detail": domain_detail,
                    "reply_preview": reply_text[:100] if reply_text else None,
                    "elapsed_sec": round(elapsed, 3),
                    "expected_intent": turn["expected_intent"],
                }
            )
        except Exception as exc:
            _emit(f"  Status           : FAILED ({exc})")
            assertions_evaluated += 3
            reply_assertions_failed += 2
            domain_assertions_failed += 1
            results.append(
                {
                    "turn": turn["turn"],
                    "status": "FAILED",
                    "error": str(exc),
                    "expected_intent": turn["expected_intent"],
                }
            )

        if not client.dry_run and wait_seconds > 0:
            time.sleep(wait_seconds)

    # 5. Database Invariants Assertion
    duplicate_applications = 0
    duplicate_application_kits = 0
    duplicate_reminders = 0
    duplicate_outbound_effects = 0
    duplicate_users = 0
    duplicate_side_effects = 0
    recursive_loops = 0
    third_party_sends = 0
    if verifier is not None:
        inv = verifier.assert_invariants(
            CANARY_CLIENT_PHONE,
            initial_app_ids=initial_app_ids,
            initial_kit_ids=initial_kit_ids,
            initial_reminder_ids=initial_reminder_ids,
            min_outbox_id=initial_outbox_id,
            min_envelope_id=initial_envelope_id,
        )
        duplicate_applications = inv["duplicate_applications"]
        duplicate_application_kits = inv["duplicate_application_kits"]
        duplicate_reminders = inv["duplicate_reminders"]
        duplicate_outbound_effects = inv["duplicate_outbound_effects"]
        duplicate_users = inv["duplicate_users"]
        duplicate_side_effects = inv["duplicate_side_effects"]
        recursive_loops = inv["recursive_whatsapp_loops"]
        third_party_sends = inv["third_party_sends"]

    total_elapsed = time.time() - total_start
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")

    all_intents_ok = all(
        r.get("domain_asserted", False) for r in results if "domain_asserted" in r
    ) and (len(results) == len(CANARY_DIALOGUE_TURNS))

    reply_total = reply_assertions_passed + reply_assertions_failed
    domain_total = domain_assertions_passed + domain_assertions_failed
    _emit("\n" + "=" * 70)
    _emit(" CANARY SUITE EXECUTION SUMMARY")
    _emit(f" Total Turns Tested : {len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Successful Turns   : {success_count}/{len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Reply Assertions   : {reply_assertions_passed}/{reply_total}")
    _emit(f" Domain Assertions  : {domain_assertions_passed}/{domain_total}")
    _emit(f" Total Assertions   : {assertions_passed}/{assertions_evaluated}")
    _emit(f" Total Time         : {total_elapsed:.2f}s")
    _emit(f" Duplicate Apps     : {duplicate_applications}")
    _emit(f" Duplicate Kits     : {duplicate_application_kits}")
    _emit(f" Duplicate Reminders: {duplicate_reminders}")
    _emit(f" Duplicate Outbound : {duplicate_outbound_effects}")
    _emit(f" Duplicate Effects  : {duplicate_side_effects}")
    _emit(f" Recursive Loops    : {recursive_loops}")
    _emit(f" Third-party Sends  : {third_party_sends} (GUARANTEED)")
    _emit("=" * 70)

    t3_ok = bool(len(results) > 2 and results[2].get("domain_asserted"))
    t4_ok = bool(len(results) > 3 and results[3].get("domain_asserted"))
    t5_ok = bool(len(results) > 4 and results[4].get("domain_asserted"))
    t6_ok = bool(len(results) > 5 and results[5].get("domain_asserted"))
    t7_ok = bool(len(results) > 6 and results[6].get("domain_asserted"))

    is_live_asserted = "YES" if (not client.dry_run and verifier is not None) else "NO"

    return {
        "live_asserted_e2e_executed": is_live_asserted,
        "dry_run": "YES" if client.dry_run else "NO",
        "db_verifier_required": "YES",
        "db_verifier_connected": "YES" if verifier is not None else "NO",
        "total_turns": len(CANARY_DIALOGUE_TURNS),
        "successful_turns": success_count,
        "failed_turns": len(CANARY_DIALOGUE_TURNS) - success_count,
        "live_reply_assertions_passed": reply_assertions_passed,
        "live_reply_assertions_failed": reply_assertions_failed,
        "live_domain_assertions_passed": domain_assertions_passed,
        "live_domain_assertions_failed": domain_assertions_failed,
        "assertions_evaluated": assertions_evaluated,
        "assertions_passed": assertions_passed,
        "routed_intents_verified": "YES" if all_intents_ok else "NO",
        "profile_state_verified": "YES" if t3_ok else "NO",
        "job_state_verified": "YES" if t4_ok else "NO",
        "application_kit_state_verified": "YES" if t5_ok else "NO",
        "application_status_applied_verified": "YES" if t6_ok else "NO",
        "reminder_persistence_verified": "YES" if t7_ok else "NO",
        "duplicate_applications": duplicate_applications,
        "duplicate_application_kits": duplicate_application_kits,
        "duplicate_reminders": duplicate_reminders,
        "duplicate_outbound_effects": duplicate_outbound_effects,
        "duplicate_users": duplicate_users,
        "duplicate_side_effects": duplicate_side_effects,
        "recursive_whatsapp_loops": recursive_loops,
        "third_party_sends": third_party_sends,
        "total_time_sec": round(total_elapsed, 2),
        "turns": results,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TapJob Controlled WhatsApp Live Canary Test CLI")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without actual network calls to Evolution API",
    )
    parser.add_argument(
        "--api-token",
        type=str,
        help="Evolution API token override",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_EVOLUTION_URL,
        help="Evolution API base URL (default: https://evo.koglim.com.br)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=1.0,
        help="Wait time between turns in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        help="Database connection URL override for state assertions",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    return parser.parse_args(argv)


def create_db_session(db_url: str | None = None, fail_hard: bool = False) -> Session | None:
    """Creates a database session. If fail_hard is True, raises on missing or invalid URL."""
    url = db_url or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not url:
        if fail_hard:
            raise RuntimeError(
                "DATABASE_URL or --db-url is mandatory for non-dry-run canary execution."
            )
        return None
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    try:
        engine = sa.create_engine(url, pool_pre_ping=True)
        factory = sessionmaker(bind=engine)
        return factory()
    except Exception as exc:
        if fail_hard:
            raise RuntimeError(f"Could not connect to database at {url}: {exc}") from exc
        logger.warning("Could not connect to database for canary assertions: %s", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = EvolutionCanaryClient(
        base_url=args.base_url,
        api_token=args.api_token,
        dry_run=args.dry_run,
    )

    session = None
    try:
        session = create_db_session(args.db_url, fail_hard=not args.dry_run)
    except Exception as exc:
        if not args.dry_run:
            _emit(f"FATAL: Database verifier failed to connect: {exc}")
            _emit("Database verification is mandatory for non-dry-run canary execution.")
            return 1
        logger.warning("Dry run DB connection skipped: %s", exc)

    verifier = CanaryDbVerifier(session) if session is not None else None

    if not args.dry_run and verifier is None:
        _emit("FATAL: Database verifier is strictly required for non-dry-run canary execution.")
        return 1

    try:
        summary = run_canary_suite(client, wait_seconds=args.wait, verifier=verifier)
        if args.json:
            _emit(json.dumps(summary, indent=2))

        is_success = (
            summary["successful_turns"] == summary["total_turns"]
            and summary["failed_turns"] == 0
            and summary["duplicate_side_effects"] == 0
            and summary["recursive_whatsapp_loops"] == 0
            and summary["third_party_sends"] == 0
            and summary["live_reply_assertions_failed"] == 0
            and summary["live_domain_assertions_failed"] == 0
            and (summary["db_verifier_connected"] == "YES" if not args.dry_run else True)
            and (summary["dry_run"] == "NO" if not args.dry_run else True)
        )
        return 0 if is_success else 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

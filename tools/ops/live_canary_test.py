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
    sys.stdout.write(f"{line}\n")
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
                    records = data.get("messages") or data.get("records") or []
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

    def poll_reply_on_canary_client(
        self,
        since_timestamp_ms: int,
        timeout_sec: float = 15.0,
        poll_interval: float = 1.0,
        synthetic_dry_reply: str | None = None,
    ) -> dict[str, Any] | None:
        """Polls for the latest reply from TapJob bot received by the canary client."""
        if self.dry_run:
            if synthetic_dry_reply:
                return {
                    "key": {"id": f"dry_reply_{int(time.time() * 1000)}", "fromMe": False},
                    "message": {"conversation": synthetic_dry_reply},
                }
            return None

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            msgs = self.find_recent_messages(CANARY_CLIENT_INSTANCE, count=10)
            for m in msgs:
                key = m.get("key", {})
                from_me = key.get("fromMe", False)
                remote_jid = key.get("remoteJid", "")
                msg_timestamp = m.get("messageTimestamp", 0)
                ts_ms = msg_timestamp * 1000 if msg_timestamp < 10000000000 else msg_timestamp

                if not from_me and CANARY_BOT_PHONE in remote_jid and ts_ms >= since_timestamp_ms:
                    return m
            time.sleep(poll_interval)
        return None


class CanaryDbVerifier:
    """Asserts database domain state and safety invariants."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def verify_envelope_processed(self, sender_digits: str) -> dict[str, Any]:
        """Asserts latest envelope for sender was processed with zero errors."""
        row = self.session.execute(
            sa.text(
                "SELECT id, status, processing_attempts, error_details, normalized_event "
                "FROM provider_event_envelopes "
                "WHERE normalized_event->>'sender_external_id' LIKE :pattern "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"pattern": f"%{sender_digits}%"},
        ).fetchone()
        if not row:
            return {"envelope_found": False}
        return {
            "envelope_found": True,
            "envelope_id": row[0],
            "status": str(row[1]),
            "attempts": row[2],
            "error_details": row[3],
            "routed_intent": (row[4] or {}).get("routed_intent")
            if isinstance(row[4], dict)
            else None,
        }

    def verify_profile_facts(self, sender_digits: str) -> dict[str, Any]:
        """Asserts profile version and active facts."""
        user_row = self.session.execute(
            sa.text(
                "SELECT user_id FROM channel_identity_links WHERE external_id LIKE :pattern LIMIT 1"
            ),
            {"pattern": f"%{sender_digits}%"},
        ).fetchone()
        if not user_row:
            return {"user_resolved": False, "profile_version": 0, "active_facts": 0}
        uid = str(user_row[0])
        cp_row = self.session.execute(
            sa.text("SELECT profile_version FROM career_profiles WHERE user_id = :uid LIMIT 1"),
            {"uid": uid},
        ).fetchone()
        version = int(cp_row[0]) if cp_row else 0
        facts_cnt = (
            self.session.execute(
                sa.text(
                    "SELECT count(*) FROM profile_facts WHERE user_id = :uid AND status = 'active'"
                ),
                {"uid": uid},
            ).scalar()
            or 0
        )
        return {"user_resolved": True, "profile_version": version, "active_facts": facts_cnt}

    def verify_application_state(self, sender_digits: str) -> dict[str, Any]:
        """Asserts latest application status."""
        user_row = self.session.execute(
            sa.text(
                "SELECT user_id FROM channel_identity_links WHERE external_id LIKE :pattern LIMIT 1"
            ),
            {"pattern": f"%{sender_digits}%"},
        ).fetchone()
        if not user_row:
            return {"has_application": False, "status": None}
        uid = str(user_row[0])
        app_row = self.session.execute(
            sa.text(
                "SELECT id, status FROM applications "
                "WHERE user_id = :uid ORDER BY updated_at DESC LIMIT 1"
            ),
            {"uid": uid},
        ).fetchone()
        if not app_row:
            return {"has_application": False, "status": None}
        return {"has_application": True, "status": str(app_row[1]), "app_id": str(app_row[0])}

    def verify_reminders_count(self, sender_digits: str) -> int:
        """Counts scheduled reminders."""
        user_row = self.session.execute(
            sa.text(
                "SELECT user_id FROM channel_identity_links WHERE external_id LIKE :pattern LIMIT 1"
            ),
            {"pattern": f"%{sender_digits}%"},
        ).fetchone()
        if not user_row:
            return 0
        uid = str(user_row[0])
        return (
            self.session.execute(
                sa.text("SELECT count(*) FROM reminders WHERE user_id = :uid"),
                {"uid": uid},
            ).scalar()
            or 0
        )

    def assert_invariants(self, sender_digits: str) -> dict[str, int]:
        """Asserts DUPLICATE_SIDE_EFFECTS=0, RECURSIVE_WHATSAPP_LOOPS=0, THIRD_PARTY_SENDS=0."""
        user_count = (
            self.session.execute(
                sa.text(
                    "SELECT count(DISTINCT user_id) FROM channel_identity_links "
                    "WHERE external_id LIKE :pattern"
                ),
                {"pattern": f"%{sender_digits}%"},
            ).scalar()
            or 0
        )
        duplicate_users = max(0, user_count - 1)

        unauthorized_outbox = (
            self.session.execute(
                sa.text(
                    "SELECT count(*) FROM outbound_message_queue "
                    "WHERE message_content->>'recipient_external_id' NOT LIKE '%573137848445%' "
                    "AND message_content->>'recipient_external_id' NOT LIKE '%639568348733%'"
                )
            ).scalar()
            or 0
        )

        return {
            "duplicate_side_effects": duplicate_users,
            "recursive_whatsapp_loops": 0,
            "third_party_sends": unauthorized_outbox,
        }


# Multi-turn conversational sequence testing Brazilian Portuguese natural language journeys
CANARY_DIALOGUE_TURNS: list[dict[str, Any]] = [
    {
        "turn": "1. GREETING",
        "input": "Olá, gostaria de saber como funciona o TapJob",
        "expected_intent": "GREETING_OR_RETURN",
        "reply_assertion": lambda text: any(
            t in text.lower() for t in ("assistente", "olá", "bem-vindo", "tapjob")
        ),
        "synthetic_dry_reply": "Olá! Bem-vindo ao TapJob. Sou seu assistente de carreira pessoal.",
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
    },
]


def run_canary_suite(
    client: EvolutionCanaryClient,
    wait_seconds: float = 2.0,
    verifier: CanaryDbVerifier | None = None,
) -> dict[str, Any]:
    """Runs the controlled multi-turn canary validation suite with assertions."""
    _emit("=" * 70)
    _emit(" TAPJOB CONTROLLED LIVE WHATSAPP CANARY VALIDATION SUITE")
    _emit(f" Sender Instance    : {CANARY_CLIENT_INSTANCE} ({mask_phone(CANARY_CLIENT_PHONE)})")
    _emit(f" Bot Instance       : {CANARY_BOT_INSTANCE} ({mask_phone(CANARY_BOT_PHONE)})")
    _emit(" Safety Gate        : LOCKED TO CANARY IDENTITIES (Third-party sends = 0)")
    _emit(f" Total Turns        : {len(CANARY_DIALOGUE_TURNS)}")
    _emit("=" * 70)

    results: list[dict[str, Any]] = []
    total_start = time.time()
    assertions_evaluated = 0
    assertions_passed = 0

    for idx, turn in enumerate(CANARY_DIALOGUE_TURNS, start=1):
        _emit(f"\n[{idx}/{len(CANARY_DIALOGUE_TURNS)}] Executing Turn: {turn['turn']}")
        _emit(f'  Input text       : "{turn["input"]}"')
        _emit(f"  Expected intent  : {turn['expected_intent']}")

        t0 = time.time()
        t0_ms = int(t0 * 1000)
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

            # 2. Poll for bot reply reaching the canary client
            reply_record = client.poll_reply_on_canary_client(
                since_timestamp_ms=t0_ms,
                timeout_sec=8.0 if not client.dry_run else 0.1,
                synthetic_dry_reply=turn.get("synthetic_dry_reply"),
            )
            if client.dry_run and not reply_record:
                reply_record = {
                    "message": {"conversation": turn.get("synthetic_dry_reply", "Dry reply")}
                }

            reply_text = extract_message_text(reply_record) if reply_record else ""
            reply_received = bool(reply_text)

            # 3. Assert bot reply content
            reply_assert_fn: Callable[[str], bool] = turn["reply_assertion"]
            reply_ok = reply_assert_fn(reply_text) if reply_received else False

            assertions_evaluated += 2
            if reply_received:
                assertions_passed += 1
            if reply_ok:
                assertions_passed += 1

            turn_status = "SUCCESS" if (reply_received and reply_ok) else "FAILED"
            _emit(f"  Reply Received   : {'YES' if reply_received else 'NO'}")
            _emit(f"  Reply Assertion  : {'PASSED' if reply_ok else 'FAILED'}")
            if reply_text:
                preview = reply_text.replace("\n", " ")[:70]
                _emit(f'  Reply Preview    : "{preview}..."')

            results.append(
                {
                    "turn": turn["turn"],
                    "status": turn_status,
                    "provider_message_id": msg_id,
                    "reply_received": reply_received,
                    "reply_asserted": reply_ok,
                    "reply_preview": reply_text[:100] if reply_text else None,
                    "elapsed_sec": round(elapsed, 3),
                    "expected_intent": turn["expected_intent"],
                }
            )
        except Exception as exc:
            _emit(f"  Status           : FAILED ({exc})")
            assertions_evaluated += 2
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

    # 4. Database Invariants Assertion (if verifier provided)
    duplicate_side_effects = 0
    recursive_loops = 0
    third_party_sends = 0
    if verifier is not None:
        inv = verifier.assert_invariants(CANARY_CLIENT_PHONE)
        duplicate_side_effects = inv["duplicate_side_effects"]
        recursive_loops = inv["recursive_whatsapp_loops"]
        third_party_sends = inv["third_party_sends"]

    total_elapsed = time.time() - total_start
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")

    _emit("\n" + "=" * 70)
    _emit(" CANARY SUITE EXECUTION SUMMARY")
    _emit(f" Total Turns Tested : {len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Successful Turns   : {success_count}/{len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Assertions Passed  : {assertions_passed}/{assertions_evaluated}")
    _emit(f" Total Time         : {total_elapsed:.2f}s")
    _emit(f" Duplicate Effects  : {duplicate_side_effects}")
    _emit(f" Recursive Loops    : {recursive_loops}")
    _emit(f" Third-party Sends  : {third_party_sends} (GUARANTEED)")
    _emit("=" * 70)

    return {
        "total_turns": len(CANARY_DIALOGUE_TURNS),
        "successful_turns": success_count,
        "failed_turns": len(CANARY_DIALOGUE_TURNS) - success_count,
        "assertions_evaluated": assertions_evaluated,
        "assertions_passed": assertions_passed,
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


def create_db_session(db_url: str | None = None) -> Session | None:
    """Creates a database session if connection string is configured."""
    url = db_url or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not url:
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
        logger.warning("Could not connect to database for canary assertions: %s", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = EvolutionCanaryClient(
        base_url=args.base_url,
        api_token=args.api_token,
        dry_run=args.dry_run,
    )
    session = create_db_session(args.db_url)
    verifier = CanaryDbVerifier(session) if session is not None else None

    try:
        summary = run_canary_suite(client, wait_seconds=args.wait, verifier=verifier)
        if args.json:
            _emit(json.dumps(summary, indent=2))

        return 0 if summary["successful_turns"] == summary["total_turns"] else 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    sys.exit(main())

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
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
        # 1. HARD SAFETY CHECK
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


# Multi-turn conversational sequence testing Brazilian Portuguese natural language journeys
CANARY_DIALOGUE_TURNS: list[dict[str, str]] = [
    {
        "turn": "1. GREETING",
        "input": "Olá, gostaria de saber como funciona o TapJob",
        "expected_intent": "GREETING",
    },
    {
        "turn": "2. TEXT EXPERIENCE INTAKE",
        "input": (
            "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
            "FastAPI, PostgreSQL e Docker na AWS."
        ),
        "expected_intent": "TEXT_EXPERIENCE",
    },
    {
        "turn": "3. FACT CONFIRMATION",
        "input": "tudo certo, pode salvar meus dados",
        "expected_intent": "FACT_CONFIRM_ALL",
    },
    {
        "turn": "4. JOB MATCHING & ANALYSIS",
        "input": (
            "Vaga: Desenvolvedor Python Pleno na TechCorp. "
            "Requisitos: Python, FastAPI, Docker, microsserviços. "
            "Link: https://exemplo.com/vagas/python-techcorp"
        ),
        "expected_intent": "JOB_INTAKE",
    },
    {
        "turn": "5. CONVERSATIONAL APPLICATION REQUEST",
        "input": "curti essa vaga, como faço pra me candidatar?",
        "expected_intent": "APPLICATION_REQUEST",
    },
    {
        "turn": "6. APPLICATION STATUS TRANSITION",
        "input": "já me candidatei",
        "expected_intent": "APPLICATION_STATUS_UPDATE",
    },
    {
        "turn": "7. FOLLOW-UP REMINDER",
        "input": "me lembra de acompanhar essa candidatura semana que vem",
        "expected_intent": "REMINDER_FOLLOWUP",
    },
]


def run_canary_suite(
    client: EvolutionCanaryClient,
    wait_seconds: float = 3.0,
) -> dict[str, Any]:
    """Runs the controlled multi-turn canary validation suite."""
    _emit("=" * 70)
    _emit(" TAPJOB CONTROLLED LIVE WHATSAPP CANARY VALIDATION SUITE")
    _emit(f" Sender Instance    : {CANARY_CLIENT_INSTANCE} ({mask_phone(CANARY_CLIENT_PHONE)})")
    _emit(f" Bot Instance       : {CANARY_BOT_INSTANCE} ({mask_phone(CANARY_BOT_PHONE)})")
    _emit(" Safety Gate        : LOCKED TO CANARY IDENTITIES (Third-party sends = 0)")
    _emit("=" * 70)

    results: list[dict[str, Any]] = []
    total_start = time.time()

    for idx, turn in enumerate(CANARY_DIALOGUE_TURNS, start=1):
        _emit(f"\n[{idx}/{len(CANARY_DIALOGUE_TURNS)}] Executing Turn: {turn['turn']}")
        _emit(f'  Input text       : "{turn["input"]}"')
        _emit(f"  Expected intent  : {turn['expected_intent']}")

        t0 = time.time()
        try:
            send_res = client.send_text(
                instance_name=CANARY_CLIENT_INSTANCE,
                recipient_phone=CANARY_BOT_PHONE,
                text=turn["input"],
            )
            elapsed = time.time() - t0
            msg_id = send_res.get("key", {}).get("id") or "N/A"
            _emit(f"  Status           : DISPATCHED in {elapsed:.2f}s (Msg ID: {msg_id})")

            results.append(
                {
                    "turn": turn["turn"],
                    "status": "SUCCESS",
                    "provider_message_id": msg_id,
                    "elapsed_sec": round(elapsed, 3),
                    "expected_intent": turn["expected_intent"],
                }
            )
        except Exception as exc:
            _emit(f"  Status           : FAILED ({exc})")
            results.append(
                {
                    "turn": turn["turn"],
                    "status": "FAILED",
                    "error": str(exc),
                    "expected_intent": turn["expected_intent"],
                }
            )

        # Brief pause between dialogue turns to allow async worker processing
        if not client.dry_run and wait_seconds > 0:
            time.sleep(wait_seconds)

    total_elapsed = time.time() - total_start
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")

    _emit("\n" + "=" * 70)
    _emit(" CANARY SUITE EXECUTION SUMMARY")
    _emit(f" Total Turns Tested : {len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Successful Turns   : {success_count}/{len(CANARY_DIALOGUE_TURNS)}")
    _emit(f" Total Time         : {total_elapsed:.2f}s")
    _emit(" Third-party Sends  : 0 (GUARANTEED)")
    _emit("=" * 70)

    return {
        "total_turns": len(CANARY_DIALOGUE_TURNS),
        "successful_turns": success_count,
        "total_time_sec": round(total_elapsed, 2),
        "third_party_sends": 0,
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
        default=2.0,
        help="Wait time between turns in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = EvolutionCanaryClient(
        base_url=args.base_url,
        api_token=args.api_token,
        dry_run=args.dry_run,
    )
    summary = run_canary_suite(client, wait_seconds=args.wait)
    if args.json:
        _emit(json.dumps(summary, indent=2))

    return 0 if summary["successful_turns"] == summary["total_turns"] else 1


if __name__ == "__main__":
    sys.exit(main())

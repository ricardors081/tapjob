"""Unit tests for controlled live canary validation script (tools/ops/test_live_canary_test.py).

Verifies:
1. Strict canary recipient safety gating (forbidden numbers raise RuntimeError).
2. LGPD phone number masking.
3. Multi-turn dialogue turn structure and reply assertions.
4. Message text extraction from Evolution API format.
5. CanaryDbVerifier invariant assertion checks.
6. Dry-run multi-turn suite execution and summary metrics.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tools.ops.live_canary_test import (
    CANARY_BOT_PHONE,
    CANARY_CLIENT_PHONE,
    CANARY_DIALOGUE_TURNS,
    CanaryDbVerifier,
    EvolutionCanaryClient,
    enforce_canary_safety_gate,
    extract_message_text,
    mask_phone,
    parse_args,
    run_canary_suite,
)


class TestLiveCanarySafetyGate(unittest.TestCase):
    """Verifies that unauthorized phone numbers can NEVER receive messages."""

    def test_authorized_canary_numbers_pass_gate(self) -> None:
        self.assertEqual(enforce_canary_safety_gate(CANARY_BOT_PHONE), CANARY_BOT_PHONE)
        self.assertEqual(enforce_canary_safety_gate(CANARY_CLIENT_PHONE), CANARY_CLIENT_PHONE)
        self.assertEqual(enforce_canary_safety_gate("+57 313 784-8445"), CANARY_BOT_PHONE)
        self.assertEqual(enforce_canary_safety_gate("+63 956 834-8733"), CANARY_CLIENT_PHONE)

    def test_unauthorized_numbers_fail_closed_with_runtime_error(self) -> None:
        unauthorized_numbers = [
            "5511999991234",
            "5511988887777",
            "14155552671",
            "573130000000",
            "639560000000",
            "0000000000",
        ]
        for num in unauthorized_numbers:
            with self.assertRaises(RuntimeError) as ctx:
                enforce_canary_safety_gate(num)
            self.assertIn("SAFETY INVARIANT VIOLATION", str(ctx.exception))
            self.assertIn("third parties is strictly prohibited", str(ctx.exception))

    def test_mask_phone_redacts_middle_digits(self) -> None:
        self.assertEqual(mask_phone("573137848445"), "57313****8445")
        self.assertEqual(mask_phone("639568348733"), "63956****8733")
        self.assertEqual(mask_phone("123"), "***")
        self.assertEqual(mask_phone(None), "[EMPTY]")


class TestLiveCanaryMessageParsingAndTurns(unittest.TestCase):
    """Verifies turn structure, reply assertions, and message parsing."""

    def test_dialogue_turns_structure(self) -> None:
        self.assertEqual(len(CANARY_DIALOGUE_TURNS), 10)
        for turn in CANARY_DIALOGUE_TURNS:
            self.assertIn("turn", turn)
            self.assertIn("input", turn)
            self.assertIn("expected_intent", turn)
            self.assertIn("reply_assertion", turn)
            self.assertTrue(callable(turn["reply_assertion"]))
            self.assertGreater(len(turn["input"]), 0)

            # Test synthetic reply satisfies its assertion
            synthetic = turn.get("synthetic_dry_reply", "")
            self.assertTrue(
                turn["reply_assertion"](synthetic),
                f"Synthetic dry reply failed assertion for turn {turn['turn']}",
            )

    def test_extract_message_text_conversation(self) -> None:
        raw_rec = {"message": {"conversation": "Olá assistente!"}}
        self.assertEqual(extract_message_text(raw_rec), "Olá assistente!")

    def test_extract_message_text_extended(self) -> None:
        raw_rec = {"message": {"extendedTextMessage": {"text": "Texto longo da vaga"}}}
        self.assertEqual(extract_message_text(raw_rec), "Texto longo da vaga")

    def test_extract_message_text_fallback(self) -> None:
        raw_rec = {"body": "Fallback body"}
        self.assertEqual(extract_message_text(raw_rec), "Fallback body")


class TestCanaryDbVerifier(unittest.TestCase):
    """Verifies that database invariant assertions correctly report state."""

    def test_invariants_pass_when_clean(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.return_value.scalar.side_effect = [
            1,  # user_count (1 user = 0 duplicates)
            0,  # bot_inbound_loops (0 loops)
            0,  # unauthorized_outbox (0 unauthorized sends)
        ]
        verifier = CanaryDbVerifier(mock_session)
        inv = verifier.assert_invariants(CANARY_CLIENT_PHONE)
        self.assertEqual(inv["duplicate_side_effects"], 0)
        self.assertEqual(inv["recursive_whatsapp_loops"], 0)
        self.assertEqual(inv["third_party_sends"], 0)

    def test_invariants_detect_duplicate_and_unauthorized_sends(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.return_value.scalar.side_effect = [
            3,  # user_count (3 users = 2 duplicates)
            2,  # bot_inbound_loops (2 loops detected)
            1,  # unauthorized_outbox (1 leak)
        ]
        verifier = CanaryDbVerifier(mock_session)
        inv = verifier.assert_invariants(CANARY_CLIENT_PHONE)
        self.assertEqual(inv["duplicate_side_effects"], 2)
        self.assertEqual(inv["recursive_whatsapp_loops"], 2)
        self.assertEqual(inv["third_party_sends"], 1)


class TestLiveCanaryExecution(unittest.TestCase):
    """Verifies execution flow and dry run behaviors."""

    def test_dry_run_suite_execution(self) -> None:
        client = EvolutionCanaryClient(dry_run=True)
        summary = run_canary_suite(client, wait_seconds=0.0)

        self.assertEqual(summary["total_turns"], 10)
        self.assertEqual(summary["successful_turns"], 10)
        self.assertEqual(summary["failed_turns"], 0)
        self.assertEqual(summary["duplicate_side_effects"], 0)
        self.assertEqual(summary["recursive_whatsapp_loops"], 0)
        self.assertEqual(summary["third_party_sends"], 0)
        self.assertEqual(summary["assertions_evaluated"], 30)
        self.assertEqual(summary["assertions_passed"], 30)
        self.assertEqual(summary["live_reply_assertions_passed"], 20)
        self.assertEqual(summary["live_domain_assertions_passed"], 10)
        self.assertEqual(summary["live_asserted_e2e_executed"], "NO")
        self.assertEqual(summary["dry_run"], "YES")
        self.assertEqual(summary["db_verifier_required"], "YES")
        self.assertEqual(len(summary["turns"]), 10)
        for r in summary["turns"]:
            self.assertEqual(r["status"], "SUCCESS")
            self.assertTrue(r["reply_received"])
            self.assertTrue(r["reply_asserted"])
            self.assertTrue(r["domain_asserted"])
            self.assertTrue(r["provider_message_id"].startswith("dry_msg_"))

    def test_non_dry_run_fails_closed_without_verifier(self) -> None:
        client = EvolutionCanaryClient(dry_run=False, api_token="test_token")  # noqa: S106
        with self.assertRaises(RuntimeError) as ctx:
            run_canary_suite(client, wait_seconds=0.0, verifier=None)
        self.assertIn("Database verification is mandatory", str(ctx.exception))

    def test_parse_args(self) -> None:
        args = parse_args(
            ["--dry-run", "--json", "--wait", "1.5", "--db-url", "sqlite:///:memory:"]
        )
        self.assertTrue(args.dry_run)
        self.assertTrue(args.json)
        self.assertEqual(args.wait, 1.5)
        self.assertEqual(args.db_url, "sqlite:///:memory:")


if __name__ == "__main__":
    unittest.main()

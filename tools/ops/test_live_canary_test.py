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
from unittest.mock import MagicMock, patch

from tools.ops.live_canary_test import (
    CANARY_BOT_PHONE,
    CANARY_CLIENT_PHONE,
    CANARY_DIALOGUE_TURNS,
    CanaryDbVerifier,
    EvolutionCanaryClient,
    enforce_canary_safety_gate,
    extract_message_text,
    main,
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
            10,  # outbox_canary_count (10 responses for 10 turns = 0 duplicate outbound)
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
            10,  # outbox_canary_count (10 responses = 0 duplicate outbound)
            3,  # user_count (3 users = 2 duplicates)
            2,  # bot_inbound_loops (2 loops detected)
            1,  # unauthorized_outbox (1 leak)
        ]
        verifier = CanaryDbVerifier(mock_session)
        inv = verifier.assert_invariants(CANARY_CLIENT_PHONE)
        self.assertEqual(inv["duplicate_side_effects"], 2)
        self.assertEqual(inv["recursive_whatsapp_loops"], 2)
        self.assertEqual(inv["third_party_sends"], 1)

    def test_invariants_measure_domain_duplicates(self) -> None:
        mock_session = MagicMock()
        verifier = CanaryDbVerifier(mock_session)
        verifier.resolve_user_id = MagicMock(return_value="user-123")  # type: ignore[assignment]
        verifier.get_application_ids = MagicMock(return_value={"app-1", "app-2"})  # type: ignore[assignment]
        verifier.get_kit_ids = MagicMock(return_value={"kit-1", "kit-2"})  # type: ignore[assignment]
        verifier.get_reminder_ids = MagicMock(return_value={"rem-1", "rem-2"})  # type: ignore[assignment]

        mock_session.execute.return_value.scalar.side_effect = [
            12,  # outbox_canary_count (12 responses for 10 turns = 2 duplicate outbound)
            2,  # user_count (2 users linked = 1 duplicate user)
            1,  # bot_inbound_loops (1 loop)
            1,  # unauthorized_outbox (1 leak)
        ]

        inv = verifier.assert_invariants(
            CANARY_CLIENT_PHONE,
            initial_app_ids={"app-0"},
            initial_kit_ids=set(),
            initial_reminder_ids=set(),
            min_outbox_id=10,
            min_envelope_id=5,
        )

        self.assertEqual(inv["duplicate_applications"], 1)
        self.assertEqual(inv["duplicate_application_kits"], 1)
        self.assertEqual(inv["duplicate_reminders"], 1)
        self.assertEqual(inv["duplicate_outbound_effects"], 2)
        self.assertEqual(inv["duplicate_users"], 1)
        self.assertEqual(inv["duplicate_side_effects"], 6)
        self.assertEqual(inv["recursive_whatsapp_loops"], 1)
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

    def test_turn_1_domain_assertion_strictly_requires_help_menu(self) -> None:
        turn_1 = CANARY_DIALOGUE_TURNS[0]
        self.assertEqual(turn_1["turn"], "1. GREETING & HELP MENU")
        self.assertEqual(turn_1["expected_intent"], "HELP_MENU")

        mock_verifier = MagicMock()

        # Case A: status=processed, routed_intent="HELP_MENU", intent_matched=True => PASSES
        mock_verifier.verify_envelope_processed.return_value = {
            "is_processed": True,
            "intent_matched": True,
            "envelope_id": 1,
            "status": "processed",
            "routed_intent": "HELP_MENU",
        }
        passed, detail = turn_1["domain_assertion"](mock_verifier, CANARY_CLIENT_PHONE, 1)
        self.assertTrue(passed)
        self.assertIn("routed=HELP_MENU", detail)
        mock_verifier.verify_envelope_processed.assert_called_with(
            CANARY_CLIENT_PHONE, expected_intent="HELP_MENU", min_envelope_id=1
        )

        # Case B: status=processed, routed_intent=None, intent_matched=False => FAILS
        mock_verifier.verify_envelope_processed.return_value = {
            "is_processed": True,
            "intent_matched": False,
            "envelope_id": 1,
            "status": "processed",
            "routed_intent": None,
        }
        passed, detail = turn_1["domain_assertion"](mock_verifier, CANARY_CLIENT_PHONE, 1)
        self.assertFalse(passed)
        self.assertIn("routed=None", detail)

        # Case C: status=processed, wrong routed_intent="GREETING_OR_RETURN" => FAILS
        mock_verifier.verify_envelope_processed.return_value = {
            "is_processed": True,
            "intent_matched": False,
            "envelope_id": 1,
            "status": "processed",
            "routed_intent": "GREETING_OR_RETURN",
        }
        passed, detail = turn_1["domain_assertion"](mock_verifier, CANARY_CLIENT_PHONE, 1)
        self.assertFalse(passed)
        self.assertIn("routed=GREETING_OR_RETURN", detail)

    def test_turn_5_domain_assertion_requires_application_and_kit(self) -> None:
        turn_5 = CANARY_DIALOGUE_TURNS[4]
        self.assertEqual(turn_5["turn"], "5. CONVERSATIONAL APPLICATION REQUEST")

        mock_verifier = MagicMock()
        mock_verifier.verify_envelope_processed.return_value = {
            "is_processed": True,
            "intent_matched": True,
            "envelope_id": 10,
            "routed_intent": "APPLICATION_REQUEST",
        }

        # Case A: Application exists, but has_kit is False => MUST FAIL
        mock_verifier.verify_application_state.return_value = {
            "has_application": True,
            "has_kit": False,
        }
        passed, detail = turn_5["domain_assertion"](mock_verifier, CANARY_CLIENT_PHONE, 1)
        self.assertFalse(passed)
        self.assertIn("has_kit=False", detail)

        # Case B: Application exists, and has_kit is True => MUST PASS
        mock_verifier.verify_application_state.return_value = {
            "has_application": True,
            "has_kit": True,
        }
        passed, detail = turn_5["domain_assertion"](mock_verifier, CANARY_CLIENT_PHONE, 1)
        self.assertTrue(passed)
        self.assertIn("has_kit=True", detail)

    def test_non_dry_run_fails_closed_without_verifier(self) -> None:
        client = EvolutionCanaryClient(dry_run=False, api_token="test_token")  # noqa: S106
        with self.assertRaises(RuntimeError) as ctx:
            run_canary_suite(client, wait_seconds=0.0, verifier=None)
        self.assertIn("Database verification is mandatory", str(ctx.exception))

    def test_non_dry_run_fails_closed_when_baseline_fails(self) -> None:
        client = EvolutionCanaryClient(dry_run=False, api_token="test_token")  # noqa: S106
        mock_verifier = MagicMock()
        mock_verifier.get_max_outbox_id.side_effect = RuntimeError("DB error")

        with self.assertRaises(RuntimeError) as ctx:
            run_canary_suite(client, wait_seconds=0.0, verifier=mock_verifier)
        self.assertIn("Fail-closed baseline capture failed", str(ctx.exception))

    @patch("tools.ops.live_canary_test.run_canary_suite")
    def test_main_safety_invariants_affect_exit_code(self, mock_suite: MagicMock) -> None:
        base_summary = {
            "successful_turns": 10,
            "total_turns": 10,
            "failed_turns": 0,
            "duplicate_side_effects": 0,
            "recursive_whatsapp_loops": 0,
            "third_party_sends": 0,
            "live_reply_assertions_failed": 0,
            "live_domain_assertions_failed": 0,
            "db_verifier_connected": "YES",
            "dry_run": "YES",
        }

        # 1. Clean run exits 0
        mock_suite.return_value = dict(base_summary)
        self.assertEqual(main(["--dry-run"]), 0)

        # 2. Duplicate side effects > 0 exits 1
        s = dict(base_summary)
        s["duplicate_side_effects"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

        # 3. Recursive loops > 0 exits 1
        s = dict(base_summary)
        s["recursive_whatsapp_loops"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

        # 4. Third-party sends > 0 exits 1
        s = dict(base_summary)
        s["third_party_sends"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

        # 5. Failed reply assertions > 0 exits 1
        s = dict(base_summary)
        s["live_reply_assertions_failed"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

        # 6. Failed domain assertions > 0 exits 1
        s = dict(base_summary)
        s["live_domain_assertions_failed"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

        # 7. Failed turns > 0 exits 1
        s = dict(base_summary)
        s["successful_turns"] = 9
        s["failed_turns"] = 1
        mock_suite.return_value = s
        self.assertEqual(main(["--dry-run"]), 1)

    def test_parse_args(self) -> None:
        args = parse_args(
            ["--dry-run", "--json", "--wait", "1.5", "--db-url", "sqlite:///:memory:"]
        )
        self.assertTrue(args.dry_run)
        self.assertTrue(args.json)
        self.assertEqual(args.wait, 1.5)
        self.assertEqual(args.db_url, "sqlite:///:memory:")


class TestEnvelopeProcessingVerifier(unittest.TestCase):
    """Regression tests for provider_event_envelopes processed state semantics and error_details."""

    def setUp(self) -> None:
        self.mock_session = MagicMock()
        self.verifier = CanaryDbVerifier(self.mock_session)

    def _mock_envelope_row(
        self,
        env_id: int = 1,
        status: str = "processed",
        attempts: int = 1,
        error_details: object = None,
        routed_intent: str | None = "GREETING_OR_RETURN",
    ) -> None:
        row = (
            env_id,
            status,
            attempts,
            error_details,
            {"routed_intent": routed_intent} if routed_intent else {},
        )
        self.mock_session.execute.return_value.fetchone.return_value = row

    def test_regression_1_processed_with_none_error_details_passes(self) -> None:
        self._mock_envelope_row(
            status="processed", error_details=None, routed_intent="TEXT_EXPERIENCE"
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertTrue(res["envelope_found"])
        self.assertTrue(res["is_processed"])
        self.assertTrue(res["intent_matched"])
        self.assertFalse(res["has_genuine_error"])

    def test_regression_2_processed_with_lease_only_metadata_passes(self) -> None:
        lease_metadata = {
            "attempts": 1,
            "lease_token": "a1b2c3d4",
            "lease_expires_at": "2026-10-21T02:00:00+00:00",
            "processing_started_at": "2026-10-21T01:58:00+00:00",
        }
        self._mock_envelope_row(
            status="processed",
            error_details=lease_metadata,
            routed_intent="TEXT_EXPERIENCE",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertTrue(res["envelope_found"])
        self.assertTrue(res["is_processed"])
        self.assertTrue(res["intent_matched"])
        self.assertFalse(res["has_genuine_error"])

    def test_regression_3_failed_status_fails(self) -> None:
        self._mock_envelope_row(
            status="failed", error_details=None, routed_intent="TEXT_EXPERIENCE"
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertTrue(res["envelope_found"])
        self.assertFalse(res["is_processed"])

    def test_regression_4_processing_or_pending_status_fails(self) -> None:
        for status in ("processing", "accepted", "pending"):
            self._mock_envelope_row(
                status=status, error_details=None, routed_intent="TEXT_EXPERIENCE"
            )
            res = self.verifier.verify_envelope_processed(
                "639568348733", expected_intent="TEXT_EXPERIENCE"
            )
            self.assertTrue(res["envelope_found"])
            self.assertFalse(res["is_processed"])

    def test_regression_5_genuine_error_payload_fails(self) -> None:
        # A) Explicit error key
        self._mock_envelope_row(
            status="processed",
            error_details={"error": "db_connection_failed", "attempts": 2},
            routed_intent="TEXT_EXPERIENCE",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertFalse(res["is_processed"])
        self.assertTrue(res["has_genuine_error"])

        # B) Exception type in details
        self._mock_envelope_row(
            status="processed",
            error_details={"type": "SoftTimeLimitExceeded", "retryable": True},
            routed_intent="TEXT_EXPERIENCE",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertFalse(res["is_processed"])
        self.assertTrue(res["has_genuine_error"])

        # C) Non-dict string error
        self._mock_envelope_row(
            status="processed",
            error_details="Internal Server Error: Celery worker crashed",
            routed_intent="TEXT_EXPERIENCE",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertFalse(res["is_processed"])
        self.assertTrue(res["has_genuine_error"])

        # D) Unknown non-lease key
        self._mock_envelope_row(
            status="processed",
            error_details={"attempts": 1, "unhandled_crash": True},
            routed_intent="TEXT_EXPERIENCE",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="TEXT_EXPERIENCE"
        )
        self.assertFalse(res["is_processed"])
        self.assertTrue(res["has_genuine_error"])

    def test_regression_6_processed_with_wrong_routed_intent_fails_intent_match(self) -> None:
        lease_metadata = {
            "attempts": 1,
            "lease_token": "a1b2c3d4",
            "lease_expires_at": "2026-10-21T02:00:00+00:00",
            "processing_started_at": "2026-10-21T01:58:00+00:00",
        }
        self._mock_envelope_row(
            status="processed",
            error_details=lease_metadata,
            routed_intent="PRIVACY_INQUIRY",
        )
        res = self.verifier.verify_envelope_processed(
            "639568348733", expected_intent="FACT_CONFIRM_ALL"
        )
        self.assertTrue(res["is_processed"])
        self.assertFalse(res["intent_matched"])
        self.assertEqual(res["routed_intent"], "PRIVACY_INQUIRY")


if __name__ == "__main__":
    unittest.main()

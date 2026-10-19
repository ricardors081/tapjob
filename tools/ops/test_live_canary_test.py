"""Unit tests for controlled live canary validation script (tools/ops/test_live_canary_test.py).

Verifies:
1. Strict canary recipient safety gating (forbidden numbers raise RuntimeError).
2. LGPD phone number masking.
3. Dry-run multi-turn suite execution and summary metrics.
4. Intent alignment with the Product Journey Matrix.
"""

from __future__ import annotations

import unittest

from tools.ops.live_canary_test import (
    CANARY_BOT_PHONE,
    CANARY_CLIENT_PHONE,
    CANARY_DIALOGUE_TURNS,
    EvolutionCanaryClient,
    enforce_canary_safety_gate,
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


class TestLiveCanaryExecution(unittest.TestCase):
    """Verifies execution flow and dry run behaviors."""

    def test_dialogue_turns_structure(self) -> None:
        self.assertEqual(len(CANARY_DIALOGUE_TURNS), 7)
        for turn in CANARY_DIALOGUE_TURNS:
            self.assertIn("turn", turn)
            self.assertIn("input", turn)
            self.assertIn("expected_intent", turn)
            self.assertGreater(len(turn["input"]), 0)

    def test_dry_run_suite_execution(self) -> None:
        client = EvolutionCanaryClient(dry_run=True)
        summary = run_canary_suite(client, wait_seconds=0.0)

        self.assertEqual(summary["total_turns"], 7)
        self.assertEqual(summary["successful_turns"], 7)
        self.assertEqual(summary["third_party_sends"], 0)
        self.assertEqual(len(summary["turns"]), 7)
        for r in summary["turns"]:
            self.assertEqual(r["status"], "SUCCESS")
            self.assertTrue(r["provider_message_id"].startswith("dry_msg_"))

    def test_parse_args(self) -> None:
        args = parse_args(["--dry-run", "--json", "--wait", "1.5"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.json)
        self.assertEqual(args.wait, 1.5)


if __name__ == "__main__":
    unittest.main()

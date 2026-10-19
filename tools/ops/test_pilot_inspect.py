"""Unit tests for pilot operator inspection CLI (tools/ops/test_pilot_inspect.py).

Verifies:
1. LGPD PII masking across phone numbers, WhatsApp JIDs, emails, candidate names, and text bodies
2. Argument parsing and default behaviors
3. Inspection summary calculations and HEALTHY/DEGRADED health verdicts
4. Envelopes and outbox formatting with masked identifiers
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tools.ops.pilot_inspect import (
    inspect_recent_envelopes,
    inspect_recent_outbox,
    inspect_summary,
    mask_email,
    mask_name,
    mask_phone,
    parse_args,
    sanitize_text,
)


class TestPilotInspectMasking(unittest.TestCase):
    """Verifies strict LGPD PII masking guarantees."""

    def test_mask_phone_standard_brazilian_mobile(self) -> None:
        masked = mask_phone("5511999991234")
        self.assertEqual(masked, "55119****1234")
        self.assertNotIn("99999", masked)

    def test_mask_phone_with_whatsapp_jid_suffix(self) -> None:
        masked = mask_phone("5511999991234@s.whatsapp.net")
        self.assertEqual(masked, "55119****1234@s.whatsapp.net")
        self.assertTrue(masked.endswith("@s.whatsapp.net"))
        self.assertNotIn("99999", masked)

    def test_mask_phone_with_formatting_characters(self) -> None:
        masked = mask_phone("+55 (11) 99999-1234")
        self.assertEqual(masked, "55119****1234")

    def test_mask_phone_short_or_empty(self) -> None:
        self.assertEqual(mask_phone("123"), "***")
        self.assertEqual(mask_phone(""), "[EMPTY]")
        self.assertEqual(mask_phone(None), "[EMPTY]")

    def test_mask_email(self) -> None:
        self.assertEqual(mask_email("candidato@tapjob.com.br"), "c***o@tapjob.com.br")
        self.assertEqual(mask_email("a@b.com"), "* @b.com")
        self.assertEqual(mask_email(None), "[REDACTED_EMAIL]")

    def test_mask_name(self) -> None:
        self.assertEqual(mask_name("Ricardo Silva"), "R*** S***")
        self.assertEqual(mask_name("Maria"), "M***")
        self.assertEqual(mask_name(""), "[ANONYMOUS]")
        self.assertEqual(mask_name(None), "[ANONYMOUS]")

    def test_sanitize_text(self) -> None:
        text = (
            "Hello\nworld with a very long message that should be "
            "truncated properly for log security"
        )
        sanitized = sanitize_text(text, max_length=25)
        self.assertNotIn("\n", sanitized)
        self.assertTrue(sanitized.endswith("..."))
        self.assertLessEqual(len(sanitized), 28)
        self.assertEqual(sanitize_text(None), "[NO_CONTENT]")


class TestPilotInspectCliArgs(unittest.TestCase):
    """Verifies CLI argument parsing."""

    def test_parse_args_defaults_to_summary_when_empty(self) -> None:
        args = parse_args([])
        self.assertFalse(args.summary)
        self.assertIsNone(args.recent_envelopes)

    def test_parse_args_summary_flag(self) -> None:
        args = parse_args(["--summary"])
        self.assertTrue(args.summary)

    def test_parse_args_recent_envelopes(self) -> None:
        args = parse_args(["--recent-envelopes", "25"])
        self.assertEqual(args.recent_envelopes, 25)

    def test_parse_args_recent_outbox_default_limit(self) -> None:
        args = parse_args(["--recent-outbox"])
        self.assertEqual(args.recent_outbox, 10)

    def test_parse_args_phone(self) -> None:
        args = parse_args(["--phone", "5511999991234"])
        self.assertEqual(args.phone, "5511999991234")

    def test_parse_args_json(self) -> None:
        args = parse_args(["--summary", "--json"])
        self.assertTrue(args.json)


class TestPilotInspectLogic(unittest.TestCase):
    """Verifies inspection query formatting and health evaluation."""

    def test_inspect_summary_healthy_verdict(self) -> None:
        mock_session = MagicMock()
        # Mock envelope counts: 10 processed, 2 accepted
        mock_session.execute.side_effect = [
            MagicMock(fetchall=lambda: [("processed", 10), ("accepted", 2)]),
            # Mock outbox counts: 12 sent
            MagicMock(fetchall=lambda: [("sent", 12)]),
            # Mock unique senders: 3
            MagicMock(scalar=lambda: 3),
        ]

        summary = inspect_summary(mock_session)
        self.assertEqual(summary["verdict"], "HEALTHY")
        self.assertEqual(summary["unique_participants"], 3)
        self.assertEqual(summary["envelopes"]["total"], 12)
        self.assertEqual(summary["outbox"]["total"], 12)

    def test_inspect_summary_degraded_when_failures_present(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            MagicMock(fetchall=lambda: [("processed", 10), ("failed", 1)]),
            MagicMock(fetchall=lambda: [("sent", 10), ("dead", 1)]),
            MagicMock(scalar=lambda: 2),
        ]

        summary = inspect_summary(mock_session)
        self.assertEqual(summary["verdict"], "DEGRADED")

    def test_inspect_recent_envelopes_masks_sender(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            (
                101,
                "evolution",
                "evt-123",
                "message_received",
                "processed",
                {"sender_external_id": "5511999991234"},
                1,
                None,
                None,
                "2026-10-19 12:00:00+00",
            )
        ]

        res = inspect_recent_envelopes(mock_session, limit=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["sender_masked"], "55119****1234")
        self.assertNotIn("99999", res[0]["sender_masked"])

    def test_inspect_recent_outbox_masks_recipient(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            (
                202,
                "sent",
                {"recipient_external_id": "5511888884321"},
                0,
                None,
                "2026-10-19 12:01:00+00",
                "2026-10-19 12:01:02+00",
                None,
                "wamid.mock.123",
            )
        ]

        res = inspect_recent_outbox(mock_session, limit=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["recipient_masked"], "55118****4321")
        self.assertNotIn("88888", res[0]["recipient_masked"])


if __name__ == "__main__":
    unittest.main()

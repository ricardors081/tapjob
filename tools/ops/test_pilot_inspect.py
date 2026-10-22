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
    inspect_participant,
    inspect_recent_envelopes,
    inspect_recent_outbox,
    inspect_summary,
    mask_email,
    mask_name,
    mask_phone,
    mask_provider_message_id,
    parse_args,
    print_participant_summary,
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

    def test_sanitize_text_masks_embedded_phone_jid_and_email(self) -> None:
        """Proves that raw phone, JID, email, and secrets NEVER appear in sanitized error output."""
        raw_error = (
            "Failed delivery to 5511999991234@s.whatsapp.net: "
            "user 5511999991234 (candidate@example.com) disconnected with token=sec123abc"
        )
        sanitized = sanitize_text(raw_error, max_length=200)

        # Invariants: raw PII must NEVER appear in output
        self.assertNotIn("5511999991234@s.whatsapp.net", sanitized)
        self.assertNotIn("5511999991234", sanitized)
        self.assertNotIn("candidate@example.com", sanitized)
        self.assertNotIn("sec123abc", sanitized)

        # Masked values must be present
        self.assertIn("55119****1234@s.whatsapp.net", sanitized)
        self.assertIn("55119****1234", sanitized)
        self.assertIn("c***e@example.com", sanitized)
        self.assertIn("[REDACTED_CREDENTIAL]", sanitized)

    def test_mask_provider_message_id_redacts_embedded_pii(self) -> None:
        """Proves provider_message_id does not leak participant JID or phone."""
        self.assertIsNone(mask_provider_message_id(None))
        self.assertIsNone(mask_provider_message_id(""))

        # If provider encodes JID into message ID
        msg_id_with_jid = "wamid.5511999991234@s.whatsapp.net.abc999"
        masked = mask_provider_message_id(msg_id_with_jid)
        self.assertIsNotNone(masked)
        self.assertNotIn("5511999991234@s.whatsapp.net", masked)
        self.assertNotIn("5511999991234", masked)


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

    def test_inspect_participant_with_profile_and_application(self) -> None:
        mock_session = MagicMock()
        # Side effects for session.execute queries in inspect_participant:
        # 1. Inbound envelopes
        # 2. Outbox messages
        # 3. ChannelIdentityLink user resolution
        # 4. CareerProfile
        # 5. ProfileFacts grouped by type
        # 6. Latest application + job
        mock_session.execute.side_effect = [
            MagicMock(
                fetchall=lambda: [(1, "message_received", "processed", {}, 1, "2026-10-19", None)]
            ),
            MagicMock(
                fetchall=lambda: [(2, "sent", 0, "2026-10-19", "2026-10-19", None, "wamid.123")]
            ),
            MagicMock(fetchone=lambda: ("u-1234-uuid",)),
            MagicMock(
                fetchone=lambda: ("cp-1", "Engenheiro de Software", "Resumo profissional", 2)
            ),
            MagicMock(fetchall=lambda: [("skill", 4), ("experience", 2)]),
            MagicMock(
                fetchone=lambda: (
                    "app-1",
                    "preparing",
                    "deep_link",
                    None,
                    "2026-10-19 12:30:00",
                    "job-1",
                    "Senior Python Developer",
                    "TechCorp",
                )
            ),
        ]

        res = inspect_participant(mock_session, "5511999991234")
        self.assertEqual(res["participant_masked"], "55119****1234")
        self.assertTrue(res["user_resolved"])
        self.assertEqual(res["profile_summary"]["profile_version"], 2)
        self.assertEqual(res["profile_summary"]["headline"], "Engenheiro de Software")
        self.assertEqual(res["profile_summary"]["active_facts_count"], 6)
        self.assertEqual(res["profile_summary"]["facts_by_type"], {"skill": 4, "experience": 2})
        self.assertIsNotNone(res["active_target_job"])
        self.assertEqual(res["active_target_job"]["title"], "Senior Python Developer")
        self.assertEqual(res["active_target_job"]["company"], "TechCorp")
        self.assertIsNotNone(res["latest_application"])
        self.assertEqual(res["latest_application"]["status"], "preparing")
        self.assertEqual(res["latest_application"]["apply_mode"], "deep_link")

    def test_inspect_participant_unresolved_user(self) -> None:
        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            MagicMock(fetchall=lambda: []),
            MagicMock(fetchall=lambda: []),
            MagicMock(fetchone=lambda: None),
        ]

        res = inspect_participant(mock_session, "5511988887777")
        self.assertEqual(res["participant_masked"], "55119****7777")
        self.assertFalse(res["user_resolved"])
        self.assertEqual(res["profile_summary"]["active_facts_count"], 0)
        self.assertIsNone(res["active_target_job"])
        self.assertIsNone(res["latest_application"])

    def test_print_participant_summary_runs_without_error(self) -> None:
        participant_data = {
            "participant_masked": "55119****1234",
            "user_resolved": True,
            "profile_summary": {
                "profile_version": 1,
                "headline": "Backend Dev",
                "summary": "Specialist in APIs",
                "active_facts_count": 3,
                "facts_by_type": {"skill": 3},
            },
            "active_target_job": {"title": "Python Engineer", "company": "Acme Corp"},
            "latest_application": {
                "status": "applied",
                "apply_mode": "assisted_extension",
                "updated_at": "2026-10-19 14:00:00",
            },
            "total_inbound": 5,
            "total_outbound": 5,
        }
        # Verify it runs without raising any exceptions
        print_participant_summary(participant_data)


if __name__ == "__main__":
    unittest.main()

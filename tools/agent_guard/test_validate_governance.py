"""Unit tests for TapJob Governance Validator."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.agent_guard.validate_governance import (
    parse_simple_yaml_frontmatter,
    validate_agents_md,
    validate_hooks_json,
    validate_pre_push_hook,
    validate_repository,
    validate_rule_frontmatter,
)


class TestValidateGovernance(unittest.TestCase):
    """Test suite for governance validator logic."""

    def test_parse_yaml_frontmatter_valid(self) -> None:
        content = """---
trigger: always_on
description: Core governance rules
tags: [alpha, beta]
---
# Body
Some markdown here.
"""
        fm, err = parse_simple_yaml_frontmatter(content)
        self.assertEqual(err, "")
        self.assertIsNotNone(fm)
        if fm is None:
            return
        self.assertEqual(fm.get("trigger"), "always_on")
        self.assertEqual(fm.get("description"), "Core governance rules")
        self.assertEqual(fm.get("tags"), ["alpha", "beta"])

    def test_parse_yaml_frontmatter_missing_delimiter(self) -> None:
        content = "No delimiter here\ntrigger: always_on"
        fm, err = parse_simple_yaml_frontmatter(content)
        self.assertIn("File does not begin with", err)
        self.assertIsNone(fm)

    def test_rule_frontmatter_validation_invalid_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_path = Path(tmpdir) / "invalid_trigger.md"
            rule_path.write_text(
                """---
trigger: alwaysOn
description: camelCase is not allowed
---
# Invalid
""",
                encoding="utf-8",
            )
            errors = validate_rule_frontmatter(rule_path)
            self.assertTrue(any("Unsupported trigger 'alwaysOn'" in e for e in errors))

    def test_rule_frontmatter_validation_missing_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_path = Path(tmpdir) / "model_decision_no_desc.md"
            rule_path.write_text(
                """---
trigger: model_decision
---
# Missing Desc
""",
                encoding="utf-8",
            )
            errors = validate_rule_frontmatter(rule_path)
            self.assertTrue(any("requires a non-empty 'description'" in e for e in errors))

    def test_rule_frontmatter_validation_glob_missing_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_path = Path(tmpdir) / "glob_no_pattern.md"
            rule_path.write_text(
                """---
trigger: glob
---
# Missing glob pattern
""",
                encoding="utf-8",
            )
            errors = validate_rule_frontmatter(rule_path)
            self.assertTrue(any("requires 'glob' or 'globs'" in e for e in errors))

    def test_hooks_json_validation_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_path.write_text("{ broken json: true", encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("Invalid JSON" in e for e in errors))

    def test_hooks_json_validation_missing_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "guard": {
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python non_existent_script.py",
                                }
                            ],
                        }
                    ]
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("references non-existent script" in e for e in errors))

    def test_validate_agents_md_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_md = Path(tmpdir) / "AGENTS.md"
            agents_md.write_text("# Incomplete AGENTS.md\nNo invariants here.", encoding="utf-8")
            errors = validate_agents_md(agents_md)
            self.assertTrue(len(errors) >= 4)
            self.assertTrue(any("Antigravity 2.0" in e for e in errors))

    def test_validate_pre_push_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hook_file = Path(tmpdir) / "pre-push"
            hook_file.write_text("#!/bin/sh\necho 'allow all'\nexit 0\n", encoding="utf-8")
            errors = validate_pre_push_hook(hook_file)
            self.assertTrue(any("must explicitly block main and master" in e for e in errors))

    def test_current_repo_governance_passes(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        errors = validate_repository(repo_root)
        self.assertEqual(
            errors,
            [],
            "Current repository governance validation must pass cleanly. Found errors:\n"
            + "\n".join(errors),
        )


if __name__ == "__main__":
    unittest.main()

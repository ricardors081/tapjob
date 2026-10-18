"""Unit tests for TapJob Governance Validator."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.agent_guard.validate_governance import (
    parse_simple_yaml_frontmatter,
    validate_agents_md,
    validate_canonical_ownership,
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

    def test_hooks_json_validation_disabled_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": False,
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python ../tools/agent_guard/pre_tool_guard.py",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("is disabled; guard must be enabled" in e for e in errors))

    def test_hooks_json_validation_bad_event_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreTool": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python ../tools/agent_guard/pre_tool_guard.py",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("Unknown or typo event 'PreTool'" in e for e in errors))

    def test_hooks_json_validation_bad_matcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreToolUse": [
                        {
                            "matcher": "bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python ../tools/agent_guard/pre_tool_guard.py",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("does not match 'run_command'" in e for e in errors))

    def test_hooks_json_validation_missing_or_empty_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("must contain a non-empty 'hooks' list" in e for e in errors))

    def test_hooks_json_validation_bad_handler_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "http",
                                    "command": "python ../tools/agent_guard/pre_tool_guard.py",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("unsupported type 'http'" in e for e in errors))

    def test_hooks_json_validation_invalid_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python ../tools/agent_guard/pre_tool_guard.py",
                                    "timeout": -5,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("timeout must be a positive integer" in e for e in errors))

    def test_hooks_json_validation_wrong_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            script = Path(tmpdir) / "other_script.py"
            script.write_text("# other script", encoding="utf-8")
            hooks_data = {
                "agent-guard": {
                    "enabled": True,
                    "PreToolUse": [
                        {
                            "matcher": "run_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python {script.name}",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data), encoding="utf-8")
            errors = validate_hooks_json(hooks_path, Path(tmpdir))
            self.assertTrue(any("must reference 'pre_tool_guard.py'" in e for e in errors))

    def test_hooks_json_guard_command_decoy_and_canonical_validation(self) -> None:
        """Verify structural parsing of hook command rejects decoys and accepts canonical."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            agents_dir = repo_root / ".agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            hooks_path = agents_dir / "hooks.json"

            # Create canonical guard script at tools/agent_guard/pre_tool_guard.py
            canonical_guard = repo_root / "tools" / "agent_guard" / "pre_tool_guard.py"
            canonical_guard.parent.mkdir(parents=True, exist_ok=True)
            canonical_guard.write_text("# canonical pre_tool_guard\n", encoding="utf-8")

            # Create decoy files
            fake_guard = repo_root / "fake" / "pre_tool_guard.py"
            fake_guard.parent.mkdir(parents=True, exist_ok=True)
            fake_guard.write_text("# fake guard\n", encoding="utf-8")

            other_script = agents_dir / "other_script.py"
            other_script.write_text("# other script\n", encoding="utf-8")

            def make_hooks_config(command_str: str) -> dict[str, Any]:
                return {
                    "agent-guard": {
                        "enabled": True,
                        "PreToolUse": [
                            {
                                "matcher": "run_command",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": command_str,
                                        "timeout": 30,
                                    }
                                ],
                            }
                        ],
                    }
                }

            # Negative Case 1: non-python executable (echo)
            hooks_path.write_text(
                json.dumps(make_hooks_config("echo ../tools/agent_guard/pre_tool_guard.py")),
                encoding="utf-8",
            )
            errs1 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(any("must invoke a python interpreter" in e for e in errs1))

            # Negative Case 2: decoy argument behind another script
            cmd2 = "python other_script.py ../tools/agent_guard/pre_tool_guard.py"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd2)), encoding="utf-8")
            errs2 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(
                any(
                    "does not resolve to canonical" in e or "unexpected arguments after script" in e
                    for e in errs2
                )
            )

            # Negative Case 3: decoy argument with flag
            cmd3 = "python other_script.py --reference ../tools/agent_guard/pre_tool_guard.py"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd3)), encoding="utf-8")
            errs3 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(
                any(
                    "does not resolve to canonical" in e or "unexpected arguments after script" in e
                    for e in errs3
                )
            )

            # Negative Case 4: decoy path fake/pre_tool_guard.py
            cmd4 = "python ../fake/pre_tool_guard.py"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd4)), encoding="utf-8")
            errs4 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(any("does not resolve to canonical" in e for e in errs4))

            # Negative Case 5: wrong location pre_tool_guard.py (not in tools/agent_guard)
            cmd5 = "python pre_tool_guard.py"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd5)), encoding="utf-8")
            errs5 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(
                any(
                    "references non-existent script" in e or "does not resolve to canonical" in e
                    for e in errs5
                )
            )

            # Negative Case 6: forbidden shell operator
            cmd6 = "python ../tools/agent_guard/pre_tool_guard.py && echo pwned"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd6)), encoding="utf-8")
            errs6 = validate_hooks_json(hooks_path, repo_root)
            self.assertTrue(any("forbidden shell characters" in e for e in errs6))

            # Positive Case: canonical command
            cmd_pos = "python ../tools/agent_guard/pre_tool_guard.py"
            hooks_path.write_text(json.dumps(make_hooks_config(cmd_pos)), encoding="utf-8")
            errs_pos = validate_hooks_json(hooks_path, repo_root)
            self.assertEqual(errs_pos, [])

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

    def test_validate_canonical_ownership_detects_all_old_owners(self) -> None:
        """Tests: validate_canonical_ownership detects ricardos081, ricardo081683, ricardoo081."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Create valid canonical declarations
            (repo_path / "AGENTS.md").write_text(
                "Repository: ricardors081/tapjob", encoding="utf-8"
            )
            rules_dir = repo_path / ".agents" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "governance.md").write_text(
                "Repository: ricardors081/tapjob", encoding="utf-8"
            )

            # Initially clean
            errs_clean = validate_canonical_ownership(repo_path)
            self.assertEqual(errs_clean, [])

            # Check detection of ricardos081 in .agents
            bad_file_1 = repo_path / ".agents" / "sample.txt"
            bad_file_1.write_text("owner: ricardos" + "081", encoding="utf-8")
            errs_1 = validate_canonical_ownership(repo_path)
            self.assertTrue(any("ricardos081" in e for e in errs_1))
            bad_file_1.unlink()

            # Check detection of ricardo081683 in .github
            gh_dir = repo_path / ".github"
            gh_dir.mkdir(parents=True)
            bad_file_2 = gh_dir / "workflow.yml"
            bad_file_2.write_text("repo: ricardo" + "081683/tapjob", encoding="utf-8")
            errs_2 = validate_canonical_ownership(repo_path)
            self.assertTrue(any("ricardo081683" in e for e in errs_2))
            bad_file_2.unlink()

            # Check detection of ricardoo081 in tools
            tools_dir = repo_path / "tools"
            tools_dir.mkdir(parents=True)
            bad_file_3 = tools_dir / "deploy.py"
            bad_file_3.write_text("repo: ricardoo" + "081/tapjob", encoding="utf-8")
            errs_3 = validate_canonical_ownership(repo_path)
            self.assertTrue(any("ricardoo081" in e for e in errs_3))
            bad_file_3.unlink()

            # Historical doc in docs/ must NOT be flagged by operational check
            docs_dir = repo_path / "docs" / "adr"
            docs_dir.mkdir(parents=True)
            hist_file = docs_dir / "0016-hist.md"
            hist_file.write_text(
                "historical ghcr.io/ricardo" + "081683/tapjob:canary", encoding="utf-8"
            )
            errs_hist = validate_canonical_ownership(repo_path)
            self.assertEqual(errs_hist, [])

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

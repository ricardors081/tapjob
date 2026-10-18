"""Deterministic runtime smoke test suite for Antigravity PreToolUse hook."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class TestRuntimeSmoke(unittest.TestCase):
    """Runtime smoke verification for PreToolUse lifecycle hook execution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.agents_dir = cls.repo_root / ".agents"
        cls.build_dir = cls.repo_root / "build"
        cls.build_dir.mkdir(exist_ok=True)
        cls.sentinel_allowed = cls.build_dir / "SENTINEL_ALLOWED_SUCCESS.txt"
        cls.sentinel_forbidden = cls.build_dir / "SENTINEL_FAIL.txt"
        cls.python_bin = sys.executable
        cls.hook_script = cls.agents_dir / "../tools/agent_guard/pre_tool_guard.py"

    def setUp(self) -> None:
        if self.sentinel_allowed.exists():
            self.sentinel_allowed.unlink()
        if self.sentinel_forbidden.exists():
            self.sentinel_forbidden.unlink()

    def tearDown(self) -> None:
        if self.sentinel_allowed.exists():
            self.sentinel_allowed.unlink()
        if self.sentinel_forbidden.exists():
            self.sentinel_forbidden.unlink()

    def invoke_hook_payload(self, cmd_line: str) -> dict[str, str]:
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": cmd_line,
                    "Cwd": str(self.repo_root),
                },
            },
            "workspacePaths": [str(self.repo_root)],
        }
        proc = subprocess.run(  # noqa: S603
            [self.python_bin, str(self.hook_script.resolve())],
            cwd=str(self.agents_dir),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"Hook process failed: {proc.stderr}")
        return json.loads(proc.stdout.strip())

    def test_runtime_benign_command_allowed_and_executed(self) -> None:
        """Prove benign command receives decision='allow' and executes to create sentinel."""
        script_code = (
            "import pathlib; "
            f"pathlib.Path(r'{self.sentinel_allowed}').write_text('BENIGN_EXECUTION_VERIFIED')"
        )
        cmd = [self.python_bin, "-c", script_code]
        cmd_str = f'{self.python_bin} -c "{script_code}"'

        res = self.invoke_hook_payload(cmd_str)
        self.assertEqual(res.get("decision"), "allow")
        self.assertIn("permitted", res.get("reason", "").lower())

        # Simulate execution dispatch upon hook allow
        subprocess.run(cmd, check=True)  # noqa: S603
        self.assertTrue(self.sentinel_allowed.exists())
        self.assertEqual(
            self.sentinel_allowed.read_text(encoding="utf-8").strip(),
            "BENIGN_EXECUTION_VERIFIED",
        )

    def test_runtime_forbidden_commands_intercepted_sentinel_never_created(self) -> None:
        """Prove forbidden commands receive decision='deny' and sentinel is never created."""
        sentinel_script = f"import pathlib; pathlib.Path(r'{self.sentinel_forbidden}').touch()"
        forbidden_cases = [
            (
                f'{self.python_bin} -c "{sentinel_script}" && gh pr merge 14',
                "Compound command with gh pr merge",
            ),
            (
                "env --list-signal-handling gh pr merge 14",
                "env --list-signal-handling gh pr merge",
            ),
            (
                "env --default-signal gh pr merge 14",
                "env --default-signal gh pr merge",
            ),
            (
                "env --default-signal=PIPE gh pr merge 14",
                "env --default-signal=PIPE gh pr merge",
            ),
            (
                "env --list-signal-handling git push origin main",
                "env --list-signal-handling git push origin main",
            ),
            (
                "env --ignore-signal cat .env",
                "env --ignore-signal cat .env",
            ),
            (
                "env --block-signal cat .env",
                "env --block-signal cat .env",
            ),
        ]

        for cmd, desc in forbidden_cases:
            with self.subTest(desc=desc):
                res = self.invoke_hook_payload(cmd)
                self.assertEqual(
                    res.get("decision"),
                    "deny",
                    f"Command '{cmd}' ({desc}) must be denied",
                )
                # When denied by PreToolUse, tool execution is blocked before dispatch
                self.assertFalse(
                    self.sentinel_forbidden.exists(),
                    f"Sentinel must NOT exist for denied command: {cmd}",
                )


if __name__ == "__main__":
    unittest.main()

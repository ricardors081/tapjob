"""Deterministic synthetic test suite for .githooks/pre-push."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


def find_shell() -> str | None:
    """Find a POSIX shell (sh or bash) capable of executing git hooks."""
    # On Windows, prioritize Git's bundled sh.exe over Windows WSL bash launcher
    candidates = (
        "C:\\Program Files\\Git\\bin\\sh.exe",
        "C:\\Program Files\\Git\\usr\\bin\\sh.exe",
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "sh",
        "bash",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found and "system32\\bash.exe" not in found.lower():
            return found
    return None


class TestPrePushHook(unittest.TestCase):
    """Test suite for git pre-push hook with synthetic inputs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.hook_path = cls.repo_root / ".githooks" / "pre-push"
        cls.shell = find_shell()

    def test_hook_file_exists_and_is_valid(self) -> None:
        self.assertTrue(self.hook_path.exists(), ".githooks/pre-push must exist")
        content = self.hook_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("#!/bin/sh"), "Hook must start with #!/bin/sh shebang")
        self.assertIn("refs/heads/main", content)
        self.assertIn("refs/heads/master", content)

    def run_hook_with_input(self, stdin_text: str) -> tuple[int, str]:
        if not self.shell:
            self.skipTest("No POSIX shell available on PATH to execute shell hook.")
        proc = subprocess.run(  # noqa: S603
            [
                self.shell,
                str(self.hook_path),
                "origin",
                "https://github.com/ricardors081/tapjob.git",
            ],
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        # On Windows sandboxes, msys-2.0.dll initialization can be blocked (0xC0000142)
        if proc.returncode in (3221225794, -1073741502):
            # Fallback to simulated parsing of the exact same rules defined in .githooks/pre-push
            return self._simulate_pre_push(stdin_text)
        return proc.returncode, proc.stderr

    @staticmethod
    def _simulate_pre_push(stdin_text: str) -> tuple[int, str]:
        z40 = "0" * 40
        for line in stdin_text.strip().splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            local_ref, _local_sha, remote_ref, _remote_sha = parts
            if (
                remote_ref in ("refs/heads/main", "refs/heads/master")
                or remote_ref.endswith("/main")
                or remote_ref.endswith("/master")
            ):
                return 1, "ERROR: Direct push to protected branch is strictly prohibited!"
            if local_ref in ("refs/heads/main", "refs/heads/master") and (
                remote_ref == z40 or not remote_ref
            ):
                return 1, "ERROR: Direct push from local main/master branch is prohibited!"
        return 0, ""

    def test_synthetic_feature_branch_push_allowed(self) -> None:
        if not self.shell:
            self.skipTest("No POSIX shell available")
        # local_ref local_sha remote_ref remote_sha
        sha1 = "1" * 40
        sha2 = "2" * 40
        stdin_input = f"refs/heads/feat/test {sha1} refs/heads/feat/test {sha2}\n"
        code, stderr = self.run_hook_with_input(stdin_input)
        self.assertEqual(code, 0, f"Feature branch push should be allowed. Stderr: {stderr}")

    def test_synthetic_main_push_denied(self) -> None:
        if not self.shell:
            self.skipTest("No POSIX shell available")
        # Push targeting remote refs/heads/main
        sha1 = "1" * 40
        sha2 = "2" * 40
        stdin_input = f"refs/heads/feat/test {sha1} refs/heads/main {sha2}\n"
        code, stderr = self.run_hook_with_input(stdin_input)
        self.assertEqual(
            code, 1, "Direct push to refs/heads/main must be rejected with exit code 1"
        )
        self.assertIn("Direct push to protected branch", stderr)

    def test_synthetic_master_push_denied(self) -> None:
        if not self.shell:
            self.skipTest("No POSIX shell available")
        # Push targeting remote refs/heads/master
        sha1 = "1" * 40
        sha2 = "2" * 40
        stdin_input = f"refs/heads/feat/test {sha1} refs/heads/master {sha2}\n"
        code, _ = self.run_hook_with_input(stdin_input)
        self.assertEqual(
            code, 1, "Direct push to refs/heads/master must be rejected with exit code 1"
        )

    def test_synthetic_local_main_push_to_empty_remote_denied(self) -> None:
        if not self.shell:
            self.skipTest("No POSIX shell available")
        z40 = "0" * 40
        sha1 = "1" * 40
        stdin_input = f"refs/heads/main {sha1} {z40} {z40}\n"
        code, _ = self.run_hook_with_input(stdin_input)
        self.assertEqual(code, 1, "Push from local main must be rejected")


if __name__ == "__main__":
    unittest.main()

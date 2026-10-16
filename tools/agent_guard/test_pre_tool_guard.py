"""Unit tests for Antigravity PreToolUse Safety Guard."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.agent_guard.pre_tool_guard import evaluate_command, handle_pre_tool_use


class TestPreToolGuard(unittest.TestCase):
    """Test suite for evaluate_command and handle_pre_tool_use."""

    def test_routine_development_commands_allowed(self) -> None:
        allowed_commands = [
            "git status",
            "git diff",
            "git diff HEAD~1",
            "git log -n 5",
            "git fetch origin --prune",
            "git add .",
            "git checkout -b feat/new-feature",
            "git push origin chore/agent-factory-af0-governance",
            "git push -u origin feat/my-branch",
            "npm test",
            "npm run lint",
            "npm run format:check",
            "npm run typecheck",
            "uv run python -m pytest",
            "uv run ruff check .",
            "uv run ruff format --check .",
            "uv run python -m mypy",
            "docker compose -f infra/compose.yaml config",
            "node --test tools/deploy/validate_image_ref.test.mjs",
            "gh pr view 42",
            "gh pr status",
            "gh pr list",
        ]
        for cmd in allowed_commands:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feature/branch")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Command '{cmd}' should be allowed, got '{decision}' with reason: {reason}",
                )

    def test_direct_push_to_main_or_master_denied(self) -> None:
        denied_push_commands = [
            "git push origin main",
            "git push origin master",
            "git push -u origin main",
            "git push origin HEAD:main",
            "git push origin HEAD:refs/heads/main",
            "git push origin HEAD:master",
            "git push origin HEAD:refs/heads/master",
            "git push origin feat/test:main",
            'git push "origin" "main"',
            "git.exe push origin main",
            "git push origin :main",
        ]
        for cmd in denied_push_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Push command '{cmd}' targeting main/master must be denied",
                )

    def test_force_push_variants_and_plus_refspecs_denied(self) -> None:
        force_commands = [
            "git push --force origin feat/branch",
            "git push -f origin feat/branch",
            "git push --force-with-lease origin feat/branch",
            "git push --force-with-lease=feat/test origin feat/test",
            "git push --force-with-lease=feat/test:master origin feat/test",
            "git push --force-if-includes origin feat/branch",
            "git push --force=true origin feat/branch",
            "git push --no-verify origin feat/branch",
            "git push origin feat/branch --no-verify",
            'git push "-f" origin feat/branch',
            'git push "--force" origin feat/branch',
            "git push origin +feat/test",
            "git push origin +HEAD:refs/heads/feature",
            "git push origin +refs/heads/*",
        ]
        for cmd in force_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/branch")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Force push or no-verify command '{cmd}' must be denied",
                )

        # Normal branch push should remain allowed
        decision_normal, _ = evaluate_command(
            "git push origin feat/test", current_branch="feat/test"
        )
        self.assertEqual(decision_normal, "allow")

    def test_global_git_options_and_hookspath_tampering(self) -> None:
        # Bypassing hooks via -c core.hooksPath
        tamper_overrides = [
            "git -c core.hooksPath=.disabled push origin main",
            "git -c core.hooksPath=/dev/null push origin main",
            "git -c core.hookspath=foo commit -m 'test'",
            "git -C . push origin main",
            "git --work-tree=. push origin main",
            "git --git-dir=.git push origin main",
            "git config --unset core.hooksPath",
            "git config --unset-all core.hooksPath",
            "git config --local core.hooksPath .disabled",
            "git config core.hooksPath /dev/null",
        ]
        for cmd in tamper_overrides:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Tampering command '{cmd}' must be denied",
                )

        # Canonical installation of core.hooksPath is permitted
        allowed_installers = [
            "git config --local core.hooksPath .githooks",
            "git config core.hooksPath .githooks",
            "git config --local core.hooksPath .githooks/",
        ]
        for cmd in allowed_installers:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Canonical hooks installer '{cmd}' should be allowed, got: {reason}",
                )

    def test_commit_on_main_and_noverify_denied(self) -> None:
        commit_cmd = 'git commit -m "Direct commit on main"'
        decision_main, _ = evaluate_command(commit_cmd, current_branch="main")
        self.assertEqual(decision_main, "deny")

        decision_master, _ = evaluate_command(commit_cmd, current_branch="master")
        self.assertEqual(decision_master, "deny")

        decision_feature, _ = evaluate_command(commit_cmd, current_branch="feat/valid")
        self.assertEqual(decision_feature, "allow")

        # Commit with --no-verify or -n must be denied even on feature branches
        noverify_cmd = 'git commit --no-verify -m "Skip hooks"'
        decision_noverify, _ = evaluate_command(noverify_cmd, current_branch="feat/valid")
        self.assertEqual(decision_noverify, "deny")

        n_cmd = 'git commit -n -m "Skip hooks"'
        decision_n, _ = evaluate_command(n_cmd, current_branch="feat/valid")
        self.assertEqual(decision_n, "deny")

    def test_github_api_merge_and_mutation_denied(self) -> None:
        denied_gh_calls = [
            "gh pr merge 52",
            "gh pr merge --squash",
            "gh pr merge 42 --auto",
            "gh.exe pr merge 10",
            "gh api repos/owner/repo/pulls/52/merge",
            "gh api --method PUT repos/owner/repo/pulls/52/merge",
            "gh api -X PUT /repos/owner/repo/pulls/52/merge",
            "gh api --method PUT repos/owner/repo/pulls/52/merge-async",
            "gh api graphql -f query='mutation { mergePullRequest(input: {}) }'",
            "curl -X PUT https://api.github.com/repos/owner/repo/pulls/52/merge",
            "curl -X POST https://api.github.com/repos/owner/repo/pulls/52/merge-async",
            "gh api -X DELETE repos/owner/repo/git/refs/heads/main",
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main",
            "curl -X DELETE https://api.github.com/repos/owner/repo/git/refs/heads/main",
        ]
        for cmd in denied_gh_calls:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Merge or mutation command '{cmd}' must be denied",
                )

        # Harmless GitHub read operations must remain allowed
        allowed_gh_reads = [
            "gh pr view 52",
            "gh pr view 52 --json state,headRefName",
            "gh api repos/owner/repo/pulls/52",
            "gh api repos/owner/repo/pulls/52 -X GET",
            "gh api repos/owner/repo/git/ref/heads/main",
            "curl https://api.github.com/repos/owner/repo/pulls/52",
        ]
        for cmd in allowed_gh_reads:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"GitHub read command '{cmd}' should be allowed, got: {reason}",
                )

    def test_windows_secret_dumping_robust(self) -> None:
        dump_commands = [
            "cat .env",
            "type .env",
            "Get-Content .env",
            "get-content .env",
            "gc .env",
            "cat .env.production",
            "type .env.staging",
            "Get-Content .env.local",
            r"Get-Content path\.env.staging",
            "Get-Content path/.env.production",
            "cat ~/.ssh/id_rsa",
            "Get-Content ~/.ssh/id_rsa",
            "type id_ed25519",
        ]
        for cmd in dump_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Secret dump command '{cmd}' must be denied",
                )

        # Examples and templates MUST be allowed
        allowed_templates = [
            "cat .env.example",
            "Get-Content .env.example",
            "get-content infra/.env.example",
            "cat path/to/.env.template",
            "type .env.sample",
        ]
        for cmd in allowed_templates:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Template read '{cmd}' should be allowed, got: {reason}",
                )

    def test_nested_shell_wrapper_bypass(self) -> None:
        nested_denied = [
            'powershell -Command "git push origin main"',
            'powershell.exe -Command "gh pr merge 52"',
            'pwsh -Command "git push origin master"',
            'cmd /c "git push origin main"',
            'cmd.exe /c "cat .env"',
            'sh -c "git push origin main"',
            'bash -c "git push --force origin feature"',
            'bash -c "git push origin +feat/test"',
        ]
        for cmd in nested_denied:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Nested shell bypass '{cmd}' must be denied, got {decision} ({reason})",
                )

        # Safe wrapped commands must remain allowed
        decision_wrapped_allow, _ = evaluate_command(
            'powershell -Command "npm test"', current_branch="feat/test"
        )
        self.assertEqual(decision_wrapped_allow, "allow")

    def test_direct_evolution_send_denied(self) -> None:
        send_commands = [
            "curl -X POST https://evolution.tapjob.internal/message/sendText -d '{}'",
            "curl -X POST https://api.example.com/message/sendMedia",
            "curl http://localhost:8080/message/sendWhatsApp",
        ]
        for cmd in send_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Direct Evolution messaging command '{cmd}' must be denied",
                )

    def test_guard_tampering_denied(self) -> None:
        tamper_commands = [
            "git config --unset core.hooksPath",
            "rm .agents/hooks.json",
            "del .agents\\hooks.json",
            "rm .githooks/pre-push",
            "Remove-Item tools/agent_guard/pre_tool_guard.py",
            "del tools\\agent_guard\\validate_governance.py",
        ]
        for cmd in tamper_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Tampering command '{cmd}' must be denied",
                )

    def test_human_gate_and_force_ask(self) -> None:
        high_impact_commands = [
            "git reset --hard HEAD~1",
            "git clean -fdx",
            "git clean -f",
            "alembic downgrade base",
            "drop table users",
            "deploy prod",
            "rotate secret token",
            "dns update tapjob.com.br",
        ]
        for cmd in high_impact_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "force_ask",
                    f"High-impact command '{cmd}' must trigger force_ask",
                )

    def test_command_chaining_catches_violation(self) -> None:
        chained_deny = "git status && git push origin main"
        decision, _ = evaluate_command(chained_deny, current_branch="feat/test")
        self.assertEqual(decision, "deny")

        chained_force_ask = "git status ; git reset --hard HEAD~1"
        decision_ask, _ = evaluate_command(chained_force_ask, current_branch="feat/test")
        self.assertEqual(decision_ask, "force_ask")

    def test_cwd_and_worktree_isolation(self) -> None:
        """Test real git worktrees and Cwd awareness without mocks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_repo = Path(tmp_dir) / "repo"
            base_repo.mkdir()

            import shutil

            git_bin = shutil.which("git") or "git"

            # Initialize base git repo on main
            subprocess.run(  # noqa: S603
                [git_bin, "init", "-b", "main"],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )
            subprocess.run(  # noqa: S603
                [git_bin, "config", "user.name", "Test User"],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )
            subprocess.run(  # noqa: S603
                [git_bin, "config", "user.email", "test@tapjob.internal"],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )
            # Create initial commit on main
            subprocess.run(  # noqa: S603
                [git_bin, "commit", "--allow-empty", "-m", "Initial commit on main"],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )

            # Create an isolated worktree for feature branch
            worktree_feat = Path(tmp_dir) / "wt-feature"
            subprocess.run(  # noqa: S603
                [git_bin, "worktree", "add", "-b", "feat/isolated-worktree", str(worktree_feat)],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )

            # Case A: Cwd points to feature worktree -> git commit allowed
            res_feat = handle_pre_tool_use(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": 'git commit --allow-empty -m "Worktree feature commit"',
                            "Cwd": str(worktree_feat),
                        },
                    },
                    "workspacePaths": [str(tmp_dir)],
                }
            )
            self.assertEqual(
                res_feat.get("decision"),
                "allow",
                f"Feature worktree commit should be allowed: {res_feat.get('reason')}",
            )

            # Case B: Cwd points to main checkout -> git commit denied
            res_main = handle_pre_tool_use(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": 'git commit --allow-empty -m "Direct commit on main"',
                            "Cwd": str(base_repo),
                        },
                    },
                    "workspacePaths": [str(tmp_dir)],
                }
            )
            self.assertEqual(
                res_main.get("decision"),
                "deny",
                "Direct commit on main checkout must be denied",
            )

            # Case C: Multiple worktrees do not contaminate one another
            worktree_feat2 = Path(tmp_dir) / "wt-feature2"
            subprocess.run(  # noqa: S603
                [git_bin, "worktree", "add", "-b", "feat/isolated-worktree2", str(worktree_feat2)],
                cwd=str(base_repo),
                check=True,
                capture_output=True,
            )
            res_feat2 = handle_pre_tool_use(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": 'git commit --allow-empty -m "Worktree 2 commit"',
                            "Cwd": str(worktree_feat2),
                        },
                    },
                    "workspacePaths": [str(tmp_dir)],
                }
            )
            self.assertEqual(res_feat2.get("decision"), "allow")


if __name__ == "__main__":
    unittest.main()

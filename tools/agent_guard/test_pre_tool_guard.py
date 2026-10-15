"""Unit tests for Antigravity PreToolUse Safety Guard."""

from __future__ import annotations

import unittest

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

    def test_force_push_and_no_verify_denied(self) -> None:
        force_commands = [
            "git push --force origin feat/branch",
            "git push -f origin feat/branch",
            "git push --force-with-lease origin feat/branch",
            "git push --force-if-includes origin feat/branch",
            "git push --no-verify origin feat/branch",
            "git push origin feat/branch --no-verify",
            'git push "-f" origin feat/branch',
            'git push "--force" origin feat/branch',
        ]
        for cmd in force_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/branch")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Force push or no-verify command '{cmd}' must be denied",
                )

    def test_commit_on_main_denied(self) -> None:
        commit_cmd = 'git commit -m "Direct commit on main"'
        decision_main, _ = evaluate_command(commit_cmd, current_branch="main")
        self.assertEqual(decision_main, "deny")

        decision_master, _ = evaluate_command(commit_cmd, current_branch="master")
        self.assertEqual(decision_master, "deny")

        decision_feature, _ = evaluate_command(commit_cmd, current_branch="feat/valid")
        self.assertEqual(decision_feature, "allow")

    def test_gh_pr_merge_denied(self) -> None:
        merge_commands = [
            "gh pr merge 52",
            "gh pr merge --squash",
            "gh pr merge 42 --auto",
            "gh.exe pr merge 10",
        ]
        for cmd in merge_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"'gh pr merge' command '{cmd}' must be denied during baseline governance",
                )

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
        ]
        for cmd in tamper_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Tampering command '{cmd}' must be denied",
                )

    def test_secret_dumping_denied(self) -> None:
        dump_commands = [
            "cat .env",
            "type .env",
            "cat .env.production",
            "type .env.staging",
            "cat ~/.ssh/id_rsa",
        ]
        for cmd in dump_commands:
            with self.subTest(cmd=cmd):
                decision, _ = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Secret dump command '{cmd}' must be denied",
                )

        # .env.example should NOT be denied
        decision, _ = evaluate_command("cat infra/.env.example", current_branch="feat/test")
        self.assertEqual(decision, "allow")

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
        # A chained command with a violation anywhere must be flagged
        chained_deny = "git status && git push origin main"
        decision, _ = evaluate_command(chained_deny, current_branch="feat/test")
        self.assertEqual(decision, "deny")

        chained_force_ask = "git status ; git reset --hard HEAD~1"
        decision_ask, _ = evaluate_command(chained_force_ask, current_branch="feat/test")
        self.assertEqual(decision_ask, "force_ask")

    def test_handle_pre_tool_use_contract(self) -> None:
        # 1. run_command allow
        res_allow = handle_pre_tool_use(
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "npm test"},
                }
            }
        )
        self.assertEqual(res_allow.get("decision"), "allow")

        # 2. run_command deny
        res_deny = handle_pre_tool_use(
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "git push origin main"},
                }
            }
        )
        self.assertEqual(res_deny.get("decision"), "deny")

        # 3. Non-run_command tool
        res_other = handle_pre_tool_use(
            {
                "toolCall": {
                    "name": "view_file",
                    "args": {"AbsolutePath": "AGENTS.md"},
                }
            }
        )
        self.assertEqual(res_other.get("decision"), "allow")


if __name__ == "__main__":
    unittest.main()

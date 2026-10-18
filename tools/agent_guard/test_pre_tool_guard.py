"""Unit tests for Antigravity PreToolUse Safety Guard."""

from __future__ import annotations

import shlex
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

    def test_git_dir_and_work_tree_denied(self) -> None:
        """Adversarial tests: --git-dir and --work-tree must be strictly denied."""
        denied_commands = [
            "git --git-dir=/tmp/other.git commit -m 'test'",
            "git --work-tree=/tmp/other commit -m 'test'",
            "git --git-dir=/tmp/other.git --work-tree=/tmp/other commit -m 'test'",
            "git --git-dir=.git push origin feat/test",
            "git --work-tree=. status",
            "git --git-dir=/path status",
        ]
        for cmd in denied_commands:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Command '{cmd}' with --git-dir/--work-tree must be denied, "
                    f"got {decision} ({reason})",
                )

    def test_git_env_retargeting_denied(self) -> None:
        """Adversarial tests: Git environment variable overrides must be strictly denied."""
        denied_env_commands = [
            "GIT_DIR=/tmp/main.git git commit -m 'test'",
            "GIT_WORK_TREE=/tmp/main git commit -m 'test'",
            "export GIT_DIR=/tmp/main.git && git commit -m 'test'",
            "$env:GIT_DIR = '/tmp/main.git'; git commit -m 'test'",
            "$env:GIT_WORK_TREE = '/tmp/main'; git commit -m 'test'",
            "env GIT_COMMON_DIR=/tmp git commit -m 'test'",
            "Set-Item env:GIT_CONFIG /tmp/gitconfig",
            "set GIT_CONFIG_GLOBAL=/tmp/gitconfig",
            "GIT_CONFIG_COUNT=1 git commit -m 'test'",
        ]
        for cmd in denied_env_commands:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Git env retargeting '{cmd}' must be denied, got {decision} ({reason})",
                )

    def test_git_alias_inline_and_config_denied(self) -> None:
        """Tests: inline aliases (-c alias.*) and git config alias.* must be denied."""
        denied_alias_commands = [
            "git -c alias.x='!echo test' x",
            "git -c alias.foo='status' foo",
            "git -c alias.p='push origin main' p",
            "git --config-env=alias.x=MY_ENV x",
            "git config alias.mycmd '!echo test'",
            "git config --global alias.st 'status'",
            "git config --local alias.cm 'commit'",
            "git config --unset alias.mycmd",
        ]
        for cmd in denied_alias_commands:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Alias command '{cmd}' must be denied, got {decision} ({reason})",
                )

    def test_configured_alias_invocation_denied_in_repo(self) -> None:
        """Tests: invoking any configured git alias in target repo must be detected and denied."""
        import shutil

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir) / "repo"
            repo_path.mkdir()
            git_bin = shutil.which("git") or "git"

            subprocess.run(  # noqa: S603
                [git_bin, "init", "-b", "feat/test"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            # Configure a shell alias
            subprocess.run(  # noqa: S603
                [git_bin, "config", "alias.mytest", "!echo safe-test"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            # Configure a built-in alias expansion
            subprocess.run(  # noqa: S603
                [git_bin, "config", "alias.st", "status"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )

            # Invocation of shell alias must be denied
            dec_test, r_test = evaluate_command("git mytest", cwd=repo_path)
            self.assertEqual(dec_test, "deny")
            self.assertIn("alias", r_test.lower())

            # Invocation of status alias must be denied
            dec_st, r_st = evaluate_command("git st", cwd=repo_path)
            self.assertEqual(dec_st, "deny")
            self.assertIn("alias", r_st.lower())

            # Regular git status must remain allowed
            dec_status, _ = evaluate_command("git status", cwd=repo_path)
            self.assertEqual(dec_status, "allow")

    def test_nested_shell_combined_posix_flags(self) -> None:
        """Tests: combined POSIX flags like -lc, -ec, -xce must be parsed and inspected."""
        denied_nested = [
            'bash -lc "git push origin main"',
            'sh -ec "cat .env"',
            'bash -xce "git push origin main"',
            'zsh -lc "gh pr merge 52"',
            "bash -c",
        ]
        for cmd in denied_nested:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Combined flag wrapper '{cmd}' must be denied, got {decision} ({reason})",
                )

        # Harmless combined flag wrapper allowed
        dec_safe, _ = evaluate_command('bash -lc "npm test"', current_branch="feat/test")
        self.assertEqual(dec_safe, "allow")

    def test_single_ampersand_and_pipe_chaining(self) -> None:
        """Tests: single & (when not redirection) and single | pipeline separators are parsed."""
        denied_chains = [
            'cmd /c "echo safe & git push origin main"',
            'sh -c "echo safe | cat .env"',
            "echo safe & git push origin main",
            "npm test | cat .env",
        ]
        for cmd in denied_chains:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Chained command '{cmd}' must be denied, got {decision} ({reason})",
                )

        # Normal shell redirection must NOT be split as command separator
        safe_redirections = [
            "npm test 2>&1",
            "pytest > /dev/null 2>&1",
            "npm run check 2>&1",
        ]
        for cmd in safe_redirections:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Redirection '{cmd}' must be allowed, got {decision} ({reason})",
                )

    def test_powershell_encoded_command_denied(self) -> None:
        """Tests: PowerShell -EncodedCommand / -enc must be strictly denied."""
        denied_encoded = [
            "powershell -EncodedCommand JABlAG4AdgA6A...",
            "pwsh -enc VABlAHMAdAA=",
            "powershell.exe -e 'dGVzdA=='",
            "pwsh -encodedcommand 'dGVzdA=='",
        ]
        for cmd in denied_encoded:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Encoded command '{cmd}' must be denied, got {decision} ({reason})",
                )

        # Normal PowerShell command execution allowed
        dec_normal, _ = evaluate_command(
            'powershell -Command "npm test"', current_branch="feat/test"
        )
        self.assertEqual(dec_normal, "allow")

    def test_bulk_and_wildcard_push_denied(self) -> None:
        """Tests: bulk push (--all, --mirror) and wildcard refspecs must be denied."""
        denied_pushes = [
            "git push --all origin",
            "git push --all",
            "git push --mirror origin",
            "git push --mirror",
            "git push origin refs/heads/*:refs/heads/*",
            "git push origin +refs/heads/*",
            "git push origin *",
            "git push origin refs/heads/*",
        ]
        for cmd in denied_pushes:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Bulk/wildcard push '{cmd}' must be denied, got {decision} ({reason})",
                )

        # Explicit feature-only push remains allowed
        allowed_pushes = [
            "git push origin feat/valid-branch",
            "git push -u origin feat/valid-branch",
            "git push origin chore/agent-factory-af0-governance",
        ]
        for cmd in allowed_pushes:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/valid-branch")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Feature push '{cmd}' must be allowed, got {decision} ({reason})",
                )

    def test_nested_shell_recursion_depth_fail_closed(self) -> None:
        """Tests: 6-level nested wrapper must fail closed (DENY), shallow safe wrappers allowed."""

        def make_deep_wrapper(target: str, levels: int = 6) -> str:
            res = target
            for _ in range(levels):
                res = f"bash -c {shlex.quote(res)}"
            return res

        deep_targets = [
            "git push origin main",
            "gh pr merge 52",
            "cat .env",
        ]
        for target in deep_targets:
            cmd = make_deep_wrapper(target, levels=6)
            with self.subTest(target=target):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Deep shell wrapper for '{target}' must fail closed, got {decision}",
                )
                self.assertIn("recursion depth", reason.lower())

        # Shallow safe wrappers must remain allowed
        shallow_safe = [
            'bash -c "npm test"',
            'powershell -Command "git status"',
            'sh -c "echo safe"',
            'pwsh -c "pytest"',
        ]
        for cmd in shallow_safe:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Shallow safe wrapper '{cmd}' should be allowed, got {decision} ({reason})",
                )

    def test_launcher_prefixes_and_powershell_call_operator(self) -> None:
        """Tests: launcher prefixes (&, command, env, exec, nohup) are stripped and inspected."""
        denied_launcher_cmds = [
            "& gh pr merge 52",
            '& "gh" pr merge 52',
            "command gh pr merge 52",
            "env gh pr merge 52",
            "env FOO=1 BAR=2 gh pr merge 52",
            "exec gh pr merge 52",
            "nohup gh pr merge 52",
            "& curl -X PUT https://api.github.com/repos/ricardors081/tapjob/pulls/52/merge",
            "& cat .env",
            "command cat .env",
            "env cat .env",
            "& git push origin main",
            "command git push origin main",
            "env git push origin main",
        ]
        for cmd in denied_launcher_cmds:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Launcher command '{cmd}' must be denied, got {decision} ({reason})",
                )

        allowed_launcher_cmds = [
            "command git status",
            "env git status",
            "& git status",
            "nohup npm test",
            "env FOO=1 npm run check",
        ]
        for cmd in allowed_launcher_cmds:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Safe launcher command '{cmd}' should be allowed, got {decision} ({reason})",
                )

    def test_git_push_head_or_at_on_protected_branch_denied(self) -> None:
        """Tests: pushing HEAD or @ while on protected branch (main/master) must be denied."""
        # On main branch: HEAD and @ resolve to main
        main_denied = [
            "git push origin HEAD",
            "git push origin @",
            "git push HEAD",
            "git push @",
            "git push origin feat/test:HEAD",
            "git push origin feat/test:@",
        ]
        for cmd in main_denied:
            with self.subTest(cmd=cmd, branch="main"):
                decision, reason = evaluate_command(cmd, current_branch="main")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Pushing '{cmd}' on main must be denied, got {decision} ({reason})",
                )

        # On master branch: HEAD and @ resolve to master
        master_denied = [
            "git push origin HEAD",
            "git push origin @",
        ]
        for cmd in master_denied:
            with self.subTest(cmd=cmd, branch="master"):
                decision, reason = evaluate_command(cmd, current_branch="master")
                self.assertEqual(
                    decision,
                    "deny",
                    f"Pushing '{cmd}' on master must be denied, got {decision} ({reason})",
                )

        # On feature branch: HEAD and @ resolve to the feature branch and are allowed
        feature_allowed = [
            "git push origin HEAD",
            "git push origin @",
            "git push origin feat/test",
            "git push origin HEAD:feat/test",
        ]
        for cmd in feature_allowed:
            with self.subTest(cmd=cmd, branch="feat/test"):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Pushing '{cmd}' on feat/test should be allowed, got {decision} ({reason})",
                )

    def test_github_api_merge_and_ref_mutations_harden(self) -> None:
        """Tests: PR merge endpoints and plural refs are unconditionally blocked."""
        denied_api_cmds = [
            # Unconditional block on /pulls/<n>/merge regardless of claimed GET method
            "gh api repos/ricardors081/tapjob/pulls/52/merge -X GET",
            "gh api repos/ricardors081/tapjob/pulls/52/merge --method GET",
            "gh api repos/ricardors081/tapjob/pulls/52/merge",
            'gh api repos/ricardors081/tapjob/pulls/52/merge -f commit_title="-X GET bypass"',
            "curl -X GET https://api.github.com/repos/ricardors081/tapjob/pulls/52/merge",
            "curl -X PUT https://api.github.com/repos/ricardors081/tapjob/pulls/52/merge",
            (
                "curl -X PUT https://api.github.com/repos/ricardors081/tapjob/pulls/52/merge "
                '-d \'{"title": "-X GET"}\''
            ),
            # Plural refs endpoint unconditionally blocked
            "gh api repos/ricardors081/tapjob/git/refs/heads/main",
            "gh api repos/ricardors081/tapjob/git/refs/heads/master",
            "curl https://api.github.com/repos/ricardors081/tapjob/git/refs/heads/main",
            # Singular ref mutation blocked
            "gh api repos/ricardors081/tapjob/git/ref/heads/main -X PATCH -f sha=123",
            "curl -X DELETE https://api.github.com/repos/ricardors081/tapjob/git/ref/heads/main",
            "curl -X PATCH https://api.github.com/repos/ricardors081/tapjob/git/ref/heads/main",
        ]
        for cmd in denied_api_cmds:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "deny",
                    f"API command '{cmd}' must be denied, got {decision} ({reason})",
                )

        allowed_api_cmds = [
            # Singular ref read-only inspection allowed
            "gh api repos/ricardors081/tapjob/git/ref/heads/main",
            "curl https://api.github.com/repos/ricardors081/tapjob/git/ref/heads/main",
            "gh pr view 52",
            "gh pr list",
        ]
        for cmd in allowed_api_cmds:
            with self.subTest(cmd=cmd):
                decision, reason = evaluate_command(cmd, current_branch="feat/test")
                self.assertEqual(
                    decision,
                    "allow",
                    f"Read-only API command '{cmd}' should be allowed, got {decision} ({reason})",
                )


if __name__ == "__main__":
    unittest.main()

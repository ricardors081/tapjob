#!/usr/bin/env python3
"""TapJob Antigravity PreToolUse Safety Guard.

Deterministic guard executed before tool invocations to enforce repository
safety, prevent unauthorized main/master mutations, and gate high-impact
operations.

Input (stdin): JSON object adhering to Antigravity PreToolUse hook contract.
Output (stdout): JSON object with decision ("allow", "deny", "ask", "force_ask")
and optional reason.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Protected branches that cannot be targeted directly
PROTECTED_BRANCHES = {"main", "master", "refs/heads/main", "refs/heads/master"}

# Destructive git commands requiring human gate
DESTRUCTIVE_GIT_RE = re.compile(
    r"\bgit\s+(?:reset\s+--hard|clean\s+-(?:[a-zA-Z]*f[a-zA-Z]*))", re.IGNORECASE
)

# High-impact production/infrastructure operations requiring human gate
FORCE_ASK_PATTERNS = [
    (
        re.compile(r"\bdeploy(?:ment)?\b.*?\b(?:prod|production)\b", re.IGNORECASE),
        "Production deployment requires explicit human gate.",
    ),
    (
        re.compile(
            r"\b(?:alembic\s+downgrade\s+base|drop\s+database|drop\s+table)\b", re.IGNORECASE
        ),
        "Destructive database operation requires explicit human gate.",
    ),
    (
        re.compile(r"\b(?:rotate|delete)\b.*?\b(?:secret|token|key|credential)\b", re.IGNORECASE),
        "Secret or credential rotation requires explicit human gate.",
    ),
    (
        re.compile(r"\b(?:dns|domain)\b.*?\b(?:change|update|delete|transfer)\b", re.IGNORECASE),
        "DNS/domain configuration change requires explicit human gate.",
    ),
]

SHELL_WRAPPERS = {
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}

READER_BINARIES = {"cat", "type", "get-content", "gc", "more", "head", "tail"}

MAX_NESTED_SHELL_DEPTH = 5
LAUNCHER_PREFIXES = {"&", "command", "env", "exec", "nohup"}

# Environment variables used to retarget git repository, worktree, or configuration
GIT_ENV_RETARGET_RE = re.compile(
    r"(?:"
    r"(?:\$env:|env:)\s*(?:GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_COUNT|GIT_CONFIG_PARAMETERS|GIT_CONFIG_KEY_\w+|GIT_CONFIG_VALUE_\w+)\b|"
    r"\b(?:export|set|env)\s+(?:GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_COUNT|GIT_CONFIG_PARAMETERS|GIT_CONFIG_KEY_\w+|GIT_CONFIG_VALUE_\w+)\b|"
    r"\b(?:GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_COUNT|GIT_CONFIG_PARAMETERS|GIT_CONFIG_KEY_\w+|GIT_CONFIG_VALUE_\w+)\s*="
    r")",
    re.IGNORECASE,
)


@dataclass
class GitInvocation:
    """Parsed representation of a git command with global options and subcommand."""

    git_token_idx: int
    cwd_override: str | None = None
    config_overrides: list[str] = field(default_factory=list)
    git_dir: str | None = None
    work_tree: str | None = None
    subcommand: str | None = None
    subcommand_args: list[str] = field(default_factory=list)


def get_current_git_branch(cwd: Path | str | None = None) -> str | None:
    """Determine current git branch safely without throwing exceptions.

    Resolves branch using exact target cwd to support worktrees and isolated checkouts.
    """
    cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    if cwd:
        target_path = Path(cwd)
        if not target_path.is_dir():
            return None
        cwd_str = str(target_path)
    else:
        cwd_str = None

    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=cwd_str,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            return branch if branch else None
    except (subprocess.SubprocessError, OSError):
        return None
    return None


def split_command_chain(cmd: str) -> list[str]:
    """Split chained commands separated by &&, ;, ||, &, |, or newlines.

    Carefully handles quotes so tokens inside strings are not split, and distinguishes
    normal shell redirections (2>&1, >&2, &>file) from command-separating & operators.
    """
    sub_commands: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        char = cmd[i]
        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
        elif not in_single and not in_double:
            if char in (";", "\n"):
                segment = "".join(current).strip()
                if segment:
                    sub_commands.append(segment)
                current = []
            elif char == "&":
                if i + 1 < n and cmd[i + 1] == "&":
                    # '&&' operator
                    segment = "".join(current).strip()
                    if segment:
                        sub_commands.append(segment)
                    current = []
                    i += 1  # Skip second &
                else:
                    # Single '&':
                    # Check if part of redirection: '>&' (e.g. 2>&1) or '&>' (e.g. &>file)
                    prev_char = cmd[i - 1] if i > 0 else ""
                    next_char = cmd[i + 1] if i + 1 < n else ""
                    if prev_char == ">" or next_char == ">":
                        # Redirection, NOT a command separator
                        current.append(char)
                    else:
                        segment = "".join(current).strip()
                        if segment:
                            sub_commands.append(segment)
                            current = []
                        else:
                            # If segment is empty, '&' is at start of command
                            # (e.g. PowerShell call operator '& cmd')
                            current.append(char)
            elif char == "|":
                if i + 1 < n and cmd[i + 1] == "|":
                    # '||' operator
                    segment = "".join(current).strip()
                    if segment:
                        sub_commands.append(segment)
                    current = []
                    i += 1  # Skip second |
                else:
                    # Single '|': pipeline separator (including '|&' pipeline)
                    segment = "".join(current).strip()
                    if segment:
                        sub_commands.append(segment)
                    current = []
                    if i + 1 < n and cmd[i + 1] == "&":
                        i += 1  # Skip '&' in '|&'
            else:
                current.append(char)
        else:
            current.append(char)
        i += 1
    segment = "".join(current).strip()
    if segment:
        sub_commands.append(segment)
    return sub_commands


def tokenize_command(cmd_str: str) -> list[str]:
    """Tokenize a single shell command into tokens handling Windows/POSIX quoting."""
    clean_cmd = cmd_str.strip()
    try:
        return shlex.split(clean_cmd, posix=False)
    except Exception:
        return clean_cmd.split()


def extract_effective_tokens(tokens: list[str]) -> list[str]:
    """Strip launcher prefixes (&, command, env, exec, nohup) and env assignments.

    Performs structural option parsing for launchers:
    - '&': PowerShell call operator (no options)
    - 'command': POSIX command built-in, supports -p, -v, -V, combined short flags, and '--'
    - 'nohup': POSIX/GNU nohup, supports '--'
    - 'exec': POSIX/Bash exec, supports -c, -l, -cl, -lc, -a <name>, and '--'
    - 'env': POSIX/GNU env, supports options without args (-i, -, -0, -v),
      options with args (-u <name>, -C <dir>, -S <str>, --unset=<name>, --chdir=<dir>,
      --split-string=<str>), KEY=VALUE assignments, and '--'

    Any unrecognized or ambiguous launcher syntax returns ['__GUARD_FAIL_CLOSED__'].
    """
    if not tokens:
        return []

    idx = 0
    n = len(tokens)

    while idx < n:
        raw = tokens[idx].strip("\"'")
        lower = raw.lower()

        if lower not in LAUNCHER_PREFIXES:
            break

        if lower == "&":
            idx += 1
            continue

        if lower == "command":
            idx += 1
            while idx < n:
                opt_raw = tokens[idx].strip("\"'")
                if opt_raw == "--":
                    idx += 1
                    break
                if opt_raw.startswith("-") and not opt_raw.startswith("--") and len(opt_raw) > 1:
                    flags = opt_raw[1:]
                    if all(c in "pvV" for c in flags):
                        idx += 1
                        continue
                    return ["__GUARD_FAIL_CLOSED__"]
                if opt_raw.startswith("-"):
                    return ["__GUARD_FAIL_CLOSED__"]
                break
            continue

        if lower == "nohup":
            idx += 1
            while idx < n:
                opt_raw = tokens[idx].strip("\"'")
                if opt_raw == "--":
                    idx += 1
                    break
                if opt_raw.startswith("-"):
                    return ["__GUARD_FAIL_CLOSED__"]
                break
            continue

        if lower == "exec":
            idx += 1
            while idx < n:
                opt_raw = tokens[idx].strip("\"'")
                if opt_raw == "--":
                    idx += 1
                    break
                if opt_raw in ("-c", "-l", "-cl", "-lc"):
                    idx += 1
                    continue
                if opt_raw == "-a":
                    if idx + 1 < n:
                        idx += 2
                        continue
                    return ["__GUARD_FAIL_CLOSED__"]
                if opt_raw.startswith("-a=") or (opt_raw.startswith("-a") and len(opt_raw) > 2):
                    idx += 1
                    continue
                if opt_raw.startswith("-"):
                    return ["__GUARD_FAIL_CLOSED__"]
                break
            continue

        if lower == "env":
            idx += 1
            while idx < n:
                opt_raw = tokens[idx].strip("\"'")
                opt_lower = opt_raw.lower()

                if opt_raw == "--":
                    idx += 1
                    while idx < n:
                        t = tokens[idx].strip("\"'")
                        if not t.startswith("-") and "=" in t:
                            idx += 1
                        else:
                            break
                    break

                if opt_raw == "-":
                    idx += 1
                    continue

                if opt_lower in ("-i", "--ignore-environment", "-0", "--null", "-v", "--verbose"):
                    idx += 1
                    continue

                if (
                    opt_raw.startswith("-")
                    and not opt_raw.startswith("--")
                    and len(opt_raw) > 1
                    and all(c in "iv0" for c in opt_raw[1:])
                ):
                    idx += 1
                    continue

                if (
                    opt_lower.startswith("--unset=")
                    or opt_lower.startswith("--chdir=")
                    or opt_lower.startswith("--split-string=")
                ):
                    idx += 1
                    continue

                if (
                    opt_raw.startswith(("-u", "-C", "-S"))
                    and len(opt_raw) > 2
                    and not opt_raw.startswith("--")
                ):
                    idx += 1
                    continue

                if opt_lower in ("-u", "--unset", "-c", "--chdir", "-s", "--split-string"):
                    if idx + 1 < n:
                        idx += 2
                        continue
                    return ["__GUARD_FAIL_CLOSED__"]

                if opt_lower == "--list-signal-handling":
                    idx += 1
                    continue

                if opt_lower.startswith("--list-signal-handling="):
                    return ["__GUARD_FAIL_CLOSED__"]

                if opt_lower in ("--default-signal", "--ignore-signal", "--block-signal"):
                    idx += 1
                    continue

                if (
                    opt_lower.startswith("--default-signal=")
                    or opt_lower.startswith("--ignore-signal=")
                    or opt_lower.startswith("--block-signal=")
                ):
                    idx += 1
                    continue

                if not opt_raw.startswith("-") and "=" in opt_raw:
                    idx += 1
                    continue

                if opt_raw.startswith("-"):
                    return ["__GUARD_FAIL_CLOSED__"]

                break
            continue

        break

    return tokens[idx:]


def extract_http_method(tokens: list[str]) -> str:
    """Extract effective HTTP method from command tokens handling flags and defaults."""
    n = len(tokens)
    idx = 0
    explicit_method: str | None = None
    has_body_data = False

    while idx < n:
        raw = tokens[idx].strip("\"'")
        lower = raw.lower()

        # Check explicit method flags: -X, --method, --request, -Method
        if lower in ("-x", "--method", "--request", "-method"):
            if idx + 1 < n:
                explicit_method = tokens[idx + 1].strip("\"'").upper()
                idx += 2
                continue
        elif lower.startswith(("-x=", "--method=", "--request=", "-method:")):
            explicit_method = raw.split("=", 1)[-1].split(":", 1)[-1].strip("\"'").upper()
            idx += 1
            continue
        elif lower.startswith("-x") and len(lower) > 2 and not lower.startswith("--"):
            explicit_method = raw[2:].strip("\"'").upper()
            idx += 1
            continue

        # Check data flags that implicitly imply POST if no explicit method
        if lower in (
            "-d",
            "--data",
            "--data-raw",
            "--data-binary",
            "--data-urlencode",
            "-f",
            "--field",
            "-F",
            "--raw-field",
        ) or lower.startswith(("-d", "--data-", "-f=", "--field=", "-F=", "--raw-field=")):
            has_body_data = True

        idx += 1

    if explicit_method:
        return explicit_method
    if has_body_data:
        return "POST"
    return "GET"


def parse_git_invocations(tokens: list[str]) -> list[GitInvocation]:
    """Parse git commands from tokens, identifying global options and subcommands."""
    invocations: list[GitInvocation] = []
    n = len(tokens)
    idx = 0

    while idx < n:
        raw_token = tokens[idx].strip("\"'")
        base_name = Path(raw_token).name.lower()
        if base_name in ("git", "git.exe"):
            inv = GitInvocation(git_token_idx=idx)
            opt_idx = idx + 1
            while opt_idx < n:
                tok = tokens[opt_idx].strip("\"'")
                if not tok.startswith("-"):
                    # First non-option is the subcommand
                    inv.subcommand = tok.lower()
                    inv.subcommand_args = [t.strip("\"'") for t in tokens[opt_idx + 1 :]]
                    break

                # Global options before subcommand
                if tok == "-C":
                    if opt_idx + 1 < n:
                        inv.cwd_override = tokens[opt_idx + 1].strip("\"'")
                        opt_idx += 2
                        continue
                elif tok.startswith("-C") and len(tok) > 2:
                    inv.cwd_override = tok[2:]
                    opt_idx += 1
                    continue
                elif tok == "-c":
                    if opt_idx + 1 < n:
                        inv.config_overrides.append(tokens[opt_idx + 1].strip("\"'"))
                        opt_idx += 2
                        continue
                elif tok.startswith("-c") and len(tok) > 2:
                    inv.config_overrides.append(tok[2:])
                    opt_idx += 1
                    continue
                elif tok == "--config-env" or tok.startswith("--config-env="):
                    if "=" in tok:
                        inv.config_overrides.append(tok.split("=", 1)[1])
                    elif opt_idx + 1 < n:
                        inv.config_overrides.append(tokens[opt_idx + 1].strip("\"'"))
                        opt_idx += 1
                elif tok == "--git-dir" or tok.startswith("--git-dir="):
                    if "=" in tok:
                        inv.git_dir = tok.split("=", 1)[1]
                    elif opt_idx + 1 < n:
                        inv.git_dir = tokens[opt_idx + 1].strip("\"'")
                        opt_idx += 1
                    else:
                        inv.git_dir = ""
                elif tok == "--work-tree" or tok.startswith("--work-tree="):
                    if "=" in tok:
                        inv.work_tree = tok.split("=", 1)[1]
                    elif opt_idx + 1 < n:
                        inv.work_tree = tokens[opt_idx + 1].strip("\"'")
                        opt_idx += 1
                    else:
                        inv.work_tree = ""

                opt_idx += 1

            invocations.append(inv)
            idx = opt_idx
        else:
            idx += 1

    return invocations


def is_core_hookspath_tampering(inv: GitInvocation) -> tuple[bool, str]:
    """Check if git command attempts to override or tamper with core.hooksPath."""
    # 1. Check -c global overrides
    for cfg in inv.config_overrides:
        cfg_lower = cfg.lower()
        if cfg_lower.startswith("core.hookspath"):
            return (
                True,
                f"Overriding core.hooksPath via git -c is strictly prohibited ({cfg}).",
            )

    # 2. Check git config subcommand
    if inv.subcommand == "config":
        args_lower = [a.lower() for a in inv.subcommand_args]
        if "core.hookspath" in args_lower:
            # Unsetting hooks is strictly prohibited
            if any(unset_flag in args_lower for unset_flag in ("--unset", "--unset-all")):
                return True, "Unsetting core.hooksPath is strictly prohibited by governance."

            # Check target value being configured
            non_flags = [a for a in inv.subcommand_args if not a.startswith("-")]
            if len(non_flags) >= 2 and non_flags[0].lower() == "core.hookspath":
                target_val = non_flags[1].strip("\"'").replace("\\", "/")
                # Only canonical .githooks is permitted
                if target_val not in (".githooks", ".githooks/"):
                    return (
                        True,
                        f"Setting core.hooksPath to non-canonical location '{target_val}' "
                        "is prohibited.",
                    )

    return False, ""


def is_git_alias_tampering_or_override(inv: GitInvocation) -> tuple[bool, str]:
    """Check if command overrides Git aliases via -c or creates/modifies git config alias.*."""
    # 1. Inline -c alias overrides and --config-env
    for cfg in inv.config_overrides:
        cfg_lower = cfg.lower()
        if cfg_lower.startswith("alias.") or re.search(r"\balias\.[a-zA-Z0-9_\-]+", cfg_lower):
            return (
                True,
                f"Defining or overriding Git aliases via git -c is strictly prohibited ({cfg}).",
            )

    # 2. git config alias.* modification
    if inv.subcommand == "config":
        combined_args = " ".join(inv.subcommand_args).lower()
        if re.search(r"\balias\.[a-zA-Z0-9_\-]+", combined_args):
            return (
                True,
                "Configuring or modifying Git aliases via 'git config alias.*' "
                "is strictly prohibited.",
            )

    return False, ""


def is_git_alias_invocation(subcommand: str | None, effective_cwd: Path | None) -> tuple[bool, str]:
    """Detect if requested git subcommand resolves to a configured git alias."""
    if not subcommand or subcommand.startswith("-"):
        return False, ""

    cmd = ["git", "config", "--get", f"alias.{subcommand}"]
    cwd_str = str(effective_cwd) if effective_cwd and effective_cwd.is_dir() else None
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=cwd_str,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            alias_val = proc.stdout.strip()
            return (
                True,
                f"Invocation of configured Git alias '{subcommand}' ({alias_val}) "
                "is prohibited by baseline governance.",
            )
    except (subprocess.SubprocessError, OSError):
        pass

    return False, ""


def is_git_push_to_protected(
    inv: GitInvocation, effective_branch: str | None = None
) -> tuple[bool, str]:
    """Check if git push targets main or master branch directly, in bulk, or via wildcards."""
    if inv.subcommand != "push":
        return False, ""

    args = inv.subcommand_args

    # 1. HARD DENY: Bulk push flags --all and --mirror
    for arg in args:
        lower_arg = arg.lower()
        if lower_arg in ("--all", "--mirror") or lower_arg.startswith(("--all=", "--mirror=")):
            return (
                True,
                f"Bulk push using '{arg}' is strictly prohibited as it targets protected branches.",
            )

    # 2. Check positional refspecs and arguments
    positional = [a for a in args if not a.startswith("-")]

    for arg in positional:
        lower_arg = arg.lower()

        # Wildcard refspecs affecting protected branches
        if "*" in lower_arg:
            return (
                True,
                f"Wildcard push refspec '{arg}' is strictly prohibited by TapJob governance.",
            )

        # Direct branch name: e.g. origin main
        if lower_arg in PROTECTED_BRANCHES:
            return True, f"Direct push to protected branch '{arg}' is strictly prohibited."

        # If on protected branch, pushing HEAD or @ resolves to the protected branch
        clean_ref = lower_arg.lstrip("+")
        if (
            clean_ref in ("head", "@")
            and effective_branch
            and effective_branch.lower() in ("main", "master")
        ):
            return (
                True,
                f"Pushing '{arg}' while on protected branch '{effective_branch}' "
                "is strictly prohibited.",
            )

        # Refspec formats:
        # HEAD:main, +HEAD:refs/heads/main, branch:main, :main (deletion)
        if ":" in clean_ref:
            dest = clean_ref.split(":", 1)[1]
            if dest in PROTECTED_BRANCHES or dest in ("main", "master"):
                return True, f"Pushing refspec to protected branch '{dest}' is strictly prohibited."
            if (
                dest in ("head", "@")
                and effective_branch
                and effective_branch.lower() in ("main", "master")
            ):
                return (
                    True,
                    f"Pushing refspec targeting '{dest}' on protected branch "
                    f"'{effective_branch}' is prohibited.",
                )
            if "*" in dest:
                return (
                    True,
                    f"Wildcard push destination refspec '{dest}' is strictly prohibited.",
                )

    # 3. If no refspec specified, git pushes current branch to tracking upstream
    if (
        (not positional or (len(positional) == 1 and not positional[0].startswith("refs/")))
        and effective_branch
        and effective_branch.lower() in ("main", "master")
    ):
        return (
            True,
            f"Pushing without refspec while on protected branch '{effective_branch}' "
            "is prohibited.",
        )

    return False, ""


def is_git_push_force_or_no_verify(inv: GitInvocation) -> tuple[bool, str]:
    """Check for force push or --no-verify bypasses across all syntax variants."""
    if inv.subcommand != "push":
        return False, ""

    args = inv.subcommand_args
    for arg in args:
        lower_arg = arg.lower()

        # Flags: -f, --force, --force-with-lease, --force-if-includes
        if lower_arg in ("-f", "--force", "--force-if-includes"):
            return True, "Force push (-f, --force) is strictly prohibited by TapJob governance."

        if lower_arg.startswith("--force-with-lease"):
            return (
                True,
                "Force push (--force-with-lease) is strictly prohibited by TapJob governance.",
            )

        if lower_arg.startswith("--force="):
            return True, "Force push (--force) is strictly prohibited by TapJob governance."

        if lower_arg == "--no-verify":
            return True, "--no-verify is prohibited on git push; safeguards cannot be bypassed."

        # Leading '+' in any non-flag refspec represents a force push
        if not arg.startswith("-") and arg.startswith("+"):
            return True, f"Force push via '+' refspec '{arg}' is strictly prohibited."

    return False, ""


def is_git_commit_on_main(
    inv: GitInvocation, effective_branch: str | None = None
) -> tuple[bool, str]:
    """Check if git commit is being run directly on main or master."""
    if inv.subcommand != "commit":
        return False, ""

    if effective_branch and effective_branch.lower() in ("main", "master"):
        return (
            True,
            f"Direct commits to protected branch '{effective_branch}' are strictly prohibited. "
            "Use a dedicated feature branch.",
        )
    return False, ""


def is_git_commit_no_verify(inv: GitInvocation) -> tuple[bool, str]:
    """Check if git commit bypasses hooks via --no-verify or -n."""
    if inv.subcommand != "commit":
        return False, ""

    for arg in inv.subcommand_args:
        lower_arg = arg.lower()
        if lower_arg in ("--no-verify", "-n"):
            return (
                True,
                "Bypassing git commit hooks via --no-verify or -n is strictly prohibited.",
            )
    return False, ""


def is_gh_or_curl_merge_or_mutation(tokens: list[str], cmd_str: str) -> tuple[bool, str]:
    """Detect and deny terminal-based GitHub PR merges and protected ref mutations."""
    eff_tokens = extract_effective_tokens(tokens)
    if not eff_tokens:
        return False, ""
    if eff_tokens[0] == "__GUARD_FAIL_CLOSED__":
        return True, "Unsupported, malformed, or ambiguous launcher options blocked to fail closed."

    cmd_lower = cmd_str.lower()
    binary = Path(eff_tokens[0].strip("\"'")).name.lower()

    # 1. gh pr merge
    if (
        binary in ("gh", "gh.exe")
        and len(eff_tokens) >= 3
        and eff_tokens[1].strip("\"'").lower() == "pr"
        and eff_tokens[2].strip("\"'").lower() == "merge"
    ):
        return True, "Automated PR merge via 'gh pr merge' is prohibited by TapJob governance."

    # 2. gh api calls targeting merge endpoints or mutations
    if (
        binary in ("gh", "gh.exe")
        and len(eff_tokens) >= 2
        and eff_tokens[1].strip("\"'").lower() == "api"
    ):
        # Check GraphQL mutations
        if "graphql" in cmd_lower and "mergepullrequest" in cmd_lower:
            return (
                True,
                "Automated GitHub PR merge via GraphQL mergePullRequest is prohibited.",
            )

        # UNCONDITIONAL BLOCK: REST pull request merge endpoints (/pulls/<n>/merge or merge-async)
        if re.search(r"(?:/?repos/[^/\s]+/[^/\s]+/)?pulls/\d+/(?:merge|merge-async)\b", cmd_lower):
            return (
                True,
                "Automated GitHub PR merge via pull request merge endpoint is strictly prohibited.",
            )

        # UNCONDITIONAL BLOCK: Plural protected git refs: /git/refs/heads/main or master
        if re.search(r"(?:/?repos/[^/\s]+/[^/\s]+/)?git/refs/heads/(?:main|master)\b", cmd_lower):
            return (
                True,
                "Direct GitHub API access targeting plural protected branch ref 'main'/'master' "
                "is prohibited.",
            )

        # Singular git ref mutation check: /git/ref/heads/main or master
        if re.search(r"(?:/?repos/[^/\s]+/[^/\s]+/)?git/ref/heads/(?:main|master)\b", cmd_lower):
            method = extract_http_method(eff_tokens)
            if method in ("PUT", "POST", "PATCH", "DELETE"):
                return (
                    True,
                    f"Direct GitHub API mutation ({method}) of protected ref 'main'/'master' "
                    "is prohibited.",
                )

    # 3. curl / Invoke-RestMethod targeting GitHub merge or ref mutation
    http_clients = (
        "curl",
        "curl.exe",
        "wget",
        "invoke-restmethod",
        "irm",
        "invoke-webrequest",
        "iwr",
    )
    if binary in http_clients and "api.github.com" in cmd_lower:
        # UNCONDITIONAL BLOCK: REST PR merge endpoints
        if re.search(r"/pulls/\d+/(?:merge|merge-async)\b", cmd_lower):
            return (
                True,
                "Terminal HTTP request attempting to merge PR is strictly prohibited.",
            )

        # UNCONDITIONAL BLOCK: Plural protected branch refs
        if re.search(r"/git/refs/heads/(?:main|master)\b", cmd_lower):
            return (
                True,
                "Terminal HTTP request targeting plural protected branch ref is prohibited.",
            )

        # Singular protected branch ref mutation check
        if re.search(r"/git/ref/heads/(?:main|master)\b", cmd_lower):
            method = extract_http_method(eff_tokens)
            if method in ("PUT", "POST", "PATCH", "DELETE"):
                return (
                    True,
                    f"Terminal HTTP request attempting to mutate ({method}) protected branch ref "
                    "is prohibited.",
                )

    return False, ""


def is_secret_dump_command(tokens: list[str]) -> bool:
    """Check for secret-dumping commands targeting env or private keys with path awareness."""
    eff_tokens = extract_effective_tokens(tokens)
    if not eff_tokens:
        return False
    if eff_tokens[0] == "__GUARD_FAIL_CLOSED__":
        return True

    for i, token in enumerate(eff_tokens):
        base = Path(token.strip("\"'")).name.lower()
        if base in READER_BINARIES:
            for arg in eff_tokens[i + 1 :]:
                clean_arg = arg.strip("\"'")
                if clean_arg.startswith("-"):
                    continue

                normalized_path = clean_arg.replace("\\", "/")
                filename = Path(normalized_path).name.lower()

                # Check for .env variants
                if filename == ".env" or filename.startswith(".env."):
                    # ALLOW canonical sample/example/template configurations
                    if filename in (
                        ".env.example",
                        ".env.sample",
                        ".env.template",
                    ) or filename.endswith((".example", ".sample", ".template")):
                        continue
                    return True

                # Check for SSH and TLS private keys
                if filename in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"):
                    return True
                if "id_rsa" in filename:
                    return True
                if filename.endswith((".key", ".pem")) and not any(
                    safe in filename for safe in ("cert", "public", "pub", "chain")
                ):
                    return True

    return False


def extract_nested_shell_command(tokens: list[str], raw_cmd: str | None = None) -> str | None:
    """Extract wrapped command string from recognized shell wrappers for recursive evaluation."""
    eff_tokens = extract_effective_tokens(tokens)
    if not eff_tokens:
        return None

    binary = Path(eff_tokens[0].strip("\"'")).name.lower()
    if binary not in SHELL_WRAPPERS:
        return None

    # POSIX shells: sh, bash, zsh, dash, ksh
    if binary in ("sh", "bash", "zsh", "dash", "ksh"):
        # If raw_cmd is provided, try POSIX-mode split first for clean nested quote handling
        if raw_cmd:
            try:
                posix_toks = extract_effective_tokens(shlex.split(raw_cmd.strip(), posix=True))
                if posix_toks and Path(posix_toks[0].strip("\"'")).name.lower() in (
                    "sh",
                    "bash",
                    "zsh",
                    "dash",
                    "ksh",
                ):
                    for i in range(1, len(posix_toks)):
                        arg = posix_toks[i].strip("\"'")
                        if arg.startswith("-") and not arg.startswith("--") and "c" in arg:
                            if i + 1 < len(posix_toks):
                                return posix_toks[i + 1]
                            c_idx = arg.index("c")
                            rem = arg[c_idx + 1 :].lstrip("=")
                            if rem:
                                return rem
                            return "__GUARD_FAIL_CLOSED__"
            except (ValueError, OSError):
                pass

        for i in range(1, len(eff_tokens)):
            arg = eff_tokens[i].strip("\"'")
            # Support standalone -c or combined flag clusters like -lc, -ec, -xce
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg:
                # If command argument follows as next token(s), extract it
                if i + 1 < len(eff_tokens):
                    return " ".join(eff_tokens[i + 1 :])
                # Check for inline command attached (e.g. -c"cmd", -c=cmd, -lc"cmd")
                c_idx = arg.index("c")
                rem = arg[c_idx + 1 :].lstrip("=")
                if rem:
                    return rem
                return "__GUARD_FAIL_CLOSED__"

    # Windows CMD: cmd /c <cmd>, cmd /k <cmd>
    if binary in ("cmd", "cmd.exe"):
        for i in range(1, len(eff_tokens)):
            arg = eff_tokens[i].strip("\"'").lower()
            if arg in ("/c", "/k") and i + 1 < len(eff_tokens):
                return " ".join(eff_tokens[i + 1 :])

    # PowerShell: powershell -Command <cmd>, pwsh -c <cmd>
    if binary in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        for i in range(1, len(eff_tokens)):
            arg = eff_tokens[i].strip("\"'").lower()
            if arg in ("-c", "-command") and i + 1 < len(eff_tokens):
                return " ".join(eff_tokens[i + 1 :])
            if arg.startswith("-c=") or arg.startswith("-command="):
                return arg.split("=", 1)[1]

    return None


def is_powershell_encoded_command(tokens: list[str]) -> bool:
    """Detect if command invokes PowerShell with encoded execution (-EncodedCommand/-enc/-e)."""
    eff_tokens = extract_effective_tokens(tokens)
    if not eff_tokens:
        return False
    binary = Path(eff_tokens[0].strip("\"'")).name.lower()
    if binary in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        for tok in eff_tokens[1:]:
            clean = tok.strip("\"'").lower()
            if clean.startswith("-") or clean.startswith("/"):
                flag = clean.lstrip("-/")
                # Match -EncodedCommand, -enc, -encoded, -e, -en, etc.
                if flag.startswith("enc") or flag in ("e", "en", "encodedcommand"):
                    return True
    return False


def is_evolution_send_endpoint(cmd_str: str) -> bool:
    """Check if command directly invokes Evolution API live message send endpoints."""
    lower = cmd_str.lower()
    patterns = [
        "/message/sendtext",
        "/message/sendmedia",
        "/message/sendwhatsapp",
        "/message/sendvoice",
        "/message/sendlocation",
        "/message/sendcontact",
    ]
    return any(p in lower for p in patterns)


def is_guard_tampering(cmd_str: str) -> bool:
    """Check if command attempts to delete, disable, or tamper with agent guards/hooks."""
    lower = cmd_str.lower()
    # Check for direct file redirection overwriting guards or git config
    guard_redir_re = r">\s*(?:\.agents[/\\]hooks\.json|\.githooks[/\\]pre-push|\.git[/\\]config)"
    if re.search(guard_redir_re, lower):
        return True

    # Tampering targets
    tamper_patterns = [
        "core.hookspath",
        ".git/config",
        ".git\\config",
        ".agents/hooks.json",
        ".agents\\hooks.json",
        ".githooks/pre-push",
        ".githooks\\pre-push",
        "tools/agent_guard/pre_tool_guard.py",
        "tools\\agent_guard\\pre_tool_guard.py",
        "tools/agent_guard/validate_governance.py",
        "tools\\agent_guard\\validate_governance.py",
    ]
    destructive_pattern = re.compile(
        r"\b(?:rm|del|erase|remove-item|unset|unlink)\b",
        re.IGNORECASE,
    )
    for pattern in tamper_patterns:
        if pattern in lower:
            if pattern == "core.hookspath" and "unset" in lower:
                return True
            if pattern != "core.hookspath" and destructive_pattern.search(lower):
                return True
    return False


def resolve_effective_cwd(base_cwd: Path | None, cwd_override: str | None) -> Path | None:
    """Resolve effective git directory considering base tool Cwd and git -C overrides."""
    if not cwd_override:
        return base_cwd

    override_path = Path(cwd_override)
    if override_path.is_absolute():
        return override_path.resolve()
    if base_cwd:
        return (base_cwd / override_path).resolve()
    return override_path.resolve()


def evaluate_command(
    command_line: str,
    cwd: Path | str | None = None,
    current_branch: str | None = None,
    depth: int = 0,
) -> tuple[str, str]:
    """Evaluate a command string against repository safety policies.

    Parameters
    ----------
    command_line : str
        The raw shell command line to evaluate.
    cwd : Path | str | None
        Target working directory for the tool call (Cwd-aware).
    current_branch : str | None
        Explicit branch override (used primarily for unit testing).
    depth : int
        Recursion depth for nested shell evaluation.

    Returns
    -------
    tuple[str, str]
        (decision, reason) where decision is one of:
        "allow", "deny", "ask", "force_ask".
    """
    if not command_line or not command_line.strip():
        return "allow", "Empty command line."

    resolved_base_cwd = Path(cwd).resolve() if cwd else None
    sub_commands = split_command_chain(command_line)

    for sub_cmd in sub_commands:
        # HARD DENY: Git environment variable retargeting
        if GIT_ENV_RETARGET_RE.search(sub_cmd):
            return (
                "deny",
                "Overriding repository or config targeting via Git environment variables "
                "is strictly prohibited.",
            )

        tokens = tokenize_command(sub_cmd)
        if not tokens:
            continue

        eff_tokens = extract_effective_tokens(tokens)
        if eff_tokens and eff_tokens[0] == "__GUARD_FAIL_CLOSED__":
            return (
                "deny",
                "Unsupported, malformed, or ambiguous launcher options blocked to fail closed.",
            )

        # HARD DENY: Encoded PowerShell commands (-EncodedCommand/-enc/-e)
        if is_powershell_encoded_command(tokens):
            return (
                "deny",
                "Encoded PowerShell commands (-EncodedCommand/-enc) are strictly "
                "prohibited by governance.",
            )

        # 0. Recursive evaluation of nested shell wrappers (powershell -c, bash -c, cmd /c)
        nested_cmd = extract_nested_shell_command(tokens, raw_cmd=sub_cmd)
        if nested_cmd == "__GUARD_FAIL_CLOSED__":
            return (
                "deny",
                "Shell wrapper specifies -c command flag but command payload "
                "cannot be safely parsed.",
            )
        if nested_cmd:
            if depth >= MAX_NESTED_SHELL_DEPTH:
                return (
                    "deny",
                    f"Maximum nested shell wrapper recursion depth ({MAX_NESTED_SHELL_DEPTH}) "
                    "exceeded; execution denied to fail closed.",
                )
            clean_nested = nested_cmd.strip()
            if (clean_nested.startswith('"') and clean_nested.endswith('"')) or (
                clean_nested.startswith("'") and clean_nested.endswith("'")
            ):
                clean_nested = clean_nested[1:-1]
            nested_dec, nested_reason = evaluate_command(
                clean_nested,
                cwd=resolved_base_cwd,
                current_branch=current_branch,
                depth=depth + 1,
            )
            if nested_dec != "allow":
                return nested_dec, f"Nested shell wrapper blocked: {nested_reason}"

        # 1. Parse git invocations and global options
        git_invocations = parse_git_invocations(tokens)
        for inv in git_invocations:
            # HARD DENY: --git-dir or --work-tree retargeting
            if inv.git_dir is not None or inv.work_tree is not None:
                return (
                    "deny",
                    "Specifying --git-dir or --work-tree is prohibited in agent Git invocations.",
                )

            # Resolve effective Cwd for this specific git command
            effective_git_cwd = resolve_effective_cwd(resolved_base_cwd, inv.cwd_override)
            effective_branch = (
                current_branch
                if current_branch is not None
                else get_current_git_branch(effective_git_cwd)
            )

            # HARD DENY: core.hooksPath override via -c or invalid git config
            tamper_hook, hook_reason = is_core_hookspath_tampering(inv)
            if tamper_hook:
                return "deny", hook_reason

            # HARD DENY: Defining or modifying Git aliases
            alias_ov, alias_ov_reason = is_git_alias_tampering_or_override(inv)
            if alias_ov:
                return "deny", alias_ov_reason

            # HARD DENY: Invocation of configured Git aliases
            alias_inv, alias_reason = is_git_alias_invocation(inv.subcommand, effective_git_cwd)
            if alias_inv:
                return "deny", alias_reason

            # HARD DENY: Direct git push to main / master
            push_prot, push_reason = is_git_push_to_protected(inv, effective_branch)
            if push_prot:
                return "deny", push_reason

            # HARD DENY: Force push or --no-verify on git push
            push_force, force_reason = is_git_push_force_or_no_verify(inv)
            if push_force:
                return "deny", force_reason

            # HARD DENY: Git commit directly on main / master
            commit_prot, commit_reason = is_git_commit_on_main(inv, effective_branch)
            if commit_prot:
                return "deny", commit_reason

            # HARD DENY: Git commit --no-verify or -n
            commit_noverify, noverify_reason = is_git_commit_no_verify(inv)
            if commit_noverify:
                return "deny", noverify_reason

        # 2. HARD DENY: gh pr merge, gh api PR merge / ref mutation, curl merge
        gh_denied, gh_reason = is_gh_or_curl_merge_or_mutation(tokens, sub_cmd)
        if gh_denied:
            return "deny", gh_reason

        # 3. HARD DENY: Direct live messaging via Evolution API endpoints
        if is_evolution_send_endpoint(sub_cmd):
            return (
                "deny",
                "Live WhatsApp messaging via Evolution API send endpoints is strictly prohibited "
                "outside explicit, human-authorized canary runs.",
            )

        # 4. HARD DENY: Tampering with governance guards
        if is_guard_tampering(sub_cmd):
            return (
                "deny",
                "Disabling, removing, or tampering with governance guards is strictly prohibited.",
            )

        # 5. HARD DENY: Secret dump commands targeting private keys / .env
        if is_secret_dump_command(tokens):
            return (
                "deny",
                "Command appears to dump secret credentials or private environment files.",
            )

        # 6. FORCE_ASK / HUMAN GATE: Destructive git reset/clean
        if DESTRUCTIVE_GIT_RE.search(sub_cmd):
            return (
                "force_ask",
                "Destructive git operation (reset --hard or clean) requires human authorization.",
            )

        # 7. FORCE_ASK / HUMAN GATE: Production, infrastructure or DB mutation
        for pattern, reason in FORCE_ASK_PATTERNS:
            if pattern.search(sub_cmd):
                return "force_ask", reason

    # Routine development commands permitted by default
    return "allow", "Routine development command permitted."


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle Antigravity PreToolUse hook payload with Cwd and workspace awareness.

    Normalizes payload supporting both standard Antigravity protojson camelCase
    (toolCall.name, args.CommandLine, args.Cwd, workspacePaths) and alternate snake_case
    (tool_call.name, tool_name, args.command_line, args.cwd, workspace_paths).
    """
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    tool_name = tool_call.get("name") or payload.get("tool_name") or payload.get("name") or ""
    args = tool_call.get("args") or payload.get("tool_args") or payload.get("args") or {}
    command_line = (
        args.get("CommandLine")
        or args.get("command_line")
        or args.get("command")
        or payload.get("command")
        or ""
    )

    if tool_name == "run_command" or (not tool_name and command_line):
        cwd_arg = args.get("Cwd") or args.get("cwd") or payload.get("cwd")
        workspace_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []

        resolved_cwd: Path | None = None
        if cwd_arg and str(cwd_arg).strip():
            try:
                candidate_cwd = Path(cwd_arg).resolve()
                if workspace_paths:
                    is_safe = False
                    for wp in workspace_paths:
                        try:
                            wp_path = Path(wp).resolve()
                            if candidate_cwd == wp_path or wp_path in candidate_cwd.parents:
                                is_safe = True
                                break
                        except (ValueError, OSError):
                            is_safe = False
                    if is_safe and candidate_cwd.is_dir():
                        resolved_cwd = candidate_cwd
                elif candidate_cwd.is_dir():
                    resolved_cwd = candidate_cwd
            except (ValueError, OSError):
                resolved_cwd = None

        decision, reason = evaluate_command(command_line, cwd=resolved_cwd)
        return {"decision": decision, "reason": reason}

    return {"decision": "allow", "reason": f"Tool '{tool_name}' permitted."}


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            result = {"decision": "allow", "reason": "No input payload provided."}
        else:
            payload = json.loads(raw_input)
            result = handle_pre_tool_use(payload)
    except Exception as exc:  # Fail closed for unexpected guard execution errors
        result = {"decision": "deny", "reason": f"Guard execution error: {exc}"}

    sys.stdout.write(f"{json.dumps(result)}\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

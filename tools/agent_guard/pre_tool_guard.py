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


def get_current_git_branch() -> str | None:
    """Determine current git branch safely without throwing exceptions."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            return branch if branch else None
    except (subprocess.SubprocessError, OSError):
        return None
    return None


def split_command_chain(cmd: str) -> list[str]:
    """Split chained commands separated by &&, ;, || or newlines.

    Carefully handles quotes so tokens inside strings are not split.
    """
    sub_commands: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
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
            elif char == "&" and i + 1 < len(cmd) and cmd[i + 1] == "&":
                segment = "".join(current).strip()
                if segment:
                    sub_commands.append(segment)
                current = []
                i += 1  # Skip second &
            elif char == "|" and i + 1 < len(cmd) and cmd[i + 1] == "|":
                segment = "".join(current).strip()
                if segment:
                    sub_commands.append(segment)
                current = []
                i += 1  # Skip second |
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
    # Normalize backslashes in binary names if any
    clean_cmd = cmd_str.strip()
    try:
        # posix=False handles Windows paths and quoting styles gracefully
        return shlex.split(clean_cmd, posix=False)
    except Exception:
        # Fallback to simple whitespace splitting
        return clean_cmd.split()


def is_git_push_to_protected(tokens: list[str]) -> bool:
    """Check if git push targets main or master branch."""
    # Find position of git
    git_idx = -1
    for idx, t in enumerate(tokens):
        base = t.strip("\"'").lower()
        if base in ("git", "git.exe"):
            git_idx = idx
            break
    if git_idx == -1 or git_idx + 1 >= len(tokens):
        return False

    subcommand = tokens[git_idx + 1].strip("\"'").lower()
    if subcommand != "push":
        return False

    # Check push args
    push_args = [t.strip("\"'") for t in tokens[git_idx + 2 :]]
    for arg in push_args:
        lower_arg = arg.lower()
        # Direct branch names
        if lower_arg in ("main", "master", "refs/heads/main", "refs/heads/master"):
            return True
        # Refspecs: e.g. HEAD:main, HEAD:refs/heads/main, feature:main, :main
        if ":" in lower_arg:
            dest = lower_arg.split(":", 1)[1]
            if dest in ("main", "master", "refs/heads/main", "refs/heads/master"):
                return True
    return False


def is_git_push_force_or_no_verify(tokens: list[str]) -> tuple[bool, str]:
    """Check for force push or --no-verify bypasses."""
    git_idx = -1
    for idx, t in enumerate(tokens):
        base = t.strip("\"'").lower()
        if base in ("git", "git.exe"):
            git_idx = idx
            break
    if git_idx == -1 or git_idx + 1 >= len(tokens):
        return False, ""

    subcommand = tokens[git_idx + 1].strip("\"'").lower()
    if subcommand != "push":
        return False, ""

    for t in tokens[git_idx + 2 :]:
        cleaned = t.strip("\"'").lower()
        if cleaned in ("-f", "--force", "--force-with-lease", "--force-if-includes"):
            return True, "Force push is strictly prohibited by TapJob governance."
        if cleaned == "--no-verify":
            return True, "--no-verify is prohibited on git push; safeguards cannot be bypassed."
    return False, ""


def is_git_commit_on_main(tokens: list[str], current_branch: str | None) -> bool:
    """Check if git commit is being run directly on main or master."""
    git_idx = -1
    for idx, t in enumerate(tokens):
        base = t.strip("\"'").lower()
        if base in ("git", "git.exe"):
            git_idx = idx
            break
    if git_idx == -1 or git_idx + 1 >= len(tokens):
        return False

    subcommand = tokens[git_idx + 1].strip("\"'").lower()
    if subcommand != "commit":
        return False

    branch = current_branch if current_branch else get_current_git_branch()
    return bool(branch and branch.lower() in ("main", "master"))


def is_gh_pr_merge(tokens: list[str]) -> bool:
    """Check if gh pr merge is invoked."""
    for i in range(len(tokens) - 2):
        t0 = tokens[i].strip("\"'").lower()
        t1 = tokens[i + 1].strip("\"'").lower()
        t2 = tokens[i + 2].strip("\"'").lower()
        if t0 in ("gh", "gh.exe") and t1 == "pr" and t2 == "merge":
            return True
    return False


def is_evolution_send_endpoint(cmd_str: str) -> bool:
    """Check if command directly invokes Evolution API live message send endpoints."""
    # Matches live messaging send paths in Evolution API
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
    # Check for direct file redirection overwriting guards
    if re.search(r">\s*(?:\.agents[/\\]hooks\.json|\.githooks[/\\]pre-push)", lower):
        return True

    # Attempts to remove or disable .agents/hooks.json or .githooks
    tamper_patterns = [
        "core.hookspath",  # e.g. git config --unset core.hooksPath
        ".agents/hooks.json",
        ".agents\\hooks.json",
        ".githooks/pre-push",
        ".githooks\\pre-push",
        "pre_tool_guard.py",
        "validate_governance.py",
    ]
    # Use word boundary regex for destructive commands
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


def is_secret_dump_command(cmd_str: str) -> bool:
    """Check for obvious secret-dumping commands targeting env or private keys."""
    lower = cmd_str.lower()
    # e.g. cat .env, type .env (unless .env.example)
    if re.search(r"\b(?:cat|type|Get-Content)\s+(?:\.env|\.env\.(?!example))", lower):
        return True
    # e.g. printing private keys
    return bool("id_rsa" in lower and any(v in lower for v in ("cat", "type", "get-content")))


def evaluate_command(command_line: str, current_branch: str | None = None) -> tuple[str, str]:
    """Evaluate a command string against safety policies.

    Returns (decision, reason).
    decision is one of: "allow", "deny", "ask", "force_ask".
    """
    if not command_line or not command_line.strip():
        return "allow", "Empty command line."

    sub_commands = split_command_chain(command_line)

    for sub_cmd in sub_commands:
        tokens = tokenize_command(sub_cmd)
        if not tokens:
            continue

        # 1. HARD DENY: Push to protected branch
        if is_git_push_to_protected(tokens):
            return (
                "deny",
                "Direct push to main or master branch is strictly prohibited by TapJob governance.",
            )

        # 2. HARD DENY: Force push or --no-verify bypass
        is_force, force_reason = is_git_push_force_or_no_verify(tokens)
        if is_force:
            return "deny", force_reason

        # 3. HARD DENY: Commit on main
        if is_git_commit_on_main(tokens, current_branch):
            return (
                "deny",
                "Direct commit to main or master branch is prohibited; must use feature branches.",
            )

        # 4. HARD DENY: gh pr merge during baseline governance
        if is_gh_pr_merge(tokens):
            return (
                "deny",
                "Automated PR merge via 'gh pr merge' is prohibited; merges require human review.",
            )

        # 5. HARD DENY: Direct Evolution live messaging endpoints
        if is_evolution_send_endpoint(sub_cmd):
            return (
                "deny",
                "Direct invocation of Evolution live messaging send endpoint is prohibited.",
            )

        # 6. HARD DENY: Tampering with governance guards
        if is_guard_tampering(sub_cmd):
            return (
                "deny",
                "Disabling, removing, or tampering with governance guards is strictly prohibited.",
            )

        # 7. HARD DENY: Secret dump commands
        if is_secret_dump_command(sub_cmd):
            return (
                "deny",
                "Command appears to dump secret credentials or private environment files.",
            )

        # 8. FORCE_ASK / HUMAN GATE: Destructive git reset/clean
        if DESTRUCTIVE_GIT_RE.search(sub_cmd):
            return (
                "force_ask",
                "Destructive git operation (reset --hard or clean) requires human authorization.",
            )

        # 9. FORCE_ASK / HUMAN GATE: Production, infrastructure or DB mutation
        for pattern, reason in FORCE_ASK_PATTERNS:
            if pattern.search(sub_cmd):
                return "force_ask", reason

    # Routine development commands are allowed by default
    return "allow", "Routine development command permitted."


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle Antigravity PreToolUse hook payload."""
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")

    if tool_name == "run_command":
        args = tool_call.get("args", {})
        command_line = args.get("CommandLine", "")
        decision, reason = evaluate_command(command_line)
        return {"decision": decision, "reason": reason}

    # All other tool calls (view_file, write_to_file, etc.) are allowed by default
    return {"decision": "allow", "reason": f"Tool '{tool_name}' permitted."}


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            # If no input provided, default to allow
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

#!/usr/bin/env python3
"""TapJob Governance & Safety Foundation Validator.

Validates that all governance documentation, modular Antigravity rules,
safety hooks, and local defense-in-depth files are properly configured,
syntactically valid, and adhere to project invariants.

Standard library only. Exits 0 on success, 1 on any validation failure.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

# Allowed official trigger values for Antigravity rules
VALID_TRIGGERS = {"always_on", "model_decision", "glob", "manual"}

# Hook command parser constants for fail-closed structural validation
PERMITTED_PYTHON_EXECUTABLES = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
}
PERMITTED_PYTHON_FLAGS = {"-u", "-b", "-e", "-s", "-v", "-o", "-oo"}
FORBIDDEN_HOOK_COMMAND_CHARS = set(";&|><$`\n\r")

# Required files across repository
REQUIRED_FILES = [
    "AGENTS.md",
    ".agents/hooks.json",
    ".agents/rules/governance.md",
    ".agents/rules/architecture.md",
    ".agents/rules/git-workflow.md",
    ".agents/rules/runtime-safety.md",
    ".agents/rules/secrets-security.md",
    ".agents/rules/quality-gates.md",
    "tools/agent_guard/pre_tool_guard.py",
    "tools/agent_guard/test_pre_tool_guard.py",
    "tools/agent_guard/validate_governance.py",
    "tools/agent_guard/test_validate_governance.py",
    "tools/agent_guard/install_local_guards.ps1",
    "tools/agent_guard/test_pre_push_hook.py",
    "tools/agent_guard/test_runtime_smoke.py",
    ".githooks/pre-push",
    "docs/agent-factory/PERMISSION_MATRIX.md",
    "docs/agent-factory/AUTONOMY_MODEL.md",
    "docs/agent-factory/OPERATING_MODEL.md",
]

# Required phrases / invariants in AGENTS.md
AGENTS_INVARIANTS = [
    ("Antigravity 2.0", "Antigravity 2.0 multi-agent orchestrator declaration"),
    ("Maestri", "Explicit exclusion of Maestri"),
    ("Career Core", "Single Career Core architecture invariant"),
    ("Evolution API", "Evolution API provider transport invariant"),
    ("Professional Truth Store", "Professional Truth Store invariant"),
]


def parse_simple_yaml_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
    """Extract and parse YAML frontmatter from markdown without external libraries.

    Supports simple key-value pairs, multiline strings, and basic lists.
    Returns (frontmatter_dict, error_message).
    """
    if not content.startswith("---"):
        return None, "File does not begin with '---' YAML frontmatter delimiter."

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, "File does not contain matching closing '---' frontmatter delimiter."

    raw_yaml = parts[1].strip()
    if not raw_yaml:
        return None, "YAML frontmatter is empty."

    data: dict[str, Any] = {}
    lines = raw_yaml.splitlines()
    current_key: str | None = None
    multiline_val: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check for key: value
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            if current_key and multiline_val:
                data[current_key] = "\n".join(multiline_val).strip()
                multiline_val = []

            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()

            if val in (">", ">-", "|", "|-"):
                current_key = key
                multiline_val = []
            elif val.startswith("[") and val.endswith("]"):
                # Simple list: [a, b, c]
                items = [item.strip().strip("'\"") for item in val[1:-1].split(",") if item.strip()]
                data[key] = items
                current_key = None
            else:
                data[key] = val.strip("'\"")
                current_key = None
        elif current_key:
            multiline_val.append(stripped)

    if current_key and multiline_val:
        data[current_key] = "\n".join(multiline_val).strip()

    return data, ""


def validate_rule_frontmatter(rule_path: Path) -> list[str]:
    """Validate YAML frontmatter and triggers in an Antigravity rule file."""
    errors: list[str] = []
    try:
        content = rule_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"{rule_path}: Failed to read file: {exc}"]

    fm, parse_err = parse_simple_yaml_frontmatter(content)
    if parse_err or fm is None:
        errors.append(f"{rule_path}: Frontmatter error: {parse_err}")
        return errors

    trigger = fm.get("trigger")
    if not trigger:
        errors.append(f"{rule_path}: Missing 'trigger' in frontmatter.")
    elif trigger not in VALID_TRIGGERS:
        triggers_str = sorted(VALID_TRIGGERS)
        errors.append(
            f"{rule_path}: Unsupported trigger '{trigger}'. Must be one of {triggers_str}."
        )

    # Validate trigger-specific fields
    if trigger == "model_decision":
        desc = fm.get("description")
        if not desc or not str(desc).strip():
            errors.append(
                f"{rule_path}: Trigger 'model_decision' requires a non-empty 'description'."
            )

    if trigger == "glob":
        glob_val = fm.get("glob") or fm.get("globs")
        if not glob_val:
            errors.append(
                f"{rule_path}: Trigger 'glob' requires 'glob' or 'globs' pattern specification."
            )

    return errors


VALID_HOOK_EVENTS = {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"}


def validate_hooks_json(hooks_path: Path, repo_root: Path) -> list[str]:
    """Validate hooks.json structure and fail closed if the required guard is missing or invalid."""
    errors: list[str] = []
    try:
        content = hooks_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        return [f"{hooks_path}: Invalid JSON or cannot read: {exc}"]

    if not isinstance(data, dict):
        return [f"{hooks_path}: Top-level structure must be a JSON object."]

    if not data:
        return [f"{hooks_path}: hooks.json must contain at least one hook configuration."]

    has_valid_run_command_guard = False

    for hook_name, hook_spec in data.items():
        if not isinstance(hook_spec, dict):
            errors.append(f"{hooks_path}: Hook '{hook_name}' must be an object.")
            continue

        # Check enabled property (must be true, cannot be disabled)
        is_enabled = hook_spec.get("enabled", True)
        if is_enabled is False:
            errors.append(f"{hooks_path}: Hook '{hook_name}' is disabled; guard must be enabled.")
            continue
        if not isinstance(is_enabled, bool):
            errors.append(f"{hooks_path}: Hook '{hook_name}' 'enabled' field must be a boolean.")
            continue

        # Detect unknown or typo events in hook spec
        for key in hook_spec:
            if key != "enabled" and key not in VALID_HOOK_EVENTS:
                errors.append(f"{hooks_path}: Unknown or typo event '{key}' in hook '{hook_name}'.")

        pre_tool_use = hook_spec.get("PreToolUse")
        if pre_tool_use is not None:
            if not isinstance(pre_tool_use, list) or not pre_tool_use:
                errors.append(
                    f"{hooks_path}: Event 'PreToolUse' in hook '{hook_name}' must be a "
                    "non-empty list."
                )
                continue

            for group_idx, group in enumerate(pre_tool_use):
                if not isinstance(group, dict):
                    errors.append(
                        f"{hooks_path}: PreToolUse group [{group_idx}] in '{hook_name}' "
                        "must be an object."
                    )
                    continue

                matcher = group.get("matcher")
                if not isinstance(matcher, str) or not matcher.strip():
                    errors.append(
                        f"{hooks_path}: PreToolUse group [{group_idx}] in '{hook_name}' "
                        "missing string 'matcher'."
                    )
                    continue

                # Matcher must match run_command
                if matcher != "run_command" and not re.search(r"\brun_command\b", matcher):
                    errors.append(
                        f"{hooks_path}: PreToolUse matcher '{matcher}' in '{hook_name}' "
                        "does not match 'run_command'."
                    )
                    continue

                hooks_list = group.get("hooks")
                if not isinstance(hooks_list, list) or not hooks_list:
                    errors.append(
                        f"{hooks_path}: PreToolUse group [{group_idx}] in '{hook_name}' "
                        "must contain a non-empty 'hooks' list."
                    )
                    continue

                for handler_idx, handler in enumerate(hooks_list):
                    if not isinstance(handler, dict):
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            "must be an object."
                        )
                        continue

                    handler_type = handler.get("type", "command")
                    if handler_type != "command":
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"has unsupported type '{handler_type}' (must be 'command')."
                        )
                        continue

                    timeout = handler.get("timeout")
                    if (
                        timeout is None
                        or isinstance(timeout, bool)
                        or not isinstance(timeout, int)
                        or timeout <= 0
                    ):
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            "timeout must be a positive integer."
                        )

                    cmd = handler.get("command")
                    if not cmd or not isinstance(cmd, str):
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            "missing 'command' string."
                        )
                        continue

                    # 1. Reject forbidden shell characters / operators
                    if any(c in FORBIDDEN_HOOK_COMMAND_CHARS for c in cmd):
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            "command contains forbidden shell characters or operators."
                        )
                        continue

                    # 2. Parse tokens structurally
                    try:
                        tokens = [t.strip("\"'") for t in shlex.split(cmd, posix=False)]
                    except ValueError as exc:
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"command syntax error: {exc}."
                        )
                        continue

                    if not tokens:
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' empty command."
                        )
                        continue

                    # 3. Invoked executable must be a permitted python interpreter
                    exe_name = Path(tokens[0]).name.lower()
                    if exe_name not in PERMITTED_PYTHON_EXECUTABLES:
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"command must invoke a python interpreter, got '{tokens[0]}'."
                        )
                        continue

                    # 4. Parse optional python flags before target script
                    arg_idx = 1
                    flag_error = False
                    while arg_idx < len(tokens) and tokens[arg_idx].startswith("-"):
                        flag = tokens[arg_idx].lower()
                        if flag not in PERMITTED_PYTHON_FLAGS:
                            errors.append(
                                f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                                f"contains unexpected python flag '{tokens[arg_idx]}'."
                            )
                            flag_error = True
                            break
                        arg_idx += 1

                    if flag_error:
                        continue

                    if arg_idx >= len(tokens):
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            "command missing target script."
                        )
                        continue

                    script_token = tokens[arg_idx]

                    # 5. Reject unexpected trailing arguments after the target script
                    if arg_idx + 1 < len(tokens):
                        extra_args = tokens[arg_idx + 1 :]
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"contains unexpected arguments after script: {extra_args}."
                        )
                        continue

                    # 6. Resolve target script relative to hooks_path.parent
                    resolved_script = (hooks_path.parent / script_token).resolve()
                    canonical_guard_path = (
                        repo_root / "tools" / "agent_guard" / "pre_tool_guard.py"
                    ).resolve()

                    if not resolved_script.exists():
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"references non-existent script '{script_token}'."
                        )
                        continue

                    if resolved_script != canonical_guard_path:
                        errors.append(
                            f"{hooks_path}: Handler [{handler_idx}] in '{hook_name}' "
                            f"command must reference 'pre_tool_guard.py' (target script "
                            f"'{script_token}' does not resolve to canonical pre_tool_guard.py)."
                        )
                        continue

                    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
                        has_valid_run_command_guard = True

    if not has_valid_run_command_guard and not any("is disabled" in e for e in errors):
        errors.append(
            f"{hooks_path}: No valid, enabled PreToolUse hook configuration found "
            "protecting 'run_command' with pre_tool_guard.py."
        )

    return errors


def validate_agents_md(agents_md_path: Path) -> list[str]:
    """Validate that AGENTS.md contains necessary invariants and reconciled bootstrap language."""
    errors: list[str] = []
    try:
        content = agents_md_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"{agents_md_path}: Cannot read: {exc}"]

    for phrase, desc in AGENTS_INVARIANTS:
        if phrase not in content:
            errors.append(f"{agents_md_path}: Missing required invariant: {desc} ('{phrase}').")

    # Verify that the historical pre-CI bootstrap exception is reconciled
    has_bootstrap = "enquanto o repositório ainda não possui workflow de CI configurado" in content
    reconciled = any(k in content for k in ("expirada", "expirou", "obrigatório"))
    if has_bootstrap and not reconciled:
        err_msg = "Pre-CI bootstrap exception appears active; must be reconciled as expired."
        errors.append(f"{agents_md_path}: {err_msg}")

    return errors


def validate_pre_push_hook(pre_push_path: Path) -> list[str]:
    """Validate that the git pre-push hook prevents pushing to main/master."""
    errors: list[str] = []
    try:
        content = pre_push_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"{pre_push_path}: Cannot read: {exc}"]

    if "main" not in content or "master" not in content:
        errors.append(
            f"{pre_push_path}: Pre-push hook must explicitly block main and master branches."
        )

    return errors


def validate_canonical_ownership(repo_root: Path) -> list[str]:
    """Validate canonical owner ricardors081 and lack of obsolete operational references."""
    errors: list[str] = []

    # Check AGENTS.md and governance.md declare canonical repo
    agents_file = repo_root / "AGENTS.md"
    if agents_file.exists():
        content = agents_file.read_text(encoding="utf-8")
        if "ricardors081/tapjob" not in content:
            errors.append("AGENTS.md must declare 'ricardors081/tapjob' as canonical repository.")

    gov_rule = repo_root / ".agents" / "rules" / "governance.md"
    if gov_rule.exists():
        content = gov_rule.read_text(encoding="utf-8")
        if "ricardors081/tapjob" not in content:
            errors.append(
                ".agents/rules/governance.md must declare 'ricardors081/tapjob' as canonical."
            )

    # Check that no obsolete operational references to old owners exist in active directories
    old_owners = ["ricardo" + "081683", "ricardos" + "081", "ricardoo" + "081"]
    operational_dirs = [".agents", ".github", "infra", "tools"]
    for dir_name in operational_dirs:
        target_dir = repo_root / dir_name
        if not target_dir.exists():
            continue
        for p in target_dir.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
                continue
            if p.name in ("validate_governance.py", "test_validate_governance.py"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                for old_owner in old_owners:
                    if old_owner in text:
                        errors.append(
                            f"Obsolete operational reference '{old_owner}' found in "
                            f"{p.relative_to(repo_root)}."
                        )
            except (OSError, UnicodeDecodeError):
                continue

    return errors


def validate_agent_factory_status(repo_root: Path) -> list[str]:
    """Validate that AF-0 and AF-1 status is properly declared in PROJECT_STATE.md."""
    errors: list[str] = []
    proj_state = repo_root / "docs" / "PROJECT_STATE.md"
    if not proj_state.exists():
        return ["docs/PROJECT_STATE.md is missing."]

    content = proj_state.read_text(encoding="utf-8")
    valid_states = (
        "AF-1 = NOT STARTED",
        "AF-1: NOT STARTED",
        "AF-1 = IN PROGRESS",
        "AF-1: IN PROGRESS",
    )
    if not any(state in content for state in valid_states):
        errors.append("docs/PROJECT_STATE.md must state that AF-1 is IN PROGRESS or NOT STARTED.")

    return errors


def validate_required_rule_triggers(repo_root: Path) -> list[str]:
    """Validate specific required triggers for core Antigravity rule files."""
    errors: list[str] = []
    rules_dir = repo_root / ".agents" / "rules"
    if not rules_dir.exists():
        return []

    always_on_rules = [
        "governance.md",
        "git-workflow.md",
        "runtime-safety.md",
        "secrets-security.md",
    ]
    model_decision_rules = [
        "architecture.md",
        "quality-gates.md",
    ]

    for r_name in always_on_rules:
        r_path = rules_dir / r_name
        if r_path.exists():
            content = r_path.read_text(encoding="utf-8")
            fm, _ = parse_simple_yaml_frontmatter(content)
            if fm and fm.get("trigger") != "always_on":
                errors.append(f"{r_name} must have trigger 'always_on', got '{fm.get('trigger')}'.")

    for r_name in model_decision_rules:
        r_path = rules_dir / r_name
        if r_path.exists():
            content = r_path.read_text(encoding="utf-8")
            fm, _ = parse_simple_yaml_frontmatter(content)
            if fm and fm.get("trigger") != "model_decision":
                errors.append(
                    f"{r_name} must have trigger 'model_decision', got '{fm.get('trigger')}'."
                )

    return errors


def validate_repository(repo_root: Path) -> list[str]:
    """Run full governance validation across the repository."""
    errors: list[str] = []

    # 1. Check all required files
    for rel_path in REQUIRED_FILES:
        full_path = repo_root / rel_path
        if not full_path.exists():
            errors.append(f"Required governance file missing: {rel_path}")

    # 2. Check rule frontmatter in .agents/rules/*.md
    rules_dir = repo_root / ".agents" / "rules"
    if rules_dir.exists():
        rule_files = list(rules_dir.glob("*.md"))
        if not rule_files:
            errors.append(".agents/rules directory exists but contains no .md rules.")
        for rf in rule_files:
            errors.extend(validate_rule_frontmatter(rf))

    # 3. Check specific required rule triggers
    errors.extend(validate_required_rule_triggers(repo_root))

    # 4. Check .agents/hooks.json
    hooks_file = repo_root / ".agents" / "hooks.json"
    if hooks_file.exists():
        errors.extend(validate_hooks_json(hooks_file, repo_root))

    # 5. Check AGENTS.md invariants
    agents_file = repo_root / "AGENTS.md"
    if agents_file.exists():
        errors.extend(validate_agents_md(agents_file))

    # 6. Check .githooks/pre-push
    pre_push_file = repo_root / ".githooks" / "pre-push"
    if pre_push_file.exists():
        errors.extend(validate_pre_push_hook(pre_push_file))

    # 7. Check canonical repository ownership and zero operational old-owner residuals
    errors.extend(validate_canonical_ownership(repo_root))

    # 8. Check Agent Factory status (AF-1 not started)
    errors.extend(validate_agent_factory_status(repo_root))

    return errors


def main() -> None:
    # Repository root is two levels up from this script
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    errors = validate_repository(repo_root)

    if errors:
        sys.stderr.write("[FAIL] TapJob Governance Validation Failed:\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        sys.exit(1)

    sys.stdout.write(
        "[PASS] TapJob Governance Validation Passed: "
        "All rules, hooks, guards, and invariants verified.\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

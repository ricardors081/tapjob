"""CLI and programmatic bootstrap helper for TapJob Agent Factory."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.agent_factory.team import export_agent_md_files, load_all_profiles, load_profile
from tools.agent_factory.worktree_dispatcher import WorktreeDispatcher


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for Agent Factory inspection and bootstrap."""
    parser = argparse.ArgumentParser(description="TapJob Agent Factory Bootstrap & Verification")
    parser.add_argument(
        "--list-team",
        "--list-roles",
        dest="list_team",
        action="store_true",
        help="List all registered agent profiles",
    )
    parser.add_argument("--list-worktrees", action="store_true", help="List active Git worktrees")
    parser.add_argument(
        "--export-manifests",
        nargs="?",
        const="tools/agent_factory/manifests",
        default="",
        help=(
            "Export agent.md manifests to destination directory "
            "(default: tools/agent_factory/manifests)"
        ),
    )
    parser.add_argument(
        "--emit-runtime-payload",
        type=str,
        default="",
        help="Emit define_subagent JSON payload for specified role",
    )
    args = parser.parse_args(argv)

    if args.emit_runtime_payload:
        profile = load_profile(args.emit_runtime_payload)
        payload = profile.to_define_subagent_payload()
        print(json.dumps(payload, indent=2))  # noqa: T201
        return 0

    if args.list_team or (
        not args.list_worktrees and not args.export_manifests and not args.emit_runtime_payload
    ):
        profiles = load_all_profiles()
        print(f"TapJob Agent Factory Profiles ({len(profiles)} total):")  # noqa: T201
        print("\n--- BASE TEAM (6) ---")  # noqa: T201
        for p in profiles.values():
            if p.category == "base_team":
                write_flag = "WRITE" if p.enable_write_tools else "READ-ONLY"
                sub_flag = "+SUBAGENTS" if p.enable_subagent_tools else ""
                print(f"  • {p.role_id:32} [{write_flag} {sub_flag}] - {p.name}")  # noqa: T201

        print("\n--- DYNAMIC SPECIALISTS (8) ---")  # noqa: T201
        for p in profiles.values():
            if p.category == "dynamic_specialist":
                write_flag = "WRITE" if p.enable_write_tools else "READ-ONLY"
                print(f"  • {p.role_id:32} [{write_flag}] - {p.name}")  # noqa: T201

    if args.list_worktrees:
        dispatcher = WorktreeDispatcher()
        worktrees = dispatcher.list_worktrees()
        print(f"\nActive Git Worktrees ({len(worktrees)} total):")  # noqa: T201
        for w in worktrees:
            clean_str = "CLEAN" if w.is_clean else "DIRTY"
            sha_short = w.head_sha[:8]
            print(f"  • [{clean_str}] {w.branch_name} -> {w.worktree_path} (HEAD: {sha_short})")  # noqa: T201

    if args.export_manifests:
        target = Path(args.export_manifests).resolve()
        target.mkdir(parents=True, exist_ok=True)
        written = export_agent_md_files(target)
        print(f"\nExported {len(written)} agent manifests to {target}")  # noqa: T201

    return 0


if __name__ == "__main__":
    sys.exit(main())

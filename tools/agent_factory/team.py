"""TapJob Agent Factory — Team definitions, profiles registry, and payload generators."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

BASE_TEAM_ROLES = {
    "engineering_manager",
    "core_platform_engineer",
    "channel_whatsapp_engineer",
    "qa_sdet_engineer",
    "reviewer_security_engineer",
    "release_platform_ops_engineer",
}

DYNAMIC_SPECIALIST_ROLES = {
    "mobile_engineer",
    "conversation_ux_designer",
    "database_specialist",
    "ai_matching_specialist",
    "deep_security_specialist",
    "performance_reliability_specialist",
    "accessibility_specialist",
    "product_data_analyst",
}

ALL_ROLES = BASE_TEAM_ROLES | DYNAMIC_SPECIALIST_ROLES


@dataclass(frozen=True)
class AgentProfile:
    """Immutable representation of an Agent Factory role profile."""

    role_id: str
    name: str
    category: str
    description: str
    enable_write_tools: bool
    enable_subagent_tools: bool
    enable_mcp_tools: bool
    ownership_paths: list[str] = field(default_factory=list)
    system_prompt: str = ""

    def validate(self) -> None:
        """Validate profile invariants and schema constraints."""
        if not self.role_id or not self.role_id.strip():
            raise ValueError("Agent profile must have a non-empty role_id")
        if self.role_id not in ALL_ROLES:
            raise ValueError(f"Unrecognized role_id '{self.role_id}'")
        if not self.name or not self.name.strip():
            raise ValueError(f"Agent profile '{self.role_id}' must have a non-empty name")
        if self.category not in {"base_team", "dynamic_specialist"}:
            raise ValueError(f"Invalid category '{self.category}' for role '{self.role_id}'")
        if not self.description or not self.description.strip():
            raise ValueError(f"Agent profile '{self.role_id}' must have a non-empty description")
        if not self.system_prompt or not self.system_prompt.strip():
            raise ValueError(f"Agent profile '{self.role_id}' must have a non-empty system_prompt")

    def to_define_subagent_payload(self) -> dict[str, Any]:
        """Generate the exact arguments dictionary for Antigravity's define_subagent tool."""
        self.validate()
        return {
            "name": self.role_id,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "enable_write_tools": self.enable_write_tools,
            "enable_subagent_tools": self.enable_subagent_tools,
            "enable_mcp_tools": self.enable_mcp_tools,
        }

    def to_agent_md(self) -> str:
        """Generate Antigravity agent.md representation with YAML frontmatter."""
        self.validate()
        tool_list = [
            "send_message",
            "view_file",
            "read_url_content",
            "search_web",
            "schedule",
            "generate_image",
        ]
        if self.enable_write_tools:
            tool_list.extend(
                [
                    "multi_replace_file_content",
                    "replace_file_content",
                    "write_to_file",
                    "run_command",
                    "manage_task",
                ]
            )
        if self.enable_subagent_tools:
            tool_list.extend(["define_subagent", "invoke_subagent", "manage_subagents"])

        tools_yaml = "\n".join(f"    - {t}" for t in tool_list)
        return (
            f"---\n"
            f"name: {self.role_id}\n"
            f"description: {self.description}\n"
            f"tools:\n{tools_yaml}\n"
            f"hidden: true\n"
            f"inheritCustomizations: false\n"
            f"inheritMcp: false\n"
            f"---\n\n"
            f"# Agent System Instructions\n\n"
            f"{self.system_prompt}\n"
        )


def load_profile(role_id: str, profiles_dir: Path | None = None) -> AgentProfile:
    """Load and validate an agent profile by role_id."""
    base_dir = profiles_dir or PROFILES_DIR
    profile_file = base_dir / f"{role_id}.json"
    if not profile_file.exists():
        raise FileNotFoundError(f"Agent profile file not found: {profile_file}")

    data = json.loads(profile_file.read_text(encoding="utf-8"))
    profile = AgentProfile(
        role_id=data["role_id"],
        name=data["name"],
        category=data["category"],
        description=data["description"],
        enable_write_tools=bool(data.get("enable_write_tools", False)),
        enable_subagent_tools=bool(data.get("enable_subagent_tools", False)),
        enable_mcp_tools=bool(data.get("enable_mcp_tools", False)),
        ownership_paths=list(data.get("ownership_paths", [])),
        system_prompt=data["system_prompt"],
    )
    profile.validate()
    return profile


def load_all_profiles(profiles_dir: Path | None = None) -> dict[str, AgentProfile]:
    """Load and validate all 14 base and dynamic agent profiles."""
    profiles: dict[str, AgentProfile] = {}
    for role_id in sorted(ALL_ROLES):
        profiles[role_id] = load_profile(role_id, profiles_dir)
    return profiles


def get_base_team_profiles(profiles_dir: Path | None = None) -> dict[str, AgentProfile]:
    """Return the 6 primary base team profiles."""
    all_profiles = load_all_profiles(profiles_dir)
    return {k: v for k, v in all_profiles.items() if k in BASE_TEAM_ROLES}


def get_dynamic_specialist_profiles(profiles_dir: Path | None = None) -> dict[str, AgentProfile]:
    """Return the 8 on-demand dynamic specialist profiles."""
    all_profiles = load_all_profiles(profiles_dir)
    return {k: v for k, v in all_profiles.items() if k in DYNAMIC_SPECIALIST_ROLES}


def export_agent_md_files(dest_dir: Path, profiles_dir: Path | None = None) -> dict[str, Path]:
    """Write agent.md manifests for all profiles under dest_dir/<role_id>/agent.md."""
    all_profiles = load_all_profiles(profiles_dir)
    written: dict[str, Path] = {}
    for role_id, profile in all_profiles.items():
        role_dir = dest_dir / role_id
        role_dir.mkdir(parents=True, exist_ok=True)
        agent_md_file = role_dir / "agent.md"
        agent_md_file.write_text(profile.to_agent_md(), encoding="utf-8")
        written[role_id] = agent_md_file
    return written

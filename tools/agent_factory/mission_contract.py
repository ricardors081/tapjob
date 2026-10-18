"""TapJob Agent Factory — MissionContract schema and deterministic validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from tools.agent_factory.task_contract import SHA_REGEX, TaskContract
from tools.agent_factory.team import ALL_ROLES

MISSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


class MissionContractError(Exception):
    """Base exception for mission contract validation failures."""


class InvalidMissionContractError(MissionContractError):
    """Raised when a MissionContract fails validation."""


@dataclass
class MissionContract:
    """Deterministic, machine-readable Mission Contract for multi-agent execution."""

    mission_id: str
    canonical_base_sha: str
    objective: str
    tasks: list[TaskContract]
    human_gate_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate all required fields, SHA inheritance, task constraints, and fail closed."""
        if not isinstance(self.mission_id, str) or not self.mission_id.strip():
            raise InvalidMissionContractError(
                "MissionContract requires a non-empty string 'mission_id'."
            )

        if not MISSION_ID_REGEX.match(self.mission_id.strip()):
            raise InvalidMissionContractError(
                f"MissionContract mission_id '{self.mission_id}' must match ^[a-zA-Z0-9_\\-]+$."
            )

        if not isinstance(self.canonical_base_sha, str) or not SHA_REGEX.match(
            self.canonical_base_sha.strip()
        ):
            raise InvalidMissionContractError(
                f"MissionContract '{self.mission_id}' has invalid canonical_base_sha "
                f"'{self.canonical_base_sha}'. Must be an exact 40-character git commit SHA."
            )

        if not isinstance(self.objective, str) or not self.objective.strip():
            raise InvalidMissionContractError(
                f"MissionContract '{self.mission_id}' requires a non-empty string 'objective'."
            )

        if (
            not isinstance(self.human_gate_required, bool)
            or type(self.human_gate_required) is not bool
        ):
            err = (
                f"MissionContract '{self.mission_id}' 'human_gate_required' "
                "must be a strict boolean."
            )
            raise InvalidMissionContractError(err)

        if not isinstance(self.metadata, dict):
            raise InvalidMissionContractError(
                f"MissionContract '{self.mission_id}' 'metadata' must be a dictionary."
            )

        if not isinstance(self.tasks, list) or not self.tasks:
            raise InvalidMissionContractError(
                f"MissionContract '{self.mission_id}' requires a non-empty list of 'tasks'."
            )

        seen_task_ids: set[str] = set()
        seen_target_branches: set[str] = set()

        for idx, task in enumerate(self.tasks):
            if not isinstance(task, TaskContract):
                raise InvalidMissionContractError(
                    f"MissionContract '{self.mission_id}' task [{idx}] must be a TaskContract "
                    f"instance, got {type(task).__name__}."
                )

            # Validate child task
            try:
                task.validate()
            except Exception as exc:
                raise InvalidMissionContractError(
                    f"MissionContract '{self.mission_id}' child task [{idx}] ({task.task_id}) "
                    f"failed validation: {exc}"
                ) from exc

            # Verify child task_id uniqueness
            tid = task.task_id.strip()
            if tid in seen_task_ids:
                raise InvalidMissionContractError(
                    f"MissionContract '{self.mission_id}' contains duplicate task_id '{tid}'."
                )
            seen_task_ids.add(tid)

            # Verify child uses the exact mission canonical_base_sha
            if task.canonical_base_sha != self.canonical_base_sha:
                raise InvalidMissionContractError(
                    f"MissionContract '{self.mission_id}' child task '{tid}' has base SHA "
                    f"'{task.canonical_base_sha}', which does not match mission base SHA "
                    f"'{self.canonical_base_sha}'."
                )

            # Verify owner_role is a valid role
            if task.owner_role not in ALL_ROLES:
                raise InvalidMissionContractError(
                    f"MissionContract '{self.mission_id}' child task '{tid}' has unknown "
                    f"owner_role '{task.owner_role}'."
                )

            # Verify target branch uniqueness
            branch = task.target_branch.strip()
            if branch:
                if branch in seen_target_branches:
                    raise InvalidMissionContractError(
                        f"MissionContract '{self.mission_id}' contains duplicate target_branch "
                        f"'{branch}' across multiple child tasks."
                    )
                seen_target_branches.add(branch)

    def to_dict(self) -> dict[str, Any]:
        """Convert MissionContract to serializable dictionary."""
        self.validate()
        return {
            "mission_id": self.mission_id,
            "canonical_base_sha": self.canonical_base_sha,
            "objective": self.objective,
            "tasks": [t.to_dict() for t in self.tasks],
            "human_gate_required": self.human_gate_required,
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize MissionContract to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionContract:
        """Instantiate and strictly validate MissionContract from dictionary."""
        if not isinstance(data, dict):
            raise InvalidMissionContractError(
                f"MissionContract data must be a dict, got {type(data).__name__}"
            )

        required_fields = ["mission_id", "canonical_base_sha", "objective", "tasks"]
        for f in required_fields:
            if f not in data:
                raise InvalidMissionContractError(f"MissionContract missing required field '{f}'.")

        # Strict bool check for human_gate_required if present
        human_gate = data.get("human_gate_required", True)
        if not isinstance(human_gate, bool) or type(human_gate) is not bool:
            raise InvalidMissionContractError(
                f"MissionContract field 'human_gate_required' must be a strict boolean, "
                f"got {type(human_gate).__name__}"
            )

        tasks_raw = data["tasks"]
        if not isinstance(tasks_raw, list):
            raise InvalidMissionContractError(
                f"MissionContract field 'tasks' must be a list, got {type(tasks_raw).__name__}"
            )

        tasks: list[TaskContract] = []
        for idx, t_data in enumerate(tasks_raw):
            if isinstance(t_data, TaskContract):
                tasks.append(t_data)
            elif isinstance(t_data, dict):
                try:
                    tasks.append(TaskContract.from_dict(t_data))
                except Exception as exc:
                    raise InvalidMissionContractError(
                        f"MissionContract child task [{idx}] failed deserialization: {exc}"
                    ) from exc
            else:
                raise InvalidMissionContractError(
                    f"MissionContract task [{idx}] must be a dict or TaskContract, "
                    f"got {type(t_data).__name__}"
                )

        metadata_val = data.get("metadata", {})
        if not isinstance(metadata_val, dict):
            tname = type(metadata_val).__name__
            raise InvalidMissionContractError(
                f"MissionContract field 'metadata' must be a dict, got {tname}"
            )

        mission = cls(
            mission_id=data["mission_id"],
            canonical_base_sha=data["canonical_base_sha"],
            objective=data["objective"],
            tasks=tasks,
            human_gate_required=human_gate,
            metadata=metadata_val,
        )
        mission.validate()
        return mission

    @classmethod
    def from_json(cls, json_str: str) -> MissionContract:
        """Instantiate and validate MissionContract from JSON string."""
        try:
            data = json.loads(json_str)
        except Exception as exc:
            raise InvalidMissionContractError(
                f"Failed to parse JSON for MissionContract: {exc}"
            ) from exc
        return cls.from_dict(data)

    def to_markdown(self) -> str:
        """Export MissionContract as human- and agent-readable Markdown specification."""
        self.validate()
        task_lines = "\n".join(
            f"- **{t.task_id}** (`{t.owner_role}`, branch: `{t.target_branch}`): {t.outcome}"
            for t in self.tasks
        )
        return f"""# Mission Contract: {self.mission_id}

- **Canonical Base SHA**: `{self.canonical_base_sha}`
- **Human Gate Required**: `{self.human_gate_required}`
- **Total Tasks**: {len(self.tasks)}

## Objective
{self.objective}

## Tasks
{task_lines}
"""

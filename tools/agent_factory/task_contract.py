"""TapJob Agent Factory — TaskContract schema and ownership validation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from tools.agent_factory.team import ALL_ROLES

SHA_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")
VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

# Critical paths that can NEVER be modified concurrently by multiple parallel agents
CRITICAL_SERIAL_PATHS = (
    "services/api/alembic/versions/",
    "services/api/src/tapjob_api/models/",
    "packages/product-config/",
    "packages/design-tokens/",
    ".agents/",
    "tools/agent_guard/",
)


@dataclass
class TaskContract:
    """Deterministic, machine-readable Task Contract for autonomous delegation."""

    task_id: str
    outcome: str
    scope: list[str]
    non_goals: list[str]
    canonical_base_sha: str
    owner_role: str
    allowed_paths: list[str]
    forbidden_paths: list[str]
    dependencies: list[str]
    contracts_frozen: bool
    test_requirements: list[str]
    acceptance_criteria: list[str]
    risk_level: str
    parallel_safe: bool
    human_gate_required: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate all required fields, SHA format, paths, and constraints."""
        if not self.task_id or not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("TaskContract requires a non-empty string 'task_id'.")

        if not self.outcome or not isinstance(self.outcome, str) or not self.outcome.strip():
            raise ValueError(f"TaskContract '{self.task_id}' requires a non-empty 'outcome'.")

        if not isinstance(self.scope, list) or not self.scope:
            raise ValueError(f"TaskContract '{self.task_id}' requires a non-empty 'scope' list.")

        if not isinstance(self.non_goals, list):
            raise ValueError(f"TaskContract '{self.task_id}' 'non_goals' must be a list.")

        if not isinstance(self.canonical_base_sha, str) or not SHA_REGEX.match(
            self.canonical_base_sha.strip()
        ):
            raise ValueError(
                f"TaskContract '{self.task_id}' has invalid 'canonical_base_sha' "
                f"'{self.canonical_base_sha}'. Must be a 40-character git commit SHA."
            )

        if self.owner_role not in ALL_ROLES:
            raise ValueError(
                f"TaskContract '{self.task_id}' has unknown 'owner_role' '{self.owner_role}'."
            )

        if not isinstance(self.allowed_paths, list) or not self.allowed_paths:
            raise ValueError(
                f"TaskContract '{self.task_id}' requires a non-empty list of 'allowed_paths'."
            )

        if not isinstance(self.forbidden_paths, list):
            raise ValueError(f"TaskContract '{self.task_id}' 'forbidden_paths' must be a list.")

        # Check for direct contradiction between allowed and forbidden
        norm_allowed = [self._normalize_path(p) for p in self.allowed_paths]
        norm_forbidden = [self._normalize_path(p) for p in self.forbidden_paths]

        for p_allow in norm_allowed:
            for p_forbid in norm_forbidden:
                if p_allow == p_forbid or p_allow.startswith(p_forbid + "/"):
                    raise ValueError(
                        f"TaskContract '{self.task_id}' contradiction: allowed_path '{p_allow}' "
                        f"is covered by forbidden_path '{p_forbid}'."
                    )

        if not isinstance(self.dependencies, list):
            raise ValueError(f"TaskContract '{self.task_id}' 'dependencies' must be a list.")

        if not isinstance(self.contracts_frozen, bool):
            raise ValueError(f"TaskContract '{self.task_id}' 'contracts_frozen' must be a boolean.")

        if not isinstance(self.test_requirements, list) or not self.test_requirements:
            raise ValueError(
                f"TaskContract '{self.task_id}' requires non-empty 'test_requirements'."
            )

        if not isinstance(self.acceptance_criteria, list) or not self.acceptance_criteria:
            raise ValueError(
                f"TaskContract '{self.task_id}' requires non-empty 'acceptance_criteria'."
            )

        if self.risk_level.upper() not in VALID_RISK_LEVELS:
            raise ValueError(
                f"TaskContract '{self.task_id}' has invalid risk_level '{self.risk_level}'. "
                f"Must be one of {sorted(VALID_RISK_LEVELS)}."
            )

        if not isinstance(self.parallel_safe, bool):
            raise ValueError(f"TaskContract '{self.task_id}' 'parallel_safe' must be a boolean.")

        if not isinstance(self.human_gate_required, bool):
            raise ValueError(
                f"TaskContract '{self.task_id}' 'human_gate_required' must be a boolean."
            )

    @staticmethod
    def _normalize_path(path_str: str) -> str:
        """Normalize a path to posix format without redundant slashes or relative components."""
        clean = path_str.replace("\\", "/").strip().rstrip("/")
        # Remove leading ./
        if clean.startswith("./"):
            clean = clean[2:]
        return str(PurePosixPath(clean))

    def touches_critical_serial_paths(self) -> list[str]:
        """Return any critical shared paths touched by this task."""
        violations: list[str] = []
        for path in self.allowed_paths:
            norm = self._normalize_path(path)
            for crit in CRITICAL_SERIAL_PATHS:
                crit_norm = self._normalize_path(crit)
                if (
                    norm == crit_norm
                    or norm.startswith(crit_norm + "/")
                    or crit_norm.startswith(norm + "/")
                ):
                    violations.append(f"allowed_path '{path}' overlaps critical path '{crit}'")
        return violations

    def check_ownership_conflict(self, other: TaskContract) -> list[str]:
        """Detect any ownership overlaps or concurrency conflicts between two tasks."""
        conflicts: list[str] = []
        self_paths = [self._normalize_path(p) for p in self.allowed_paths]
        other_paths = [self._normalize_path(p) for p in other.allowed_paths]

        for sp in self_paths:
            for op in other_paths:
                if sp == op:
                    conflicts.append(f"Direct path collision: both tasks claim '{sp}'")
                elif sp.startswith(op + "/"):
                    conflicts.append(
                        f"Nested path collision: '{sp}' (task {self.task_id}) inside "
                        f"'{op}' (task {other.task_id})"
                    )
                elif op.startswith(sp + "/"):
                    conflicts.append(
                        f"Nested path collision: '{op}' (task {other.task_id}) inside "
                        f"'{sp}' (task {self.task_id})"
                    )

        # Prohibit parallel dispatch if either touches critical serial paths
        self_crit = self.touches_critical_serial_paths()
        other_crit = other.touches_critical_serial_paths()
        if self_crit and other_crit:
            conflicts.append(
                "Both tasks touch critical serial paths that cannot be edited concurrently: "
                f"{self_crit} vs {other_crit}"
            )

        return conflicts

    def to_dict(self) -> dict[str, Any]:
        """Convert TaskContract to serializable dictionary."""
        self.validate()
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialize TaskContract to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        """Instantiate and validate TaskContract from dictionary."""
        contract = cls(
            task_id=data["task_id"],
            outcome=data["outcome"],
            scope=list(data["scope"]),
            non_goals=list(data.get("non_goals", [])),
            canonical_base_sha=data["canonical_base_sha"],
            owner_role=data["owner_role"],
            allowed_paths=list(data["allowed_paths"]),
            forbidden_paths=list(data.get("forbidden_paths", [])),
            dependencies=list(data.get("dependencies", [])),
            contracts_frozen=bool(data["contracts_frozen"]),
            test_requirements=list(data["test_requirements"]),
            acceptance_criteria=list(data["acceptance_criteria"]),
            risk_level=data["risk_level"],
            parallel_safe=bool(data["parallel_safe"]),
            human_gate_required=bool(data["human_gate_required"]),
            metadata=dict(data.get("metadata", {})),
        )
        contract.validate()
        return contract

    @classmethod
    def from_json(cls, json_str: str) -> TaskContract:
        """Instantiate and validate TaskContract from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def to_markdown(self) -> str:
        """Export TaskContract as human- and agent-readable Markdown specification."""
        self.validate()
        scope_md = "\n".join(f"- {s}" for s in self.scope)
        non_goals_list = self.non_goals if self.non_goals else ["None"]
        non_goals_md = "\n".join(f"- {ng}" for ng in non_goals_list)
        allowed_md = "\n".join(f"- `{p}`" for p in self.allowed_paths)
        if self.forbidden_paths:
            forbidden_md = "\n".join(f"- `{p}`" for p in self.forbidden_paths)
        else:
            forbidden_md = "- None"
        deps_md = ", ".join(self.dependencies) if self.dependencies else "None"
        tests_md = "\n".join(f"- {t}" for t in self.test_requirements)
        criteria_md = "\n".join(f"- {c}" for c in self.acceptance_criteria)

        return f"""# Task Contract: {self.task_id}

- **Owner Role**: `{self.owner_role}`
- **Canonical Base SHA**: `{self.canonical_base_sha}`
- **Risk Level**: `{self.risk_level}`
- **Parallel Safe**: `{self.parallel_safe}`
- **Contracts Frozen**: `{self.contracts_frozen}`
- **Human Gate Required**: `{self.human_gate_required}`
- **Dependencies**: {deps_md}

## Outcome
{self.outcome}

## Scope
{scope_md}

## Non-Goals
{non_goals_md}

## Allowed Paths
{allowed_md}

## Forbidden Paths
{forbidden_md}

## Test Requirements
{tests_md}

## Acceptance Criteria
{criteria_md}
"""

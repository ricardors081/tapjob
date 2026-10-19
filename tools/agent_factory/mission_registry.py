"""TapJob Agent Factory — Persistent, cross-process Mission Registry with fail-closed locking."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from tools.agent_factory.lock import DirectoryLock
from tools.agent_factory.repair_loop import ReviewVerdict
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.team import ALL_ROLES

MISSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")
SHA_40_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")


class MissionCoordinatorError(Exception):
    """Base error for MissionCoordinator failures."""


class MissionRegistryError(Exception):
    """Base exception for mission registry operations."""


class DuplicateMissionIdError(MissionRegistryError):
    """Raised when attempting to create a mission whose mission_id already exists."""


class CorruptMissionRegistryError(MissionRegistryError):
    """Raised when the mission registry file is corrupted or unparseable."""


class MissionNotFoundError(MissionRegistryError):
    """Raised when a requested mission is not present in the registry."""


class MissionLockTimeoutError(MissionRegistryError):
    """Raised when acquiring the persistent mission lock times out."""


class InvalidStateTransitionError(MissionRegistryError, MissionCoordinatorError):
    """Raised when an atomic state transition is attempted from an unexpected or stale state."""


class MissionState(StrEnum):
    """Explicit state of an entire mission."""

    PLANNED = "planned"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    READY_FOR_CONSOLIDATION = "ready_for_consolidation"
    CONSOLIDATED = "consolidated"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskMissionState(StrEnum):
    """Explicit lifecycle state of a child task within a mission."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    IMPLEMENTED = "implemented"
    REVIEW_REQUIRED = "review_required"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    SCOPE_VERIFIED = "scope_verified"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class MissionRegistryRecord:
    """Persistent representation of a mission execution lifecycle."""

    mission_id: str
    base_sha: str
    objective: str
    human_gate_required: bool
    state: MissionState
    task_ids: list[str]
    execution_groups: list[list[str]]
    task_states: dict[str, TaskMissionState]
    task_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Strict validation of all fields and invariants."""
        # 1. mission_id validation
        if not isinstance(self.mission_id, str) or not self.mission_id.strip():
            raise CorruptMissionRegistryError(
                "MissionRegistryRecord requires non-empty mission_id."
            )
        if not MISSION_ID_REGEX.match(self.mission_id.strip()):
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' mission_id "
                f"must match {MISSION_ID_REGEX.pattern}."
            )
            raise CorruptMissionRegistryError(msg)

        # 2. base_sha validation
        if not isinstance(self.base_sha, str) or not SHA_40_REGEX.match(self.base_sha.strip()):
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' has invalid base_sha '{self.base_sha}'."
            )

        # 3. objective validation
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' requires non-empty objective."
            )

        # 4. human_gate_required MUST be strict True
        if (
            not isinstance(self.human_gate_required, bool)
            or type(self.human_gate_required) is not bool
            or self.human_gate_required is not True
        ):
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' human_gate_required "
                "must be strictly True."
            )
            raise CorruptMissionRegistryError(msg)

        # 5. state validation
        if not isinstance(self.state, MissionState):
            try:
                self.state = MissionState(self.state)
            except ValueError as exc:
                raise CorruptMissionRegistryError(
                    f"MissionRegistryRecord '{self.mission_id}' has invalid state '{self.state}'."
                ) from exc

        # 6. task_ids validation
        if type(self.task_ids) is not list or not self.task_ids:
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' requires non-empty task_ids list."
            )
        seen_tids: set[str] = set()
        for idx, tid in enumerate(self.task_ids):
            if not isinstance(tid, str) or not tid.strip():
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task_ids[{idx}] "
                    "must be a non-empty string."
                )
                raise CorruptMissionRegistryError(msg)
            if tid in seen_tids:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' contains duplicate "
                    f"task_id '{tid}' in task_ids."
                )
                raise CorruptMissionRegistryError(msg)
            seen_tids.add(tid)

        # 7. execution_groups validation
        if type(self.execution_groups) is not list or not self.execution_groups:
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' execution_groups "
                "must be a non-empty list."
            )
            raise CorruptMissionRegistryError(msg)
        flattened_group_tasks: list[str] = []
        for g_idx, grp in enumerate(self.execution_groups):
            if type(grp) is not list or not grp:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' "
                    f"execution_groups[{g_idx}] must be a non-empty list."
                )
                raise CorruptMissionRegistryError(msg)
            for t in grp:
                if not isinstance(t, str) or not t.strip():
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' "
                        f"execution_groups[{g_idx}] contains invalid task '{t}'."
                    )
                    raise CorruptMissionRegistryError(msg)
                if t not in seen_tids:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' "
                        f"execution_groups[{g_idx}] contains unregistered task '{t}'."
                    )
                    raise CorruptMissionRegistryError(msg)
                flattened_group_tasks.append(t)

        if len(flattened_group_tasks) != len(set(flattened_group_tasks)):
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' contains duplicate "
                "tasks across execution groups."
            )
            raise CorruptMissionRegistryError(msg)
        if set(flattened_group_tasks) != seen_tids:
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' execution groups "
                "must cover all task_ids exactly once."
            )
            raise CorruptMissionRegistryError(msg)

        # 8. task_states validation
        if type(self.task_states) is not dict:
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' task_states must be a dict."
            )
        if set(self.task_states.keys()) != seen_tids:
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' task_states keys "
                "must exactly match task_ids."
            )
            raise CorruptMissionRegistryError(msg)
        for tid, tstate in self.task_states.items():
            if not isinstance(tstate, TaskMissionState):
                try:
                    self.task_states[tid] = TaskMissionState(tstate)
                except ValueError as exc:
                    err_msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"has invalid state '{tstate}'."
                    )
                    raise CorruptMissionRegistryError(err_msg) from exc

        # 9. task_records validation
        if type(self.task_records) is not dict:
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' task_records must be a dict."
            )
        if set(self.task_records.keys()) != seen_tids:
            msg = (
                f"MissionRegistryRecord '{self.mission_id}' task_records keys "
                "must exactly match task_ids."
            )
            raise CorruptMissionRegistryError(msg)

        for tid, trec in self.task_records.items():
            if type(trec) is not dict:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' "
                    f"task_records['{tid}'] must be a dict."
                )
                raise CorruptMissionRegistryError(msg)

            # Validate nested TaskContract
            cdict = trec.get("contract")
            if type(cdict) is not dict or not cdict:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    "missing or empty contract dict."
                )
                raise CorruptMissionRegistryError(msg)
            try:
                contract = TaskContract.from_dict(cdict)
                contract.validate()
            except Exception as exc:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"contract is invalid: {exc}"
                )
                raise CorruptMissionRegistryError(msg) from exc

            if contract.task_id != tid:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"contract task_id mismatch: '{contract.task_id}'."
                )
                raise CorruptMissionRegistryError(msg)
            if contract.canonical_base_sha != self.base_sha:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"contract base SHA mismatch: '{contract.canonical_base_sha}'."
                )
                raise CorruptMissionRegistryError(msg)

            # Consistency checks
            owner_role = trec.get("owner_role")
            if owner_role != contract.owner_role or owner_role not in ALL_ROLES:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"owner_role mismatch or invalid: '{owner_role}'."
                )
                raise CorruptMissionRegistryError(msg)

            target_branch = trec.get("target_branch")
            if target_branch != contract.target_branch:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"target_branch mismatch: '{target_branch}'."
                )
                raise CorruptMissionRegistryError(msg)

            # Repair records validation
            repair_records = trec.get("repair_records", [])
            if type(repair_records) is not list:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    "repair_records must be a list."
                )
                raise CorruptMissionRegistryError(msg)

            repair_count = trec.get("repair_count", 0)
            if type(repair_count) is not int or repair_count < 0:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    "repair_count must be a non-negative int."
                )
                raise CorruptMissionRegistryError(msg)

            if repair_count != len(repair_records):
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    f"repair_count ({repair_count}) does not match "
                    f"len(repair_records) ({len(repair_records)})."
                )
                raise CorruptMissionRegistryError(msg)

            for idx, r in enumerate(repair_records):
                if type(r) is not dict:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] must be a dict."
                    )
                    raise CorruptMissionRegistryError(msg)
                it_idx = r.get("iteration_index")
                if type(it_idx) is not int or it_idx != idx:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] iteration_index ({it_idx}) must equal {idx}."
                    )
                    raise CorruptMissionRegistryError(msg)
                impl_role = r.get("implementer_role")
                if impl_role != owner_role:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] implementer_role ('{impl_role}') must equal "
                        f"owner_role ('{owner_role}')."
                    )
                    raise CorruptMissionRegistryError(msg)
                rev_role = r.get("reviewer_role")
                if (
                    not isinstance(rev_role, str)
                    or rev_role not in ALL_ROLES
                    or rev_role == owner_role
                ):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] reviewer_role ('{rev_role}') must be valid role "
                        f"and != owner_role."
                    )
                    raise CorruptMissionRegistryError(msg)
                tp = r.get("test_passed")
                if type(tp) is not bool:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] test_passed must be strict bool."
                    )
                    raise CorruptMissionRegistryError(msg)
                verd = r.get("verdict")
                if verd not in (ReviewVerdict.APPROVE.value, ReviewVerdict.CHANGES_REQUIRED.value):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] verdict ('{verd}') invalid."
                    )
                    raise CorruptMissionRegistryError(msg)
                findings = r.get("findings", [])
                if type(findings) is not list or not all(isinstance(f, str) for f in findings):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] findings must be list of strings."
                    )
                    raise CorruptMissionRegistryError(msg)
                it_sha = r.get("iteration_head_sha")
                if type(it_sha) is not str or not SHA_40_REGEX.match(it_sha):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"repair_records[{idx}] iteration_head_sha ('{it_sha}') "
                        f"must be a 40-character SHA."
                    )
                    raise CorruptMissionRegistryError(msg)

            # Semantic binding with task_states
            t_state = self.task_states[tid]
            if t_state == TaskMissionState.CHANGES_REQUESTED:
                if (
                    not repair_records
                    or repair_records[-1].get("verdict") != ReviewVerdict.CHANGES_REQUIRED.value
                ):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' in "
                        f"CHANGES_REQUESTED must have last repair_record verdict CHANGES_REQUIRED."
                    )
                    raise CorruptMissionRegistryError(msg)
            elif t_state in (TaskMissionState.APPROVED, TaskMissionState.SCOPE_VERIFIED):
                if (
                    not repair_records
                    or repair_records[-1].get("verdict") != ReviewVerdict.APPROVE.value
                    or repair_records[-1].get("test_passed") is not True
                ):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' in "
                        f"{t_state.value} must have last repair_record verdict APPROVE and "
                        f"test_passed=True."
                    )
                    raise CorruptMissionRegistryError(msg)
                reviewed_head = trec.get("reviewed_head_sha")
                last_it_sha = repair_records[-1].get("iteration_head_sha")
                if not reviewed_head or last_it_sha != reviewed_head:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' in "
                        f"{t_state.value} last repair_record iteration_head_sha "
                        f"('{last_it_sha}') must equal reviewed_head_sha ('{reviewed_head}')."
                    )
                    raise CorruptMissionRegistryError(msg)
                impl_head = trec.get("implemented_head_sha")
                if not impl_head or reviewed_head != impl_head:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' in "
                        f"{t_state.value} reviewed_head_sha ('{reviewed_head}') must equal "
                        f"implemented_head_sha ('{impl_head}')."
                    )
                    raise CorruptMissionRegistryError(msg)

            # Review fields validation if present (for top-level mirrors)
            if "reviewer_role" in trec:
                rev = trec["reviewer_role"]
                if not isinstance(rev, str) or rev not in ALL_ROLES or rev == owner_role:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"has invalid reviewer_role '{rev}'."
                    )
                    raise CorruptMissionRegistryError(msg)
            if "review_verdict" in trec:
                verd = trec["review_verdict"]
                if verd not in (ReviewVerdict.APPROVE.value, ReviewVerdict.CHANGES_REQUIRED.value):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"has invalid review_verdict '{verd}'."
                    )
                    raise CorruptMissionRegistryError(msg)
            if "test_passed" in trec:
                tp = trec["test_passed"]
                if type(tp) is not bool:
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        "test_passed must be a strict bool."
                    )
                    raise CorruptMissionRegistryError(msg)
            if "findings" in trec:
                findings = trec["findings"]
                if type(findings) is not list or not all(isinstance(f, str) for f in findings):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        "findings must be a list of strings."
                    )
                    raise CorruptMissionRegistryError(msg)

            # Scope verification fields validation
            if self.task_states[tid] == TaskMissionState.SCOPE_VERIFIED:
                verified_head = trec.get("scope_verified_head_sha")
                if not isinstance(verified_head, str) or not SHA_40_REGEX.match(verified_head):
                    msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"in SCOPE_VERIFIED lacks valid SHA-40: '{verified_head}'."
                    )
                    raise CorruptMissionRegistryError(msg)

        # 10. metadata validation
        if type(self.metadata) is not dict:
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' metadata must be a dict."
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "mission_id": self.mission_id,
            "base_sha": self.base_sha,
            "objective": self.objective,
            "human_gate_required": True,
            "state": self.state.value,
            "task_ids": list(self.task_ids),
            "execution_groups": [list(g) for g in self.execution_groups],
            "task_states": {k: v.value for k, v in self.task_states.items()},
            "task_records": self.task_records,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionRegistryRecord:
        if type(data) is not dict:
            raise CorruptMissionRegistryError(
                f"Registry record must be a dict, got {type(data).__name__}"
            )

        required = [
            "mission_id",
            "base_sha",
            "objective",
            "human_gate_required",
            "state",
            "task_ids",
            "execution_groups",
            "task_states",
            "task_records",
        ]
        for req in required:
            if req not in data:
                raise CorruptMissionRegistryError(f"Registry record missing required key '{req}'.")

        # human_gate_required must be strictly True
        hg = data["human_gate_required"]
        if type(hg) is not bool or hg is not True:
            raise CorruptMissionRegistryError(
                "Registry record human_gate_required must be strictly True."
            )

        try:
            state = MissionState(data["state"])
        except ValueError as exc:
            raise CorruptMissionRegistryError(f"Invalid mission state: {data['state']}") from exc

        # task_ids: strict list[str]
        raw_tids = data["task_ids"]
        if type(raw_tids) is not list or not all(isinstance(x, str) for x in raw_tids):
            raise CorruptMissionRegistryError("task_ids must be a list of strings.")

        # execution_groups: strict list[list[str]]
        raw_egroups = data["execution_groups"]
        if type(raw_egroups) is not list:
            raise CorruptMissionRegistryError("execution_groups must be a list.")
        for g in raw_egroups:
            if type(g) is not list or not all(isinstance(x, str) for x in g):
                raise CorruptMissionRegistryError(
                    "execution_groups must be a list of lists of strings."
                )

        raw_states = data["task_states"]
        if type(raw_states) is not dict:
            raise CorruptMissionRegistryError("task_states must be a dict.")

        parsed_states: dict[str, TaskMissionState] = {}
        for k, v in raw_states.items():
            try:
                parsed_states[k] = TaskMissionState(v)
            except ValueError as exc:
                raise CorruptMissionRegistryError(
                    f"Invalid task state '{v}' for task '{k}'."
                ) from exc

        raw_trecs = data.get("task_records", {})
        if type(raw_trecs) is not dict:
            raise CorruptMissionRegistryError("task_records must be a dict.")

        record = cls(
            mission_id=data["mission_id"],
            base_sha=data["base_sha"],
            objective=data["objective"],
            human_gate_required=True,
            state=state,
            task_ids=list(raw_tids),
            execution_groups=[list(g) for g in raw_egroups],
            task_states=parsed_states,
            task_records=raw_trecs,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            metadata=data.get("metadata", {}),
        )
        record.validate()
        return record


class MissionRegistry:
    """Persistent, thread-safe, and process-safe Mission Registry."""

    def __init__(self, worktrees_dir: Path | str) -> None:
        self.worktrees_dir = Path(worktrees_dir).resolve()
        self.registry_dir = (self.worktrees_dir / ".tapjob-agent-factory").resolve()
        self.missions_dir = (self.registry_dir / "missions").resolve()
        self.lock_path = (self.registry_dir / ".mission_lock").resolve()
        self.missions_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self, timeout_seconds: float = 10.0) -> Iterator[None]:
        """Cross-platform directory-based file lock ensuring single-writer mission mutations."""
        lock = DirectoryLock(
            lock_path=self.lock_path,
            timeout_seconds=timeout_seconds,
            timeout_error_cls=MissionLockTimeoutError,
            corrupt_error_cls=CorruptMissionRegistryError,
        )
        with lock.acquire(timeout_seconds=timeout_seconds):
            yield

    def _mission_file(self, mission_id: str) -> Path:
        clean = mission_id.strip()
        if not MISSION_ID_REGEX.match(clean) or ".." in clean or "/" in clean or "\\" in clean:
            raise MissionRegistryError(f"Illegal mission_id path traversal: '{mission_id}'")
        return self.missions_dir / f"{clean}.json"

    def create_mission(self, record: MissionRegistryRecord) -> None:
        """Atomically create a new mission record.

        Fails closed with DuplicateMissionIdError if it already exists.
        """
        record.validate()
        now_iso = datetime.now(UTC).isoformat()
        if not record.created_at:
            record.created_at = now_iso
        record.updated_at = now_iso

        with self._lock():
            target_file = self._mission_file(record.mission_id)
            if target_file.exists():
                raise DuplicateMissionIdError(
                    f"Mission '{record.mission_id}' already exists in registry at '{target_file}'."
                )
            tmp_file = (
                self.missions_dir / f"{record.mission_id}.json.tmp.{os.getpid()}.{time.time_ns()}"
            )
            tmp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            tmp_file.replace(target_file)

    def save_mission(self, record: MissionRegistryRecord) -> None:
        """Alias for create_mission. Explicitly prohibits replacing an existing mission record."""
        self.create_mission(record)

    def get_mission(self, mission_id: str) -> MissionRegistryRecord:
        """Read and validate a MissionRegistryRecord. Fails closed on corruption."""
        target_file = self._mission_file(mission_id)
        if not target_file.exists():
            raise MissionNotFoundError(
                f"Mission '{mission_id}' not found in registry at '{target_file}'."
            )

        try:
            raw = target_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            raise CorruptMissionRegistryError(
                f"Failed to parse mission file '{target_file}': {exc}"
            ) from exc

        return MissionRegistryRecord.from_dict(data)

    def transition_mission_state(
        self,
        mission_id: str,
        expected_states: Sequence[MissionState] | set[MissionState],
        new_state: MissionState,
    ) -> MissionRegistryRecord:
        """Atomically validate expected mission state and transition to new_state under lock.

        Fails closed with InvalidStateTransitionError if current state is not in expected_states.
        """
        with self._lock():
            record = self.get_mission(mission_id)
            curr = record.state
            expected_set = set(expected_states)
            if curr not in expected_set:
                msg = (
                    f"Mission '{mission_id}' invalid state transition: "
                    f"current state '{curr.value}' is not in expected states "
                    f"{[s.value for s in expected_set]}. Cannot transition to '{new_state.value}'."
                )
                raise InvalidStateTransitionError(msg)
            record.state = new_state
            record.updated_at = datetime.now(UTC).isoformat()
            record.validate()
            target_file = self._mission_file(mission_id)
            tmp_file = self.missions_dir / f"{mission_id}.json.tmp.{os.getpid()}.{time.time_ns()}"
            tmp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            tmp_file.replace(target_file)
            return record

    def transition_task_state(
        self,
        mission_id: str,
        task_id: str,
        expected_states: Sequence[TaskMissionState] | set[TaskMissionState],
        new_state: TaskMissionState,
        details: dict[str, Any] | None = None,
    ) -> MissionRegistryRecord:
        """Atomically validate expected task state and transition to new_state under lock.

        Fails closed with InvalidStateTransitionError if current state is not in expected_states.
        """
        with self._lock():
            record = self.get_mission(mission_id)
            if task_id not in record.task_states:
                raise MissionRegistryError(
                    f"Task '{task_id}' is not registered under mission '{mission_id}'."
                )
            curr = record.task_states[task_id]
            expected_set = set(expected_states)
            if curr not in expected_set:
                msg = (
                    f"Task '{task_id}' invalid state transition: current state '{curr.value}' "
                    f"is not in expected states {[s.value for s in expected_set]}. "
                    f"Cannot transition to '{new_state.value}'."
                )
                raise InvalidStateTransitionError(msg)
            record.task_states[task_id] = new_state
            if details:
                if task_id not in record.task_records:
                    record.task_records[task_id] = {}
                record.task_records[task_id].update(details)
            record.updated_at = datetime.now(UTC).isoformat()
            record.validate()
            target_file = self._mission_file(mission_id)
            tmp_file = self.missions_dir / f"{mission_id}.json.tmp.{os.getpid()}.{time.time_ns()}"
            tmp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            tmp_file.replace(target_file)
            return record

    def list_missions(self) -> list[MissionRegistryRecord]:
        """List all valid missions currently stored in the registry."""
        missions: list[MissionRegistryRecord] = []
        if not self.missions_dir.exists():
            return missions

        for p in sorted(self.missions_dir.glob("*.json")):
            if p.name.endswith(".tmp") or ".tmp." in p.name:
                continue
            missions.append(self.get_mission(p.stem))
        return missions

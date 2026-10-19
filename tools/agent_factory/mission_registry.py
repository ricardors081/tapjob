"""TapJob Agent Factory — Persistent, cross-process Mission Registry with fail-closed locking."""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from tools.agent_factory.repair_loop import ReviewVerdict
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.team import ALL_ROLES
from tools.agent_factory.worktree_dispatcher import _is_pid_alive

MISSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")
SHA_40_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")


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

            repair_count = trec.get("repair_count", 0)
            if type(repair_count) is not int or repair_count < 0:
                msg = (
                    f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                    "repair_count must be a non-negative int."
                )
                raise CorruptMissionRegistryError(msg)

            # Review fields validation if present
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

        record = cls(
            mission_id=data["mission_id"],
            base_sha=data["base_sha"],
            objective=data["objective"],
            human_gate_required=True,
            state=state,
            task_ids=list(data["task_ids"]) if type(data["task_ids"]) is list else [],
            execution_groups=[list(g) for g in data["execution_groups"]]
            if type(data["execution_groups"]) is list
            else [],
            task_states=parsed_states,
            task_records=data.get("task_records", {}),
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
        """Cross-platform directory-based file lock ensuring single-writer mission mutations.

        Safety invariants:
        - Fails closed on malformed owner metadata (no blind suppression).
        - Dead-process locks are deterministically stolen after liveness verification.
        - Foreign locks can never be removed by non-owners.
        """
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        owner_file = self.lock_path / "owner.json"
        start_time = time.time()
        acquired = False
        while time.time() - start_time < timeout_seconds:
            try:
                self.lock_path.mkdir(parents=False, exist_ok=False)
                owner_data = {
                    "pid": os.getpid(),
                    "token": token,
                    "created_at": time.time(),
                }
                owner_file.write_text(json.dumps(owner_data), encoding="utf-8")
                acquired = True
                break
            except FileExistsError:
                if owner_file.exists():
                    try:
                        owner_raw = owner_file.read_text(encoding="utf-8")
                        owner_data = json.loads(owner_raw)
                    except Exception as exc:
                        raise CorruptMissionRegistryError(
                            f"Corrupt lock owner metadata at '{owner_file}': {exc}"
                        ) from exc

                    if type(owner_data) is not dict:
                        raise CorruptMissionRegistryError(
                            f"Lock owner metadata at '{owner_file}' must be a JSON object."
                        ) from None

                    owner_pid = owner_data.get("pid")
                    if type(owner_pid) is not int or owner_pid <= 0:
                        raise CorruptMissionRegistryError(
                            f"Malformed PID in lock owner metadata: {owner_pid!r}"
                        ) from None

                    if not _is_pid_alive(owner_pid):
                        # Proven dead owner process: safely reclaim lock
                        owner_file.unlink(missing_ok=True)
                        with contextlib.suppress(OSError):
                            self.lock_path.rmdir()
                        continue
                time.sleep(0.05)

        if not acquired:
            msg = (
                f"Timed out acquiring mission lock at '{self.lock_path}' after {timeout_seconds}s."
            )
            raise MissionLockTimeoutError(msg)

        try:
            yield
        finally:
            if acquired and owner_file.exists():
                try:
                    cur_raw = owner_file.read_text(encoding="utf-8")
                    cur_data = json.loads(cur_raw)
                except Exception as exc:
                    raise MissionRegistryError(
                        f"Failed to read lock owner file on release: {exc}"
                    ) from exc

                if type(cur_data) is dict and cur_data.get("token") == token:
                    owner_file.unlink(missing_ok=True)
                    try:
                        self.lock_path.rmdir()
                    except OSError as exc:
                        raise MissionRegistryError(
                            f"Failed to remove lock dir '{self.lock_path}': {exc}"
                        ) from exc

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

    def update_mission_state(
        self, mission_id: str, new_state: MissionState
    ) -> MissionRegistryRecord:
        """Atomically update the overall state of a mission."""
        with self._lock():
            record = self.get_mission(mission_id)
            record.state = new_state
            record.updated_at = datetime.now(UTC).isoformat()
            target_file = self._mission_file(mission_id)
            tmp_file = self.missions_dir / f"{mission_id}.json.tmp.{os.getpid()}.{time.time_ns()}"
            tmp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            tmp_file.replace(target_file)
            return record

    def update_task_state(
        self,
        mission_id: str,
        task_id: str,
        task_state: TaskMissionState,
        details: dict[str, Any] | None = None,
    ) -> MissionRegistryRecord:
        """Atomically update a child task's state and record details."""
        with self._lock():
            record = self.get_mission(mission_id)
            if task_id not in record.task_states:
                raise MissionRegistryError(
                    f"Task '{task_id}' is not registered under mission '{mission_id}'."
                )
            record.task_states[task_id] = task_state
            if details:
                if task_id not in record.task_records:
                    record.task_records[task_id] = {}
                record.task_records[task_id].update(details)
            record.updated_at = datetime.now(UTC).isoformat()
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

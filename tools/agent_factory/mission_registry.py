"""TapJob Agent Factory — Persistent, cross-process Mission Registry with fail-closed locking."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from tools.agent_factory.task_contract import SHA_REGEX


class MissionRegistryError(Exception):
    """Base exception for mission registry operations."""


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
        if not isinstance(self.mission_id, str) or not self.mission_id.strip():
            raise CorruptMissionRegistryError(
                "MissionRegistryRecord requires non-empty mission_id."
            )

        if not isinstance(self.base_sha, str) or not SHA_REGEX.match(self.base_sha.strip()):
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' has invalid base_sha '{self.base_sha}'."
            )

        if not isinstance(self.state, MissionState):
            try:
                self.state = MissionState(self.state)
            except ValueError as exc:
                raise CorruptMissionRegistryError(
                    f"MissionRegistryRecord '{self.mission_id}' has invalid state '{self.state}'."
                ) from exc

        if not isinstance(self.task_ids, list) or not self.task_ids:
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' requires non-empty task_ids list."
            )

        if not isinstance(self.execution_groups, list):
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' execution_groups must be a list."
            )

        if not isinstance(self.task_states, dict):
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' task_states must be a dict."
            )

        for tid in self.task_ids:
            if tid not in self.task_states:
                raise CorruptMissionRegistryError(
                    f"MissionRegistryRecord '{self.mission_id}' missing state for task '{tid}'."
                )
            tstate = self.task_states[tid]
            if not isinstance(tstate, TaskMissionState):
                try:
                    self.task_states[tid] = TaskMissionState(tstate)
                except ValueError as exc:
                    err_msg = (
                        f"MissionRegistryRecord '{self.mission_id}' task '{tid}' "
                        f"has invalid state '{tstate}'."
                    )
                    raise CorruptMissionRegistryError(err_msg) from exc

        if not isinstance(self.task_records, dict):
            raise CorruptMissionRegistryError(
                f"MissionRegistryRecord '{self.mission_id}' task_records must be a dict."
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "mission_id": self.mission_id,
            "base_sha": self.base_sha,
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
        if not isinstance(data, dict):
            raise CorruptMissionRegistryError(
                f"Registry record must be a dict, got {type(data).__name__}"
            )

        required = [
            "mission_id",
            "base_sha",
            "state",
            "task_ids",
            "execution_groups",
            "task_states",
        ]
        for req in required:
            if req not in data:
                raise CorruptMissionRegistryError(f"Registry record missing required key '{req}'.")

        try:
            state = MissionState(data["state"])
        except ValueError as exc:
            raise CorruptMissionRegistryError(f"Invalid mission state: {data['state']}") from exc

        raw_states = data["task_states"]
        if not isinstance(raw_states, dict):
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
            state=state,
            task_ids=list(data["task_ids"]),
            execution_groups=[list(g) for g in data["execution_groups"]],
            task_states=parsed_states,
            task_records=data.get("task_records", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            metadata=data.get("metadata", {}),
        )
        record.validate()
        return record


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform check whether process PID is currently alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            process_query = 0x1000
            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(process_query | synchronize, False, pid)
            if handle == 0:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == 259
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as err:
            import errno

            return err.errno != errno.ESRCH


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
                    with contextlib.suppress(Exception):
                        owner_data = json.loads(owner_file.read_text(encoding="utf-8"))
                        owner_pid = owner_data.get("pid")
                        if isinstance(owner_pid, int) and not _is_pid_alive(owner_pid):
                            owner_file.unlink(missing_ok=True)
                            with contextlib.suppress(Exception):
                                self.lock_path.rmdir()
                            continue
                time.sleep(0.05)

        if not acquired:
            raise MissionLockTimeoutError(
                f"Timed out acquiring mission lock at '{self.lock_path}' after {timeout_seconds}s."
            )

        try:
            yield
        finally:
            if acquired:
                with contextlib.suppress(Exception):
                    if owner_file.exists():
                        cur_data = json.loads(owner_file.read_text(encoding="utf-8"))
                        if cur_data.get("token") == token:
                            owner_file.unlink(missing_ok=True)
                            self.lock_path.rmdir()

    def _mission_file(self, mission_id: str) -> Path:
        clean = mission_id.strip()
        if ".." in clean or "/" in clean or "\\" in clean:
            raise MissionRegistryError(f"Illegal mission_id path traversal: '{mission_id}'")
        return self.missions_dir / f"{clean}.json"

    def save_mission(self, record: MissionRegistryRecord) -> None:
        """Atomically persist a MissionRegistryRecord under the global mutex."""
        record.validate()
        now_iso = datetime.now(UTC).isoformat()
        if not record.created_at:
            record.created_at = now_iso
        record.updated_at = now_iso

        with self._lock():
            target_file = self._mission_file(record.mission_id)
            tmp_file = (
                self.missions_dir / f"{record.mission_id}.json.tmp.{os.getpid()}.{time.time_ns()}"
            )
            tmp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            tmp_file.replace(target_file)

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

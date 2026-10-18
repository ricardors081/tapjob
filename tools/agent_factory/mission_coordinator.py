"""TapJob Agent Factory — Mission Coordinator for parallel multi-agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.agent_factory.mission_contract import MissionContract
from tools.agent_factory.mission_planner import ExecutionPlan, MissionPlanner
from tools.agent_factory.mission_registry import (
    MissionRegistry,
    MissionRegistryRecord,
    MissionState,
    TaskMissionState,
)
from tools.agent_factory.repair_loop import (
    MAX_INTERNAL_REPAIR_LOOPS,
    BudgetExhaustedError,
    ReviewerSeparationError,
    ReviewVerdict,
)
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.worktree_dispatcher import (
    DirtyWorktreeError,
    ScopeViolationError,
    WorktreeDispatcher,
    WorktreeInfo,
)


class MissionCoordinatorError(Exception):
    """Base error for MissionCoordinator failures."""


class MissionDispatchError(MissionCoordinatorError):
    """Raised when dispatching an execution group fails."""


@dataclass
class ConsolidationStatus:
    """Structured report on whether a mission is ready for consolidation."""

    ready: bool
    status: str
    blockers: list[str]


class MissionCoordinator:
    """Orchestrates end-to-end multi-agent parallel mission execution and consolidation gates."""

    def __init__(
        self,
        dispatcher: WorktreeDispatcher | None = None,
        registry: MissionRegistry | None = None,
        repo_root: Path | str | None = None,
        worktrees_dir: Path | str | None = None,
    ) -> None:
        self.dispatcher = dispatcher or WorktreeDispatcher(
            repo_root=repo_root, worktrees_dir=worktrees_dir
        )
        self.registry = registry or MissionRegistry(worktrees_dir=self.dispatcher.worktrees_dir)
        self._missions: dict[str, MissionContract] = {}

    def create_mission(self, mission: MissionContract) -> ExecutionPlan:
        """Validate, plan, and register a new mission in PLANNED state."""
        mission.validate()
        plan = MissionPlanner.plan_mission(mission)
        self._missions[mission.mission_id] = mission

        record = MissionRegistryRecord(
            mission_id=mission.mission_id,
            base_sha=mission.canonical_base_sha,
            state=MissionState.PLANNED,
            task_ids=[t.task_id for t in mission.tasks],
            execution_groups=[g.task_ids for g in plan.groups],
            task_states={t.task_id: TaskMissionState.PENDING for t in mission.tasks},
            task_records={
                t.task_id: {
                    "owner_role": t.owner_role,
                    "target_branch": t.target_branch,
                    "repair_count": 0,
                    "contract": t.to_dict(),
                }
                for t in mission.tasks
            },
            metadata=dict(mission.metadata),
        )
        self.registry.save_mission(record)
        return plan

    def _get_mission_contract(self, mission_id: str) -> MissionContract:
        """Retrieve the in-memory or persisted MissionContract."""
        if mission_id in self._missions:
            return self._missions[mission_id]

        record = self.registry.get_mission(mission_id)
        tasks: list[TaskContract] = []
        for tid in record.task_ids:
            trec = record.task_records.get(tid, {})
            cdict = trec.get("contract")
            if not isinstance(cdict, dict):
                raise MissionCoordinatorError(
                    f"Mission '{mission_id}' record missing contract for child task '{tid}'."
                )
            tasks.append(TaskContract.from_dict(cdict))

        mission = MissionContract(
            mission_id=record.mission_id,
            canonical_base_sha=record.base_sha,
            objective=str(record.metadata.get("objective", f"Mission {mission_id}")),
            tasks=tasks,
            human_gate_required=bool(record.metadata.get("human_gate_required", True)),
            metadata=dict(record.metadata),
        )
        self._missions[mission_id] = mission
        return mission

    def dispatch_execution_group(
        self,
        mission_id: str,
        group_index: int,
    ) -> dict[str, WorktreeInfo]:
        """Dispatch a planned execution group via WorktreeDispatcher.

        Fails closed on any error while preserving registry state for deterministic diagnosis.
        """
        record = self.registry.get_mission(mission_id)
        if record.state in (MissionState.BLOCKED, MissionState.FAILED):
            msg = (
                f"Cannot dispatch execution group: mission '{mission_id}' is in "
                f"{record.state} state."
            )
            raise MissionDispatchError(msg)

        if group_index < 0 or group_index >= len(record.execution_groups):
            raise MissionDispatchError(
                f"Invalid group_index {group_index} for mission '{mission_id}'. "
                f"Total groups: {len(record.execution_groups)}."
            )

        mission = self._get_mission_contract(mission_id)
        tasks_by_id = {t.task_id: t for t in mission.tasks}
        target_group = record.execution_groups[group_index]

        # Verify all tasks in group are PENDING
        for tid in target_group:
            curr_state = record.task_states.get(tid)
            if curr_state != TaskMissionState.PENDING:
                msg = (
                    f"Task '{tid}' in group {group_index} is in state '{curr_state}', "
                    "expected 'pending'."
                )
                raise MissionDispatchError(msg)

        self.registry.update_mission_state(mission_id, MissionState.DISPATCHING)
        dispatched_worktrees: dict[str, WorktreeInfo] = {}

        try:
            for tid in target_group:
                contract = tasks_by_id[tid]
                w_info = self.dispatcher.dispatch_task(contract)
                dispatched_worktrees[tid] = w_info
                self.registry.update_task_state(
                    mission_id=mission_id,
                    task_id=tid,
                    task_state=TaskMissionState.DISPATCHED,
                    details={
                        "worktree_path": str(w_info.worktree_path),
                        "branch_name": w_info.branch_name,
                        "base_sha": w_info.base_sha,
                        "head_sha": w_info.head_sha,
                    },
                )
        except Exception as exc:
            # Partial dispatch failure: fail closed, mark failed tasks, preserve already dispatched
            self.registry.update_mission_state(mission_id, MissionState.BLOCKED)
            raise MissionDispatchError(
                f"Partial dispatch failure in group {group_index} of mission '{mission_id}': {exc}"
            ) from exc

        self.registry.update_mission_state(mission_id, MissionState.RUNNING)
        return dispatched_worktrees

    def record_task_implemented(
        self,
        mission_id: str,
        task_id: str,
        head_sha: str,
    ) -> None:
        """Record that the implementer agent has completed code changes."""
        record = self.registry.get_mission(mission_id)
        curr_state = record.task_states.get(task_id)
        if curr_state not in (TaskMissionState.DISPATCHED, TaskMissionState.CHANGES_REQUESTED):
            msg = (
                f"Cannot record implementation for task '{task_id}': "
                f"current state is '{curr_state}'."
            )
            raise MissionCoordinatorError(msg)

        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=TaskMissionState.IMPLEMENTED,
            details={"head_sha": head_sha.strip()},
        )

    def record_task_review(
        self,
        mission_id: str,
        task_id: str,
        reviewer_role: str,
        verdict: ReviewVerdict,
        test_passed: bool,
        findings: list[str] | None = None,
    ) -> TaskMissionState:
        """Record the review and QA verdict with strict separation of duties and repair budget."""
        record = self.registry.get_mission(mission_id)
        if task_id not in record.task_records:
            raise MissionCoordinatorError(f"Task '{task_id}' not found in mission '{mission_id}'.")

        task_rec = record.task_records[task_id]
        owner_role = task_rec.get("owner_role", "")
        if reviewer_role == owner_role:
            msg = (
                f"Role '{reviewer_role}' cannot review its own task '{task_id}' "
                f"(owner: '{owner_role}'). Separation of duties violation."
            )
            raise ReviewerSeparationError(msg)

        current_repairs = int(task_rec.get("repair_count", 0))

        if test_passed and verdict == ReviewVerdict.APPROVE:
            new_state = TaskMissionState.APPROVED
            self.registry.update_task_state(
                mission_id=mission_id,
                task_id=task_id,
                task_state=new_state,
                details={
                    "reviewer_role": reviewer_role,
                    "review_verdict": verdict.value,
                    "test_passed": True,
                    "findings": findings or [],
                },
            )
            return new_state

        # Tests failed or changes required: handle repair loop
        current_repairs += 1
        if current_repairs > MAX_INTERNAL_REPAIR_LOOPS:
            self.registry.update_task_state(
                mission_id=mission_id,
                task_id=task_id,
                task_state=TaskMissionState.BLOCKED,
                details={
                    "reviewer_role": reviewer_role,
                    "review_verdict": verdict.value,
                    "test_passed": test_passed,
                    "findings": findings or [],
                    "repair_count": current_repairs,
                    "escalated_to_human": True,
                },
            )
            self.registry.update_mission_state(mission_id, MissionState.BLOCKED)
            msg = (
                f"Repair budget of {MAX_INTERNAL_REPAIR_LOOPS} loops exhausted for "
                f"task '{task_id}'. Escalating to human operator."
            )
            raise BudgetExhaustedError(msg)

        new_state = TaskMissionState.CHANGES_REQUESTED
        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=new_state,
            details={
                "reviewer_role": reviewer_role,
                "review_verdict": verdict.value,
                "test_passed": test_passed,
                "findings": findings or [],
                "repair_count": current_repairs,
            },
        )
        self.registry.update_mission_state(mission_id, MissionState.REPAIRING)
        return new_state

    def verify_task_scope(
        self,
        mission_id: str,
        task_id: str,
        exact_head_sha: str | None = None,
    ) -> list[str]:
        """Perform exact scope verification on an approved task worktree."""
        record = self.registry.get_mission(mission_id)
        curr_state = record.task_states.get(task_id)
        if curr_state != TaskMissionState.APPROVED:
            raise MissionCoordinatorError(
                f"Task '{task_id}' must be APPROVED before scope verification, got '{curr_state}'."
            )

        try:
            self.dispatcher.verify_task_scope(task_id)
        except ScopeViolationError as exc:
            self.registry.update_task_state(
                mission_id=mission_id,
                task_id=task_id,
                task_state=TaskMissionState.FAILED,
                details={"scope_violations": [str(exc)]},
            )
            self.registry.update_mission_state(mission_id, MissionState.BLOCKED)
            raise

        # Inspect current HEAD to freeze verified SHA
        active = self.dispatcher.get_active_task(task_id)
        if not active:
            raise MissionCoordinatorError(f"Task '{task_id}' has no active worktree.")
        _, winfo = active
        verified_head = winfo.head_sha
        if exact_head_sha and verified_head != exact_head_sha:
            msg = (
                f"Task '{task_id}' HEAD mismatch: expected '{exact_head_sha}', "
                f"got '{verified_head}'."
            )
            raise ScopeViolationError(msg)

        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=TaskMissionState.SCOPE_VERIFIED,
            details={"scope_verified_head_sha": verified_head},
        )
        return []

    def check_consolidation_readiness(self, mission_id: str) -> ConsolidationStatus:
        """Deterministic consolidation-readiness check across all mission child tasks.

        Does NOT merge anything. Merge remains strictly human-gated.
        """
        record = self.registry.get_mission(mission_id)
        blockers: list[str] = []

        if record.state in (MissionState.BLOCKED, MissionState.FAILED):
            blockers.append(f"Mission '{mission_id}' is in '{record.state}' state.")

        for tid in record.task_ids:
            state = record.task_states.get(tid)
            trec = record.task_records.get(tid, {})

            if state != TaskMissionState.SCOPE_VERIFIED:
                blockers.append(f"Task '{tid}' is not scope verified (current state: '{state}').")

            # Check review verdict
            verdict = trec.get("review_verdict")
            if verdict != ReviewVerdict.APPROVE.value:
                blockers.append(
                    f"Task '{tid}' lacks required review approval (verdict: '{verdict}')."
                )

            # Check reviewer separation
            reviewer = trec.get("reviewer_role")
            owner = trec.get("owner_role")
            if not reviewer or reviewer == owner:
                msg = (
                    f"Task '{tid}' reviewer separation missing "
                    f"(reviewer: '{reviewer}', owner: '{owner}')."
                )
                blockers.append(msg)

            # Check repair budget
            repairs = int(trec.get("repair_count", 0))
            if repairs > MAX_INTERNAL_REPAIR_LOOPS:
                msg = (
                    f"Task '{tid}' exceeded repair loop budget "
                    f"({repairs} > {MAX_INTERNAL_REPAIR_LOOPS})."
                )
                blockers.append(msg)

            # Check worktree state and TOCTOU HEAD pinning
            verified_head = trec.get("scope_verified_head_sha")
            active = self.dispatcher.get_active_task(tid)
            if not active:
                blockers.append(f"Task '{tid}' active worktree is missing.")
            else:
                _, winfo = active
                if not winfo.is_clean:
                    blockers.append(f"Task '{tid}' worktree contains uncommitted/dirty changes.")
                if verified_head and winfo.head_sha != verified_head:
                    blockers.append(
                        f"Task '{tid}' HEAD changed after scope verification "
                        f"({winfo.head_sha} != {verified_head})."
                    )

        if blockers:
            return ConsolidationStatus(
                ready=False,
                status="BLOCKED",
                blockers=blockers,
            )

        self.registry.update_mission_state(mission_id, MissionState.READY_FOR_CONSOLIDATION)
        return ConsolidationStatus(
            ready=True,
            status="READY_FOR_CONSOLIDATION",
            blockers=[],
        )

    def cleanup_mission(self, mission_id: str, force: bool = False) -> list[str]:
        """Safely clean up worktrees for completed or verified tasks without destroying evidence."""
        record = self.registry.get_mission(mission_id)
        cleaned: list[str] = []

        for tid in record.task_ids:
            active = self.dispatcher.get_active_task(tid)
            if not active:
                continue
            _contract, winfo = active
            state = record.task_states.get(tid)

            # Never delete worktrees with dirty uncommitted changes unless force=True
            if not winfo.is_clean and not force:
                msg = (
                    f"Refusing to cleanup dirty worktree for task '{tid}' at "
                    f"'{winfo.worktree_path}'."
                )
                raise DirtyWorktreeError(msg)

            # In safe mode, only cleanup tasks that are scope-verified or ready
            if state not in (TaskMissionState.SCOPE_VERIFIED, TaskMissionState.READY) and not force:
                continue

            if state == TaskMissionState.SCOPE_VERIFIED:
                self.dispatcher.release_task(tid)
            else:
                self.dispatcher.remove_worktree(winfo.worktree_path)
                with self.dispatcher._registry_lock():
                    rdata = self.dispatcher._read_registry_file()
                    rdata.pop(tid, None)
                    self.dispatcher._write_registry_file(rdata)

            cleaned.append(tid)

        return cleaned

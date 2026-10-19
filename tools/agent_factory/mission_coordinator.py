"""TapJob Agent Factory — Mission Coordinator for parallel multi-agent execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
    RepairIterationRecord,
    RepairLoopController,
    ReviewerSeparationError,
    ReviewVerdict,
)
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.team import ALL_ROLES
from tools.agent_factory.worktree_dispatcher import (
    DirtyWorktreeError,
    ScopeViolationError,
    WorktreeDispatcher,
    WorktreeInfo,
)

SHA_40_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")


class MissionCoordinatorError(Exception):
    """Base error for MissionCoordinator failures."""


class MissionDispatchError(MissionCoordinatorError):
    """Raised when dispatching an execution group fails."""


class UnresolvedTaskCleanupError(MissionCoordinatorError):
    """Raised when cleanup is attempted on an unresolved or unverified task."""


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
            objective=mission.objective,
            human_gate_required=True,
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
        self.registry.create_mission(record)
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
            objective=record.objective,
            tasks=tasks,
            human_gate_required=True,
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

        Partial dispatch failure semantics:
        - Successfully dispatched tasks remain registered, active, and visible in worktree registry.
        - The mission transitions to BLOCKED to halt further autonomous progression.
        - Undispatched tasks in the group remain PENDING.
        - Deterministic diagnostic information is raised so the operator has complete visibility.
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
            # Partial dispatch failure: preserve successfully dispatched tasks, mark mission BLOCKED
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
        """Record that the implementer agent has completed code changes in the worktree.

        Allowed only from DISPATCHED (initial implementation) or CHANGES_REQUESTED (repair).
        """
        record = self.registry.get_mission(mission_id)
        curr_state = record.task_states.get(task_id)
        if curr_state not in (TaskMissionState.DISPATCHED, TaskMissionState.CHANGES_REQUESTED):
            msg = (
                f"Cannot record implementation for task '{task_id}': "
                f"current state is '{curr_state}'. Must be DISPATCHED or CHANGES_REQUESTED."
            )
            raise MissionCoordinatorError(msg)

        clean_sha = head_sha.strip()
        if not SHA_40_REGEX.match(clean_sha):
            raise ValueError(
                f"Invalid head_sha '{head_sha}' for task '{task_id}'. Must be a 40-character SHA."
            )

        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=TaskMissionState.IMPLEMENTED,
            details={"head_sha": clean_sha},
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
        """Record the review and QA verdict with strict separation of duties and repair budget.

        Transition invariants:
        - May ONLY occur from IMPLEMENTED state.
        - reviewer_role must be a valid registered ALL_ROLES role.
        - reviewer_role != owner_role (ReviewerSeparationError).
        - test_passed must be strict boolean.
        - Uses RepairLoopController to manage budget (capped at 5 loops).
        """
        record = self.registry.get_mission(mission_id)
        if task_id not in record.task_records:
            raise MissionCoordinatorError(f"Task '{task_id}' not found in mission '{mission_id}'.")

        curr_state = record.task_states.get(task_id)
        if curr_state != TaskMissionState.IMPLEMENTED:
            raise MissionCoordinatorError(
                f"Task '{task_id}' cannot be reviewed from state '{curr_state}'. "
                "Must be in IMPLEMENTED state."
            )

        # 1. Validate reviewer_role
        if not isinstance(reviewer_role, str) or reviewer_role not in ALL_ROLES:
            raise ValueError(
                f"Invalid reviewer_role '{reviewer_role}'. Must be a valid registered role."
            )

        task_rec = record.task_records[task_id]
        owner_role = task_rec.get("owner_role", "")
        if reviewer_role == owner_role:
            msg = (
                f"Role '{reviewer_role}' cannot review its own task '{task_id}' "
                f"(owner: '{owner_role}'). Separation of duties violation."
            )
            raise ReviewerSeparationError(msg)

        # 2. Validate verdict
        if not isinstance(verdict, ReviewVerdict):
            try:
                verdict = ReviewVerdict(verdict)
            except ValueError as exc:
                raise ValueError(f"Invalid ReviewVerdict: {verdict!r}") from exc

        # 3. Validate test_passed strict boolean
        if not isinstance(test_passed, bool) or type(test_passed) is not bool:
            msg = (
                f"test_passed must be a strict boolean, "
                f"got {type(test_passed).__name__} ({test_passed!r})"
            )
            raise TypeError(msg)

        # 4. Validate findings
        if findings is not None and (
            not isinstance(findings, list) or not all(isinstance(f, str) for f in findings)
        ):
            raise TypeError("findings must be a list of strings if provided.")

        # 5. Use RepairLoopController to manage iteration bounds
        controller = RepairLoopController(
            task_id=task_id,
            implementer_role=owner_role,
            reviewer_role=reviewer_role,
            max_loops=MAX_INTERNAL_REPAIR_LOOPS,
        )

        # Reconstruct previous iterations
        raw_repairs = task_rec.get("repair_records", [])
        for r in raw_repairs:
            r_rec = RepairIterationRecord(
                iteration_index=r["iteration_index"],
                implementer_role=r["implementer_role"],
                reviewer_role=r["reviewer_role"],
                test_passed=r["test_passed"],
                verdict=ReviewVerdict(r["verdict"]),
                findings=r.get("findings", []),
            )
            controller.records.append(r_rec)
        controller.current_iteration = len(controller.records)

        try:
            controller.start_iteration()
        except BudgetExhaustedError:
            self.registry.update_task_state(
                mission_id=mission_id,
                task_id=task_id,
                task_state=TaskMissionState.BLOCKED,
                details={
                    "reviewer_role": reviewer_role,
                    "review_verdict": verdict.value,
                    "test_passed": test_passed,
                    "findings": findings or [],
                    "repair_count": MAX_INTERNAL_REPAIR_LOOPS + 1,
                    "escalated_to_human": True,
                },
            )
            self.registry.update_mission_state(mission_id, MissionState.BLOCKED)
            raise

        controller.record_review_result(
            test_passed=test_passed,
            verdict=verdict,
            reviewer_role=reviewer_role,
            findings=findings,
        )

        persisted_repairs = [
            {
                "iteration_index": r.iteration_index,
                "implementer_role": r.implementer_role,
                "reviewer_role": r.reviewer_role,
                "test_passed": r.test_passed,
                "verdict": r.verdict.value,
                "findings": r.findings,
            }
            for r in controller.records
        ]

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
                    "repair_count": len(controller.records) - 1,
                    "repair_records": persisted_repairs,
                },
            )
            return new_state

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
                "repair_count": len(controller.records),
                "repair_records": persisted_repairs,
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
        """Perform exact scope verification on an approved task worktree.

        Allowed ONLY from APPROVED state. Freezes the exact verified HEAD commit SHA.
        """
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

        now_iso = datetime.now(UTC).isoformat()
        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=TaskMissionState.SCOPE_VERIFIED,
            details={
                "scope_verified_head_sha": verified_head,
                "scope_verified_at": now_iso,
            },
        )
        return []

    def check_consolidation_readiness(self, mission_id: str) -> ConsolidationStatus:
        """Deterministic consolidation-readiness check binding strictly to AF-1 evidence.

        Fails closed unless every child task has canonical verification evidence.
        Does NOT merge anything. Merge remains strictly human-gated.
        """
        record = self.registry.get_mission(mission_id)
        blockers: list[str] = []

        if record.state in (MissionState.BLOCKED, MissionState.FAILED):
            blockers.append(f"Mission '{mission_id}' is in '{record.state}' state.")

        mission = self._get_mission_contract(mission_id)
        tasks_by_id = {t.task_id: t for t in mission.tasks}

        for tid in record.task_ids:
            state = record.task_states.get(tid)
            trec = record.task_records.get(tid, {})
            contract = tasks_by_id[tid]

            # 1. Task state must be SCOPE_VERIFIED
            if state != TaskMissionState.SCOPE_VERIFIED:
                blockers.append(f"Task '{tid}' is not scope verified (current state: '{state}').")
                continue

            # 2. scope_verified_head_sha must exist and match exact SHA-40
            verified_head = trec.get("scope_verified_head_sha", "")
            if not isinstance(verified_head, str) or not SHA_40_REGEX.match(verified_head):
                blockers.append(
                    f"Task '{tid}' lacks valid scope_verified_head_sha: '{verified_head}'."
                )
                continue

            # 3. Review verdict must be APPROVE
            verdict = trec.get("review_verdict")
            if verdict != ReviewVerdict.APPROVE.value:
                blockers.append(
                    f"Task '{tid}' lacks required review approval (verdict: '{verdict}')."
                )

            # 4. test_passed must be strict True
            if trec.get("test_passed") is not True:
                blockers.append(f"Task '{tid}' test_passed is not True.")

            # 5. Reviewer separation check
            reviewer = trec.get("reviewer_role")
            owner = trec.get("owner_role")
            if not reviewer or reviewer not in ALL_ROLES or reviewer == owner:
                msg = (
                    f"Task '{tid}' reviewer separation missing or invalid "
                    f"(reviewer: '{reviewer}', owner: '{owner}')."
                )
                blockers.append(msg)

            # 6. Repair budget check (<= 5)
            repairs = trec.get("repair_count", 0)
            if (
                not isinstance(repairs, int)
                or type(repairs) is not int
                or repairs < 0
                or repairs > MAX_INTERNAL_REPAIR_LOOPS
            ):
                msg = (
                    f"Task '{tid}' repair count invalid or exceeded budget "
                    f"({repairs} > {MAX_INTERNAL_REPAIR_LOOPS})."
                )
                blockers.append(msg)

            # 7. Bind to WorktreeDispatcher persistent registry and live worktree evidence
            disp_blockers = self.dispatcher.validate_task_consolidation_evidence(
                contract=contract,
                expected_head_sha=verified_head,
            )
            blockers.extend(disp_blockers)

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

    def cleanup_mission(self, mission_id: str) -> list[str]:
        """Safely clean up worktrees for scope-verified tasks.

        Invariants:
        - NEVER removes task ownership or worktrees for unverified, in-progress, or failed tasks.
        - Refuses cleanup of dirty worktrees.
        - Only SCOPE_VERIFIED or READY tasks can be released via WorktreeDispatcher.release_task.
        """
        record = self.registry.get_mission(mission_id)
        cleaned: list[str] = []

        for tid in record.task_ids:
            active = self.dispatcher.get_active_task(tid)
            if not active:
                continue
            _contract, winfo = active
            state = record.task_states.get(tid)

            # Refuse dirty worktrees
            if not winfo.is_clean:
                msg = (
                    f"Refusing to cleanup dirty worktree for task '{tid}' at "
                    f"'{winfo.worktree_path}'."
                )
                raise DirtyWorktreeError(msg)

            # Automated cleanup must NEVER remove task ownership unless the task has passed
            # the canonical scope/release gate
            if state not in (TaskMissionState.SCOPE_VERIFIED, TaskMissionState.READY):
                msg = (
                    f"Refusing to clean up unresolved task '{tid}' in state '{state}'. "
                    "Only SCOPE_VERIFIED or READY tasks can be released."
                )
                raise UnresolvedTaskCleanupError(msg)

            self.dispatcher.release_task(tid)
            cleaned.append(tid)

        return cleaned

    def emergency_purge_unverified_task(
        self,
        mission_id: str,
        task_id: str,
        human_operator_confirmation: str,
    ) -> None:
        """Emergency destructive recovery path reserved strictly for human operators.

        Autonomous agents and orchestrators are prohibited from invoking this method.
        """
        if human_operator_confirmation != "CONFIRM_EMERGENCY_DESTRUCTIVE_PURGE":
            raise PermissionError(
                "Human operator confirmation token required for emergency destructive purge."
            )

        active = self.dispatcher.get_active_task(task_id)
        if active:
            _, winfo = active
            self.dispatcher.remove_worktree(winfo.worktree_path)

        with self.dispatcher._registry_lock():
            rdata = self.dispatcher._read_registry_file()
            rdata.pop(task_id, None)
            self.dispatcher._write_registry_file(rdata)

        self.registry.update_task_state(
            mission_id=mission_id,
            task_id=task_id,
            task_state=TaskMissionState.FAILED,
            details={"purged_by_human_emergency": True},
        )

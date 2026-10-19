"""TapJob Agent Factory — Mission Coordinator for parallel multi-agent execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.agent_factory.mission_contract import MissionContract
from tools.agent_factory.mission_planner import ExecutionPlan, MissionPlanner
from tools.agent_factory.mission_registry import (
    InvalidStateTransitionError,
    MissionRegistry,
    MissionRegistryRecord,
    MissionState,
    TaskMissionState,
)
from tools.agent_factory.mission_registry import (
    MissionCoordinatorError as MissionCoordinatorError,
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
        # Register in persistent registry BEFORE caching in-memory to prevent
        # cache poisoning on duplicates
        self.registry.create_mission(record)
        self._missions[mission.mission_id] = mission
        return plan

    def _get_mission_contract(self, mission_id: str) -> MissionContract:
        """Retrieve the canonical MissionContract validated against the persistent registry."""
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

        DAG Ordering & Dependency Invariants:
        - All earlier groups (0 .. group_index - 1) must have all tasks in SCOPE_VERIFIED.
        - All declared task dependencies must be in SCOPE_VERIFIED.
        - Partial dispatch failures preserve successfully dispatched tasks and mark mission BLOCKED.
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

        # 1. Enforce DAG ordering: all tasks in earlier groups must be SCOPE_VERIFIED
        for prev_idx in range(group_index):
            for prev_tid in record.execution_groups[prev_idx]:
                prev_state = record.task_states.get(prev_tid)
                if prev_state != TaskMissionState.SCOPE_VERIFIED:
                    msg = (
                        f"Cannot dispatch group {group_index}: task '{prev_tid}' in earlier "
                        f"group {prev_idx} is in state '{prev_state}', expected SCOPE_VERIFIED."
                    )
                    raise MissionDispatchError(msg)

        mission = self._get_mission_contract(mission_id)
        tasks_by_id = {t.task_id: t for t in mission.tasks}
        target_group = record.execution_groups[group_index]

        # 2. Enforce explicit task dependencies: each task's dependencies must be SCOPE_VERIFIED
        for tid in target_group:
            contract = tasks_by_id[tid]
            for dep_tid in contract.dependencies:
                dep_state = record.task_states.get(dep_tid)
                if dep_state != TaskMissionState.SCOPE_VERIFIED:
                    msg = (
                        f"Cannot dispatch group {group_index}: task '{tid}' has dependency "
                        f"'{dep_tid}' in state '{dep_state}', expected SCOPE_VERIFIED."
                    )
                    raise MissionDispatchError(msg)

        # 3. Verify all tasks in target group are PENDING
        for tid in target_group:
            curr_state = record.task_states.get(tid)
            if curr_state != TaskMissionState.PENDING:
                msg = (
                    f"Task '{tid}' in group {group_index} is in state '{curr_state}', "
                    "expected 'pending'."
                )
                raise MissionDispatchError(msg)

        self.registry.transition_mission_state(
            mission_id=mission_id,
            expected_states={
                MissionState.PLANNED,
                MissionState.DISPATCHING,
                MissionState.RUNNING,
                MissionState.REPAIRING,
            },
            new_state=MissionState.DISPATCHING,
        )
        dispatched_worktrees: dict[str, WorktreeInfo] = {}

        try:
            for tid in target_group:
                contract = tasks_by_id[tid]
                w_info = self.dispatcher.dispatch_task(contract)
                dispatched_worktrees[tid] = w_info
                self.registry.transition_task_state(
                    mission_id=mission_id,
                    task_id=tid,
                    expected_states={TaskMissionState.PENDING},
                    new_state=TaskMissionState.DISPATCHED,
                    details={
                        "worktree_path": str(w_info.worktree_path),
                        "branch_name": w_info.branch_name,
                        "base_sha": w_info.base_sha,
                        "head_sha": w_info.head_sha,
                    },
                )
        except Exception as exc:
            # Partial dispatch failure: preserve successfully dispatched tasks, mark mission BLOCKED
            self.registry.transition_mission_state(
                mission_id=mission_id,
                expected_states={MissionState.DISPATCHING, MissionState.RUNNING},
                new_state=MissionState.BLOCKED,
            )
            raise MissionDispatchError(
                f"Partial dispatch failure in group {group_index} of mission '{mission_id}': {exc}"
            ) from exc

        self.registry.transition_mission_state(
            mission_id=mission_id,
            expected_states={MissionState.DISPATCHING},
            new_state=MissionState.RUNNING,
        )
        return dispatched_worktrees

    def get_next_eligible_group(self, mission_id: str) -> int | None:
        """Return index of the next group eligible for dispatch, or None if blocked/completed."""
        record = self.registry.get_mission(mission_id)
        if record.state in (MissionState.BLOCKED, MissionState.FAILED):
            return None

        mission = self._get_mission_contract(mission_id)
        tasks_by_id = {t.task_id: t for t in mission.tasks}

        for g_idx, grp in enumerate(record.execution_groups):
            group_states = [record.task_states.get(tid) for tid in grp]
            if all(s == TaskMissionState.SCOPE_VERIFIED for s in group_states):
                continue
            if all(s == TaskMissionState.PENDING for s in group_states):
                # Verify earlier groups are SCOPE_VERIFIED
                earlier_done = True
                for prev_idx in range(g_idx):
                    for prev_tid in record.execution_groups[prev_idx]:
                        if record.task_states.get(prev_tid) != TaskMissionState.SCOPE_VERIFIED:
                            earlier_done = False
                            break
                    if not earlier_done:
                        break
                if not earlier_done:
                    return None

                # Verify task dependencies
                deps_satisfied = True
                for tid in grp:
                    contract = tasks_by_id[tid]
                    for dep in contract.dependencies:
                        if record.task_states.get(dep) != TaskMissionState.SCOPE_VERIFIED:
                            deps_satisfied = False
                            break
                    if not deps_satisfied:
                        break

                if deps_satisfied:
                    return g_idx
                return None
            return None

        return None

    def record_task_implemented(
        self,
        mission_id: str,
        task_id: str,
        head_sha: str,
    ) -> None:
        """Record that the implementer agent has completed code changes in the worktree.

        Allowed only from DISPATCHED (initial implementation) or CHANGES_REQUESTED (repair).
        Validates live worktree existence, exact HEAD SHA match, clean working tree, and branch.
        """
        record = self.registry.get_mission(mission_id)
        curr_state = record.task_states.get(task_id)
        if curr_state not in (TaskMissionState.DISPATCHED, TaskMissionState.CHANGES_REQUESTED):
            msg = (
                f"Cannot record implementation for task '{task_id}': "
                f"current state is '{curr_state}'. Must be DISPATCHED or CHANGES_REQUESTED."
            )
            raise InvalidStateTransitionError(msg)

        clean_sha = head_sha.strip()
        if not SHA_40_REGEX.match(clean_sha):
            raise ValueError(
                f"Invalid head_sha '{head_sha}' for task '{task_id}'. Must be a 40-character SHA."
            )

        active = self.dispatcher.get_active_task(task_id)
        if not active:
            raise MissionCoordinatorError(f"Task '{task_id}' has no active worktree.")
        contract, winfo = active

        # Live worktree validations
        live_head = self.dispatcher.get_worktree_head(winfo.worktree_path)
        if live_head != clean_sha:
            msg = (
                f"Task '{task_id}' live HEAD mismatch: reported '{clean_sha}', "
                f"live worktree HEAD is '{live_head}'."
            )
            raise MissionCoordinatorError(msg)

        if not self.dispatcher.is_worktree_clean(winfo.worktree_path):
            msg = f"Task '{task_id}' worktree at '{winfo.worktree_path}' has uncommitted changes."
            raise DirtyWorktreeError(msg)

        curr_branch = self.dispatcher.get_worktree_branch(winfo.worktree_path)
        if curr_branch != contract.target_branch:
            msg = (
                f"Task '{task_id}' worktree branch mismatch: expected '{contract.target_branch}', "
                f"got '{curr_branch}'."
            )
            raise MissionCoordinatorError(msg)

        self.registry.transition_task_state(
            mission_id=mission_id,
            task_id=task_id,
            expected_states={TaskMissionState.DISPATCHED, TaskMissionState.CHANGES_REQUESTED},
            new_state=TaskMissionState.IMPLEMENTED,
            details={"implemented_head_sha": clean_sha, "head_sha": clean_sha},
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
        - Active worktree live HEAD must match implemented_head_sha and be clean.
        - Uses RepairLoopController to manage budget (capped at 5 loops).
        """
        record = self.registry.get_mission(mission_id)
        if task_id not in record.task_records:
            raise MissionCoordinatorError(f"Task '{task_id}' not found in mission '{mission_id}'.")

        curr_state = record.task_states.get(task_id)
        if curr_state != TaskMissionState.IMPLEMENTED:
            raise InvalidStateTransitionError(
                f"Task '{task_id}' cannot be reviewed from state '{curr_state}'. "
                "Must be in IMPLEMENTED state."
            )

        task_rec = record.task_records[task_id]
        owner_role = task_rec.get("owner_role", "")

        # 1. Validate active worktree live HEAD and cleanliness against implemented HEAD
        active = self.dispatcher.get_active_task(task_id)
        if not active:
            raise MissionCoordinatorError(f"Task '{task_id}' has no active worktree.")
        _, winfo = active

        implemented_sha = task_rec.get("implemented_head_sha") or task_rec.get("head_sha")
        if not implemented_sha or not SHA_40_REGEX.match(implemented_sha):
            raise MissionCoordinatorError(
                f"Task '{task_id}' lacks valid implemented_head_sha: '{implemented_sha}'."
            )

        live_head = self.dispatcher.get_worktree_head(winfo.worktree_path)
        if live_head != implemented_sha:
            msg = (
                f"Task '{task_id}' live HEAD changed after implementation: "
                f"expected '{implemented_sha}', live is '{live_head}'."
            )
            raise MissionCoordinatorError(msg)

        if not self.dispatcher.is_worktree_clean(winfo.worktree_path):
            raise DirtyWorktreeError(
                f"Task '{task_id}' worktree at '{winfo.worktree_path}' is dirty during review."
            )

        # 2. Validate reviewer_role
        if not isinstance(reviewer_role, str) or reviewer_role not in ALL_ROLES:
            raise ValueError(
                f"Invalid reviewer_role '{reviewer_role}'. Must be a valid registered role."
            )

        if reviewer_role == owner_role:
            msg = (
                f"Role '{reviewer_role}' cannot review its own task '{task_id}' "
                f"(owner: '{owner_role}'). Separation of duties violation."
            )
            raise ReviewerSeparationError(msg)

        # 3. Validate verdict
        if not isinstance(verdict, ReviewVerdict):
            try:
                verdict = ReviewVerdict(verdict)
            except ValueError as exc:
                raise ValueError(f"Invalid ReviewVerdict: {verdict!r}") from exc

        # 4. Validate test_passed strict boolean
        if not isinstance(test_passed, bool) or type(test_passed) is not bool:
            msg = (
                f"test_passed must be a strict boolean, "
                f"got {type(test_passed).__name__} ({test_passed!r})"
            )
            raise TypeError(msg)

        # 5. Validate findings
        if findings is not None and (
            not isinstance(findings, list) or not all(isinstance(f, str) for f in findings)
        ):
            raise TypeError("findings must be a list of strings if provided.")

        # 6. Use RepairLoopController to manage iteration bounds
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
                iteration_head_sha=r.get("iteration_head_sha", ""),
            )
            controller.records.append(r_rec)
        controller.current_iteration = len(controller.records)

        try:
            controller.start_iteration()
        except BudgetExhaustedError:
            self.registry.transition_task_state(
                mission_id=mission_id,
                task_id=task_id,
                expected_states={TaskMissionState.IMPLEMENTED},
                new_state=TaskMissionState.BLOCKED,
                details={
                    "reviewer_role": reviewer_role,
                    "review_verdict": verdict.value,
                    "test_passed": test_passed,
                    "findings": findings or [],
                    "repair_count": len(controller.records),
                    "escalated_to_human": True,
                },
            )
            self.registry.transition_mission_state(
                mission_id=mission_id,
                expected_states={MissionState.RUNNING, MissionState.REPAIRING},
                new_state=MissionState.BLOCKED,
            )
            raise

        controller.record_review_result(
            test_passed=test_passed,
            verdict=verdict,
            reviewer_role=reviewer_role,
            findings=findings,
            iteration_head_sha=implemented_sha,
        )

        persisted_repairs = [
            {
                "iteration_index": idx,
                "iteration_head_sha": r.iteration_head_sha,
                "implementer_role": r.implementer_role,
                "reviewer_role": r.reviewer_role,
                "test_passed": r.test_passed,
                "verdict": r.verdict.value,
                "findings": r.findings,
            }
            for idx, r in enumerate(controller.records)
        ]

        if test_passed and verdict == ReviewVerdict.APPROVE:
            new_state = TaskMissionState.APPROVED
            self.registry.transition_task_state(
                mission_id=mission_id,
                task_id=task_id,
                expected_states={TaskMissionState.IMPLEMENTED},
                new_state=new_state,
                details={
                    "reviewer_role": reviewer_role,
                    "review_verdict": verdict.value,
                    "test_passed": True,
                    "findings": findings or [],
                    "repair_count": len(controller.records),
                    "repair_records": persisted_repairs,
                    "reviewed_head_sha": implemented_sha,
                },
            )
            return new_state

        new_state = TaskMissionState.CHANGES_REQUESTED
        self.registry.transition_task_state(
            mission_id=mission_id,
            task_id=task_id,
            expected_states={TaskMissionState.IMPLEMENTED},
            new_state=new_state,
            details={
                "reviewer_role": reviewer_role,
                "review_verdict": verdict.value,
                "test_passed": test_passed,
                "findings": findings or [],
                "repair_count": len(controller.records),
                "repair_records": persisted_repairs,
            },
        )
        self.registry.transition_mission_state(
            mission_id=mission_id,
            expected_states={MissionState.RUNNING, MissionState.REPAIRING},
            new_state=MissionState.REPAIRING,
        )
        return new_state

    def verify_task_scope(
        self,
        mission_id: str,
        task_id: str,
        exact_head_sha: str,
    ) -> list[str]:
        """Perform exact scope verification on an approved task worktree.

        Allowed ONLY from APPROVED state. Freezes the exact verified HEAD commit SHA.
        Fails closed if exact_head_sha does not match reviewed_head_sha or live worktree HEAD.
        """
        if not exact_head_sha or not SHA_40_REGEX.match(exact_head_sha.strip()):
            msg = (
                f"exact_head_sha is strictly required and must be a 40-character SHA, "
                f"got '{exact_head_sha}'."
            )
            raise MissionCoordinatorError(msg)
        exact_head = exact_head_sha.strip()

        record = self.registry.get_mission(mission_id)
        curr_state = record.task_states.get(task_id)
        if curr_state != TaskMissionState.APPROVED:
            raise InvalidStateTransitionError(
                f"Task '{task_id}' must be APPROVED before scope verification, got '{curr_state}'."
            )

        task_rec = record.task_records.get(task_id, {})
        reviewed_head = task_rec.get("reviewed_head_sha")
        if reviewed_head != exact_head:
            msg = (
                f"Task '{task_id}' exact_head_sha mismatch: expected reviewed '{reviewed_head}', "
                f"got '{exact_head}'."
            )
            raise ScopeViolationError(msg)

        active = self.dispatcher.get_active_task(task_id)
        if not active:
            raise MissionCoordinatorError(f"Task '{task_id}' has no active worktree.")
        _, winfo = active

        live_head = self.dispatcher.get_worktree_head(winfo.worktree_path)
        if live_head != exact_head:
            msg = (
                f"Task '{task_id}' live HEAD changed after review approval: "
                f"reviewed '{reviewed_head}', live is '{live_head}'."
            )
            raise ScopeViolationError(msg)

        if not self.dispatcher.is_worktree_clean(winfo.worktree_path):
            msg = (
                f"Task '{task_id}' worktree at '{winfo.worktree_path}' is dirty "
                f"during scope verification."
            )
            raise DirtyWorktreeError(msg)

        try:
            self.dispatcher.verify_task_scope(task_id)
        except ScopeViolationError as exc:
            self.registry.transition_task_state(
                mission_id=mission_id,
                task_id=task_id,
                expected_states={TaskMissionState.APPROVED},
                new_state=TaskMissionState.FAILED,
                details={"scope_violations": [str(exc)]},
            )
            self.registry.transition_mission_state(
                mission_id=mission_id,
                expected_states={MissionState.RUNNING, MissionState.REPAIRING},
                new_state=MissionState.BLOCKED,
            )
            raise

        now_iso = datetime.now(UTC).isoformat()
        self.registry.transition_task_state(
            mission_id=mission_id,
            task_id=task_id,
            expected_states={TaskMissionState.APPROVED},
            new_state=TaskMissionState.SCOPE_VERIFIED,
            details={
                "scope_verified_head_sha": exact_head,
                "scope_verified_at": now_iso,
            },
        )

        # If mission is in REPAIRING and no task in the mission remains in
        # CHANGES_REQUESTED or IMPLEMENTED, deterministically transition mission back to RUNNING.
        refreshed = self.registry.get_mission(mission_id)
        if refreshed.state == MissionState.REPAIRING:
            has_pending_repairs = any(
                st in (TaskMissionState.CHANGES_REQUESTED, TaskMissionState.IMPLEMENTED)
                for st in refreshed.task_states.values()
            )
            if not has_pending_repairs:
                self.registry.transition_mission_state(
                    mission_id=mission_id,
                    expected_states={MissionState.REPAIRING},
                    new_state=MissionState.RUNNING,
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

            # 7. Check full lifecycle HEAD SHA binding: implemented == reviewed == verified == live
            implemented_sha = trec.get("implemented_head_sha") or trec.get("head_sha")
            reviewed_sha = trec.get("reviewed_head_sha")
            active = self.dispatcher.get_active_task(tid)
            if not active:
                blockers.append(f"Task '{tid}' has no active worktree.")
                continue
            _, winfo = active
            live_head = self.dispatcher.get_worktree_head(winfo.worktree_path)

            if not (implemented_sha == reviewed_sha == verified_head == live_head):
                msg = (
                    f"Task '{tid}' HEAD mismatch across lifecycle: "
                    f"implemented='{implemented_sha}', reviewed='{reviewed_sha}', "
                    f"verified='{verified_head}', live='{live_head}'."
                )
                blockers.append(msg)

            # 8. Bind to WorktreeDispatcher persistent registry and live worktree evidence
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

        self.registry.transition_mission_state(
            mission_id=mission_id,
            expected_states={
                MissionState.RUNNING,
                MissionState.REPAIRING,
                MissionState.READY_FOR_CONSOLIDATION,
            },
            new_state=MissionState.READY_FOR_CONSOLIDATION,
        )
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

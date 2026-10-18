"""Deterministic synthetic unit test suite for TapJob Agent Factory Mission execution (AF-2)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.agent_factory.mission_contract import (
    InvalidMissionContractError,
    MissionContract,
)
from tools.agent_factory.mission_coordinator import (
    MissionCoordinator,
    MissionDispatchError,
)
from tools.agent_factory.mission_planner import (
    MissionPlanner,
)
from tools.agent_factory.mission_registry import (
    CorruptMissionRegistryError,
    MissionRegistry,
    MissionRegistryRecord,
    MissionState,
    TaskMissionState,
)
from tools.agent_factory.repair_loop import (
    BudgetExhaustedError,
    ReviewerSeparationError,
    ReviewVerdict,
)
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.worktree_dispatcher import (
    DirtyWorktreeError,
    WorktreeDispatcher,
)


class TestMissionExecution(unittest.TestCase):
    """Synthetic test suite for AF-2 Mission contracts, planning, registry, and coordination."""

    git_bin: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.git_bin = shutil.which("git") or "git"

    @classmethod
    def _run_git(
        cls, args: list[str], cwd: Path | str, text: bool = False
    ) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(  # noqa: S603
            [cls.git_bin, *args], cwd=str(cwd), capture_output=True, text=text, check=True
        )

    @classmethod
    def _create_temp_git_repo(cls, root_dir: Path) -> tuple[Path, str]:
        """Create a synthetic deterministic git repository with initial commit."""
        repo_dir = root_dir / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        cls._run_git(["init", "-b", "main"], repo_dir)
        cls._run_git(["config", "user.email", "agent@tapjob.internal"], repo_dir)
        cls._run_git(["config", "user.name", "Agent Test"], repo_dir)

        (repo_dir / "README.md").write_text("# TapJob Synthetic\n", encoding="utf-8")
        api_dir = repo_dir / "services" / "api" / "src" / "tapjob_api"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "app.py").write_text("# app\n", encoding="utf-8")
        docs_dir = repo_dir / "docs" / "agent-factory"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "GUIDE.md").write_text("# Guide\n", encoding="utf-8")

        cls._run_git(["add", "."], repo_dir)
        cls._run_git(["commit", "-m", "chore: initial commit"], repo_dir)

        res = cls._run_git(["rev-parse", "HEAD"], repo_dir, text=True)
        return repo_dir, res.stdout.strip()

    def _make_task(
        self,
        task_id: str,
        base_sha: str,
        role: str = "core_platform_engineer",
        allowed_paths: list[str] | None = None,
        dependencies: list[str] | None = None,
        parallel_safe: bool = True,
        contracts_frozen: bool = True,
        target_branch: str = "",
    ) -> TaskContract:
        paths = allowed_paths or ["services/api/src/tapjob_api/app.py"]
        return TaskContract(
            task_id=task_id,
            outcome=f"Outcome for {task_id}",
            scope=[f"Scope {task_id}"],
            non_goals=[],
            canonical_base_sha=base_sha,
            owner_role=role,
            allowed_paths=paths,
            forbidden_paths=[],
            dependencies=dependencies or [],
            contracts_frozen=contracts_frozen,
            test_requirements=["uv run pytest"],
            acceptance_criteria=["Passes all tests"],
            risk_level="LOW",
            parallel_safe=parallel_safe,
            human_gate_required=False,
            target_branch=target_branch or f"feat/{task_id.lower().replace('_', '-')}",
        )

    # 1. Mission strict parsing
    def test_mission_strict_parsing(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        task = self._make_task("task_1", valid_sha)
        mission = MissionContract(
            mission_id="mission-alpha",
            canonical_base_sha=valid_sha,
            objective="Deliver AF-2 functionality",
            tasks=[task],
            human_gate_required=True,
        )
        mission.validate()
        d = mission.to_dict()
        self.assertEqual(d["mission_id"], "mission-alpha")

        # Re-parse
        parsed = MissionContract.from_dict(d)
        self.assertEqual(parsed.mission_id, "mission-alpha")
        self.assertEqual(len(parsed.tasks), 1)

        # Fail closed on invalid type
        with self.assertRaises(InvalidMissionContractError):
            MissionContract.from_dict("not a dict")  # type: ignore[arg-type]

        # Fail closed on non-bool human_gate_required
        d_bad = dict(d)
        d_bad["human_gate_required"] = "true"  # bool coercion forbidden
        with self.assertRaises(InvalidMissionContractError):
            MissionContract.from_dict(d_bad)

    # 2. Duplicate mission ID / path traversal
    def test_mission_id_traversal_and_format(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        task = self._make_task("task_1", valid_sha)
        with self.assertRaises(InvalidMissionContractError):
            MissionContract(
                mission_id="../traversal",
                canonical_base_sha=valid_sha,
                objective="Objective",
                tasks=[task],
            ).validate()

    # 3. Duplicate task IDs rejected
    def test_duplicate_task_ids_rejected(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("task_dup", valid_sha, target_branch="feat/task-dup-1")
        t2 = self._make_task("task_dup", valid_sha, target_branch="feat/task-dup-2")
        with self.assertRaises(InvalidMissionContractError) as ctx:
            MissionContract(
                mission_id="mission-dup",
                canonical_base_sha=valid_sha,
                objective="Objective",
                tasks=[t1, t2],
            ).validate()
        self.assertIn("duplicate task_id 'task_dup'", str(ctx.exception))

    # 4. Different base SHA child rejected
    def test_different_base_sha_child_rejected(self) -> None:
        sha_1 = "0123456789abcdef0123456789abcdef01234567"
        sha_2 = "abcdef0123456789abcdef0123456789abcdef01"
        t1 = self._make_task("task_1", sha_1)
        t2 = self._make_task("task_2", sha_2)
        with self.assertRaises(InvalidMissionContractError) as ctx:
            MissionContract(
                mission_id="mission-sha",
                canonical_base_sha=sha_1,
                objective="Objective",
                tasks=[t1, t2],
            ).validate()
        self.assertIn("does not match mission base SHA", str(ctx.exception))

    # 5. Unsafe overlapping tasks serialized/rejected from parallel
    def test_unsafe_overlapping_tasks_serialized(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        # Two tasks claiming the exact same path
        t1 = self._make_task(
            "task_a", valid_sha, allowed_paths=["services/api/src/tapjob_api/app.py"]
        )
        t2 = self._make_task(
            "task_b", valid_sha, allowed_paths=["services/api/src/tapjob_api/app.py"]
        )
        mission = MissionContract(
            mission_id="mission-overlap",
            canonical_base_sha=valid_sha,
            objective="Test overlap",
            tasks=[t1, t2],
        )
        plan = MissionPlanner.plan_mission(mission)
        self.assertEqual(len(plan.groups), 2)
        self.assertEqual(plan.groups[0].task_ids, ["task_a"])
        self.assertEqual(plan.groups[1].task_ids, ["task_b"])
        self.assertFalse(plan.groups[0].parallel)
        self.assertFalse(plan.groups[1].parallel)

    # 6. Critical serial path handling
    def test_critical_serial_path_handling(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task(
            "task_crit",
            valid_sha,
            role="core_platform_engineer",
            allowed_paths=["services/api/alembic/env.py"],
        )
        t2 = self._make_task(
            "task_doc",
            valid_sha,
            role="engineering_manager",
            allowed_paths=["docs/agent-factory/AF2_PARALLEL_EXECUTION.md"],
        )
        mission = MissionContract(
            mission_id="mission-crit",
            canonical_base_sha=valid_sha,
            objective="Test critical path",
            tasks=[t1, t2],
        )
        plan = MissionPlanner.plan_mission(mission)
        self.assertEqual(len(plan.groups), 2)
        group_tasks = [g.task_ids for g in plan.groups]
        self.assertIn(["task_crit"], group_tasks)
        self.assertIn(["task_doc"], group_tasks)

    # 7. Safe tasks grouped in parallel deterministically
    def test_safe_tasks_grouped_in_parallel_deterministically(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task(
            "task_doc1",
            valid_sha,
            role="engineering_manager",
            allowed_paths=["docs/agent-factory/DOC1.md"],
        )
        t2 = self._make_task(
            "task_doc2",
            valid_sha,
            role="engineering_manager",
            allowed_paths=["docs/agent-factory/DOC2.md"],
        )
        t3 = self._make_task(
            "task_doc3",
            valid_sha,
            role="engineering_manager",
            allowed_paths=["docs/agent-factory/DOC3.md"],
        )
        mission = MissionContract(
            mission_id="mission-parallel",
            canonical_base_sha=valid_sha,
            objective="Safe parallel",
            tasks=[t1, t2, t3],
        )
        plan1 = MissionPlanner.plan_mission(mission)
        plan2 = MissionPlanner.plan_mission(mission)
        self.assertEqual(len(plan1.groups), 1)
        self.assertTrue(plan1.groups[0].parallel)
        self.assertEqual(plan1.groups[0].task_ids, ["task_doc1", "task_doc2", "task_doc3"])
        self.assertEqual(plan1.to_dict(), plan2.to_dict())

    # 8. Mission registry corruption fails closed
    def test_mission_registry_corruption_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MissionRegistry(tmpdir)
            corrupt_file = registry._mission_file("bad_mission")
            corrupt_file.write_text("{ corrupt json ...", encoding="utf-8")
            with self.assertRaises(CorruptMissionRegistryError):
                registry.get_mission("bad_mission")

    # 9. Mission registry atomic state update
    def test_mission_registry_atomic_state_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MissionRegistry(tmpdir)
            record = MissionRegistryRecord(
                mission_id="mission-state",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
            )
            registry.save_mission(record)
            loaded = registry.get_mission("mission-state")
            self.assertEqual(loaded.state, MissionState.PLANNED)

            updated = registry.update_mission_state("mission-state", MissionState.RUNNING)
            self.assertEqual(updated.state, MissionState.RUNNING)
            self.assertTrue(updated.updated_at >= loaded.created_at)

    # 10. Partial dispatch failure remains fail closed
    def test_partial_dispatch_failure_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)
            registry = MissionRegistry(worktrees_dir=worktrees_dir)
            coordinator = MissionCoordinator(dispatcher=dispatcher, registry=registry)

            # Task 1 is valid, Task 2 has invalid branch name that will raise InvalidBranchNameError
            t1 = self._make_task(
                "task_1_ok",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            t2 = self._make_task(
                "task_2_bad",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC2.md"],
                target_branch="main",
            )

            mission = MissionContract(
                mission_id="mission-partial",
                canonical_base_sha=head_sha,
                objective="Test partial dispatch",
                tasks=[t1, t2],
            )
            coordinator.create_mission(mission)

            with self.assertRaises(MissionDispatchError):
                coordinator.dispatch_execution_group("mission-partial", 0)

            rec = registry.get_mission("mission-partial")
            self.assertEqual(rec.state, MissionState.BLOCKED)
            self.assertEqual(rec.task_states["task_1_ok"], TaskMissionState.DISPATCHED)

    # 11. Cross-process mission state visibility
    def test_cross_process_mission_state_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg1 = MissionRegistry(tmpdir)
            reg2 = MissionRegistry(tmpdir)

            record = MissionRegistryRecord(
                mission_id="mission-ipc",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
            )
            reg1.save_mission(record)

            loaded_by_2 = reg2.get_mission("mission-ipc")
            self.assertEqual(loaded_by_2.state, MissionState.PLANNED)

            reg2.update_mission_state("mission-ipc", MissionState.RUNNING)
            loaded_by_1 = reg1.get_mission("mission-ipc")
            self.assertEqual(loaded_by_1.state, MissionState.RUNNING)

    # 12. Reviewer / implementer separation
    def test_reviewer_implementer_separation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_rev",
                head_sha,
                role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/app.py"],
            )
            mission = MissionContract(
                mission_id="mission-rev",
                canonical_base_sha=head_sha,
                objective="Test review",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-rev", 0)

            with self.assertRaises(ReviewerSeparationError):
                coordinator.record_task_review(
                    mission_id="mission-rev",
                    task_id="task_rev",
                    reviewer_role="core_platform_engineer",
                    verdict=ReviewVerdict.APPROVE,
                    test_passed=True,
                )

    # 13. Repair loop 1..5 accepted
    def test_repair_loop_1_to_5_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_repair",
                head_sha,
                role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/app.py"],
            )
            mission = MissionContract(
                mission_id="mission-rep",
                canonical_base_sha=head_sha,
                objective="Test repair",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-rep", 0)

            for i in range(1, 6):
                coordinator.record_task_implemented("mission-rep", "task_repair", head_sha)
                state = coordinator.record_task_review(
                    mission_id="mission-rep",
                    task_id="task_repair",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.CHANGES_REQUIRED,
                    test_passed=False,
                    findings=[f"Repair finding {i}"],
                )
                self.assertEqual(state, TaskMissionState.CHANGES_REQUESTED)

    # 14. Repair attempt 6 blocked/escalated
    def test_repair_attempt_6_blocked_escalated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_repair_ex",
                head_sha,
                role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/app.py"],
            )
            mission = MissionContract(
                mission_id="mission-rep-ex",
                canonical_base_sha=head_sha,
                objective="Test repair exhaustion",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-rep-ex", 0)

            for _i in range(1, 6):
                coordinator.record_task_implemented("mission-rep-ex", "task_repair_ex", head_sha)
                coordinator.record_task_review(
                    mission_id="mission-rep-ex",
                    task_id="task_repair_ex",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.CHANGES_REQUIRED,
                    test_passed=False,
                )

            # 6th attempt must raise BudgetExhaustedError and escalate
            coordinator.record_task_implemented("mission-rep-ex", "task_repair_ex", head_sha)
            with self.assertRaises(BudgetExhaustedError):
                coordinator.record_task_review(
                    mission_id="mission-rep-ex",
                    task_id="task_repair_ex",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.CHANGES_REQUIRED,
                    test_passed=False,
                )

            rec = coordinator.registry.get_mission("mission-rep-ex")
            self.assertEqual(rec.task_states["task_repair_ex"], TaskMissionState.BLOCKED)
            self.assertEqual(rec.state, MissionState.BLOCKED)

    # 15. Missing approval prevents consolidation
    def test_missing_approval_prevents_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_unapproved",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-unapproved",
                canonical_base_sha=head_sha,
                objective="Test unapproved",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-unapproved", 0)

            status = coordinator.check_consolidation_readiness("mission-unapproved")
            self.assertFalse(status.ready)
            self.assertEqual(status.status, "BLOCKED")
            self.assertTrue(any("lacks required review approval" in b for b in status.blockers))

    # 16. Missing scope verification prevents consolidation
    def test_missing_scope_verification_prevents_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_unverified",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-unverified",
                canonical_base_sha=head_sha,
                objective="Test unverified",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-unverified", 0)
            coordinator.record_task_implemented("mission-unverified", "task_unverified", head_sha)
            coordinator.record_task_review(
                "mission-unverified",
                "task_unverified",
                reviewer_role="qa_sdet_engineer",
                verdict=ReviewVerdict.APPROVE,
                test_passed=True,
            )

            # Task is APPROVED, but not yet SCOPE_VERIFIED
            status = coordinator.check_consolidation_readiness("mission-unverified")
            self.assertFalse(status.ready)
            self.assertTrue(any("not scope verified" in b for b in status.blockers))

    # 17. Changed HEAD after verification prevents consolidation
    def test_changed_head_after_verification_prevents_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_toctou",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-toctou",
                canonical_base_sha=head_sha,
                objective="Test toctou",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-toctou", 0)
            w_path = wtrees["task_toctou"].worktree_path

            # Make valid change and commit
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Doc 1\n", encoding="utf-8"
            )
            self._run_git(["add", "."], w_path)
            self._run_git(["commit", "-m", "docs: update"], w_path)
            res = self._run_git(["rev-parse", "HEAD"], w_path, text=True)
            committed_head = res.stdout.strip()

            coordinator.record_task_implemented("mission-toctou", "task_toctou", committed_head)
            coordinator.record_task_review(
                "mission-toctou", "task_toctou", "qa_sdet_engineer", ReviewVerdict.APPROVE, True
            )
            coordinator.verify_task_scope("mission-toctou", "task_toctou", committed_head)

            # Rogue commit added to worktree after scope verification!
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Rogue update\n", encoding="utf-8"
            )
            self._run_git(["add", "."], w_path)
            self._run_git(["commit", "-m", "docs: rogue commit"], w_path)

            status = coordinator.check_consolidation_readiness("mission-toctou")
            self.assertFalse(status.ready)
            self.assertTrue(
                any("HEAD changed after scope verification" in b for b in status.blockers)
            )

    # 18. Dirty worktree prevents consolidation
    def test_dirty_worktree_prevents_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_dirty",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-dirty",
                canonical_base_sha=head_sha,
                objective="Test dirty worktree",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-dirty", 0)
            w_path = wtrees["task_dirty"].worktree_path

            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Doc 1\n", encoding="utf-8"
            )
            self._run_git(["add", "."], w_path)
            self._run_git(["commit", "-m", "docs: update"], w_path)
            res = self._run_git(["rev-parse", "HEAD"], w_path, text=True)
            committed_head = res.stdout.strip()

            coordinator.record_task_implemented("mission-dirty", "task_dirty", committed_head)
            coordinator.record_task_review(
                "mission-dirty", "task_dirty", "qa_sdet_engineer", ReviewVerdict.APPROVE, True
            )
            coordinator.verify_task_scope("mission-dirty", "task_dirty", committed_head)

            # Add uncommitted modification
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Uncommitted dirty change\n", encoding="utf-8"
            )

            status = coordinator.check_consolidation_readiness("mission-dirty")
            self.assertFalse(status.ready)
            self.assertTrue(any("uncommitted/dirty changes" in b for b in status.blockers))

    # 19. All tasks valid => READY_FOR_CONSOLIDATION
    def test_all_tasks_valid_ready_for_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_clean1",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            t2 = self._make_task(
                "task_clean2",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC2.md"],
            )

            mission = MissionContract(
                mission_id="mission-clean",
                canonical_base_sha=head_sha,
                objective="Test clean consolidation",
                tasks=[t1, t2],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-clean", 0)

            for tid in ("task_clean1", "task_clean2"):
                wpath = wtrees[tid].worktree_path
                doc_name = "DOC1.md" if tid == "task_clean1" else "DOC2.md"
                (wpath / "docs" / "agent-factory" / doc_name).write_text(
                    f"# {doc_name}\n", encoding="utf-8"
                )
                self._run_git(["add", "."], wpath)
                self._run_git(["commit", "-m", f"docs: update {doc_name}"], wpath)
                res = self._run_git(["rev-parse", "HEAD"], wpath, text=True)
                c_head = res.stdout.strip()

                coordinator.record_task_implemented("mission-clean", tid, c_head)
                coordinator.record_task_review(
                    "mission-clean", tid, "qa_sdet_engineer", ReviewVerdict.APPROVE, True
                )
                coordinator.verify_task_scope("mission-clean", tid, c_head)

            status = coordinator.check_consolidation_readiness("mission-clean")
            self.assertTrue(status.ready)
            self.assertEqual(status.status, "READY_FOR_CONSOLIDATION")
            self.assertEqual(status.blockers, [])

            rec = coordinator.registry.get_mission("mission-clean")
            self.assertEqual(rec.state, MissionState.READY_FOR_CONSOLIDATION)

    # 20. Cleanup cannot erase unresolved task ownership / dirty worktree
    def test_cleanup_cannot_erase_unresolved_or_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_protect",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-protect",
                canonical_base_sha=head_sha,
                objective="Test cleanup protection",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-protect", 0)
            wpath = wtrees["task_protect"].worktree_path

            # Add dirty modification
            (wpath / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Dirty content\n", encoding="utf-8"
            )

            # Must refuse to clean up dirty worktree
            with self.assertRaises(DirtyWorktreeError):
                coordinator.cleanup_mission("mission-protect", force=False)


if __name__ == "__main__":
    unittest.main()

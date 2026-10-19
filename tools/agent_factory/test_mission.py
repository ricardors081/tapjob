"""Comprehensive synthetic tests for TapJob Agent Factory AF-2 parallel mission execution."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tools.agent_factory.mission_contract import (
    InvalidMissionContractError,
    MissionContract,
)
from tools.agent_factory.mission_coordinator import (
    MissionCoordinator,
    MissionCoordinatorError,
    MissionDispatchError,
    UnresolvedTaskCleanupError,
)
from tools.agent_factory.mission_planner import (
    DependencyCycleError,
    MissingDependencyError,
    MissionPlanner,
    RoleOwnershipViolationError,
    RolePermissionError,
)
from tools.agent_factory.mission_registry import (
    CorruptMissionRegistryError,
    DuplicateMissionIdError,
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
    ScopeViolationError,
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
        repo_dir = root_dir / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        cls._run_git(["init"], repo_dir)
        cls._run_git(["config", "user.name", "TapJob Agent"], repo_dir)
        cls._run_git(["config", "user.email", "agent@tapjob.internal"], repo_dir)
        cls._run_git(["config", "commit.gpgsign", "false"], repo_dir)

        dummy_file = repo_dir / "README.md"
        dummy_file.write_text("# TapJob Test Repo\n", encoding="utf-8")
        cls._run_git(["add", "README.md"], repo_dir)
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
            test_requirements=["python -c 'print(True)'"],
            acceptance_criteria=["Passes all tests"],
            risk_level="LOW",
            parallel_safe=parallel_safe,
            human_gate_required=False,
            target_branch=target_branch or f"feat/{task_id.lower().replace('_', '-')}",
        )

    # 1. Mission strict parsing
    def test_01_mission_strict_parsing(self) -> None:
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
        reconstructed = MissionContract.from_dict(d)
        self.assertEqual(mission.mission_id, reconstructed.mission_id)
        self.assertEqual(mission.canonical_base_sha, reconstructed.canonical_base_sha)
        self.assertTrue(reconstructed.human_gate_required)

    # 2. human_gate_required cannot be disabled
    def test_02_human_gate_required_cannot_be_disabled(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        task = self._make_task("task_1", valid_sha)

        with self.assertRaises(InvalidMissionContractError):
            MissionContract(
                mission_id="mission-alpha",
                canonical_base_sha=valid_sha,
                objective="Deliver AF-2",
                tasks=[task],
                human_gate_required=False,
            ).validate()

        raw_dict = {
            "mission_id": "mission-alpha",
            "canonical_base_sha": valid_sha,
            "objective": "Deliver AF-2",
            "tasks": [task.to_dict()],
            "human_gate_required": False,
        }
        with self.assertRaises(InvalidMissionContractError):
            MissionContract.from_dict(raw_dict)

    # 3. All tasks must match mission base SHA
    def test_03_all_tasks_must_match_base_sha(self) -> None:
        sha1 = "0123456789abcdef0123456789abcdef01234567"
        sha2 = "fedcba9876543210fedcba9876543210fedcba98"
        t1 = self._make_task("task_1", sha1)
        t2 = self._make_task("task_2", sha2)
        mission = MissionContract(
            mission_id="mission-mismatch",
            canonical_base_sha=sha1,
            objective="Test mismatch",
            tasks=[t1, t2],
        )
        with self.assertRaises(InvalidMissionContractError):
            mission.validate()

    # 4. Unique task_ids required
    def test_04_unique_task_ids_required(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("duplicate_id", valid_sha, target_branch="feat/branch-1")
        t2 = self._make_task("duplicate_id", valid_sha, target_branch="feat/branch-2")
        mission = MissionContract(
            mission_id="mission-dup",
            canonical_base_sha=valid_sha,
            objective="Test duplicate",
            tasks=[t1, t2],
        )
        with self.assertRaises(InvalidMissionContractError):
            mission.validate()

    # 5. Unique target branches required
    def test_05_unique_target_branches_required(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("task_a", valid_sha, target_branch="feat/same-branch")
        t2 = self._make_task("task_b", valid_sha, target_branch="feat/same-branch")
        mission = MissionContract(
            mission_id="mission-dup-branch",
            canonical_base_sha=valid_sha,
            objective="Test duplicate branch",
            tasks=[t1, t2],
        )
        with self.assertRaises(InvalidMissionContractError):
            mission.validate()

    # 6. Deterministic topological plan
    def test_06_deterministic_topological_plan(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("task_a", valid_sha, allowed_paths=["services/api/a.py"])
        t2 = self._make_task("task_b", valid_sha, allowed_paths=["services/api/b.py"])
        t3 = self._make_task(
            "task_c", valid_sha, allowed_paths=["services/api/c.py"], dependencies=["task_a"]
        )
        mission = MissionContract(
            mission_id="mission-plan",
            canonical_base_sha=valid_sha,
            objective="Test plan",
            tasks=[t3, t2, t1],  # Unordered
        )
        plan1 = MissionPlanner.plan_mission(mission)
        plan2 = MissionPlanner.plan_mission(mission)
        self.assertEqual(len(plan1.groups), len(plan2.groups))
        self.assertEqual(plan1.groups[0].task_ids, plan2.groups[0].task_ids)
        self.assertEqual(plan1.groups[1].task_ids, plan2.groups[1].task_ids)
        self.assertEqual(plan1.groups[0].task_ids, ["task_a", "task_b"])
        self.assertEqual(plan1.groups[1].task_ids, ["task_c"])

    # 7. Dependency cycle detection
    def test_07_dependency_cycle_detection(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("task_a", valid_sha, dependencies=["task_b"])
        t2 = self._make_task("task_b", valid_sha, dependencies=["task_a"])
        mission = MissionContract(
            mission_id="mission-cycle",
            canonical_base_sha=valid_sha,
            objective="Test cycle",
            tasks=[t1, t2],
        )
        with self.assertRaises(DependencyCycleError):
            MissionPlanner.plan_mission(mission)

    # 8. Missing dependency rejected
    def test_08_missing_dependency_rejected(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task("task_a", valid_sha, dependencies=["non_existent_task"])
        mission = MissionContract(
            mission_id="mission-missing-dep",
            canonical_base_sha=valid_sha,
            objective="Test missing dep",
            tasks=[t1],
        )
        with self.assertRaises(MissingDependencyError):
            MissionPlanner.plan_mission(mission)

    # 9. Critical serial paths isolated
    def test_09_critical_serial_paths_isolated(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t2 = self._make_task(
            "task_normal",
            valid_sha,
            role="core_platform_engineer",
            allowed_paths=["services/api/test.py"],
        )
        # database_specialist owns services/api/alembic/ (which is in CRITICAL_SERIAL_PATHS)
        t_crit = self._make_task(
            "task_guard",
            valid_sha,
            role="database_specialist",
            allowed_paths=["services/api/alembic/versions/test.py"],
        )
        mission = MissionContract(
            mission_id="mission-crit",
            canonical_base_sha=valid_sha,
            objective="Test crit",
            tasks=[t_crit, t2],
        )
        plan = MissionPlanner.plan_mission(mission)
        self.assertGreaterEqual(len(plan.groups), 2)
        for g in plan.groups:
            if "task_guard" in g.task_ids:
                self.assertFalse(g.parallel)
                self.assertEqual(len(g.task_ids), 1)

    # 10. Conflicting paths partitioned
    def test_10_conflicting_paths_partitioned(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        t1 = self._make_task(
            "task_1",
            valid_sha,
            role="core_platform_engineer",
            allowed_paths=["services/api/src/tapjob_api/app.py"],
        )
        t2 = self._make_task(
            "task_2",
            valid_sha,
            role="core_platform_engineer",
            allowed_paths=["services/api/src/tapjob_api/app.py"],  # Direct conflict
        )
        mission = MissionContract(
            mission_id="mission-conflict",
            canonical_base_sha=valid_sha,
            objective="Test conflict",
            tasks=[t1, t2],
        )
        plan = MissionPlanner.plan_mission(mission)
        self.assertEqual(len(plan.groups), 2)
        self.assertEqual(plan.groups[0].task_ids, ["task_1"])
        self.assertEqual(plan.groups[1].task_ids, ["task_2"])

    # 11. Planner validates role ownership
    def test_11_planner_validates_role_ownership(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        # core_platform_engineer cannot own apps/mobile/
        t1 = self._make_task(
            "task_unauthorized",
            valid_sha,
            role="core_platform_engineer",
            allowed_paths=["apps/mobile/src/App.tsx"],
        )
        mission = MissionContract(
            mission_id="mission-role-violation",
            canonical_base_sha=valid_sha,
            objective="Test unauthorized role path",
            tasks=[t1],
        )
        with self.assertRaises(RoleOwnershipViolationError):
            MissionPlanner.plan_mission(mission)

    # 12. Planner rejects read-only role with write paths
    def test_12_planner_read_only_role_with_writes_rejected(self) -> None:
        valid_sha = "0123456789abcdef0123456789abcdef01234567"
        # reviewer_security_engineer is read-only (enable_write_tools=False)
        t1 = self._make_task(
            "task_readonly",
            valid_sha,
            role="reviewer_security_engineer",
            allowed_paths=["docs/PROJECT_STATE.md"],
        )
        mission = MissionContract(
            mission_id="mission-ro-writes",
            canonical_base_sha=valid_sha,
            objective="Test read-only role with writes",
            tasks=[t1],
        )
        with self.assertRaises(RolePermissionError):
            MissionPlanner.plan_mission(mission)

    # 13. Registry persistence and atomic writes
    def test_13_registry_persistence_atomic_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            record = MissionRegistryRecord(
                mission_id="mission-atomic",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                objective="Test atomic persistence",
                human_gate_required=True,
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
                task_records={
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    }
                },
            )
            registry.create_mission(record)

            # Re-read
            loaded = registry.get_mission("mission-atomic")
            self.assertEqual(loaded.mission_id, "mission-atomic")
            self.assertEqual(loaded.state, MissionState.PLANNED)

    # 14. Corrupt registry fails closed on invalid JSON
    def test_14_corrupt_registry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)
            corrupt_file = registry.missions_dir / "bad-mission.json"
            corrupt_file.write_text("{invalid json", encoding="utf-8")

            with self.assertRaises(CorruptMissionRegistryError):
                registry.get_mission("bad-mission")

    # 15. Strict registry semantic corruption
    def test_15_strict_registry_semantic_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            base_record = {
                "mission_id": "mission-sem-corrupt",
                "base_sha": "0123456789abcdef0123456789abcdef01234567",
                "objective": "Test semantic corruption",
                "human_gate_required": True,
                "state": "planned",
                "task_ids": ["t1", "t2"],
                "execution_groups": [["t1"]],  # Missing t2 in execution groups!
                "task_states": {"t1": "pending", "t2": "pending"},
                "task_records": {
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    },
                    "t2": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t2",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t2", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    },
                },
            }
            f = registry.missions_dir / "mission-sem-corrupt.json"
            f.write_text(json.dumps(base_record), encoding="utf-8")

            # Must fail closed with CorruptMissionRegistryError
            with self.assertRaises(CorruptMissionRegistryError):
                registry.get_mission("mission-sem-corrupt")

    # 16. Duplicate mission_id in same process rejected
    def test_16_duplicate_mission_id_same_process_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            record = MissionRegistryRecord(
                mission_id="mission-dup-test",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                objective="Test duplicate in same process",
                human_gate_required=True,
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
                task_records={
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    }
                },
            )
            registry.create_mission(record)

            # Second create_mission on same mission_id must fail closed with DuplicateMissionIdError
            with self.assertRaises(DuplicateMissionIdError):
                registry.create_mission(record)

    # 17. Duplicate mission_id cross-instance rejected
    def test_17_duplicate_mission_id_cross_instance_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            reg1 = MissionRegistry(worktrees_dir=worktrees_dir)
            reg2 = MissionRegistry(worktrees_dir=worktrees_dir)

            record = MissionRegistryRecord(
                mission_id="mission-cross-inst",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                objective="Test cross instance duplicate",
                human_gate_required=True,
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
                task_records={
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    }
                },
            )
            reg1.create_mission(record)

            with self.assertRaises(DuplicateMissionIdError):
                reg2.create_mission(record)

    # 18. Original mission record remains intact after duplicate rejection
    def test_18_original_mission_preserved_on_duplicate_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            original = MissionRegistryRecord(
                mission_id="mission-preserve",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                objective="Original objective",
                human_gate_required=True,
                state=MissionState.RUNNING,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.DISPATCHED},
                task_records={
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 2,
                        "repair_records": [
                            {
                                "iteration_index": 0,
                                "iteration_head_sha": "0123456789abcdef0123456789abcdef01234567",
                                "implementer_role": "core_platform_engineer",
                                "reviewer_role": "reviewer_security_engineer",
                                "test_passed": False,
                                "verdict": "CHANGES_REQUIRED",
                                "findings": ["finding 1"],
                            },
                            {
                                "iteration_index": 1,
                                "iteration_head_sha": "0123456789abcdef0123456789abcdef01234567",
                                "implementer_role": "core_platform_engineer",
                                "reviewer_role": "reviewer_security_engineer",
                                "test_passed": False,
                                "verdict": "CHANGES_REQUIRED",
                                "findings": ["finding 2"],
                            },
                        ],
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    }
                },
            )
            registry.create_mission(original)
            original_bytes = (registry.missions_dir / "mission-preserve.json").read_bytes()

            # Attempt duplicate create with different state
            attacker_record = MissionRegistryRecord(
                mission_id="mission-preserve",
                base_sha="0123456789abcdef0123456789abcdef01234567",
                objective="Overwritten objective",
                human_gate_required=True,
                state=MissionState.PLANNED,
                task_ids=["t1"],
                execution_groups=[["t1"]],
                task_states={"t1": TaskMissionState.PENDING},
                task_records={
                    "t1": {
                        "owner_role": "core_platform_engineer",
                        "target_branch": "feat/t1",
                        "repair_count": 0,
                        "contract": self._make_task(
                            "t1", "0123456789abcdef0123456789abcdef01234567"
                        ).to_dict(),
                    }
                },
            )
            with self.assertRaises(DuplicateMissionIdError):
                registry.create_mission(attacker_record)

            after_bytes = (registry.missions_dir / "mission-preserve.json").read_bytes()
            self.assertEqual(original_bytes, after_bytes)
            reloaded = registry.get_mission("mission-preserve")
            self.assertEqual(reloaded.state, MissionState.RUNNING)
            self.assertEqual(reloaded.task_records["t1"]["repair_count"], 2)

    # 19. Cross process locking and recovery
    def test_19_cross_process_locking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            # Dead PID recovery test
            registry.lock_path.mkdir(parents=True, exist_ok=True)
            dead_owner = {"pid": 9999999, "token": "dead-token", "created_at": 1000}
            (registry.lock_path / "owner.json").write_text(json.dumps(dead_owner), encoding="utf-8")

            with registry._lock(timeout_seconds=2.0):
                self.assertTrue(registry.lock_path.exists())

            self.assertFalse(registry.lock_path.exists())

    # 20. Lock malformed metadata fails closed without suppress
    def test_20_lock_malformed_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktrees_dir = Path(tmpdir) / "worktrees"
            registry = MissionRegistry(worktrees_dir=worktrees_dir)

            registry.lock_path.mkdir(parents=True, exist_ok=True)
            (registry.lock_path / "owner.json").write_text("{malformed json", encoding="utf-8")

            with (
                self.assertRaises(CorruptMissionRegistryError),
                registry._lock(timeout_seconds=0.5),
            ):
                pass

    # 21. Group dispatch creates worktrees
    def test_21_group_dispatch_creates_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_dispatch", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-dispatch",
                canonical_base_sha=head_sha,
                objective="Test dispatch",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            dispatched = coordinator.dispatch_execution_group("mission-dispatch", 0)

            self.assertIn("task_dispatch", dispatched)
            winfo = dispatched["task_dispatch"]
            self.assertTrue(winfo.worktree_path.exists())
            self.assertEqual(winfo.base_sha, head_sha)
            self.assertEqual(winfo.head_sha, head_sha)

            rec = coordinator.registry.get_mission("mission-dispatch")
            self.assertEqual(rec.task_states["task_dispatch"], TaskMissionState.DISPATCHED)
            self.assertEqual(rec.state, MissionState.RUNNING)

    # 22. Partial dispatch preserves already-dispatched ownership and marks mission BLOCKED
    def test_22_partial_dispatch_preserves_registered_and_marks_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_p1",
                head_sha,
                role="core_platform_engineer",
                target_branch="feat/p1",
                allowed_paths=["services/api/a.py"],
            )
            t2 = self._make_task(
                "task_p2",
                head_sha,
                role="core_platform_engineer",
                target_branch="feat/p2",
                allowed_paths=["services/api/b.py"],
            )
            mission = MissionContract(
                mission_id="mission-partial",
                canonical_base_sha=head_sha,
                objective="Test partial dispatch",
                tasks=[t1, t2],
            )
            coordinator.create_mission(mission)

            # Pre-create conflicting branch for task_p2 to cause dispatch_task failure on t2
            self._run_git(["branch", "feat/p2"], repo_dir)

            with self.assertRaises(MissionDispatchError):
                coordinator.dispatch_execution_group("mission-partial", 0)

            rec = coordinator.registry.get_mission("mission-partial")
            self.assertEqual(rec.state, MissionState.BLOCKED)
            # t1 was successfully dispatched and remains registered
            self.assertEqual(rec.task_states["task_p1"], TaskMissionState.DISPATCHED)
            # t2 was never dispatched and remains pending
            self.assertEqual(rec.task_states["task_p2"], TaskMissionState.PENDING)

    # 23. Task lifecycle: review before implementation rejected
    def test_23_task_lifecycle_review_before_implementation_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_rev_early", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-rev-early",
                canonical_base_sha=head_sha,
                objective="Test review early",
                tasks=[t1],
            )
            coordinator.create_mission(mission)

            # 1. From PENDING: must be rejected
            with self.assertRaises(MissionCoordinatorError):
                coordinator.record_task_review(
                    "mission-rev-early",
                    "task_rev_early",
                    "reviewer_security_engineer",
                    ReviewVerdict.APPROVE,
                    True,
                )

            # 2. From DISPATCHED: must be rejected
            coordinator.dispatch_execution_group("mission-rev-early", 0)
            with self.assertRaises(MissionCoordinatorError):
                coordinator.record_task_review(
                    "mission-rev-early",
                    "task_rev_early",
                    "reviewer_security_engineer",
                    ReviewVerdict.APPROVE,
                    True,
                )

    # 24. Task lifecycle: implementation state guards
    def test_24_task_lifecycle_implementation_state_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_impl_guard", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-impl-guard",
                canonical_base_sha=head_sha,
                objective="Test impl guards",
                tasks=[t1],
            )
            coordinator.create_mission(mission)

            # From PENDING: cannot record implemented
            with self.assertRaises(MissionCoordinatorError):
                coordinator.record_task_implemented(
                    "mission-impl-guard", "task_impl_guard", head_sha
                )

            coordinator.dispatch_execution_group("mission-impl-guard", 0)
            coordinator.record_task_implemented("mission-impl-guard", "task_impl_guard", head_sha)

            # From IMPLEMENTED: cannot record implemented again without CHANGES_REQUESTED
            with self.assertRaises(MissionCoordinatorError):
                coordinator.record_task_implemented(
                    "mission-impl-guard", "task_impl_guard", head_sha
                )

    # 25. Reviewer separation enforced
    def test_25_reviewer_separation_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_sep", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-sep",
                canonical_base_sha=head_sha,
                objective="Test reviewer separation",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-sep", 0)
            coordinator.record_task_implemented("mission-sep", "task_sep", head_sha)

            # Author reviewing own task must raise ReviewerSeparationError
            with self.assertRaises(ReviewerSeparationError):
                coordinator.record_task_review(
                    mission_id="mission-sep",
                    task_id="task_sep",
                    reviewer_role="core_platform_engineer",
                    verdict=ReviewVerdict.APPROVE,
                    test_passed=True,
                )

    # 26. Arbitrary reviewer role rejected
    def test_26_arbitrary_reviewer_role_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_bad_rev", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-bad-rev",
                canonical_base_sha=head_sha,
                objective="Test arbitrary reviewer",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-bad-rev", 0)
            coordinator.record_task_implemented("mission-bad-rev", "task_bad_rev", head_sha)

            with self.assertRaises(ValueError):
                coordinator.record_task_review(
                    mission_id="mission-bad-rev",
                    task_id="task_bad_rev",
                    reviewer_role="non_existent_role_foo",
                    verdict=ReviewVerdict.APPROVE,
                    test_passed=True,
                )

    # 27. Strict bool test_passed required
    def test_27_strict_bool_test_passed_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_strict_bool", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-strict-bool",
                canonical_base_sha=head_sha,
                objective="Test strict bool",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-strict-bool", 0)
            coordinator.record_task_implemented("mission-strict-bool", "task_strict_bool", head_sha)

            # String "true" must be rejected
            with self.assertRaises(TypeError):
                coordinator.record_task_review(
                    mission_id="mission-strict-bool",
                    task_id="task_strict_bool",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.APPROVE,
                    test_passed="true",  # type: ignore[arg-type]
                )

    # 28. Repair loop accounting via RepairLoopController
    def test_28_repair_loop_accounting_via_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_rep", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-rep",
                canonical_base_sha=head_sha,
                objective="Test repair accounting",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-rep", 0)

            for i in range(1, 6):
                coordinator.record_task_implemented("mission-rep", "task_rep", head_sha)
                state = coordinator.record_task_review(
                    mission_id="mission-rep",
                    task_id="task_rep",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.CHANGES_REQUIRED,
                    test_passed=False,
                    findings=[f"Repair finding {i}"],
                )
                self.assertEqual(state, TaskMissionState.CHANGES_REQUESTED)
                rec = coordinator.registry.get_mission("mission-rep")
                self.assertEqual(rec.task_records["task_rep"]["repair_count"], i)

    # 29. Repair attempt 6 blocked and escalated
    def test_29_repair_attempt_6_blocked_escalated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_ex", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-ex",
                canonical_base_sha=head_sha,
                objective="Test repair exhaustion",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-ex", 0)

            for _ in range(5):
                coordinator.record_task_implemented("mission-ex", "task_ex", head_sha)
                coordinator.record_task_review(
                    "mission-ex",
                    "task_ex",
                    "reviewer_security_engineer",
                    ReviewVerdict.CHANGES_REQUIRED,
                    False,
                )

            # 6th attempt must raise BudgetExhaustedError and mark task and mission BLOCKED
            coordinator.record_task_implemented("mission-ex", "task_ex", head_sha)
            with self.assertRaises(BudgetExhaustedError):
                coordinator.record_task_review(
                    "mission-ex",
                    "task_ex",
                    "reviewer_security_engineer",
                    ReviewVerdict.CHANGES_REQUIRED,
                    False,
                )

            rec = coordinator.registry.get_mission("mission-ex")
            self.assertEqual(rec.task_states["task_ex"], TaskMissionState.BLOCKED)
            self.assertEqual(rec.state, MissionState.BLOCKED)
            self.assertTrue(rec.task_records["task_ex"]["escalated_to_human"])

    # 30. Verify scope only allowed from APPROVED
    def test_30_verify_scope_only_from_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_scope_guard", head_sha, role="core_platform_engineer")
            mission = MissionContract(
                mission_id="mission-scope-guard",
                canonical_base_sha=head_sha,
                objective="Test scope guard",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-scope-guard", 0)

            # From DISPATCHED: fails
            with self.assertRaises(MissionCoordinatorError):
                coordinator.verify_task_scope(
                    "mission-scope-guard", "task_scope_guard", exact_head_sha=head_sha
                )

            # From IMPLEMENTED: fails
            coordinator.record_task_implemented("mission-scope-guard", "task_scope_guard", head_sha)
            with self.assertRaises(MissionCoordinatorError):
                coordinator.verify_task_scope(
                    "mission-scope-guard", "task_scope_guard", exact_head_sha=head_sha
                )

    # 31. Consolidation requires valid scope_verified_head_sha
    def test_31_consolidation_requires_verified_head_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_sha_check",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/test.md"],
            )
            mission = MissionContract(
                mission_id="mission-sha-check",
                canonical_base_sha=head_sha,
                objective="Test sha check",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-sha-check", 0)
            coordinator.record_task_implemented("mission-sha-check", "task_sha_check", head_sha)
            coordinator.record_task_review(
                "mission-sha-check",
                "task_sha_check",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-sha-check", "task_sha_check", head_sha)

            # Modify scope_verified_head_sha in mission record to an unverified SHA
            coordinator.registry.update_task_state(
                "mission-sha-check",
                "task_sha_check",
                TaskMissionState.SCOPE_VERIFIED,
                details={"scope_verified_head_sha": "a" * 40},
            )

            status = coordinator.check_consolidation_readiness("mission-sha-check")
            self.assertFalse(status.ready)
            self.assertTrue(
                any("head" in b.lower() and "match" in b.lower() for b in status.blockers)
            )

    # 32. Consolidation requires matching branch
    def test_32_consolidation_requires_matching_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_branch_check",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/test.md"],
            )
            mission = MissionContract(
                mission_id="mission-br-check",
                canonical_base_sha=head_sha,
                objective="Test branch check",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-br-check", 0)
            wpath = wtrees["task_branch_check"].worktree_path

            coordinator.record_task_implemented("mission-br-check", "task_branch_check", head_sha)
            coordinator.record_task_review(
                "mission-br-check",
                "task_branch_check",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-br-check", "task_branch_check", head_sha)

            # Change branch in worktree after verification
            self._run_git(["checkout", "-b", "feat/rogue-branch"], wpath)

            status = coordinator.check_consolidation_readiness("mission-br-check")
            self.assertFalse(status.ready)
            self.assertTrue(any("does not match target_branch" in b for b in status.blockers))

    # 33. Consolidation requires dispatcher scope_verified status
    def test_33_consolidation_requires_dispatcher_scope_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_disp_stat",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/test.md"],
            )
            mission = MissionContract(
                mission_id="mission-disp-stat",
                canonical_base_sha=head_sha,
                objective="Test dispatcher status",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-disp-stat", 0)
            coordinator.record_task_implemented("mission-disp-stat", "task_disp_stat", head_sha)
            coordinator.record_task_review(
                "mission-disp-stat",
                "task_disp_stat",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-disp-stat", "task_disp_stat", head_sha)

            # Roll back dispatcher registry entry status to active
            with coordinator.dispatcher._registry_lock():
                rdata = coordinator.dispatcher._read_registry_file()
                rdata["task_disp_stat"]["status"] = "active"
                rdata["task_disp_stat"]["scope_verified"] = False
                coordinator.dispatcher._write_registry_file(rdata)

            status = coordinator.check_consolidation_readiness("mission-disp-stat")
            self.assertFalse(status.ready)
            self.assertTrue(
                any("dispatcher registry status is 'active'" in b for b in status.blockers)
            )

    # 34. Consolidation requires matching dispatcher head
    def test_34_consolidation_requires_matching_dispatcher_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_disp_head",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/test.md"],
            )
            mission = MissionContract(
                mission_id="mission-disp-head",
                canonical_base_sha=head_sha,
                objective="Test dispatcher head",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-disp-head", 0)
            coordinator.record_task_implemented("mission-disp-head", "task_disp_head", head_sha)
            coordinator.record_task_review(
                "mission-disp-head",
                "task_disp_head",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-disp-head", "task_disp_head", head_sha)

            # Mismatch between dispatcher verified head and mission verified head
            with coordinator.dispatcher._registry_lock():
                rdata = coordinator.dispatcher._read_registry_file()
                rdata["task_disp_head"]["scope_verified_head_sha"] = (
                    "0000000000000000000000000000000000000000"
                )
                coordinator.dispatcher._write_registry_file(rdata)

            status = coordinator.check_consolidation_readiness("mission-disp-head")
            self.assertFalse(status.ready)
            self.assertTrue(any("dispatcher verified HEAD mismatch" in b for b in status.blockers))

    # 35. Consolidation requires matching contracts
    def test_35_consolidation_requires_matching_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_contract_match",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/test.md"],
            )
            mission = MissionContract(
                mission_id="mission-contract-match",
                canonical_base_sha=head_sha,
                objective="Test contract match",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-contract-match", 0)
            coordinator.record_task_implemented(
                "mission-contract-match", "task_contract_match", head_sha
            )
            coordinator.record_task_review(
                "mission-contract-match",
                "task_contract_match",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-contract-match", "task_contract_match", head_sha)

            # Tamper nested contract in dispatcher
            with coordinator.dispatcher._registry_lock():
                rdata = coordinator.dispatcher._read_registry_file()
                rdata["task_contract_match"]["contract"]["outcome"] = "Tampered outcome"
                coordinator.dispatcher._write_registry_file(rdata)

            status = coordinator.check_consolidation_readiness("mission-contract-match")
            self.assertFalse(status.ready)
            self.assertTrue(
                any(
                    "nested TaskContract in dispatcher registry does not match" in b
                    for b in status.blockers
                )
            )

    # 36. TOCTOU: live commit added after scope verification blocks consolidation
    def test_36_toctou_head_mutation_detected(self) -> None:
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
                objective="Test TOCTOU",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-toctou", 0)
            w_path = wtrees["task_toctou"].worktree_path

            (w_path / "docs" / "agent-factory").mkdir(parents=True, exist_ok=True)
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Doc 1\n", encoding="utf-8"
            )
            self._run_git(["add", "."], w_path)
            self._run_git(["commit", "-m", "docs: update"], w_path)
            res = self._run_git(["rev-parse", "HEAD"], w_path, text=True)
            committed_head = res.stdout.strip()

            coordinator.record_task_implemented("mission-toctou", "task_toctou", committed_head)
            coordinator.record_task_review(
                "mission-toctou",
                "task_toctou",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-toctou", "task_toctou", committed_head)

            # Rogue commit added to worktree after scope verification
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

    # 37. Dirty worktree prevents consolidation
    def test_37_dirty_worktree_prevents_consolidation(self) -> None:
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

            (w_path / "docs" / "agent-factory").mkdir(parents=True, exist_ok=True)
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Doc 1\n", encoding="utf-8"
            )
            self._run_git(["add", "."], w_path)
            self._run_git(["commit", "-m", "docs: update"], w_path)
            res = self._run_git(["rev-parse", "HEAD"], w_path, text=True)
            committed_head = res.stdout.strip()

            coordinator.record_task_implemented("mission-dirty", "task_dirty", committed_head)
            coordinator.record_task_review(
                "mission-dirty",
                "task_dirty",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-dirty", "task_dirty", committed_head)

            # Add uncommitted modification
            (w_path / "docs" / "agent-factory" / "DOC1.md").write_text(
                "# Uncommitted\n", encoding="utf-8"
            )

            status = coordinator.check_consolidation_readiness("mission-dirty")
            self.assertFalse(status.ready)
            self.assertTrue(any("uncommitted/dirty changes" in b for b in status.blockers))

    # 38. All tasks valid returns READY_FOR_CONSOLIDATION
    def test_38_all_tasks_valid_ready_for_consolidation(self) -> None:
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
                (wpath / "docs" / "agent-factory").mkdir(parents=True, exist_ok=True)
                (wpath / "docs" / "agent-factory" / doc_name).write_text(
                    f"# {doc_name}\n", encoding="utf-8"
                )
                self._run_git(["add", "."], wpath)
                self._run_git(["commit", "-m", f"docs: update {doc_name}"], wpath)
                res = self._run_git(["rev-parse", "HEAD"], wpath, text=True)
                c_head = res.stdout.strip()

                coordinator.record_task_implemented("mission-clean", tid, c_head)
                coordinator.record_task_review(
                    "mission-clean", tid, "reviewer_security_engineer", ReviewVerdict.APPROVE, True
                )
                coordinator.verify_task_scope("mission-clean", tid, c_head)

            status = coordinator.check_consolidation_readiness("mission-clean")
            self.assertTrue(status.ready)
            self.assertEqual(status.status, "READY_FOR_CONSOLIDATION")
            self.assertEqual(status.blockers, [])

            rec = coordinator.registry.get_mission("mission-clean")
            self.assertEqual(rec.state, MissionState.READY_FOR_CONSOLIDATION)

    # 39. Cleanup of unresolved clean task is rejected
    def test_39_cleanup_unresolved_clean_task_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_unres",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-unres",
                canonical_base_sha=head_sha,
                objective="Test unresolved cleanup rejection",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-unres", 0)

            # In DISPATCHED: cleanup must raise UnresolvedTaskCleanupError
            with self.assertRaises(UnresolvedTaskCleanupError):
                coordinator.cleanup_mission("mission-unres")

            # In IMPLEMENTED: cleanup must raise UnresolvedTaskCleanupError
            coordinator.record_task_implemented("mission-unres", "task_unres", head_sha)
            with self.assertRaises(UnresolvedTaskCleanupError):
                coordinator.cleanup_mission("mission-unres")

            # In CHANGES_REQUESTED: cleanup must raise UnresolvedTaskCleanupError
            coordinator.record_task_review(
                "mission-unres",
                "task_unres",
                "reviewer_security_engineer",
                ReviewVerdict.CHANGES_REQUIRED,
                False,
            )
            with self.assertRaises(UnresolvedTaskCleanupError):
                coordinator.cleanup_mission("mission-unres")

            # In APPROVED (but unverified): cleanup must raise UnresolvedTaskCleanupError
            coordinator.record_task_implemented("mission-unres", "task_unres", head_sha)
            coordinator.record_task_review(
                "mission-unres",
                "task_unres",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            with self.assertRaises(UnresolvedTaskCleanupError):
                coordinator.cleanup_mission("mission-unres")

    # 40. Cleanup of dirty task is rejected
    def test_40_cleanup_dirty_task_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_dirty_clean",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-dirty-clean",
                canonical_base_sha=head_sha,
                objective="Test dirty task cleanup rejection",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            wtrees = coordinator.dispatch_execution_group("mission-dirty-clean", 0)
            wpath = wtrees["task_dirty_clean"].worktree_path

            coordinator.record_task_implemented("mission-dirty-clean", "task_dirty_clean", head_sha)
            coordinator.record_task_review(
                "mission-dirty-clean",
                "task_dirty_clean",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-dirty-clean", "task_dirty_clean", head_sha)

            # Make dirty
            (wpath / "docs" / "agent-factory").mkdir(parents=True, exist_ok=True)
            (wpath / "docs" / "agent-factory" / "DOC1.md").write_text("# Dirty\n", encoding="utf-8")

            with self.assertRaises(DirtyWorktreeError):
                coordinator.cleanup_mission("mission-dirty-clean")

    # 41. Cleanup of scope_verified clean task succeeds
    def test_41_cleanup_scope_verified_clean_task_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task(
                "task_ok_clean",
                head_sha,
                role="engineering_manager",
                allowed_paths=["docs/agent-factory/DOC1.md"],
            )
            mission = MissionContract(
                mission_id="mission-ok-clean",
                canonical_base_sha=head_sha,
                objective="Test clean release",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-ok-clean", 0)
            coordinator.record_task_implemented("mission-ok-clean", "task_ok_clean", head_sha)
            coordinator.record_task_review(
                "mission-ok-clean",
                "task_ok_clean",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-ok-clean", "task_ok_clean", head_sha)

            cleaned = coordinator.cleanup_mission("mission-ok-clean")
            self.assertEqual(cleaned, ["task_ok_clean"])
            self.assertIsNone(coordinator.dispatcher.get_active_task("task_ok_clean"))

    # 42. Duplicate mission does not poison coordinator in-memory cache (Finding 1)
    def test_42_duplicate_mission_does_not_poison_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_orig", head_sha, role="core_platform_engineer")
            orig_mission = MissionContract(
                mission_id="mission-cache-guard",
                canonical_base_sha=head_sha,
                objective="Original objective",
                tasks=[t1],
            )
            coordinator.create_mission(orig_mission)

            # Attempt to register duplicate with different task/objective
            t_fake = self._make_task(
                "task_fake", head_sha, role="qa_sdet_engineer", allowed_paths=["tests/test_fake.py"]
            )
            fake_mission = MissionContract(
                mission_id="mission-cache-guard",
                canonical_base_sha=head_sha,
                objective="Attacker modified objective",
                tasks=[t_fake],
            )
            with self.assertRaises(DuplicateMissionIdError):
                coordinator.create_mission(fake_mission)

            # Cache and contract retrieval must retain original
            loaded = coordinator._get_mission_contract("mission-cache-guard")
            self.assertEqual(loaded.objective, "Original objective")
            self.assertEqual([t.task_id for t in loaded.tasks], ["task_orig"])

    # 43. Post-review re-commit attack detected during scope verify and consolidation (Finding 2)
    def test_43_post_review_recommit_attack_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)
            registry = MissionRegistry(worktrees_dir=worktrees_dir)
            coordinator = MissionCoordinator(dispatcher=dispatcher, registry=registry)

            t1 = self._make_task(
                "task_attack",
                head_sha,
                role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/app.py"],
            )
            mission = MissionContract(
                mission_id="mission-attack",
                canonical_base_sha=head_sha,
                objective="Test re-commit attack",
                tasks=[t1],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission-attack", 0)

            active = dispatcher.get_active_task("task_attack")
            if active is None:
                self.fail("Expected active task_attack")
            _, winfo = active

            # 1. Implement legitimate change
            app_file = winfo.worktree_path / "services" / "api" / "src" / "tapjob_api" / "app.py"
            app_file.parent.mkdir(parents=True, exist_ok=True)
            app_file.write_text("# legitimate implementation\n", encoding="utf-8")
            self._run_git(["add", "services/api/src/tapjob_api/app.py"], winfo.worktree_path)
            self._run_git(["commit", "-m", "feat: legitimate code"], winfo.worktree_path)
            res = self._run_git(["rev-parse", "HEAD"], winfo.worktree_path, text=True)
            reviewed_sha = res.stdout.strip()

            coordinator.record_task_implemented("mission-attack", "task_attack", reviewed_sha)
            coordinator.record_task_review(
                "mission-attack",
                "task_attack",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )

            # 2. Attacker adds sneaky commit between APPROVE and verify_task_scope
            rogue_file = (
                winfo.worktree_path / "services" / "api" / "src" / "tapjob_api" / "rogue.py"
            )
            rogue_file.write_text("# sneaky unauthorized commit\n", encoding="utf-8")
            self._run_git(["add", "services/api/src/tapjob_api/rogue.py"], winfo.worktree_path)
            self._run_git(["commit", "-m", "fix: sneaking after approve"], winfo.worktree_path)

            # verify_task_scope must detect the live HEAD != reviewed_head_sha
            with self.assertRaises(ScopeViolationError):
                coordinator.verify_task_scope("mission-attack", "task_attack", reviewed_sha)

            # Revert sneaky commit back to reviewed_sha
            self._run_git(["reset", "--hard", reviewed_sha], winfo.worktree_path)
            # Now verify_task_scope passes
            coordinator.verify_task_scope("mission-attack", "task_attack", reviewed_sha)

            # 3. Sneaky commit added after verify_task_scope must block consolidation
            rogue_file.write_text("# another sneaky commit\n", encoding="utf-8")
            self._run_git(["add", "services/api/src/tapjob_api/rogue.py"], winfo.worktree_path)
            self._run_git(["commit", "-m", "fix: sneaky after scope verify"], winfo.worktree_path)
            c_status = coordinator.check_consolidation_readiness("mission-attack")
            self.assertFalse(c_status.ready)
            self.assertTrue(any("HEAD mismatch across lifecycle" in b for b in c_status.blockers))

    # 44. DAG ordering runtime enforcement & get_next_eligible_group (Finding 3)
    def test_44_dag_ordering_runtime_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, head_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            t1 = self._make_task("task_dag_1", head_sha, role="core_platform_engineer")
            t2 = self._make_task(
                "task_dag_2",
                head_sha,
                role="channel_whatsapp_engineer",
                allowed_paths=["services/api/src/tapjob_api/messaging/router.py"],
                dependencies=["task_dag_1"],
            )
            mission = MissionContract(
                mission_id="mission-dag",
                canonical_base_sha=head_sha,
                objective="Test DAG runtime dispatch",
                tasks=[t1, t2],
            )
            coordinator.create_mission(mission)

            # Next eligible group must be 0
            self.assertEqual(coordinator.get_next_eligible_group("mission-dag"), 0)

            # Attempting to dispatch group 1 directly must fail closed
            with self.assertRaises(MissionDispatchError):
                coordinator.dispatch_execution_group("mission-dag", 1)

            # Dispatch group 0
            coordinator.dispatch_execution_group("mission-dag", 0)
            # While group 0 is in-flight, next eligible group is None
            self.assertIsNone(coordinator.get_next_eligible_group("mission-dag"))

            # Complete task 1
            coordinator.record_task_implemented("mission-dag", "task_dag_1", head_sha)
            coordinator.record_task_review(
                "mission-dag",
                "task_dag_1",
                "reviewer_security_engineer",
                ReviewVerdict.APPROVE,
                True,
            )
            coordinator.verify_task_scope("mission-dag", "task_dag_1", head_sha)

            # Now next eligible group is 1
            self.assertEqual(coordinator.get_next_eligible_group("mission-dag"), 1)
            # Dispatch group 1 succeeds
            dispatched = coordinator.dispatch_execution_group("mission-dag", 1)
            self.assertIn("task_dag_2", dispatched)

    # 45. Real OS-backed mutual exclusion, process death recovery, and fail-closed lock tests
    def test_45_os_backed_concurrent_lock(self) -> None:
        """Finding 1 (Round 3): Verify OS-backed kernel lock properties and concurrency."""
        import subprocess
        import sys
        import time

        from tools.agent_factory.lock import (
            CorruptLockMetadataError,
            DirectoryLock,
            ForeignLockError,
            LockTimeoutError,
            _is_pid_alive,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".test_lock"
            lock1 = DirectoryLock(lock_path=lock_path, timeout_seconds=2.0)

            # 1. Live lock cannot be stolen
            with lock1.acquire(timeout_seconds=2.0):
                self.assertTrue(lock1.is_locked)
                lock2 = DirectoryLock(lock_path=lock_path, timeout_seconds=0.2)
                with self.assertRaises(LockTimeoutError):
                    lock2.acquire_lock()
                # lock1 still owns the lock intact
                self.assertTrue(lock1.is_locked)

            # 2. Inspection failure cannot classify owner as dead (fail closed)
            # Specifically test _is_pid_alive fail-closed behavior
            if sys.platform == "win32":
                import ctypes

                # Access Denied (5): fail closed -> alive (True)
                with (
                    mock.patch.object(ctypes.windll.kernel32, "OpenProcess", return_value=0),
                    mock.patch.object(ctypes.windll.kernel32, "GetLastError", return_value=5),
                ):
                    self.assertTrue(_is_pid_alive(12345))

                # Nonexistent process (87): positively dead -> (False)
                with (
                    mock.patch.object(ctypes.windll.kernel32, "OpenProcess", return_value=0),
                    mock.patch.object(ctypes.windll.kernel32, "GetLastError", return_value=87),
                ):
                    self.assertFalse(_is_pid_alive(12345))

                # Any unhandled inspection error fails closed as alive
                with mock.patch.object(
                    ctypes.windll.kernel32,
                    "OpenProcess",
                    side_effect=RuntimeError("ctypes boom"),
                ):
                    self.assertTrue(_is_pid_alive(12345))
            else:
                import errno

                # EPERM means process exists but we lack permission -> ALIVE
                eperm_err = OSError()
                eperm_err.errno = errno.EPERM
                with mock.patch("os.kill", side_effect=eperm_err):
                    self.assertTrue(_is_pid_alive(12345))

                # ESRCH means no such process -> DEAD
                esrch_err = OSError()
                esrch_err.errno = errno.ESRCH
                with mock.patch("os.kill", side_effect=esrch_err):
                    self.assertFalse(_is_pid_alive(12345))

            # 4. Two processes contend for same lock; simultaneous ownership impossible
            # 5. Process death releases lock safely
            code = (
                "import time, sys\n"
                "from pathlib import Path\n"
                "from tools.agent_factory.lock import DirectoryLock\n"
                f"l = DirectoryLock(Path(r'{lock_path}'), timeout_seconds=5.0)\n"
                "l.acquire_lock()\n"
                "sys.stdout.write('ACQUIRED\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(1.0)\n"
                "# Abrupt exit simulating crash\n"
                "import os; os._exit(0)\n"
            )
            proc = subprocess.Popen(  # noqa: S603
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(Path.cwd()),
            )
            # Wait for child to acquire
            line = proc.stdout.readline() if proc.stdout else ""
            self.assertEqual(line.strip(), "ACQUIRED")

            # While child holds the lock, second lock cannot acquire
            lock_contender = DirectoryLock(lock_path=lock_path, timeout_seconds=0.2)
            with self.assertRaises(LockTimeoutError):
                lock_contender.acquire_lock()

            # Wait for child to exit abruptly (crash)
            proc.wait(timeout=5)

            # 6. Process death automatically freed kernel lock: contender can now acquire
            lock_after_crash = DirectoryLock(lock_path=lock_path, timeout_seconds=2.0)
            with lock_after_crash.acquire(timeout_seconds=2.0):
                self.assertTrue(lock_after_crash.is_locked)

            # 7. Token/ownership mismatch on release fails closed
            lock_tamper = DirectoryLock(lock_path=lock_path, timeout_seconds=1.0)
            with lock_tamper.acquire(timeout_seconds=1.0):
                # Tamper with owner.json to simulate foreign process
                tampered_owner = {"pid": 99999, "token": "foreign-tok", "created_at": time.time()}
                lock_tamper.owner_file.write_text(json.dumps(tampered_owner), encoding="utf-8")
                with self.assertRaises(ForeignLockError):
                    lock_tamper.release()

            # 8. Malformed metadata fails closed if metadata remains relevant
            lock_path.mkdir(parents=True, exist_ok=True)
            (lock_path / "owner.json").write_text("{malformed json", encoding="utf-8")
            lock_malformed = DirectoryLock(lock_path=lock_path, timeout_seconds=0.5)
            with self.assertRaises(CorruptLockMetadataError):
                lock_malformed.acquire_lock()

    # 46. Strict registry schema & repair state semantic binding (Finding 5)
    def test_46_strict_registry_repair_state_schema(self) -> None:
        contract_dict = self._make_task("t1", "0123456789abcdef0123456789abcdef01234567").to_dict()

        base_data: dict[str, Any] = {
            "mission_id": "m-strict",
            "base_sha": "0123456789abcdef0123456789abcdef01234567",
            "objective": "Strict validation test",
            "human_gate_required": True,
            "state": "running",
            "task_ids": ["t1"],
            "execution_groups": [["t1"]],
            "task_states": {"t1": "dispatched"},
            "task_records": {
                "t1": {
                    "owner_role": "core_platform_engineer",
                    "target_branch": "feat/t1",
                    "repair_count": 0,
                    "repair_records": [],
                    "contract": contract_dict,
                }
            },
        }

        # 1. Valid record parses cleanly
        rec = MissionRegistryRecord.from_dict(base_data)
        self.assertEqual(rec.mission_id, "m-strict")

        # 2. execution_groups = ["t1"] (flat list instead of list[list[str]])
        # raises CorruptMissionRegistryError
        bad_groups = dict(base_data, execution_groups=["t1"])
        with self.assertRaises(CorruptMissionRegistryError):
            MissionRegistryRecord.from_dict(bad_groups)

        # 3. Non-contiguous iteration_index in repair_records raises
        bad_repairs = dict(base_data)
        bad_repairs["task_records"] = {
            "t1": {
                "owner_role": "core_platform_engineer",
                "target_branch": "feat/t1",
                "repair_count": 1,
                "contract": contract_dict,
                "repair_records": [
                    {
                        "iteration_index": 5,  # non-contiguous (expected 0)
                        "implementer_role": "core_platform_engineer",
                        "reviewer_role": "reviewer_security_engineer",
                        "test_passed": False,
                        "verdict": "CHANGES_REQUIRED",
                        "findings": ["finding"],
                    }
                ],
            }
        }
        with self.assertRaises(CorruptMissionRegistryError):
            MissionRegistryRecord.from_dict(bad_repairs)

        # 4. Implementer role != owner_role in repair_records raises
        bad_impl = dict(base_data)
        bad_impl["task_records"] = {
            "t1": {
                "owner_role": "core_platform_engineer",
                "target_branch": "feat/t1",
                "repair_count": 1,
                "contract": contract_dict,
                "repair_records": [
                    {
                        "iteration_index": 0,
                        "implementer_role": "wrong_role",
                        "reviewer_role": "reviewer_security_engineer",
                        "test_passed": False,
                        "verdict": "CHANGES_REQUIRED",
                        "findings": ["finding"],
                    }
                ],
            }
        }
        with self.assertRaises(CorruptMissionRegistryError):
            MissionRegistryRecord.from_dict(bad_impl)

        # 5. Reviewer role == owner_role raises
        bad_rev = dict(base_data)
        bad_rev["task_records"] = {
            "t1": {
                "owner_role": "core_platform_engineer",
                "target_branch": "feat/t1",
                "repair_count": 1,
                "contract": contract_dict,
                "repair_records": [
                    {
                        "iteration_index": 0,
                        "implementer_role": "core_platform_engineer",
                        "reviewer_role": "core_platform_engineer",  # self-review
                        "test_passed": False,
                        "verdict": "CHANGES_REQUIRED",
                        "findings": ["finding"],
                    }
                ],
            }
        }
        with self.assertRaises(CorruptMissionRegistryError):
            MissionRegistryRecord.from_dict(bad_rev)

        # 6. Semantic binding: CHANGES_REQUESTED with APPROVE verdict raises
        bad_semantic = dict(base_data)
        bad_semantic["task_states"] = {"t1": "changes_requested"}
        bad_semantic["task_records"] = {
            "t1": {
                "owner_role": "core_platform_engineer",
                "target_branch": "feat/t1",
                "repair_count": 1,
                "contract": contract_dict,
                "repair_records": [
                    {
                        "iteration_index": 0,
                        "implementer_role": "core_platform_engineer",
                        "reviewer_role": "reviewer_security_engineer",
                        "test_passed": True,
                        "verdict": "APPROVE",  # mismatch with changes_requested
                        "findings": [],
                    }
                ],
            }
        }
        with self.assertRaises(CorruptMissionRegistryError):
            MissionRegistryRecord.from_dict(bad_semantic)

    # 47. Emergency purge method removed from Coordinator and guarded in agent_guard (Finding 6)
    def test_47_no_emergency_purge_in_coordinator_and_guard_pattern(self) -> None:
        from tools.agent_guard.pre_tool_guard import FORCE_ASK_PATTERNS

        # 1. MissionCoordinator has NO emergency_purge_unverified_task method
        self.assertFalse(hasattr(MissionCoordinator, "emergency_purge_unverified_task"))

        # 2. pre_tool_guard blocks emergency_purge / manual_recovery scripts
        # under autonomous execution
        blocked_commands = [
            "python tools/agent_factory/manual_recovery.py",
            "uv run python scripts/emergency_purge.py --confirm",
            "destructive_purge_tasks",
            "purge_unverified_worktree",
        ]
        for cmd in blocked_commands:
            matched = any(pattern.search(cmd) for pattern, _ in FORCE_ASK_PATTERNS)
            self.assertTrue(matched, f"Command '{cmd}' should have matched FORCE_ASK_PATTERNS")

    # 48. Immutable repair iteration HEAD history (Finding 2)
    def test_48_repair_history_heads_immutable(self) -> None:
        """Finding 2 (Round 3): Verify repair history preserves immutable iteration_head_sha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, base_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            coordinator = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )

            task = self._make_task("task_repair_history", base_sha)
            mission = MissionContract(
                mission_id="mission_repair_history",
                canonical_base_sha=base_sha,
                objective="Test immutable repair history",
                human_gate_required=True,
                tasks=[task],
            )
            coordinator.create_mission(mission)
            coordinator.dispatch_execution_group("mission_repair_history", 0)

            # Iteration 0: implementer creates HEAD A
            active = coordinator.dispatcher.get_active_task("task_repair_history")
            self.assertIsNotNone(active)
            _, winfo = active  # type: ignore[misc]
            wpath = winfo.worktree_path

            # Allowed file change
            test_file = wpath / "services" / "api" / "src" / "tapjob_api" / "app.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("# version 1\n", encoding="utf-8")
            self._run_git(["add", "."], wpath)
            self._run_git(["commit", "-m", "commit 1"], wpath)
            head_a = coordinator.dispatcher.get_worktree_head(wpath)

            coordinator.record_task_implemented(
                "mission_repair_history", "task_repair_history", head_a
            )

            # Review 0: CHANGES_REQUESTED
            coordinator.record_task_review(
                mission_id="mission_repair_history",
                task_id="task_repair_history",
                reviewer_role="reviewer_security_engineer",
                verdict=ReviewVerdict.CHANGES_REQUIRED,
                test_passed=False,
                findings=["fix version 1"],
            )

            # Iteration 1: implementer repairs and creates HEAD B
            test_file.write_text("# version 2 - repaired\n", encoding="utf-8")
            self._run_git(["add", "."], wpath)
            self._run_git(["commit", "-m", "commit 2"], wpath)
            head_b = coordinator.dispatcher.get_worktree_head(wpath)
            self.assertNotEqual(head_a, head_b)

            coordinator.record_task_implemented(
                "mission_repair_history", "task_repair_history", head_b
            )

            # Review 1: APPROVE
            coordinator.record_task_review(
                mission_id="mission_repair_history",
                task_id="task_repair_history",
                reviewer_role="reviewer_security_engineer",
                verdict=ReviewVerdict.APPROVE,
                test_passed=True,
                findings=[],
            )

            # Reload record freshly from disk
            disk_rec = coordinator.registry.get_mission("mission_repair_history")
            task_rec = disk_rec.task_records["task_repair_history"]
            repair_records = task_rec["repair_records"]
            self.assertEqual(len(repair_records), 2)

            # Assert immutable exact HEAD per iteration
            self.assertEqual(repair_records[0]["iteration_head_sha"], head_a)
            self.assertEqual(repair_records[1]["iteration_head_sha"], head_b)
            self.assertEqual(task_rec["reviewed_head_sha"], head_b)
            self.assertEqual(task_rec["implemented_head_sha"], head_b)

            # Scope verification and consolidation must use head_b
            coordinator.verify_task_scope("mission_repair_history", "task_repair_history", head_b)
            status = coordinator.check_consolidation_readiness("mission_repair_history")
            self.assertTrue(status.ready)
            self.assertEqual(status.status, "READY_FOR_CONSOLIDATION")

    # 49. Atomic state transitions and stale writer protection (Finding 3)
    def test_49_atomic_state_transitions_and_stale_writer_protection(self) -> None:
        """Finding 3 (Round 3): Concurrent reviewers and stale writers fail closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir, base_sha = self._create_temp_git_repo(root)
            worktrees_dir = root / "worktrees"

            # Create coordinator A and coordinator B pointing to the SAME shared worktrees/registry
            coord_a = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )
            coord_b = MissionCoordinator(
                dispatcher=WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir),
                registry=MissionRegistry(worktrees_dir=worktrees_dir),
            )

            task = self._make_task("t_atomic", base_sha)
            mission = MissionContract(
                mission_id="m_atomic",
                canonical_base_sha=base_sha,
                objective="Test atomic transitions",
                human_gate_required=True,
                tasks=[task],
            )
            coord_a.create_mission(mission)
            coord_a.dispatch_execution_group("m_atomic", 0)

            active = coord_a.dispatcher.get_active_task("t_atomic")
            self.assertIsNotNone(active)
            _, winfo = active  # type: ignore[misc]
            wpath = winfo.worktree_path

            test_file = wpath / "services" / "api" / "src" / "tapjob_api" / "app.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("# atomic content\n", encoding="utf-8")
            self._run_git(["add", "."], wpath)
            self._run_git(["commit", "-m", "impl commit"], wpath)
            head = coord_a.dispatcher.get_worktree_head(wpath)

            coord_a.record_task_implemented("m_atomic", "t_atomic", head)

            # Both instances observe IMPLEMENTED
            rec_a = coord_a.registry.get_mission("m_atomic")
            self.assertEqual(rec_a.task_states["t_atomic"], TaskMissionState.IMPLEMENTED)

            # Reviewer A submits CHANGES_REQUIRED
            coord_a.record_task_review(
                mission_id="m_atomic",
                task_id="t_atomic",
                reviewer_role="reviewer_security_engineer",
                verdict=ReviewVerdict.CHANGES_REQUIRED,
                test_passed=False,
                findings=["changes needed"],
            )

            # State is now CHANGES_REQUESTED
            rec_now = coord_a.registry.get_mission("m_atomic")
            self.assertEqual(rec_now.task_states["t_atomic"], TaskMissionState.CHANGES_REQUESTED)

            # Reviewer B now tries to submit APPROVE with stale state (believing it is IMPLEMENTED)
            from tools.agent_factory.mission_registry import InvalidStateTransitionError

            with self.assertRaises(InvalidStateTransitionError):
                coord_b.record_task_review(
                    mission_id="m_atomic",
                    task_id="t_atomic",
                    reviewer_role="reviewer_security_engineer",
                    verdict=ReviewVerdict.APPROVE,
                    test_passed=True,
                    findings=[],
                )

            # Verify stale second transition did NOT corrupt state
            rec_check = coord_a.registry.get_mission("m_atomic")
            self.assertEqual(rec_check.task_states["t_atomic"], TaskMissionState.CHANGES_REQUESTED)

            # Stale IMPLEMENTED write cannot overwrite APPROVED
            # Let's transition properly to IMPLEMENTED and then APPROVED
            test_file.write_text("# atomic content v2\n", encoding="utf-8")
            self._run_git(["add", "."], wpath)
            self._run_git(["commit", "-m", "fix commit"], wpath)
            head2 = coord_a.dispatcher.get_worktree_head(wpath)
            coord_a.record_task_implemented("m_atomic", "t_atomic", head2)
            coord_a.record_task_review(
                mission_id="m_atomic",
                task_id="t_atomic",
                reviewer_role="reviewer_security_engineer",
                verdict=ReviewVerdict.APPROVE,
                test_passed=True,
                findings=[],
            )

            # Now task is APPROVED.
            # Attempting record_task_implemented raises InvalidStateTransitionError
            with self.assertRaises(InvalidStateTransitionError):
                coord_b.record_task_implemented("m_atomic", "t_atomic", head2)

            # Stale scope verify cannot overwrite FAILED/BLOCKED
            coord_a.registry.transition_task_state(
                mission_id="m_atomic",
                task_id="t_atomic",
                expected_states={TaskMissionState.APPROVED},
                new_state=TaskMissionState.FAILED,
                details={"scope_violations": ["manual fail"]},
            )
            with self.assertRaises(InvalidStateTransitionError):
                coord_b.verify_task_scope("m_atomic", "t_atomic", head2)

            # BLOCKED mission cannot be returned to RUNNING by stale writer
            coord_a.registry.transition_mission_state(
                mission_id="m_atomic",
                expected_states={MissionState.RUNNING, MissionState.REPAIRING},
                new_state=MissionState.BLOCKED,
            )
            with self.assertRaises(InvalidStateTransitionError):
                coord_b.registry.transition_mission_state(
                    mission_id="m_atomic",
                    expected_states={MissionState.RUNNING, MissionState.REPAIRING},
                    new_state=MissionState.RUNNING,
                )


if __name__ == "__main__":
    unittest.main()

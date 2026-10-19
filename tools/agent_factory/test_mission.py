"""Comprehensive synthetic tests for TapJob Agent Factory AF-2 parallel mission execution."""

from __future__ import annotations

import json
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
                coordinator.verify_task_scope("mission-scope-guard", "task_scope_guard")

            # From IMPLEMENTED: fails
            coordinator.record_task_implemented("mission-scope-guard", "task_scope_guard", head_sha)
            with self.assertRaises(MissionCoordinatorError):
                coordinator.verify_task_scope("mission-scope-guard", "task_scope_guard")

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


if __name__ == "__main__":
    unittest.main()

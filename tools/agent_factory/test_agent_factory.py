"""Comprehensive unit and smoke test suite for TapJob Agent Factory (AF-1)."""

from __future__ import annotations

import inspect
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

from tools.agent_factory.bootstrap import main as bootstrap_main
from tools.agent_factory.repair_loop import (
    MAX_INTERNAL_REPAIR_LOOPS,
    BudgetExhaustedError,
    RepairLoopController,
    RepairPhase,
    ReviewerSeparationError,
    ReviewVerdict,
)
from tools.agent_factory.task_contract import CRITICAL_SERIAL_PATHS, TaskContract
from tools.agent_factory.team import (
    BASE_TEAM_ROLES,
    DYNAMIC_SPECIALIST_ROLES,
    AgentProfile,
    load_all_profiles,
    load_profile,
)
from tools.agent_factory.worktree_dispatcher import (
    DirtyWorktreeError,
    HumanGateRequiredError,
    InvalidBranchNameError,
    OwnershipCollisionError,
    ScopeViolationError,
    WorktreeDispatcher,
    WorktreeDispatchError,
)

SYNTHETIC_SHA = "0123456789abcdef0123456789abcdef01234567"


class TestAgentFactory(unittest.TestCase):
    """Test suite covering team profiles, task contracts, dispatchers, and repair loops."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.git_bin = shutil.which("git") or "git"

    @classmethod
    def _create_temp_git_repo(cls, root_dir: Path) -> tuple[Path, str]:
        """Create a synthetic, completely deterministic local git repository."""
        repo_dir = root_dir / "synthetic-repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603
            [cls.git_bin, "init", "-b", "main"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(  # noqa: S603
            [cls.git_bin, "config", "user.email", "agent@tapjob.internal"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(  # noqa: S603
            [cls.git_bin, "config", "user.name", "Agent Factory Test"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        (repo_dir / "README.md").write_text("# Synthetic TapJob Repository\n", encoding="utf-8")
        api_dir = repo_dir / "services" / "api" / "src" / "tapjob_api" / "jobs"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "service.py").write_text("# service\n", encoding="utf-8")
        subprocess.run(  # noqa: S603
            [cls.git_bin, "add", "."],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(  # noqa: S603
            [cls.git_bin, "commit", "-m", "initial commit"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        rev = subprocess.run(  # noqa: S603
            [cls.git_bin, "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return repo_dir, rev.stdout.strip()

    # 1. Manager definition carregável pelo runtime
    def test_01_manager_definition_loadable(self) -> None:
        """Requirement 1: Engineering Manager profile is loadable and valid."""
        manager = load_profile("engineering_manager")
        self.assertEqual(manager.role_id, "engineering_manager")
        self.assertEqual(manager.category, "base_team")
        self.assertTrue(manager.enable_write_tools)
        self.assertTrue(manager.enable_subagent_tools)
        self.assertFalse(manager.enable_mcp_tools)
        payload = manager.to_define_subagent_payload()
        self.assertEqual(payload["name"], "engineering_manager")
        self.assertIn("ricardors081/tapjob", payload["system_prompt"])
        agent_md = manager.to_agent_md()
        self.assertIn("name: engineering_manager", agent_md)

    # 2. Todos os 14 perfis carregáveis e válidos
    def test_02_all_profiles_loadable(self) -> None:
        """Requirement 2: All 14 agent profiles (6 base + 8 dynamic) are loadable and valid."""
        profiles = load_all_profiles()
        self.assertEqual(len(profiles), 14)
        for base_role in BASE_TEAM_ROLES:
            self.assertIn(base_role, profiles)
            self.assertEqual(profiles[base_role].category, "base_team")
        for dyn_role in DYNAMIC_SPECIALIST_ROLES:
            self.assertIn(dyn_role, profiles)
            self.assertEqual(profiles[dyn_role].category, "dynamic_specialist")

        # Verify reviewer security engineer is strictly read-only
        reviewer = profiles["reviewer_security_engineer"]
        self.assertFalse(reviewer.enable_write_tools)
        self.assertFalse(reviewer.enable_subagent_tools)

        # Verify deep security specialist is strictly read-only
        deep_sec = profiles["deep_security_specialist"]
        self.assertFalse(deep_sec.enable_write_tools)

    # 3. Task contract válido e serializável
    def test_03_task_contract_valid(self) -> None:
        """Requirement 3: TaskContract creation, validation, and serialization."""
        contract = TaskContract(
            task_id="TASK-WP2.4-001",
            outcome="Implement candidate search filters",
            scope=["Add salary range and remote filter"],
            non_goals=["Do not implement billing"],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="core_platform_engineer",
            allowed_paths=["services/api/src/tapjob_api/jobs/"],
            forbidden_paths=["services/api/alembic/versions/"],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["Unit tests in services/api/tests/"],
            acceptance_criteria=["Filter returns correct jobs"],
            risk_level="MEDIUM",
            parallel_safe=True,
            human_gate_required=False,
        )
        contract.validate()
        json_repr = contract.to_json()
        reconstituted = TaskContract.from_json(json_repr)
        self.assertEqual(reconstituted.task_id, "TASK-WP2.4-001")
        self.assertEqual(reconstituted.canonical_base_sha, SYNTHETIC_SHA)
        self.assertTrue(reconstituted.target_branch.startswith("feat/task-wp2.4-001"))

    # 4. Strict Boolean Schema & Type Rejection (Finding 2)
    def test_04_strict_boolean_schema_rejection(self) -> None:
        """Finding 2: TaskContract and AgentProfile reject string booleans and non-lists."""
        # TaskContract rejects string boolean for parallel_safe
        base_data = {
            "task_id": "TASK-BOOL-001",
            "outcome": "test",
            "scope": ["test"],
            "non_goals": [],
            "canonical_base_sha": SYNTHETIC_SHA,
            "owner_role": "core_platform_engineer",
            "allowed_paths": ["services/api/"],
            "forbidden_paths": [],
            "dependencies": [],
            "contracts_frozen": True,
            "test_requirements": ["tests"],
            "acceptance_criteria": ["done"],
            "risk_level": "LOW",
            "parallel_safe": True,
            "human_gate_required": False,
        }

        # parallel_safe="false" -> REJECT
        data_bad_ps = dict(base_data, parallel_safe="false")
        with self.assertRaises(ValueError):
            TaskContract.from_dict(data_bad_ps)

        # contracts_frozen="false" -> REJECT
        data_bad_cf = dict(base_data, contracts_frozen="false")
        with self.assertRaises(ValueError):
            TaskContract.from_dict(data_bad_cf)

        # human_gate_required="false" -> REJECT
        data_bad_hg = dict(base_data, human_gate_required="false")
        with self.assertRaises(ValueError):
            TaskContract.from_dict(data_bad_hg)

        # allowed_paths="services/api/" (string instead of list) -> REJECT
        data_bad_paths = dict(base_data, allowed_paths="services/api/")
        with self.assertRaises(ValueError):
            TaskContract.from_dict(data_bad_paths)

        # scope="foo" (string instead of list) -> REJECT
        data_bad_scope = dict(base_data, scope="foo")
        with self.assertRaises(ValueError):
            TaskContract.from_dict(data_bad_scope)

        # AgentProfile rejects string boolean for enable_write_tools
        manager = load_profile("engineering_manager")
        mgr_payload = manager.to_define_subagent_payload()
        mgr_payload["enable_write_tools"] = "false"
        mgr_payload["category"] = "base_team"
        mgr_payload["role_id"] = "engineering_manager"
        with self.assertRaises(ValueError):
            AgentProfile.from_dict(mgr_payload)

    # 5. Dispatcher criando worktree isolada via fixture determinístico
    def test_05_dispatcher_worktree_lifecycle(self) -> None:
        """Requirements 4, 5, 6: WorktreeDispatcher creates isolated worktree at base SHA."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(
                repo_root=repo_dir,
                worktrees_dir=worktrees_dir,
            )
            branch = f"feat/af1-smoke-{uuid.uuid4().hex[:8]}"
            info = dispatcher.create_worktree(
                branch_name=branch,
                base_sha=head_sha,
            )

            try:
                self.assertTrue(info.worktree_path.exists())
                self.assertTrue(info.worktree_path.is_relative_to(worktrees_dir))
                self.assertEqual(info.base_sha, head_sha)
                self.assertEqual(info.head_sha, head_sha)
                self.assertEqual(info.branch_name, branch)
                self.assertTrue(info.is_clean)
            finally:
                dispatcher.remove_worktree(info.worktree_path)
                self.assertFalse(info.worktree_path.exists())

    # 6. Duas tarefas parallel-safe conseguindo coexistir
    def test_06_parallel_safe_coexistence(self) -> None:
        """Requirement 7: Parallel-safe tasks with non-overlapping paths pass validation."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)

        task_api = TaskContract(
            task_id="TASK-API-001",
            outcome="Add API endpoint",
            scope=["services/api/src/tapjob_api/routers/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="core_platform_engineer",
            allowed_paths=["services/api/src/tapjob_api/routers/jobs.py"],
            forbidden_paths=[],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="LOW",
            parallel_safe=True,
            human_gate_required=False,
        )

        task_qa = TaskContract(
            task_id="TASK-QA-001",
            outcome="Prepare QA contract tests",
            scope=["packages/contracts/tests/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="qa_sdet_engineer",
            allowed_paths=["packages/contracts/tests/test_jobs_contract.py"],
            forbidden_paths=[],
            dependencies=["TASK-API-001"],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="LOW",
            parallel_safe=True,
            human_gate_required=False,
        )

        errors = dispatcher.validate_parallel_dispatch([task_api, task_qa])
        self.assertEqual(errors, [])

    # 7. Ownership overlap sendo rejeitado
    def test_07_ownership_overlap_rejected(self) -> None:
        """Requirement 8: Concurrent overlapping allowed_paths are rejected."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)

        task_a = TaskContract(
            task_id="TASK-A",
            outcome="Edit jobs service",
            scope=["services/api/src/tapjob_api/jobs/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="core_platform_engineer",
            allowed_paths=["services/api/src/tapjob_api/jobs/service.py"],
            forbidden_paths=[],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="LOW",
            parallel_safe=True,
            human_gate_required=False,
        )

        task_b = TaskContract(
            task_id="TASK-B",
            outcome="Also edit jobs service",
            scope=["services/api/src/tapjob_api/jobs/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="channel_whatsapp_engineer",
            allowed_paths=["services/api/src/tapjob_api/jobs/"],
            forbidden_paths=[],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="LOW",
            parallel_safe=True,
            human_gate_required=False,
        )

        errors = dispatcher.validate_parallel_dispatch([task_a, task_b])
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("Ownership collision" in e for e in errors))

    # 8. Critical serial paths proibidos em paralelo
    def test_08_critical_serial_paths_rejected(self) -> None:
        """Additional Hardening C: Tasks touching critical serial paths cannot run in parallel."""
        task_migration = TaskContract(
            task_id="TASK-MIG-001",
            outcome="Run db migration",
            scope=["services/api/alembic/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="database_specialist",
            allowed_paths=["services/api/alembic/versions/001_new.py"],
            forbidden_paths=[],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="HIGH",
            parallel_safe=True,
            human_gate_required=False,
        )
        task_infra = TaskContract(
            task_id="TASK-INF-001",
            outcome="Edit compose",
            scope=["infra/"],
            non_goals=[],
            canonical_base_sha=SYNTHETIC_SHA,
            owner_role="release_platform_ops_engineer",
            allowed_paths=["infra/compose.yaml"],
            forbidden_paths=[],
            dependencies=[],
            contracts_frozen=True,
            test_requirements=["tests"],
            acceptance_criteria=["done"],
            risk_level="HIGH",
            parallel_safe=True,
            human_gate_required=False,
        )
        self.assertTrue(len(task_migration.touches_critical_serial_paths()) > 0)
        self.assertTrue(len(task_infra.touches_critical_serial_paths()) > 0)
        self.assertIn("infra/", CRITICAL_SERIAL_PATHS)
        conflicts = task_migration.check_ownership_conflict(task_infra)
        self.assertTrue(any("critical serial paths" in c for c in conflicts))

    # 9. main/master e path traversal rejeitados
    def test_09_branch_safety_main_master_rejected(self) -> None:
        """Requirement 9: Prohibit worktree creation on main, master, or via traversal."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("main", SYNTHETIC_SHA)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("master", SYNTHETIC_SHA)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("refs/heads/main", SYNTHETIC_SHA)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("../../etc/passwd", SYNTHETIC_SHA)

    # 10. Cleanup recusando worktree dirty e eliminação do force parameter (Finding 3)
    def test_10_cleanup_refuses_dirty_worktree_and_force_eliminated(self) -> None:
        """Finding 3: remove_worktree refuses dirty worktrees; force parameter eliminated."""
        # Verify force parameter does not exist in remove_worktree signature
        sig = inspect.signature(WorktreeDispatcher.remove_worktree)
        self.assertNotIn("force", sig.parameters)

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(
                repo_root=repo_dir,
                worktrees_dir=worktrees_dir,
            )
            branch = f"fix/af1-dirty-{uuid.uuid4().hex[:8]}"
            info = dispatcher.create_worktree(
                branch_name=branch,
                base_sha=head_sha,
            )

            # Dirty the worktree by writing a new file
            dirty_file = info.worktree_path / "DIRTY_FILE.txt"
            dirty_file.write_text("uncommitted changes", encoding="utf-8")
            self.assertFalse(dispatcher.is_worktree_clean(info.worktree_path))

            # Safe cleanup must be refused with DirtyWorktreeError!
            with self.assertRaises(DirtyWorktreeError):
                dispatcher.remove_worktree(info.worktree_path)

            # Worktree must still exist
            self.assertTrue(info.worktree_path.exists())

            # Teardown explicitly cleans synthetic file before normal removal
            dirty_file.unlink()
            dispatcher.remove_worktree(info.worktree_path)
            self.assertFalse(info.worktree_path.exists())

    # 11. Proteção contra remoção de worktree não gerenciado ou fora do escopo (Finding 3)
    def test_11_unmanaged_and_out_of_bounds_worktree_delete_guarded(self) -> None:
        """Finding 3: remove_worktree refuses repo_root, external and unmanaged paths."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, _ = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(
                repo_root=repo_dir,
                worktrees_dir=worktrees_dir,
            )

            # 1. Attempting to remove repo_root -> refused
            with self.assertRaises(WorktreeDispatchError):
                dispatcher.remove_worktree(repo_dir)

            # 2. Attempting to remove path outside managed worktrees_dir -> refused
            outside_dir = temp_root / "outside-folder"
            outside_dir.mkdir()
            with self.assertRaises(WorktreeDispatchError):
                dispatcher.remove_worktree(outside_dir)

            # 3. Attempting to remove unmanaged folder inside worktrees_dir -> refused
            unmanaged = worktrees_dir / "unmanaged-worktree"
            unmanaged.mkdir()
            with self.assertRaises(WorktreeDispatchError):
                dispatcher.remove_worktree(unmanaged)

    # 12. MAX_INTERNAL_REPAIR_LOOPS = 5 Invariant (Finding 4)
    def test_12_repair_loop_controller_max_loops_invariant(self) -> None:
        """Finding 4: max_loops must be 1 <= max_loops <= 5; >5 is strictly rejected."""
        # max_loops=6 -> REJECT
        with self.assertRaises(ValueError):
            RepairLoopController(
                task_id="TASK-MAX-001",
                implementer_role="core_platform_engineer",
                reviewer_role="reviewer_security_engineer",
                max_loops=6,
            )

        # max_loops=0 -> REJECT
        with self.assertRaises(ValueError):
            RepairLoopController(
                task_id="TASK-MAX-002",
                implementer_role="core_platform_engineer",
                reviewer_role="reviewer_security_engineer",
                max_loops=0,
            )

        # 5 loops strictly transition to ESCALATE_TO_HUMAN
        controller = RepairLoopController(
            task_id="TASK-MAX-003",
            implementer_role="core_platform_engineer",
            reviewer_role="reviewer_security_engineer",
            max_loops=MAX_INTERNAL_REPAIR_LOOPS,
        )
        for i in range(1, 6):
            idx = controller.start_iteration()
            self.assertEqual(idx, i)
            phase = controller.record_review_result(
                test_passed=False,
                verdict=ReviewVerdict.CHANGES_REQUIRED,
                findings=[f"Failure {i}"],
            )
            if i < 5:
                self.assertEqual(phase, RepairPhase.CORRECTIONS)
            else:
                self.assertEqual(phase, RepairPhase.ESCALATE_TO_HUMAN)

        with self.assertRaises(BudgetExhaustedError):
            controller.start_iteration()
        self.assertEqual(controller.current_phase, RepairPhase.ESCALATE_TO_HUMAN)

    # 13. Reviewer separado do implementer e autoridade de merge nula
    def test_13_reviewer_separated_from_implementer_and_no_merge(self) -> None:
        """Requirements 12 & 14: Duty separation and zero merge authority."""
        with self.assertRaises(ReviewerSeparationError):
            RepairLoopController(
                task_id="TASK-001",
                implementer_role="core_platform_engineer",
                reviewer_role="core_platform_engineer",
            )

        controller = RepairLoopController(
            task_id="TASK-002",
            implementer_role="core_platform_engineer",
            reviewer_role="reviewer_security_engineer",
        )
        self.assertFalse(controller.can_merge())

    # 14. Canonical dispatch_task, base SHA, branch, human gate, and reservation (Finding 5)
    def test_14_canonical_dispatch_task_and_reservation(self) -> None:
        """Finding 5: dispatch_task enforces validation, SHA, human gating, and reservation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(
                repo_root=repo_dir,
                worktrees_dir=worktrees_dir,
            )

            # 1. Successful dispatch of valid contract
            contract_1 = TaskContract(
                task_id="TASK-DISPATCH-001",
                outcome="Implement user router",
                scope=["services/api/src/tapjob_api/routers/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/routers/users.py"],
                forbidden_paths=["services/api/alembic/"],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info_1 = dispatcher.dispatch_task(contract_1)
            self.assertTrue(info_1.worktree_path.exists())
            self.assertIn("TASK-DISPATCH-001", dispatcher._reserved_tasks)

            # 2. Concurrency collision with active contract -> OwnershipCollisionError
            contract_conflict = TaskContract(
                task_id="TASK-DISPATCH-002",
                outcome="Modify same user router",
                scope=["services/api/src/tapjob_api/routers/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="qa_sdet_engineer",
                allowed_paths=["services/api/src/tapjob_api/routers/users.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(OwnershipCollisionError):
                dispatcher.dispatch_task(contract_conflict)

            # 3. Base SHA mismatch between concurrent tasks -> WorktreeDispatchError
            other_sha = "ffffffffffffffffffffffffffffffffffffffff"
            contract_diff_base = TaskContract(
                task_id="TASK-DISPATCH-003",
                outcome="Different base task",
                scope=["packages/contracts/"],
                non_goals=[],
                canonical_base_sha=other_sha,
                owner_role="qa_sdet_engineer",
                allowed_paths=["packages/contracts/"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(WorktreeDispatchError):
                dispatcher.dispatch_task(contract_diff_base)

            # 4. Human gate required task -> HumanGateRequiredError
            contract_human_gate = TaskContract(
                task_id="TASK-DISPATCH-004",
                outcome="Production deploy task",
                scope=["infra/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="release_platform_ops_engineer",
                allowed_paths=["infra/deploy.yaml"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="CRITICAL",
                parallel_safe=True,
                human_gate_required=True,
            )
            with self.assertRaises(HumanGateRequiredError):
                dispatcher.dispatch_task(contract_human_gate)

            # 5. Clean release of task
            dispatcher.release_task(contract_1.task_id)
            self.assertFalse(info_1.worktree_path.exists())
            self.assertNotIn("TASK-DISPATCH-001", dispatcher._reserved_tasks)

    # 15. Scope validation of worktree altered files (Finding 5)
    def test_15_validate_worktree_scope_enforcement(self) -> None:
        """Finding 5: validate_worktree_scope verifies allowed/forbidden paths and traversals."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(
                repo_root=repo_dir,
                worktrees_dir=worktrees_dir,
            )
            contract = TaskContract(
                task_id="TASK-SCOPE-001",
                outcome="Edit jobs service only",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/"],
                forbidden_paths=["services/api/src/tapjob_api/jobs/forbidden.py"],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)
            try:
                # 1. Clean worktree has 0 violations
                violations_clean = dispatcher.validate_worktree_scope(contract, info.worktree_path)
                self.assertEqual(violations_clean, [])

                # 2. Compliant modification inside allowed_paths
                allowed_file = (
                    info.worktree_path
                    / "services"
                    / "api"
                    / "src"
                    / "tapjob_api"
                    / "jobs"
                    / "new.py"
                )
                allowed_file.write_text("# new compliant file", encoding="utf-8")
                violations_allowed = dispatcher.validate_worktree_scope(
                    contract, info.worktree_path
                )
                self.assertEqual(violations_allowed, [])
                dispatcher.enforce_worktree_scope(contract, info.worktree_path)  # Must not raise

                # 3. Modification in forbidden_paths -> violation!
                forbidden_file = (
                    info.worktree_path
                    / "services"
                    / "api"
                    / "src"
                    / "tapjob_api"
                    / "jobs"
                    / "forbidden.py"
                )
                forbidden_file.write_text("# forbidden edit", encoding="utf-8")
                violations_forbidden = dispatcher.validate_worktree_scope(
                    contract, info.worktree_path
                )
                self.assertTrue(any("forbidden_path" in v for v in violations_forbidden))
                with self.assertRaises(ScopeViolationError):
                    dispatcher.enforce_worktree_scope(contract, info.worktree_path)
                forbidden_file.unlink()

                # 4. Modification outside allowed_paths -> violation!
                out_of_bounds = info.worktree_path / "services" / "api" / "outside.py"
                out_of_bounds.write_text("# out of bounds", encoding="utf-8")
                violations_out = dispatcher.validate_worktree_scope(contract, info.worktree_path)
                self.assertTrue(any("not covered by any allowed_path" in v for v in violations_out))
                with self.assertRaises(ScopeViolationError):
                    dispatcher.enforce_worktree_scope(contract, info.worktree_path)
                out_of_bounds.unlink()

            finally:
                # Clean up synthetic file before release
                if allowed_file.exists():
                    allowed_file.unlink()
                dispatcher.release_task(contract.task_id)

    # 16. Base SHA Regex & worktrees_dir isolation (Additional Hardening A & B)
    def test_16_base_sha_regex_and_worktrees_dir_outside_repo(self) -> None:
        """Additional Hardening A & B: Reject HEAD, short SHAs, and worktrees_dir inside repo."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)

        # 1. Non-40-char SHA rejected
        with self.assertRaises(WorktreeDispatchError):
            dispatcher.validate_base_sha("HEAD")
        with self.assertRaises(WorktreeDispatchError):
            dispatcher.validate_base_sha("main")
        with self.assertRaises(WorktreeDispatchError):
            dispatcher.validate_base_sha("origin/main")
        with self.assertRaises(WorktreeDispatchError):
            dispatcher.validate_base_sha("5c56526")  # short SHA

        # 2. worktrees_dir cannot be repo_root or inside repo_root
        with self.assertRaises(WorktreeDispatchError):
            WorktreeDispatcher(repo_root=self.repo_root, worktrees_dir=self.repo_root)

        with self.assertRaises(WorktreeDispatchError):
            WorktreeDispatcher(
                repo_root=self.repo_root,
                worktrees_dir=self.repo_root / "nested-worktrees",
            )

    # 17. Bootstrap CLI options (--list-roles alias and --emit-runtime-payload)
    def test_17_bootstrap_cli_options(self) -> None:
        """Finding 6: bootstrap.py supports --list-roles and --emit-runtime-payload."""
        # 1. Test --list-roles
        buf_roles = io.StringIO()
        with redirect_stdout(buf_roles):
            code_roles = bootstrap_main(["--list-roles"])
        self.assertEqual(code_roles, 0)
        self.assertIn("engineering_manager", buf_roles.getvalue())

        # 2. Test --list-team
        buf_team = io.StringIO()
        with redirect_stdout(buf_team):
            code_team = bootstrap_main(["--list-team"])
        self.assertEqual(code_team, 0)
        self.assertIn("core_platform_engineer", buf_team.getvalue())

        # 3. Test --emit-runtime-payload engineering_manager
        buf_payload = io.StringIO()
        with redirect_stdout(buf_payload):
            code_payload = bootstrap_main(["--emit-runtime-payload", "engineering_manager"])
        self.assertEqual(code_payload, 0)
        payload_data = json.loads(buf_payload.getvalue())
        self.assertEqual(payload_data["name"], "engineering_manager")
        self.assertTrue(payload_data["enable_write_tools"])


if __name__ == "__main__":
    unittest.main()

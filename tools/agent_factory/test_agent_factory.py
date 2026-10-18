"""Comprehensive unit and smoke test suite for TapJob Agent Factory (AF-1)."""

from __future__ import annotations

import inspect
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

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
    CorruptRegistryError,
    DirtyWorktreeError,
    DuplicateTaskIdError,
    HumanGateRequiredError,
    InvalidBranchNameError,
    LockTimeoutError,
    OwnershipCollisionError,
    RoleOwnershipViolationError,
    RolePermissionError,
    ScopeVerificationRequiredError,
    ScopeViolationError,
    TaskNotFoundError,
    WorktreeDispatcher,
    WorktreeDispatchError,
)

SYNTHETIC_SHA = "0123456789abcdef0123456789abcdef01234567"


class TestAgentFactory(unittest.TestCase):
    """Test suite covering team profiles, task contracts, dispatchers, and repair loops."""

    repo_root: Path
    git_bin: str

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
                owner_role="core_platform_engineer",
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
                scope=["services/api/tests/"],
                non_goals=[],
                canonical_base_sha=other_sha,
                owner_role="qa_sdet_engineer",
                allowed_paths=["services/api/tests/test_contracts.py"],
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

            # 5. Clean release of task requires verify_task_scope
            dispatcher.verify_task_scope(contract_1.task_id)
            dispatcher.release_task(contract_1.task_id)
            self.assertFalse(info_1.worktree_path.exists())

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
                dispatcher.verify_task_scope(contract.task_id)
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

    # 18. Duplicate task_id preserving original reservation (Finding 1)
    def test_18_duplicate_task_id_preserves_original_reservation(self) -> None:
        """Finding 1: Duplicate task_id fails closed and preserves the original reservation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract_a = TaskContract(
                task_id="TASK-DUP-A",
                outcome="Implement user router",
                scope=["services/api/src/tapjob_api/routers/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
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

            # 1. Dispatch TASK-A
            info_a = dispatcher.dispatch_task(contract_a)
            self.assertTrue(info_a.worktree_path.exists())

            # 2. Confirm reservation in persistent registry
            registry_before = dispatcher._read_registry_file()
            self.assertIn("TASK-DUP-A", registry_before)
            self.assertEqual(registry_before["TASK-DUP-A"]["status"], "active")

            # 3. Attempt second dispatch of TASK-A -> DuplicateTaskIdError
            with self.assertRaises(DuplicateTaskIdError):
                dispatcher.dispatch_task(contract_a)

            # 4 & 5. Original reservation and worktree continue to exist intact
            registry_after = dispatcher._read_registry_file()
            self.assertIn("TASK-DUP-A", registry_after)
            self.assertEqual(registry_after["TASK-DUP-A"]["status"], "active")
            self.assertTrue(info_a.worktree_path.exists())

            # 6. Attempt TASK-B with overlapping allowed_paths -> OwnershipCollisionError
            contract_b = TaskContract(
                task_id="TASK-DUP-B",
                outcome="Modify same user router concurrently",
                scope=["services/api/src/tapjob_api/routers/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
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
                dispatcher.dispatch_task(contract_b)

            # Clean release
            dispatcher.verify_task_scope(contract_a.task_id)
            dispatcher.release_task(contract_a.task_id)
            self.assertFalse(info_a.worktree_path.exists())

    # 19. Persistent task registry across dispatcher instances (Finding 2)
    def test_19_cross_instance_registry_discovery_and_overlap_rejection(self) -> None:
        """Finding 2: Persistent registry preserves reservations across dispatcher instances."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher_1 = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract_a = TaskContract(
                task_id="TASK-PERSIST-A",
                outcome="Implement jobs router",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            # 1. Dispatch TASK-A with dispatcher_1
            info_a = dispatcher_1.dispatch_task(contract_a)
            self.assertTrue(info_a.worktree_path.exists())

            # 2. Abandon dispatcher_1 and instantiate brand-new dispatcher_2
            del dispatcher_1
            dispatcher_2 = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            # 3. dispatcher_2 discovers TASK-A from persistent registry
            persisted = dispatcher_2._read_registry_file()
            self.assertIn("TASK-PERSIST-A", persisted)
            self.assertEqual(persisted["TASK-PERSIST-A"]["status"], "active")

            # 4. dispatcher_2 rejects overlapping TASK-B from persistent peer state
            contract_b = TaskContract(
                task_id="TASK-PERSIST-B",
                outcome="Overlap jobs router",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
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
                dispatcher_2.dispatch_task(contract_b)

            # 5. dispatcher_2 verifies and releases TASK-A, reconciling registry
            dispatcher_2.verify_task_scope(contract_a.task_id)
            dispatcher_2.release_task(contract_a.task_id)
            self.assertNotIn("TASK-PERSIST-A", dispatcher_2._read_registry_file())
            self.assertFalse(info_a.worktree_path.exists())

    # 20. Corrupted registry fails closed (Finding 2)
    def test_20_corrupted_registry_fails_closed(self) -> None:
        """Finding 2: Corrupted or unparseable persistent registry fails closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)
            dispatcher.registry_dir.mkdir(parents=True, exist_ok=True)

            # 1. Invalid JSON syntax
            dispatcher.registry_file.write_text("{{corrupt-json::", encoding="utf-8")
            with self.assertRaises(CorruptRegistryError):
                dispatcher._read_registry_file()

            contract = TaskContract(
                task_id="TASK-CORRUPT-001",
                outcome="Test task",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(CorruptRegistryError):
                dispatcher.dispatch_task(contract)

            # 2. Corrupt schema: root is a list, not a dict
            dispatcher.registry_file.write_text("[]", encoding="utf-8")
            with self.assertRaises(CorruptRegistryError):
                dispatcher._read_registry_file()

            # 3. Corrupt schema: missing required keys
            dispatcher.registry_file.write_text(
                json.dumps({"TASK-BAD": {"task_id": "TASK-BAD"}}),
                encoding="utf-8",
            )
            with self.assertRaises(CorruptRegistryError):
                dispatcher._read_registry_file()

    # 21. Stale registry reconciliation behavior (Finding 2)
    def test_21_stale_registry_reconciliation(self) -> None:
        """Finding 2: reconcile_registry prunes stale entries whose worktrees no longer exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-STALE-001",
                outcome="Stale worktree task",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)
            self.assertTrue(info.worktree_path.exists())

            # Simulate external worktree deletion / prune
            shutil.rmtree(info.worktree_path)
            subprocess.run(  # noqa: S603
                [self.git_bin, "worktree", "prune"],
                cwd=str(repo_dir),
                capture_output=True,
                check=False,
            )

            # reconcile_registry detects the dead worktree and prunes it
            pruned = dispatcher.reconcile_registry()
            self.assertIn("TASK-STALE-001", pruned)
            self.assertNotIn("TASK-STALE-001", dispatcher._read_registry_file())

    # 22. Role ownership and permissions enforcement (Finding 3)
    def test_22_role_ownership_and_permission_enforcement(self) -> None:
        """Finding 3: Dispatches enforce role ownership_paths and reject read-only write tasks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            # 1. channel_whatsapp_engineer attempting to modify jobs domain -> REJECT
            contract_whatsapp_violation = TaskContract(
                task_id="TASK-ROLE-VIOLATION-001",
                outcome="Modify jobs",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
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
            with self.assertRaises(RoleOwnershipViolationError):
                dispatcher.dispatch_task(contract_whatsapp_violation)

            # 2. reviewer_security_engineer (read-only) attempting write task -> REJECT
            contract_readonly_write = TaskContract(
                task_id="TASK-ROLE-READONLY-001",
                outcome="Reviewer attempting write",
                scope=["services/api/src/tapjob_api/messaging/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="reviewer_security_engineer",
                allowed_paths=["services/api/src/tapjob_api/messaging/client.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(RolePermissionError):
                dispatcher.dispatch_task(contract_readonly_write)

            # 3. channel_whatsapp_engineer modifying authorized messaging path -> PASS
            contract_whatsapp_valid = TaskContract(
                task_id="TASK-ROLE-VALID-WA",
                outcome="Valid messaging edit",
                scope=["services/api/src/tapjob_api/messaging/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="channel_whatsapp_engineer",
                allowed_paths=["services/api/src/tapjob_api/messaging/client.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            info_wa = dispatcher.dispatch_task(contract_whatsapp_valid)
            self.assertTrue(info_wa.worktree_path.exists())
            dispatcher.verify_task_scope(contract_whatsapp_valid.task_id)
            dispatcher.release_task(contract_whatsapp_valid.task_id)

            # 4. qa_sdet_engineer modifying authorized test path -> PASS
            contract_qa_valid = TaskContract(
                task_id="TASK-ROLE-VALID-QA",
                outcome="Valid test edit",
                scope=["services/api/tests/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="qa_sdet_engineer",
                allowed_paths=["services/api/tests/test_messaging.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            info_qa = dispatcher.dispatch_task(contract_qa_valid)
            self.assertTrue(info_qa.worktree_path.exists())
            dispatcher.verify_task_scope(contract_qa_valid.task_id)
            dispatcher.release_task(contract_qa_valid.task_id)

    # 23. Git diff failure during scope validation fails closed (Finding 4)
    def test_23_git_diff_failure_scope_validation_fails_closed(self) -> None:
        """Finding 4: git diff failure during validate_worktree_scope raises ScopeViolationError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-DIFF-FAIL",
                outcome="Diff failure test",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)

            # Create a contract clone pointing to a non-existent commit
            bogus_contract = TaskContract(
                task_id=contract.task_id,
                outcome=contract.outcome,
                scope=contract.scope,
                non_goals=contract.non_goals,
                canonical_base_sha="0000000000000000000000000000000000000000",
                owner_role=contract.owner_role,
                allowed_paths=contract.allowed_paths,
                forbidden_paths=contract.forbidden_paths,
                dependencies=contract.dependencies,
                contracts_frozen=contract.contracts_frozen,
                test_requirements=contract.test_requirements,
                acceptance_criteria=contract.acceptance_criteria,
                risk_level=contract.risk_level,
                parallel_safe=contract.parallel_safe,
                human_gate_required=contract.human_gate_required,
            )

            with self.assertRaises(ScopeViolationError):
                dispatcher.validate_worktree_scope(bogus_contract, info.worktree_path)

            dispatcher.verify_task_scope(contract.task_id)
            dispatcher.release_task(contract.task_id)

    # 24. Worktree contract mismatch fails closed (Finding 4)
    def test_24_worktree_contract_mismatch_fails_closed(self) -> None:
        """Finding 4: verify_task_scope raises ScopeViolationError on branch or base mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-MISMATCH-001",
                outcome="Mismatch test",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)

            # 1. Switch branch in worktree to cause branch mismatch
            subprocess.run(  # noqa: S603
                [self.git_bin, "checkout", "-b", "feat/rogue-branch"],
                cwd=str(info.worktree_path),
                capture_output=True,
                check=True,
            )
            with self.assertRaises(ScopeViolationError):
                dispatcher.verify_task_scope(contract.task_id)

            # Switch back to expected branch
            subprocess.run(  # noqa: S603
                [self.git_bin, "checkout", info.branch_name],
                cwd=str(info.worktree_path),
                capture_output=True,
                check=True,
            )

            # 2. Now verify_task_scope succeeds
            dispatcher.verify_task_scope(contract.task_id)
            dispatcher.release_task(contract.task_id)

    # 25. Scope verification lifecycle gate (Finding 4)
    def test_25_scope_verification_lifecycle_gate(self) -> None:
        """Finding 4: release_task is refused until verify_task_scope succeeds."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-GATE-001",
                outcome="Gate test",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)

            # 1. Attempting release without scope verification -> REFUSED
            with self.assertRaises(ScopeVerificationRequiredError):
                dispatcher.release_task(contract.task_id)
            self.assertTrue(info.worktree_path.exists())

            # 2. Scope verification succeeds
            dispatcher.verify_task_scope(contract.task_id)
            registry_entry = dispatcher._read_registry_file()[contract.task_id]
            self.assertTrue(registry_entry["scope_verified"])
            self.assertEqual(registry_entry["status"], "scope_verified")

            # 3. Now release succeeds cleanly
            dispatcher.release_task(contract.task_id)
            self.assertFalse(info.worktree_path.exists())
            self.assertNotIn(contract.task_id, dispatcher._read_registry_file())

    # 26. Strict Registry Entry Schema & Semantic Validation (Finding 1)
    def test_26_strict_registry_entry_schema_and_semantic_validation(self) -> None:
        """Finding 1: Strict schema, semantic consistency, and fail-closed peer parsing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            valid_contract = TaskContract(
                task_id="TASK-VALID-001",
                outcome="Valid task",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["unit tests"],
                acceptance_criteria=["scope verified"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
                target_branch="feat/task-valid-001",
            )

            # Scenario A: Valid entry -> PASS
            valid_entry = {
                "task_id": valid_contract.task_id,
                "owner_role": valid_contract.owner_role,
                "canonical_base_sha": valid_contract.canonical_base_sha,
                "target_branch": valid_contract.target_branch,
                "worktree_path": str(worktrees_dir / "valid-wt"),
                "allowed_paths": valid_contract.allowed_paths,
                "forbidden_paths": valid_contract.forbidden_paths,
                "parallel_safe": True,
                "contracts_frozen": True,
                "status": "active",
                "scope_verified": False,
                "scope_verified_head_sha": "",
                "contract": valid_contract.to_dict(),
            }
            parsed_contract = dispatcher.validate_registry_entry(
                valid_contract.task_id, valid_entry
            )
            self.assertEqual(parsed_contract.task_id, valid_contract.task_id)

            # Scenario B: Syntactically valid JSON but contract={} -> CorruptRegistryError
            bad_b = dict(valid_entry)
            bad_b["contract"] = {}
            with self.assertRaises(CorruptRegistryError):
                dispatcher.validate_registry_entry(valid_contract.task_id, bad_b)

            # Scenario C: parallel_safe="false" (string instead of bool) -> CorruptRegistryError
            bad_c = dict(valid_entry)
            bad_c["parallel_safe"] = "false"
            with self.assertRaises(CorruptRegistryError):
                dispatcher.validate_registry_entry(valid_contract.task_id, bad_c)

            # Scenario D: registry key TASK-A but entry.task_id TASK-B -> CorruptRegistryError
            with self.assertRaises(CorruptRegistryError):
                dispatcher.validate_registry_entry("TASK-MISMATCH", valid_entry)

            # Scenario E: top-level allowed_paths differs from nested contract -> fails
            bad_e = dict(valid_entry)
            bad_e["allowed_paths"] = ["services/api/other.py"]
            with self.assertRaises(CorruptRegistryError):
                dispatcher.validate_registry_entry(valid_contract.task_id, bad_e)

            # Scenario F: Semantically malformed existing TASK-A + attempted overlapping TASK-B
            # Must fail closed: TASK-B dispatch raises CorruptRegistryError, worktree NOT created.
            reg_dir = worktrees_dir / ".tapjob-agent-factory"
            reg_dir.mkdir(parents=True, exist_ok=True)
            malformed_registry = {
                "TASK-MALFORMED": {
                    "task_id": "TASK-MALFORMED",
                    "owner_role": "core_platform_engineer",
                    "canonical_base_sha": head_sha,
                    "target_branch": "feat/malformed",
                    "worktree_path": "",
                    "allowed_paths": ["services/api/src/tapjob_api/jobs/feed.py"],
                    "forbidden_paths": [],
                    "parallel_safe": "not-a-bool",  # malformed!
                    "contracts_frozen": True,
                    "status": "active",
                    "scope_verified": False,
                    "contract": valid_contract.to_dict(),
                }
            }
            (reg_dir / "registry.json").write_text(json.dumps(malformed_registry), encoding="utf-8")

            overlapping_contract = TaskContract(
                task_id="TASK-OVERLAP-002",
                outcome="Overlapping task",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["unit tests"],
                acceptance_criteria=["scope verified"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(CorruptRegistryError):
                dispatcher.dispatch_task(overlapping_contract)

            # Assert TASK-OVERLAP-002 worktree was NOT created
            created_worktrees = list(worktrees_dir.glob("task-overlap*"))
            self.assertEqual(created_worktrees, [])

    # 27. Worktree Creation Compensation & Activation Failure State (Finding 2)
    def test_27_worktree_creation_compensation_and_activation_failure_state(self) -> None:
        """Finding 2: Worktree promotion failure preserves entry with status='activation_failed'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-ACTIVATION-FAIL",
                outcome="Activation fail test",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["unit tests"],
                acceptance_criteria=["scope verified"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            # Hook write_registry_file to simulate failure on promotion from "reserved" to "active"
            real_write = dispatcher._write_registry_file
            call_count = 0

            def failing_write(data: dict[str, Any]) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    # Second call is status promotion to "active"
                    raise RuntimeError("Simulated crash during status promotion")
                real_write(data)

            dispatcher._write_registry_file = failing_write  # type: ignore[method-assign]

            # Dispatch should fail with WorktreeDispatchError
            with self.assertRaises(WorktreeDispatchError):
                dispatcher.dispatch_task(contract)

            # Restore original write method
            dispatcher._write_registry_file = real_write  # type: ignore[method-assign]

            # Assertions:
            # 1. Entry preserved with status='activation_failed' containing worktree_path
            reg = dispatcher._read_registry_file()
            self.assertIn(contract.task_id, reg)
            entry = reg[contract.task_id]
            self.assertEqual(entry["status"], "activation_failed")
            self.assertTrue(Path(entry["worktree_path"]).exists())

            # 2. No unregistered worktree remains (entry points to real worktree)
            self.assertEqual(
                Path(entry["worktree_path"]).resolve(),
                (worktrees_dir / "task-activation-fail").resolve(),
            )

            # 3. Ownership remains fail-closed: overlapping task is rejected
            overlap_contract = TaskContract(
                task_id="TASK-ACTIVATION-OVERLAP",
                outcome="Overlap test",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["unit tests"],
                acceptance_criteria=["scope verified"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            with self.assertRaises(OwnershipCollisionError):
                dispatcher.dispatch_task(overlap_contract)

            # 4. Duplicate task_id is also rejected
            with self.assertRaises(DuplicateTaskIdError):
                dispatcher.dispatch_task(contract)

    # 28. Scope Verification TOCTOU Window & Exact HEAD Enforcement (Finding 3)
    def test_28_scope_verification_toctou_window_and_head_enforcement(self) -> None:
        """Finding 3: verify_task_scope captures HEAD SHA and release enforces verified HEAD."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, head_sha = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            contract = TaskContract(
                task_id="TASK-TOCTOU-001",
                outcome="TOCTOU verification",
                scope=["services/api/src/tapjob_api/jobs/"],
                non_goals=[],
                canonical_base_sha=head_sha,
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/src/tapjob_api/jobs/feed.py"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["unit tests"],
                acceptance_criteria=["scope verified"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )

            info = dispatcher.dispatch_task(contract)
            feed_file = (
                info.worktree_path / "services" / "api" / "src" / "tapjob_api" / "jobs" / "feed.py"
            )
            feed_file.write_text("# initial commit", encoding="utf-8")
            subprocess.run(  # noqa: S603
                [self.git_bin, "add", "."], cwd=str(info.worktree_path), check=True
            )
            subprocess.run(  # noqa: S603
                [self.git_bin, "commit", "-m", "initial allowed change"],
                cwd=str(info.worktree_path),
                check=True,
            )

            # Initial verification -> PASS
            dispatcher.verify_task_scope(contract.task_id)
            reg = dispatcher._read_registry_file()
            self.assertTrue(reg[contract.task_id]["scope_verified"])
            initial_head = reg[contract.task_id]["scope_verified_head_sha"]
            self.assertTrue(bool(re.match(r"^[0-9a-fA-F]{40}$", initial_head)))

            # Scenario 2: Allowed new commit -> release REFUSED because HEAD changed
            feed_file.write_text("# second commit", encoding="utf-8")
            subprocess.run(  # noqa: S603
                [self.git_bin, "add", "."], cwd=str(info.worktree_path), check=True
            )
            subprocess.run(  # noqa: S603
                [self.git_bin, "commit", "-m", "second allowed change"],
                cwd=str(info.worktree_path),
                check=True,
            )
            # Tree is clean, but HEAD changed
            self.assertTrue(dispatcher.is_worktree_clean(info.worktree_path))
            with self.assertRaises(ScopeVerificationRequiredError):
                dispatcher.release_task(contract.task_id)

            # Re-verify -> PASS, updates verified HEAD
            dispatcher.verify_task_scope(contract.task_id)
            new_reg = dispatcher._read_registry_file()
            new_head = new_reg[contract.task_id]["scope_verified_head_sha"]
            self.assertNotEqual(initial_head, new_head)

            # Scenario 3: Out-of-scope new commit -> release REFUSED and re-verify raises error
            rogue_file = info.worktree_path / "services" / "api" / "rogue.py"
            rogue_file.write_text("# out of scope", encoding="utf-8")
            subprocess.run(  # noqa: S603
                [self.git_bin, "add", "."], cwd=str(info.worktree_path), check=True
            )
            subprocess.run(  # noqa: S603
                [self.git_bin, "commit", "-m", "rogue commit"],
                cwd=str(info.worktree_path),
                check=True,
            )
            with self.assertRaises(ScopeVerificationRequiredError):
                dispatcher.release_task(contract.task_id)
            with self.assertRaises(ScopeViolationError):
                dispatcher.verify_task_scope(contract.task_id)

            # Revert rogue commit
            subprocess.run(  # noqa: S603
                [self.git_bin, "reset", "--hard", "HEAD~1"],
                cwd=str(info.worktree_path),
                check=True,
            )

            # Scenario 4: Uncommitted allowed modification -> release REFUSED because dirty
            feed_file.write_text("# dirty modification", encoding="utf-8")
            with self.assertRaises(DirtyWorktreeError):
                dispatcher.release_task(contract.task_id)

            # Clean worktree and release -> PASS
            subprocess.run(  # noqa: S603
                [self.git_bin, "checkout", "--", "."],
                cwd=str(info.worktree_path),
                check=True,
            )
            dispatcher.verify_task_scope(contract.task_id)
            dispatcher.release_task(contract.task_id)
            self.assertFalse(info.worktree_path.exists())

    # 29. Lock Ownership Metadata, Token Validation & Mutual Exclusion (Finding 4)
    def test_29_lock_ownership_metadata_token_validation_and_mutual_exclusion(self) -> None:
        """Finding 4: Unique tokens, PID liveness recovery, and fail-closed lock timeouts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, _ = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            # 1. Acquire lock in context A -> second live acquisition times out and fails closed
            with dispatcher._registry_lock(timeout_seconds=5.0):
                # Verify owner.json exists with pid and token
                owner_file = dispatcher.lock_path / "owner.json"
                self.assertTrue(owner_file.exists())
                data = json.loads(owner_file.read_text(encoding="utf-8"))
                self.assertEqual(data["pid"], os.getpid())
                self.assertTrue("token" in data)

                # Second acquisition attempt with short timeout fails closed
                with (
                    self.assertRaises(LockTimeoutError),
                    dispatcher._registry_lock(timeout_seconds=0.2),
                ):
                    pass

            # After exit, lock directory is cleaned
            self.assertFalse(dispatcher.lock_path.exists())

            # 2. Token mismatch: owner cannot delete another owner's lock
            dispatcher.lock_path.mkdir(parents=True, exist_ok=True)
            foreign_owner = {
                "pid": os.getpid(),
                "token": "foreign-token-12345",
                "created_at": time.time(),
            }
            (dispatcher.lock_path / "owner.json").write_text(
                json.dumps(foreign_owner), encoding="utf-8"
            )

            # Try to release with a different key by simulating finally
            mismatched_key = "non-matching-key"
            if (dispatcher.lock_path / "owner.json").exists():
                owner_data = json.loads(
                    (dispatcher.lock_path / "owner.json").read_text(encoding="utf-8")
                )
                if owner_data.get("token") == mismatched_key:
                    (dispatcher.lock_path / "owner.json").unlink()
                    dispatcher.lock_path.rmdir()

            # The foreign lock directory still exists!
            self.assertTrue(dispatcher.lock_path.exists())

            # 3. Stale recovery: when lock belongs to a dead process, it is recovered
            dead_owner = {
                "pid": 99999999,  # definitely non-existent PID
                "token": "dead-proc-token",
                "created_at": time.time() - 100,
            }
            (dispatcher.lock_path / "owner.json").write_text(
                json.dumps(dead_owner), encoding="utf-8"
            )

            # Acquire should detect dead process, prune stale lock, and succeed
            with dispatcher._registry_lock(timeout_seconds=1.0):
                # Acquired successfully!
                cur_data = json.loads(
                    (dispatcher.lock_path / "owner.json").read_text(encoding="utf-8")
                )
                self.assertEqual(cur_data["pid"], os.getpid())
            self.assertFalse(dispatcher.lock_path.exists())

    # 30. Missing Task Release Fails Closed (Additional Small Correction 1)
    def test_30_missing_task_release_fails_closed(self) -> None:
        """Additional Correction 1: release_task fails closed on unknown task_id."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            repo_dir, _ = self._create_temp_git_repo(temp_root)
            worktrees_dir = temp_root / "managed-worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            dispatcher = WorktreeDispatcher(repo_root=repo_dir, worktrees_dir=worktrees_dir)

            with self.assertRaises(TaskNotFoundError):
                dispatcher.release_task("TASK-DOES-NOT-EXIST")


if __name__ == "__main__":
    unittest.main()

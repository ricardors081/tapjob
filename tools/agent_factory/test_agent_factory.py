"""Comprehensive unit and smoke test suite for TapJob Agent Factory (AF-1)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.agent_factory.repair_loop import (
    MAX_INTERNAL_REPAIR_LOOPS,
    BudgetExhaustedError,
    RepairLoopController,
    RepairPhase,
    ReviewerSeparationError,
    ReviewVerdict,
)
from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.team import (
    BASE_TEAM_ROLES,
    DYNAMIC_SPECIALIST_ROLES,
    load_all_profiles,
    load_profile,
)
from tools.agent_factory.worktree_dispatcher import (
    DirtyWorktreeError,
    InvalidBranchNameError,
    WorktreeDispatcher,
    WorktreeDispatchError,
)

DUMMY_SHA = "5c56526e18928babc9b2b8ea4ecf022d68c2d1a4"


class TestAgentFactory(unittest.TestCase):
    """Test suite covering team profiles, task contracts, dispatchers, and repair loops."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

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

    # 2. Pelo menos um specialist carregável (e todos os 14 perfis)
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

    # 3. Task contract válido
    def test_03_task_contract_valid(self) -> None:
        """Requirement 3: TaskContract creation, validation, and serialization."""
        contract = TaskContract(
            task_id="TASK-WP2.4-001",
            outcome="Implement candidate search filters",
            scope=["Add salary range and remote filter"],
            non_goals=["Do not implement billing"],
            canonical_base_sha=DUMMY_SHA,
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
        self.assertEqual(reconstituted.canonical_base_sha, DUMMY_SHA)

        # Validation rejects bad SHA
        with self.assertRaises(ValueError):
            bad_contract = TaskContract(
                task_id="BAD-SHA",
                outcome="test",
                scope=["test"],
                non_goals=[],
                canonical_base_sha="invalid-sha",
                owner_role="core_platform_engineer",
                allowed_paths=["services/api/"],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            bad_contract.validate()

        # Validation rejects empty allowed_paths
        with self.assertRaises(ValueError):
            bad_contract = TaskContract(
                task_id="EMPTY-PATHS",
                outcome="test",
                scope=["test"],
                non_goals=[],
                canonical_base_sha=DUMMY_SHA,
                owner_role="core_platform_engineer",
                allowed_paths=[],
                forbidden_paths=[],
                dependencies=[],
                contracts_frozen=True,
                test_requirements=["tests"],
                acceptance_criteria=["done"],
                risk_level="LOW",
                parallel_safe=True,
                human_gate_required=False,
            )
            bad_contract.validate()

    # 4, 5, 6. Dispatcher criando worktree isolada, baseada no SHA correto, branch independente
    def test_04_05_06_dispatcher_worktree_lifecycle(self) -> None:
        """Requirements 4, 5, 6: WorktreeDispatcher creates isolated worktree at base SHA."""
        with tempfile.TemporaryDirectory() as tmp_worktrees_dir:
            dispatcher = WorktreeDispatcher(
                repo_root=self.repo_root,
                worktrees_dir=Path(tmp_worktrees_dir),
            )
            branch = "feat/af1-smoke-test-branch-01"
            info = dispatcher.create_worktree(
                branch_name=branch,
                base_sha=DUMMY_SHA,
            )

            try:
                # 4: Worktree created in isolated directory
                self.assertTrue(info.worktree_path.exists())
                self.assertTrue(info.worktree_path.is_relative_to(Path(tmp_worktrees_dir)))
                # 5: Worktree based on correct SHA
                self.assertEqual(info.base_sha, DUMMY_SHA)
                self.assertEqual(info.head_sha, DUMMY_SHA)
                # 6: Independent branch
                self.assertEqual(info.branch_name, branch)
                self.assertTrue(info.is_clean)
            finally:
                dispatcher.remove_worktree(info.worktree_path)
                self.assertFalse(info.worktree_path.exists())

    # 7. Duas tarefas parallel-safe conseguindo coexistir
    def test_07_parallel_safe_coexistence(self) -> None:
        """Requirement 7: Parallel-safe tasks with non-overlapping paths pass validation."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)

        task_api = TaskContract(
            task_id="TASK-API-001",
            outcome="Add API endpoint",
            scope=["services/api/src/tapjob_api/routers/"],
            non_goals=[],
            canonical_base_sha=DUMMY_SHA,
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
            canonical_base_sha=DUMMY_SHA,
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

    # 8. Ownership overlap sendo rejeitado
    def test_08_ownership_overlap_rejected(self) -> None:
        """Requirement 8: Concurrent overlapping allowed_paths are rejected."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)

        task_a = TaskContract(
            task_id="TASK-A",
            outcome="Edit jobs service",
            scope=["services/api/src/tapjob_api/jobs/"],
            non_goals=[],
            canonical_base_sha=DUMMY_SHA,
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
            canonical_base_sha=DUMMY_SHA,
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

    # 9. main/master sendo rejeitado
    def test_09_main_master_rejected(self) -> None:
        """Requirement 9: Prohibit worktree creation on main or master."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("main", DUMMY_SHA)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("master", DUMMY_SHA)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("refs/heads/main", DUMMY_SHA)

    # 10. Path traversal rejeitado
    def test_10_path_traversal_rejected(self) -> None:
        """Requirement 10: Path traversal in branch or worktree destination is rejected."""
        dispatcher = WorktreeDispatcher(repo_root=self.repo_root)
        with self.assertRaises(InvalidBranchNameError):
            dispatcher.create_worktree("../../etc/passwd", DUMMY_SHA)
        with self.assertRaises(WorktreeDispatchError):
            dispatcher.create_worktree(
                "feat/valid-branch", DUMMY_SHA, worktree_id="../../../outside"
            )

    # 11. Cleanup recusando worktree dirty
    def test_11_cleanup_refuses_dirty_worktree(self) -> None:
        """Requirement 11: remove_worktree refuses to remove a dirty worktree."""
        with tempfile.TemporaryDirectory() as tmp_worktrees_dir:
            dispatcher = WorktreeDispatcher(
                repo_root=self.repo_root,
                worktrees_dir=Path(tmp_worktrees_dir),
            )
            branch = "fix/af1-dirty-test"
            info = dispatcher.create_worktree(
                branch_name=branch,
                base_sha=DUMMY_SHA,
            )

            try:
                # Dirty the worktree by writing a new file
                dirty_file = info.worktree_path / "DIRTY_FILE.txt"
                dirty_file.write_text("uncommitted changes", encoding="utf-8")
                self.assertFalse(dispatcher.is_worktree_clean(info.worktree_path))

                # Safe cleanup must be refused!
                with self.assertRaises(DirtyWorktreeError):
                    dispatcher.remove_worktree(info.worktree_path, force=False)

                # Worktree must still exist
                self.assertTrue(info.worktree_path.exists())

                # Clean up file to allow test teardown
                dirty_file.unlink()
            finally:
                dispatcher.remove_worktree(info.worktree_path, force=True)

    # 12. Reviewer separado do implementer
    def test_12_reviewer_separated_from_implementer(self) -> None:
        """Requirement 12: Separation of duties prevents author from reviewing own code."""
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
        controller.start_iteration()

        with self.assertRaises(ReviewerSeparationError):
            controller.record_review_result(
                test_passed=True,
                verdict=ReviewVerdict.APPROVE,
                reviewer_role="core_platform_engineer",
            )

    # 13. MAX_INTERNAL_REPAIR_LOOPS = 5
    def test_13_max_internal_repair_loops_enforced(self) -> None:
        """Requirement 13: Budget of 5 repair loops is strictly enforced before human escalation."""
        controller = RepairLoopController(
            task_id="TASK-003",
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
                findings=[f"Fix failure {i}"],
            )
            if i < 5:
                self.assertEqual(phase, RepairPhase.CORRECTIONS)
            else:
                self.assertEqual(phase, RepairPhase.ESCALATE_TO_HUMAN)

        # 6th attempt must raise BudgetExhaustedError and transition to ESCALATE_TO_HUMAN
        with self.assertRaises(BudgetExhaustedError):
            controller.start_iteration()
        self.assertEqual(controller.current_phase, RepairPhase.ESCALATE_TO_HUMAN)

    # 14. Merge authority inexistente
    def test_14_merge_authority_non_existent(self) -> None:
        """Requirement 14: Autonomous agents possess zero merge authority."""
        controller = RepairLoopController(
            task_id="TASK-004",
            implementer_role="core_platform_engineer",
            reviewer_role="reviewer_security_engineer",
        )
        self.assertFalse(controller.can_merge())


if __name__ == "__main__":
    unittest.main()

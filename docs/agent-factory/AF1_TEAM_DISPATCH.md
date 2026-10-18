# TapJob Agent Factory — AF-1 Team Instantiation & Automated Worktree Dispatch

## 1. Overview & Objective

The TapJob Agent Factory (AF-1) establishes an operational multi-agent team and worktree dispatch mechanism for Google Antigravity 2.0. It enables an Engineering Manager to receive a high-level engineering objective, select specialized agent roles, establish immutable task contracts, dispatch work safely across isolated Git worktrees, supervise up to 5 internal repair loops, enforce separation of duties, and deliver verified pull requests ready for independent external review and human-gated merge.

---

## 2. Team Topology & Role Specialization

The Agent Factory defines 14 formal role profiles organized into two tiers:

### Base Team (6 Roles)
1. **`engineering_manager`**: Team orchestrator; responsible for objective decomposition, specialist selection, task contract definition, worktree dispatch, repair loop supervision, and consolidation. Zero merge authority.
2. **`core_platform_engineer`**: Implements Career Core domain logic (`services/api`, `services/worker`), API endpoints, database models, and unit/integration tests.
3. **`channel_whatsapp_engineer`**: Implements WhatsApp channel adapters, Evolution API integrations, and webhook ingestion behind the core `MessagingChannel` port. Prohibited from business logic mutations.
4. **`qa_sdet_engineer`**: Designs integration tests, adversarial suites, invariant validations, and regression barriers. Prohibited from loosening assertions.
5. **`reviewer_security_engineer`**: Conducts independent code, security, and architectural reviews. **Strictly read-only** (`enable_write_tools: false`). Prohibited from reviewing own work.
6. **`release_platform_ops_engineer`**: Manages Docker, CI workflows, and staging operational scaffolding under human gating. Zero production deployment authority.

### Dynamic Specialists (8 Roles)
1. **`mobile_engineer`**: Implements React Native / Expo screens consuming backend contracts.
2. **`conversation_ux_designer`**: Designs conversational dialogs, tone, prompt templates, and interaction states.
3. **`database_specialist`**: Designs PostgreSQL migrations, index optimizations, and query performance tuning.
4. **`ai_matching_specialist`**: Tunes matching heuristics, prompt templates, and evaluation datasets.
5. **`deep_security_specialist`**: Threat modeling, taint analysis, and prompt injection mitigation. **Strictly read-only** (`enable_write_tools: false`).
6. **`performance_reliability_specialist`**: Benchmarking, load testing, Redis cache tuning, and concurrency optimization.
7. **`accessibility_specialist`**: WCAG 2.1 compliance, contrast auditing, and screen-reader semantics.
8. **`product_data_analyst`**: Career analytics telemetry, funnel tracking, and funnel metrics.

---

## 3. Antigravity Runtime Contract & Profile Management

Empirical binary analysis of Google Antigravity 2.0 (`language_server.exe`) proved that custom subagents are defined dynamically in-session via the `define_subagent` tool:
- Subagents defined in-session are stored by Antigravity in `<brainDir>/.agents/agents/<name>/agent.md`.
- Unregistered disk files in `.agents/agents/` are not auto-discovered passively by the runtime.
- Profiles are centrally stored as validated JSON schemas in `tools/agent_factory/profiles/<role_id>.json`.
- The `TeamManager` (`tools/agent_factory/team.py`) validates profiles and provides:
  - `to_define_subagent_payload()`: formats payloads for runtime registration.
  - `to_agent_md()`: serializes standard Antigravity Markdown manifests.
  - Offline manifest synchronization to `tools/agent_factory/manifests/<role_id>/agent.md`.

---

## 4. Task Contract & Ownership Invariants

Every dispatch requires an immutable `TaskContract` (`tools/agent_factory/task_contract.py`):

```python
@dataclass
class TaskContract:
    task_id: str
    title: str
    description: str
    assigned_role: str
    base_commit_sha: str      # Exact 40-char SHA verified in Git
    target_branch: str        # Feature/chore branch, never main/master
    allowed_paths: list[str]  # Disjoint file tree ownership
    forbidden_paths: list[str]
    prohibited_actions: list[str]
    acceptance_criteria: list[str]
    reviewer_role: str        # Must differ from assigned_role
```

### Critical Serial Paths
The following paths are designated critical single-lane paths and **can never be dispatched in parallel**:
- `infra/` (Docker, compose, infrastructure)
- `services/api/alembic/` (Database migrations)
- `services/api/src/tapjob_api/database.py` (Core DB session factory)
- `tools/agent_guard/` (PreToolUse security guards and governance)

### Pairwise Ownership Conflict Detection
Before dispatching multiple tasks concurrently, `check_ownership_conflict(c1, c2)` verifies that:
1. Neither contract targets critical serial paths.
2. No path prefix in `c1.allowed_paths` overlaps with any path prefix in `c2.allowed_paths`.
Any collision causes an immediate `OwnershipConflictError`.

---

## 5. Automated Worktree Dispatch & Isolation Safeguards

The `WorktreeDispatcher` (`tools/agent_factory/worktree_dispatcher.py`) provides automated lifecycle management:

1. **Isolation Topology**: Worktrees are created outside the repository working tree in a sibling directory (`../tapjob-worktrees/<branch-slug>` or custom `TAPJOB_WORKTREES_DIR`).
2. **Branch Protection**: Dispatching on or creating branches named `main` or `master` is strictly prohibited (`InvalidBranchNameError`).
3. **Base Commit Pinning**: Every worktree is created from an exact, verified 40-character commit SHA (`git worktree add -b <branch> <path> <commit-sha>`).
4. **Dirty Worktree Protection**: Cleanup via `remove_worktree()` executes `git status --porcelain` to verify worktree cleanliness. If uncommitted changes exist, cleanup is blocked with `DirtyWorktreeError` to prevent silent data loss.
5. **Path Traversal Protection**: Worktree paths are strictly resolved and validated against directory escape attempts.

---

## 6. Internal Autonomous Repair Loop & Duty Separation

Self-healing and correction are governed by `RepairLoopController` (`tools/agent_factory/repair_loop.py`):

- **Budget Limit**: Autonomous iteration is capped at `MAX_INTERNAL_REPAIR_LOOPS = 5`.
- **Separation of Duties**: The author of changes cannot review or issue an approval verdict (`ReviewerSeparationError`). Reviewer roles default to `reviewer_security_engineer` or `qa_sdet_engineer`.
- **Zero Merge Authority**: `can_merge()` unconditionally returns `False`. No agent possesses merge authority.
- **Escalation Trigger**: If tests fail after 5 attempts or a blocking security finding is encountered, the phase transitions to `ESCALATE_TO_HUMAN`.

---

## 7. Review and Merge Governance Gates

1. **Internal Verification Gate**: All local quality checks must pass before PR creation (`ruff`, `mypy`, `pytest`, `npm run check`, `validate_governance.py`).
2. **Pull Request Gate**: PR must include objective, scope, test results, security audit, and rollback strategy.
3. **CI Gate**: 100% green status on all CI checks for the exact HEAD commit.
4. **Human Merge Gate**: Merging to `main` requires human review and confirmation. Autonomous self-merge and `gh pr merge` by agents are strictly blocked by `pre_tool_guard.py`.

---

## 8. CLI Usage & Bootstrapping

The Agent Factory provides a CLI interface in `tools/agent_factory/bootstrap.py`:

```bash
# List all 14 available agent roles and tool permissions
python -m tools.agent_factory.bootstrap --list-roles

# List active git worktrees and status
python -m tools.agent_factory.bootstrap --list-worktrees

# Export Antigravity agent.md manifests
python -m tools.agent_factory.bootstrap --export-manifests
```

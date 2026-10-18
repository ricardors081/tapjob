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

## 3. Architecture Boundary & Runtime Contract

The Agent Factory strictly separates repository-side logic from Antigravity runtime capabilities:

### Repository Side (Python & Contracts)
- **Role Registry** (`tools/agent_factory/profiles/<role_id>.json`): Central, version-controlled JSON definitions defining role names, permissions, and system prompts.
- **Profile Loader** (`tools/agent_factory/team.py`): Loads and validates `AgentProfile` instances with strict fail-closed boolean validation. Produces `to_define_subagent_payload()` and `to_agent_md()`.
- **Task Contracts** (`tools/agent_factory/task_contract.py`): Immutable, strongly typed schema defining task boundaries, allowed/forbidden paths, base SHA, and parallel safety.
- **Worktree Dispatcher** (`tools/agent_factory/worktree_dispatcher.py`): Manages Git worktree lifecycles outside the repository, enforcing branch conventions, dirty check guards, and scope validation.
- **Repair Loop Controller** (`tools/agent_factory/repair_loop.py`): Enforces separation of duties and caps autonomous correction loops at `MAX_INTERNAL_REPAIR_LOOPS = 5`.
- **CLI Helper** (`tools/agent_factory/bootstrap.py`): Emits runtime payloads and inspects team profiles and worktrees.

### Antigravity Runtime Side
- **Subagent Registration (`define_subagent`)**: Antigravity registers custom agent types dynamically in-session. The tool takes `{name, description, system_prompt, enable_write_tools, enable_subagent_tools, enable_mcp_tools}` and writes the agent definition to `<brainDir>/.agents/agents/<name>/agent.md`.
- **Persistence & Scope**: Subagents defined via `define_subagent` persist for the duration of the current Antigravity conversation. In a new conversation session, subagents must be re-registered if not present.
- **Subagent Invocation (`invoke_subagent`)**: Spawns subagents with `{TypeName, Role, Prompt, Model, Workspace}`. Antigravity executes them in isolated subprocesses and streams results back reactively.

---

## 4. Operational Walkthrough: Activating and Directing the Manager

### Question 1: "Como eu ativo o Manager?"
To activate the Engineering Manager in an Antigravity 2.0 session:
1. Generate the runtime payload using the repository helper:
   ```bash
   python -m tools.agent_factory.bootstrap --emit-runtime-payload engineering_manager
   ```
2. The orchestrating Antigravity agent calls the `define_subagent` tool using this exact payload.
3. The Engineering Manager is now registered and available as a subagent type named `engineering_manager`.

### Question 2: "Como eu dou uma missão para ele?"
Once registered, the parent orchestrator invokes the Engineering Manager using `invoke_subagent`:
```json
{
  "Subagents": [
    {
      "TypeName": "engineering_manager",
      "Role": "Engineering Manager",
      "Prompt": "Deliver WP2.4: Implement Candidate Search Filters. Canonical base SHA is 5c56526e18928babc9b2b8ea4ecf022d68c2d1a4. Decompose tasks, allocate worktrees, supervise implementation and review, and report back when PR is ready."
    }
  ]
}
```

### Question 3: "O que acontece depois?"
1. **Decomposition & Contract Formulation**:
   The Engineering Manager breaks down the outcome into one or more `TaskContract` instances (`tools/agent_factory/task_contract.py`).
2. **Canonical Dispatch**:
   The Manager dispatches tasks using `dispatcher.dispatch_task(contract)`. The dispatcher:
   - Validates the contract schema and fail-closed types.
   - Verifies the 40-character base commit SHA exists in Git.
   - Rejects `main` or `master` branches.
   - Enforces human gating if `human_gate_required=True`.
   - Checks parallel safety and pairwise path ownership against all active contracts.
   - Reserves path ownership.
   - Creates an isolated Git worktree in `../tapjob-worktrees/<task-slug>`.
3. **Specialist Delegation**:
   The Manager registers the assigned specialist (e.g. `core_platform_engineer`) via `define_subagent` (if not already registered) and invokes them via `invoke_subagent`, passing the worktree directory path and contract instructions.
4. **Implementation & Scope Validation**:
   The specialist works exclusively within the allocated worktree. Before accepting changes, the Manager verifies that altered files respect the contract using `dispatcher.validate_worktree_scope(contract, worktree_path)`. Any modification outside `allowed_paths` or touching `forbidden_paths` triggers immediate failure.
5. **Quality Gate & Independent Review**:
   - The Manager invokes `qa_sdet_engineer` to run test suites.
   - The Manager invokes `reviewer_security_engineer` (strictly read-only) to review the diff.
6. **Internal Repair Loop**:
   If review returns `CHANGES_REQUIRED` or tests fail, the Manager records the iteration in `RepairLoopController`. Up to 5 correction cycles are permitted. On the 6th failure, the loop halts and escalates to a human operator (`ESCALATE_TO_HUMAN`).
7. **Consolidation & PR**:
   Upon approval, the Manager consolidates the work into the feature branch, pushes to GitHub, opens a pull request, monitors CI, and signals `READY_FOR_EXTERNAL_REVIEW`.
8. **Safe Cleanup**:
   Worktrees are removed via `dispatcher.release_task(task_id)` only after verification. Dirty worktrees are never force-deleted.
9. **Human Merge Gate**:
   Merging into `main` requires human review and confirmation. Autonomous self-merge is blocked by governance rules and `pre_tool_guard.py`.

---

## 5. TaskContract Specification & Ownership Invariants

Every dispatch requires an immutable `TaskContract` (`tools/agent_factory/task_contract.py`):

```python
@dataclass
class TaskContract:
    task_id: str  # Unique task identifier (e.g. TASK-WP2.4-001)
    outcome: str  # High-level outcome description
    scope: list[str]  # Non-empty list of included work items
    non_goals: list[str]  # Explicitly excluded items
    canonical_base_sha: str  # Exact 40-character hex commit SHA verified in Git
    owner_role: str  # Assigned role from ALL_ROLES
    allowed_paths: list[str]  # Disjoint file tree ownership (non-empty)
    forbidden_paths: list[str]  # Explicitly forbidden file paths
    dependencies: list[str]  # Peer task dependencies
    contracts_frozen: bool  # Strict boolean; required for parallel dispatch
    test_requirements: list[str]  # Non-empty list of required tests
    acceptance_criteria: list[str]  # Non-empty list of acceptance criteria
    risk_level: str  # LOW, MEDIUM, HIGH, or CRITICAL
    parallel_safe: bool  # Strict boolean; True allows concurrent dispatch
    human_gate_required: bool  # Strict boolean; True blocks autonomous dispatch
    target_branch: str = ""  # Feature branch (defaults to feat/<task-id-slug>)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Critical Serial Paths
The following paths are designated critical serial paths and **can never be modified concurrently** by parallel tasks:
- `services/api/alembic/` (Database migrations)
- `services/api/src/tapjob_api/models/` (Core database models & schemas)
- `packages/product-config/` (Product configuration & tokens)
- `packages/design-tokens/` (Design tokens)
- `.agents/` (Agent governance rules & hooks)
- `tools/agent_guard/` (PreToolUse security guards)
- `infra/` (Docker, compose, infrastructure)

### Pairwise Ownership Conflict Detection
Before dispatching multiple tasks concurrently, `contract.check_ownership_conflict(other)` verifies that:
1. Neither contract targets critical serial paths concurrently.
2. No path prefix in `allowed_paths` overlaps with any path prefix in the other contract.
3. Contracts are based on the same `canonical_base_sha`.
Any collision causes an immediate `OwnershipCollisionError`.

---

## 6. Automated Worktree Dispatch & Isolation Safeguards

The `WorktreeDispatcher` (`tools/agent_factory/worktree_dispatcher.py`) provides automated lifecycle management:

1. **Isolation Topology**: Worktrees are created strictly outside the repository working tree in a sibling directory (`../tapjob-worktrees/<task-slug>` or custom `TAPJOB_WORKTREES_DIR`). Setting `worktrees_dir` to `repo_root` or inside `repo_root` is forbidden.
2. **Branch Protection**: Dispatching on or creating branches named `main` or `master` is strictly prohibited (`InvalidBranchNameError`).
3. **Base Commit Pinning**: Every worktree is created from an exact, verified 40-character hexadecimal commit SHA matching `^[0-9a-fA-F]{40}$`.
4. **Persistent Cross-Process Task Registry**: All active task reservations and contracts persist to `<worktrees_dir>/.tapjob-agent-factory/registry.json` using atomic file replacement and directory locking (`_registry_lock`). Separate dispatcher instances and subprocesses reconstruct reservations from disk, preventing contract overwrites and detecting cross-instance path collisions. Corrupted or unparseable registries fail closed (`CorruptRegistryError`). Dispatcher provides `reconcile_registry()` to cleanly prune externally removed worktrees while keeping active reservations intact.
5. **Role Permission & Scope Containment Enforcement**: Before creating a worktree, `_validate_role_ownership(contract)` checks the assigned role against the role registry. Write tasks assigned to read-only roles are rejected (`RolePermissionError`). Every declared `allowed_path` must be fully contained within the role's declared `ownership_paths` (`RoleOwnershipViolationError`).
6. **Dirty Worktree Protection**: Cleanup via `remove_worktree()` executes `git status --porcelain`. If uncommitted changes exist, cleanup is blocked with `DirtyWorktreeError`. Force deletion (`--force`) has been completely eliminated from production paths.
7. **Target Bounds Protection**: `remove_worktree()` strictly refuses `repo_root`, paths outside `worktrees_dir`, or directories that are not registered Git worktrees belonging to the repository.
8. **Scope Enforcement & Mandatory Lifecycle Gate**: `validate_worktree_scope()` inspects all unstaged, staged, and committed changes against `allowed_paths` and `forbidden_paths`, blocking path traversal and directory escapes. If `git diff` or `git status` fails, validation fails closed with `ScopeViolationError`. Furthermore, `verify_task_scope(task_id)` is a mandatory lifecycle gate that verifies the worktree diff against the contract and marks the task as `scope_verified`. Calling `release_task(task_id)` without prior successful verification raises `ScopeVerificationRequiredError`.

---

## 7. Internal Autonomous Repair Loop & Duty Separation

Self-healing and correction are governed by `RepairLoopController` (`tools/agent_factory/repair_loop.py`):

- **Budget Limit**: Autonomous iteration is capped at `MAX_INTERNAL_REPAIR_LOOPS = 5`. The constructor enforces `1 <= max_loops <= 5`; values `> 5` are strictly rejected.
- **Separation of Duties**: The author of changes cannot review or issue an approval verdict (`ReviewerSeparationError`). Reviewer roles default to `reviewer_security_engineer` or `qa_sdet_engineer`.
- **Zero Merge Authority**: `can_merge()` unconditionally returns `False`. No agent possesses merge authority.
- **Escalation Trigger**: If tests fail after 5 attempts or a blocking security finding is encountered, the controller transitions to `ESCALATE_TO_HUMAN`.

---

## 8. CLI Usage & Bootstrapping

The Agent Factory provides a CLI interface in `tools/agent_factory/bootstrap.py`:

```bash
# List all 14 available agent roles and tool permissions
python -m tools.agent_factory.bootstrap --list-roles
python -m tools.agent_factory.bootstrap --list-team

# Emit the exact define_subagent JSON payload for Antigravity registration
python -m tools.agent_factory.bootstrap --emit-runtime-payload engineering_manager
python -m tools.agent_factory.bootstrap --emit-runtime-payload reviewer_security_engineer

# List active git worktrees and cleanliness status
python -m tools.agent_factory.bootstrap --list-worktrees

# Export Antigravity agent.md manifests to destination directory
python -m tools.agent_factory.bootstrap --export-manifests
python -m tools.agent_factory.bootstrap --export-manifests custom/path
```

---

## 9. Test Suite & Verification

The Agent Factory is verified by an extensive 25-test unit suite in `tools/agent_factory/test_agent_factory.py`, executed both locally and during GitHub CI:

```bash
uv run python -m unittest discover -s tools/agent_factory -p "test_*.py"
```

The test suite exercises:
- 14 agent profiles, fail-closed boolean validation, and Antigravity payload serialization.
- TaskContract schema validation, 40-char base SHA pinning, critical serial path locks, and pairwise disjoint ownership collision checks.
- WorktreeDispatcher lifecycle: creation, dirty worktree protection, scope containment, and traversal defenses.
- Persistent task registry: cross-instance discovery, corrupted registry fail-closed behavior, atomic persistence, and stale reconciliation.
- Role permission and scope containment: write rejection for read-only roles, out-of-boundary path rejection.
- Mandatory lifecycle gates: git diff failure handling and `verify_task_scope` gating before task release.
- RepairLoopController: budget cap (5 loops), reviewer separation of duties, and unconditional `can_merge() == False`.

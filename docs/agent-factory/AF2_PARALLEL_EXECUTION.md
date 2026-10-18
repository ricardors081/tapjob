# TapJob Agent Factory — AF-2 Multi-Agent Parallel Execution & Consolidation

## 1. Overview & Objective

The TapJob Agent Factory AF-2 milestone extends the operational multi-agent capabilities established in AF-1 (formal team instantiation and isolated worktree dispatch) into an automated, deterministic framework for **Multi-Agent Parallel Execution and Consolidation Gating** under Google Antigravity 2.0.

AF-2 allows the Engineering Manager to take a high-level engineering mission, decompose it into a structured directed acyclic graph (DAG) of task contracts, partition those tasks into deterministic execution groups, execute independent tasks concurrently across isolated Git worktrees, supervise parallel repair loops with strict reviewer separation, enforce exact scope boundaries, and evaluate an automated readiness gate for branch consolidation **without performing autonomous merges**.

---

## 2. Architecture & Components

The AF-2 parallel execution subsystem is built strictly around four repository-side components and their integration with Antigravity 2.0 runtime primitives:

```
+--------------------------------------------------------------------------------------------------+
|                                    Google Antigravity 2.0                                        |
|  +--------------------+    +-----------------------+    +-------------------------------------+  |
|  |   define_subagent  |    |    invoke_subagent    |    |           send_message              |  |
|  +--------------------+    +-----------------------+    +-------------------------------------+  |
+---------------------------------------------|----------------------------------------------------+
                                              | Orchestrates
                                              v
+--------------------------------------------------------------------------------------------------+
|                                  TapJob Agent Factory (AF-2)                                     |
|                                                                                                  |
|   +-----------------------+       Deterministic       +-----------------------+                  |
|   |    MissionContract    | ------------------------> |    MissionPlanner     |                  |
|   | (schema, DAG, SHA-40) |                           |  (toposort, groups)   |                  |
|   +-----------------------+                           +-----------------------+                  |
|               |                                                   |                              |
|               | State Persistence                                 | Execution Plan               |
|               v                                                   v                              |
|   +-----------------------+  Process-Safe Lock File   +-----------------------+                  |
|   |    MissionRegistry    | <-----------------------> |  MissionCoordinator   |                  |
|   |  (atomic, multi-PID)  |                           |  (dispatch, review)   |                  |
|   +-----------------------+                           +-----------------------+                  |
|                                                                   |                              |
|                                                 Worktree Control  v                              |
|                                                       +-----------------------+                  |
|                                                       |  WorktreeDispatcher   |                  |
|                                                       | (worktrees, registry) |                  |
|                                                       +-----------------------+                  |
+--------------------------------------------------------------------------------------------------+
```

### 2.1 MissionContract (`tools/agent_factory/mission_contract.py`)
Represents an immutable, strongly typed engineering mission:
- **`mission_id`**: Non-empty identifier adhering to `^[a-zA-Z0-9_-]+$`.
- **`canonical_base_sha`**: Exact 40-character hexadecimal commit SHA pinned across the entire mission.
- **`objective`**: Human-readable definition of the intended engineering outcome.
- **`tasks`**: Ordered sequence of `TaskContract` items forming the execution DAG.
- **Strict Invariants**:
  - All child tasks inherit and must match the mission's exact `canonical_base_sha`.
  - All `task_id` values within a mission must be globally unique.
  - All `target_branch` values within a mission must be distinct.
  - Every dependency declared in `task.dependencies` must reference a valid `task_id` defined within the mission.

### 2.2 MissionPlanner (`tools/agent_factory/mission_planner.py`)
Computes an immutable `ExecutionPlan` consisting of sequential `ExecutionGroup` stages:
- **Topological Sorting & Cycle Detection**: Builds an adjacency graph and computes in-degrees. Detects dependency cycles fail-closed via DFS.
- **Critical Serial Path Isolation**: Any task whose `allowed_paths` touch `CRITICAL_SERIAL_PATHS` (such as `.agents/`, `tools/agent_guard/`, `packages/contracts/`, `infra/`, etc.) is strictly isolated into a single-task execution group (`is_parallel=False`), regardless of dependency depth.
- **Pairwise Conflict Avoidance**: Tasks in the same dependency layer are checked for path overlap (`check_ownership_conflict`). Conflicting tasks are automatically partitioned into successive execution groups to guarantee collision-free concurrent execution.
- **Determinism**: Sorting and grouping use deterministic lexical ordering on `task_id` to ensure reproducible execution plans across processes and test environments.

### 2.3 MissionRegistry (`tools/agent_factory/mission_registry.py`)
Provides persistent, crash-resilient storage for mission state outside the repository (`<worktrees_dir>/.tapjob-agent-factory/missions/<mission_id>.json`):
- **Cross-Process Mutex**: Uses directory-based lock files (`.mission_lock`) containing PID, token, and timestamp. Dead-process locks are detected and safely reclaimed via OS-level PID liveness checks (`ctypes.windll.kernel32.OpenProcess` on Windows, `os.kill(pid, 0)` on POSIX).
- **Atomic Writes**: Writes mission records to temporary files followed by atomic filesystem replacement (`os.replace`) to prevent corruption from abrupt aborts.
- **Strict Validation**: Fails closed if any registry file contains missing fields, unparseable states, or inconsistent task states.

### 2.4 MissionCoordinator (`tools/agent_factory/mission_coordinator.py`)
Executes the mission lifecycle and orchestrates worktree dispatch:
- **Group Dispatching**: Coordinates parallel worktree creation via `WorktreeDispatcher`.
- **Partial Dispatch Rollback**: If any task in an execution group fails to dispatch, all successfully created worktrees in that group are rolled back immediately and the mission transitions to `BLOCKED`.
- **Reviewer Separation**: Enforces that the reviewer role cannot match the implementing owner role (`ReviewerSeparationError`).
- **Bounded Repair Loop**: Enforces `MAX_INTERNAL_REPAIR_LOOPS = 5`. On the 6th failed attempt, the task transitions to `BLOCKED` and halts autonomous dispatch, escalating to human intervention.
- **Exact TOCTOU Scope Verification**: Verifies committed diffs and working directory cleanliness against the assigned `allowed_paths`. Freezes the exact verified HEAD commit SHA.
- **Consolidation Readiness Gate**: Evaluates all tasks across the mission to ensure:
  1. All tasks are `SCOPE_VERIFIED`.
  2. Every task has a formal `APPROVE` verdict from an independent reviewer role.
  3. No task exceeded the repair budget.
  4. All worktrees remain clean with zero uncommitted changes.
  5. Current worktree HEAD matches the exact `scope_verified_head_sha` recorded during review (preventing post-review rogue commits).

---

## 3. Mission & Task Lifecycle State Machines

### 3.1 Mission State Machine (`MissionState`)

```mermaid
stateDiagram-v2
    [*] --> PLANNED: create_mission()
    PLANNED --> DISPATCHING: dispatch_execution_group()
    DISPATCHING --> RUNNING: all group worktrees active
    DISPATCHING --> BLOCKED: dispatch error (rollback)
    RUNNING --> REVIEWING: all group tasks implemented
    REVIEWING --> REPAIRING: review requested changes
    REPAIRING --> RUNNING: repair implemented
    REVIEWING --> READY_FOR_CONSOLIDATION: all tasks approved & scope-verified
    READY_FOR_CONSOLIDATION --> CONSOLIDATED: human-gated PR merge
    RUNNING --> BLOCKED: repair budget exhausted (attempt 6)
    RUNNING --> FAILED: unrecoverable scope violation
```

### 3.2 Task Mission State Machine (`TaskMissionState`)

```mermaid
stateDiagram-v2
    [*] --> PENDING: mission planned
    PENDING --> DISPATCHED: worktree created
    DISPATCHED --> IMPLEMENTED: code changes committed
    IMPLEMENTED --> CHANGES_REQUESTED: review verdict CHANGES_REQUIRED (repairs <= 5)
    CHANGES_REQUESTED --> IMPLEMENTED: repair commit added
    CHANGES_REQUESTED --> BLOCKED: repair budget exhausted (> 5)
    IMPLEMENTED --> APPROVED: review verdict APPROVE
    APPROVED --> SCOPE_VERIFIED: verify_task_scope() passed
    SCOPE_VERIFIED --> [*]: ready for consolidation / release
```

---

## 4. Invariants & Safety Boundaries

1. **Strictly No Autonomous Merging**: The Agent Factory and `MissionCoordinator` evaluate readiness and report `READY_FOR_CONSOLIDATION`. Branch merging remains strictly human-gated (`gh pr merge` is prohibited in autonomous workflows).
2. **Reviewer Separation of Duties**: The task implementer can never approve their own work. Code reviews require an independent reviewer role (e.g., `reviewer_security_engineer` or `qa_sdet_engineer`).
3. **Repair Budget Guard**: Internal repair loops are strictly bounded to 5 attempts. A 6th attempt triggers an uncatchable escalation to human operators.
4. **Time-of-Check to Time-of-Use (TOCTOU) Protection**: Scope verification freezes the verified HEAD commit SHA. Any subsequent commit added to the worktree before consolidation invalidates readiness.
5. **No Direct Main Commits**: All worktree branches are restricted to feature conventions (`chore/`, `feat/`, `fix/`, `docs/`, `test/`, `security/`, `ops/`). Direct checkouts of `main` or `master` fail closed.
6. **Zero Product Alterations in Governance Scope**: AF-2 modifies zero Career Core or mobile code. It is purely agent infrastructure.

---

## 5. Live Multi-Agent Runtime Proof Evidence

A real, end-to-end multi-agent execution was performed under Google Antigravity 2.0 to validate the AF-2 architecture in live operation.

### 5.1 Mission Definition & Dispatch
- **Mission ID**: `af2-live-proof-01`
- **Canonical Base SHA**: `80cc5d6477dfd71a9f209c53f273bda9c56e38fa`
- **Tasks**:
  1. `af2-proof-task-doc`: Assigned to `engineering_manager`, target branch `docs/af2-task-doc`, allowed paths `["docs/agent-factory/"]`.
  2. `af2-proof-task-harness`: Assigned to `qa_sdet_engineer`, target branch `test/af2-task-harness`, allowed paths `["tests/fixtures/"]`.
- **Planning Result**: Group 0 contained both tasks concurrently (`is_parallel=True`).
- **Dispatch**:
  - Worktree 1: `C:\Projetos\tapjob-worktrees\af2-proof-task-doc`
  - Worktree 2: `C:\Projetos\tapjob-worktrees\af2-proof-task-harness`

### 5.2 Antigravity Subagent Registration & Invocation
Two specialized Antigravity subagents were defined and invoked concurrently:
1. **`af2_doc_specialist`** (Conversation ID: `3722de27-73a7-4b35-ad60-38e27bfe2ec6`):
   - Result: `DOC_SPECIALIST_SUCCESS: AGENTS.md verified in worktree C:/Projetos/tapjob-worktrees/af2-proof-task-doc`
2. **`af2_harness_specialist`** (Conversation ID: `99371fe5-2960-4e56-bc97-2c73ed3a9100`):
   - Result: `HARNESS_SPECIALIST_SUCCESS: AGENTS.md verified in worktree C:/Projetos/tapjob-worktrees/af2-proof-task-harness`

### 5.3 Simulated Repair Loop & Independent Review
- **Task 1 Review Cycle**:
  1. `reviewer_security_engineer` issued verdict `CHANGES_REQUIRED` (findings: simulated markdown formatting correction). Task state transitioned to `CHANGES_REQUESTED` with `repair_count: 1`.
  2. Implementer committed repair changes (`record_task_implemented`).
  3. `reviewer_security_engineer` re-reviewed and issued verdict `APPROVE`. Task state transitioned to `APPROVED`.
- **Task 2 Review Cycle**:
  1. `reviewer_security_engineer` issued verdict `APPROVE` on first pass. Task state transitioned to `APPROVED`.

### 5.4 Scope Verification & Consolidation Readiness
- Exact scope verification executed on both worktrees via `coordinator.verify_task_scope`:
  - `af2-proof-task-doc`: `SCOPE_VERIFIED` at HEAD `80cc5d6477dfd71a9f209c53f273bda9c56e38fa`.
  - `af2-proof-task-harness`: `SCOPE_VERIFIED` at HEAD `80cc5d6477dfd71a9f209c53f273bda9c56e38fa`.
- Consolidation evaluation executed:
  - `ready`: `True`
  - `status`: `"READY_FOR_CONSOLIDATION"`
  - `blockers`: `[]`
  - `state`: `MissionState.READY_FOR_CONSOLIDATION`

### 5.5 Managed Cleanup
- Safe teardown executed via `coordinator.cleanup_mission("af2-live-proof-01")`. Both isolated worktrees were removed without data loss or lingering locks. Transient proof branches were cleaned up.

---

## 6. Testing & Verification

The AF-2 test harness in `tools/agent_factory/test_mission.py` executes 20 comprehensive unit and integration scenarios:

| # | Test Scenario | Verified Invariant |
|---|---------------|--------------------|
| 1 | `test_mission_strict_parsing` | Validates strict boolean and string parsing in `MissionContract`. |
| 2 | `test_all_tasks_must_match_base_sha` | Rejects child tasks whose `canonical_base_sha` diverges from mission base. |
| 3 | `test_unique_task_ids_required` | Fails closed on duplicate `task_id` definitions within a mission. |
| 4 | `test_unique_target_branches_required` | Prohibits multiple tasks targeting the same branch. |
| 5 | `test_deterministic_topological_plan` | Computes identical, deterministic execution groups across repeated runs. |
| 6 | `test_dependency_cycle_detection` | Detects circular dependencies (A -> B -> A) and raises `DependencyCycleError`. |
| 7 | `test_critical_serial_paths_isolated` | Tasks touching critical paths are placed into dedicated single-task groups. |
| 8 | `test_conflicting_paths_partitioned` | Overlapping ownership paths in same dependency layer are partitioned sequentially. |
| 9 | `test_registry_persistence_atomic_writes` | Registry records survive reload and use atomic replacement. |
| 10 | `test_corrupt_registry_fails_closed` | Corrupted JSON or invalid schema in registry triggers `CorruptMissionRegistryError`. |
| 11 | `test_cross_process_locking` | Directory lock enforces single-writer mutual exclusion; dead PID locks are stolen. |
| 12 | `test_group_dispatch_creates_worktrees` | Dispatches worktrees in parallel with correct branch and base SHA bindings. |
| 13 | `test_partial_dispatch_failure_rolls_back` | If one task in group fails dispatch, all peer worktrees are cleaned up. |
| 14 | `test_reviewer_separation_enforced` | Task owner cannot review their own task (`ReviewerSeparationError`). |
| 15 | `test_repair_loop_accounting` | Tracks repair iterations up to `MAX_INTERNAL_REPAIR_LOOPS = 5`. |
| 16 | `test_repair_attempt_6_blocked_escalated` | 6th failed attempt raises `BudgetExhaustedError` and transitions to `BLOCKED`. |
| 17 | `test_toctou_head_mutation_detected` | Post-review commit added to worktree blocks consolidation readiness. |
| 18 | `test_dirty_worktree_prevents_consolidation`| Uncommitted working tree changes block consolidation readiness. |
| 19 | `test_all_tasks_valid_ready_for_consolidation`| When all tasks pass review and scope check, status is `READY_FOR_CONSOLIDATION`. |
| 20 | `test_cleanup_cannot_erase_unresolved_or_dirty`| Refuses to clean up unverified or dirty worktrees without explicit `force=True`. |

---

## 7. Recovery, Diagnostics & Escalation Procedures

1. **Deadlock Recovery**: If a worker process dies holding `.mission_lock`, the lock manager automatically validates the PID from `owner.json`. If the process is dead, the stale lock is removed and acquisition proceeds.
2. **Budget Exhaustion Escalation**: When a task reaches 5 failed repair iterations, the coordinator marks it `BLOCKED` and halts execution group progression. The Engineering Manager presents the findings directly to the human engineer for intervention.
3. **Dirty Worktree Protection**: If an agent process crashes leaving uncommitted files, cleanup refuses deletion (`DirtyWorktreeError`) to ensure candidate work is not destroyed.
4. **Registry Inspection CLI**:
   - List active missions: `python -m tools.agent_factory.bootstrap --list-missions`
   - Show mission details: `python -m tools.agent_factory.bootstrap --show-mission <mission_id>`

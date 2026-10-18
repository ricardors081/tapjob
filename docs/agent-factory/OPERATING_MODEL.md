# TapJob Agent Factory — Operating Model & Worktree Policy

This document defines the execution flow, internal repair budget, escalation triggers, and worktree parallelism policies for the TapJob Agent Factory.

---

## 1. Target Operating Model Flow

```
USER / PRODUCT OUTCOME
        ↓
TAPJOB ENGINEERING MANAGER
        ↓
TASK DECOMPOSITION & CONTRACT DEFINITION
        ↓
DYNAMIC SPECIALISTS ASSIGNMENT
        ↓
ISOLATED GIT WORKTREES (When parallelizing independent modules)
        ↓
IMPLEMENTATION & UNIT VERIFICATION
        ↓
QA / SDET ADVERSARIAL & INTEGRATION SUITES
        ↓
INDEPENDENT CODE & SECURITY REVIEW
        ↓
INTERNAL REPAIR LOOP (Max 5 iterations)
        ↓
PULL REQUEST CREATION / UPDATE
        ↓
GITHUB ACTIONS CI VERIFICATION (Exact PR HEAD)
        ↓
READY_FOR_EXTERNAL_REVIEW
        ↓
HUMAN / EXTERNAL INDEPENDENT REVIEW
        ↓
HUMAN-GATED MERGE TO CANONICAL MAIN
        ↓
POST-MERGE SMOKE & HEALTH VERIFICATION
```

---

## 2. Internal Repair Budget & Autonomous Problem-Solving

### Budget Rule
- **`MAX_INTERNAL_REPAIR_LOOPS = 5`**: The engineering team is allocated up to 5 consecutive self-correction iterations to resolve test failures, lint violations, type errors, or CI build issues.
- **Self-Sufficiency**: Agents must resolve routine engineering questions autonomously using existing codebase patterns, accepted ADRs, typed contracts, and canonical documentation without interrupting the human operator.

### Escalation Triggers
The Engineering Manager must pause the team and escalate to the human user **only under the following explicit conditions**:
1. **Unresolved Blocker**: The repair budget of 5 loops is exhausted without a passing build;
2. **Product Ambiguity**: A requirement is genuinely missing or self-contradictory with major UX implications;
3. **Architectural Tradeoff**: A decision involves significant cost, new third-party vendors, or irreversible coupling;
4. **Security/Privacy Policy**: A potential violation of LGPD or security standards requires executive sign-off;
5. **Tier C Action**: The workflow requires execution of an action that requires a human gate (e.g. production deploy, secret rotation).

---

## 3. Worktree & Parallelism Policy

### Parallel Execution Guidelines
- **Granular Integration Units**: Large feature goals must be broken down into coherent, individually reviewable Pull Requests.
- **Contract-First Sequencing**: Before launching parallel specialists, the shared contract (OpenAPI schema, database schema, design token, or interface types) must be defined and validated.
- **Isolated Git Worktrees**: When specialists work on independent modules in parallel, they must operate in isolated Git worktrees (`git worktree add`) to avoid file system race conditions and lock conflicts.

### Safe vs. Unsafe Parallelism

| Safe Parallelism Patterns (APPROVED) | Unsafe Parallelism Patterns (PROHIBITED) |
| :----------------------------------- | :--------------------------------------- |
| Core API endpoint implementation + QA integration test suite creation (once contract is frozen) | Multiple agents simultaneously modifying the same Alembic database migration |
| WhatsApp channel adapter + Mobile screen consuming frozen API contract | Multiple agents editing the same state machine or orchestration workflow service concurrently |
| Documentation updates + Independent QA test preparation | Multiple agents altering core database models (`services/api/src/tapjob_api/models/`) at the same time |
| Design token additions + Webhook payload parser implementation | Multiple agents refactoring shared tooling or monorepo configuration concurrently |

---

## 4. Transition Roadmap

- **AF-0 (Current Checkpoint)**: Governance and safety foundation, guards, validation tooling, and documentation only. No custom agents created yet.
- **AF-1 (Next Checkpoint)**: Formal instantiation of the base team definitions (`.agents/agents/`), agent prompts, and automated worktree dispatching.

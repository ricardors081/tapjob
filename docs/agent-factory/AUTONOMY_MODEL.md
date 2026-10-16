# TapJob Agent Factory — Autonomy & Safety Model

This document establishes the autonomy boundaries, operational tiers, and multi-layered defense-in-depth model for autonomous agent orchestration in TapJob.

---

## 1. Autonomy Tiers

TapJob operates under a three-tiered autonomy framework designed to maximize engineering throughput while ensuring zero uncontrolled real-world side effects.

### Tier A — Autonomous Development (Default Operating Mode)
Agents have full autonomy to plan, implement, and verify engineering work without interrupting the human operator.
- **Permitted without confirmation**:
  - Creating and switching feature/worktree branches (`chore/*`, `feat/*`, `fix/*`, `docs/*`);
  - Reading repository code, documentation, and configuration;
  - Writing code, tests, and documentation;
  - Running local tests, linters, formatters, typecheckers, and local builds;
  - Running local Docker Compose stacks for smoke and integration tests;
  - Creating commits on feature branches;
  - Pushing feature branches to remote (`origin <feature-branch>`);
  - Creating and updating Pull Requests via `gh` CLI;
  - Monitoring GitHub Actions CI status;
  - Executing internal repair loops (up to 5 iterations) to fix failed checks.

### Tier B — Controlled Staging (Task-Scoped Authorization)
Operations affecting shared staging infrastructure are permitted **only when the active task explicitly authorizes staging scope**:
- **Permitted only with task scope**:
  - Deploying immutable container images to EasyPanel staging;
  - Running forward Alembic migrations on the staging database;
  - Updating staging Evolution API instance settings or webhook routes;
  - Triggering controlled canary tests against dedicated staging test identities;
  - Uploading test assets to staging Cloudflare R2 buckets.

### Tier C — Human Gate (Strictly Blocked for Autonomous Agents)
High-impact, destructive, or irreversible actions **always require explicit human confirmation and approval**:
- **Prohibited without human gate**:
  - Production deployments of any service;
  - Destructive database migrations (e.g., dropping columns, tables, or databases);
  - Direct messaging to real candidates, customers, or third-party WhatsApp numbers;
  - Secret, token, or credential rotation or revocation;
  - DNS record updates, domain ownership modifications, or Cloudflare zone changes;
  - Billing, payment provider, or commercial contract modifications;
  - Merging Pull Requests into canonical `main`.

---

## 2. Multi-Layer Defense-in-Depth Architecture

TapJob operates on a GitHub Free private repository where server-side branch protection rules are not available without paid licensing. Therefore, TapJob implements an eight-layer defense-in-depth system:

```
[ Layer 1: Antigravity Sandbox & Tooling Controls ]
                     ↓
[ Layer 2: Root AGENTS.md Invariants ]
                     ↓
[ Layer 3: Modular Antigravity Rules (.agents/rules/*.md) ]
                     ↓
[ Layer 4: Antigravity PreToolUse Hook (pre_tool_guard.py) ]
                     ↓
[ Layer 5: Local Git Pre-Push Hook (.githooks/pre-push) ]
                     ↓
[ Layer 6: GitHub Actions CI Verification Pipeline ]
                     ↓
[ Layer 7: Independent External Review Gate ]
                     ↓
[ Layer 8: Human-Gated Merge & Post-Merge Verification ]
```

### Honest Disclosure on Defense Limitations
- **Local Enforcement**: Local git hooks and Antigravity guards are client-side controls. They are not cryptographically equivalent to server-side branch protection rules.
- **Fail-Closed Design**: The Antigravity PreToolUse guard fails closed (`deny`) if an unhandled exception occurs.
- **Process Compliance**: Procedural compliance remains mandatory: no PR may merge without an independent review and 100% green CI on the exact HEAD commit.

---

## 3. Model Context Protocol (MCP) Safety Invariants

As TapJob evolves toward AF-1 and beyond, Model Context Protocol (MCP) servers and custom tool integrations may be introduced. The presence of non-terminal tools introduces alternative invocation pathways that must adhere to the same governance:

1. **GitHub MCP Gating**: Any GitHub MCP server or tool with capability to merge Pull Requests, approve PRs, or mutate protected branch refs (`main`/`master`) remains strictly **disabled** during baseline governance. Enabling such tools without independent guardrails is prohibited.
2. **Production & Messaging MCP Gating**: Any MCP tool capable of triggering live Evolution API messaging, production deployments, or database migrations is bound to the exact same **Tier B / Tier C authority model**. Autonomous agents may never invoke live sends or production mutations through MCP interfaces.
3. **No Safeguard Bypass**: Enabling MCP tools must never silently bypass Terminal/PreToolUse safety invariants. All tool calls, whether native, CLI-based, or MCP-driven, are subject to equivalent deterministic safety gating.


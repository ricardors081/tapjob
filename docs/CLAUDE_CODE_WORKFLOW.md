# Claude Code Development Workflow — TapJob

## 1. Objective

Use Claude Code as an implementation agent without letting session context become the source of truth.

GitHub documentation, ADRs, work packages, tests and PR evidence remain authoritative.

## 2. Recommended skill/tool stack

### A. grill-me — requirements/design gate
Use before a work package or expensive architectural choice.

Purpose:
- surface hidden assumptions;
- resolve acceptance criteria;
- clarify boundaries;
- challenge scope before code.

Recommended usage:
- WP kickoff;
- ADR;
- connector design;
- user-facing workflow;
- security-sensitive feature.

Do not use it to endlessly reopen decisions already Accepted unless new evidence exists.

Reference:
https://github.com/RobMitt/grill-me-skill

### B. Anthropic feature-dev
Use for non-trivial feature implementation after the work package is approved.

Purpose:
- explore codebase;
- design implementation;
- execute with quality review.

Official plugin reference:
https://github.com/anthropics/claude-plugins-official/tree/main/plugins/feature-dev

### C. Anthropic code-review
Run on every meaningful PR before independent final review.

Purpose:
- parallel code review;
- CLAUDE.md/AGENTS compliance;
- bug detection;
- history-aware review.

Official plugin reference:
https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review

### D. Anthropic frontend-design
Use when implementing or reshaping UI.

Purpose:
- deliberate visual direction;
- avoid generic AI-generated interfaces;
- improve typography/layout/detail.

It does not override TapJob design tokens or product UX rules.

Official plugin reference:
https://github.com/anthropics/claude-plugins-official/tree/main/plugins/frontend-design

### E. Expo official skills
Use for every Expo/EAS implementation task.

Install/use official Expo agent skills rather than relying on stale generic React Native knowledge.

Reference:
https://github.com/expo/skills

### F. repo-security-review
Use:
- before adopting a large upstream module;
- before security-sensitive PRs;
- before release.

Reference:
https://github.com/Consensys/repo-security-review

### G. Superpowers — optional engineering discipline
Useful subskills:
- writing plans;
- test-driven development;
- systematic debugging;
- verification before completion.

Avoid running overlapping planning frameworks blindly. TapJob's `AGENTS.md` and governance win if a skill conflicts with repository policy.

Reference:
https://github.com/obra/superpowers

### Recommended baseline setup

Use the official Anthropic marketplace for Anthropic-authored plugins. Exact CLI behavior can change, so verify with `claude plugin marketplace list` / `/plugins` if a command differs in the installed Claude Code version.

Suggested baseline:

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install feature-dev@claude-plugins-official
claude plugin install code-review@claude-plugins-official
claude plugin install frontend-design@claude-plugins-official
claude plugin install expo@claude-plugins-official
```

Install `grill-me` and `repo-security-review` from their repositories following their own installation instructions.

Do not install a large overlapping skill pack just because it exists. Start with this baseline; add optional skills only when a concrete workflow gap appears.

## 3. Standard Claude Code cycle

```text
Read AGENTS + PROJECT_STATE
        ↓
Confirm authorized WP
        ↓
grill-me (if design ambiguity exists)
        ↓
Write/confirm implementation plan
        ↓
Create branch
        ↓
Implement smallest checkpoint
        ↓
Tests + typecheck + lint
        ↓
Security/source-reuse checks
        ↓
Update PROJECT_STATE/docs
        ↓
Push + PR
        ↓
Anthropic code-review
        ↓
Independent GitHub review
        ↓
Fix findings
        ↓
CI green at latest HEAD
        ↓
Merge
```

## 4. Prompt to start a new Claude Code session

Use this structure:

```text
You are working on TapJob.

First read AGENTS.md and follow its mandatory reading order.
Then read docs/PROJECT_STATE.md and docs/IMPLEMENTATION_PLAN.md.

Do not implement anything outside the currently authorized work package.
GitHub/main is the source of truth.
Do not claim completion from summaries; verify files/tests/CI.
If importing upstream code, follow docs/SOURCE_REUSE_BLUEPRINT.md and record every imported artifact in docs/SOURCE_REUSE_LEDGER.md.
Do not alter Accepted decisions without an ADR/superseding decision.

Current task:
<WORK PACKAGE / CHECKPOINT>

Before editing, show:
1. repository state you verified;
2. exact scope;
3. files you expect to touch;
4. tests/acceptance criteria;
5. upstream source code you intend to reuse, with repo/SHA/path.
```

## 5. Upstream code rule

Claude must never be told simply:

> "copy the useful parts from these repositories"

Instead it must receive:
- one TapJob capability;
- one primary donor;
- exact source SHA;
- exact files/functions;
- destination interface;
- required tests;
- prohibited source coupling.

This prevents architecture drift.

## 6. Checkpoint size

Prefer checkpoints that can be independently reviewed.

Examples:
- WP2.1-A: JobSource protocol + test fixtures;
- WP2.1-B: Greenhouse adapter;
- WP2.1-C: Lever adapter;
- WP2.1-D: Ashby adapter;
- WP2.1-E: ingestion/dedupe runner.

Do not ask Claude to implement an entire phase in one unreviewable PR.

## 7. Multi-agent usage

Useful roles:
- Architect;
- Backend;
- Mobile;
- Security;
- QA;
- Source Reuse Auditor;
- Reviewer.

One agent can implement; a different agent should review.

Avoid multiple implementation agents editing the same module simultaneously.

## 8. Definition of completion for Claude

Claude may only report a checkpoint as complete when:
- working tree/diff inspected;
- tests run and results shown;
- lint/typecheck green;
- source ledger updated where needed;
- docs updated;
- secrets scan clean where applicable;
- PR created;
- CI verified at latest HEAD.

Merge remains governed by `docs/GOVERNANCE.md`.
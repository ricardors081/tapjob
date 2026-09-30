# Claude Code Development Workflow — TapJob

## 1. Objective

Use Claude Code as an implementation agent without letting session context become the source of truth.

GitHub documentation, ADRs, work packages, tests and PR evidence remain authoritative.

## 2. Recommended Windows execution

For the owner's current Windows workflow, use **Claude Code CLI launched from PowerShell in the local TapJob clone**.

Requirements:
- Windows 10+;
- Git for Windows;
- Claude Code installed and authenticated;
- project opened at the repository root.

Claude Code supports native Windows when Git for Windows/Git Bash is available. If discovery of Git Bash fails, configure `CLAUDE_CODE_GIT_BASH_PATH` in PowerShell.

WSL2 remains a valid fallback for Linux parity, but it is not required for the initial TapJob workflow.

Preferred daily pattern:

```powershell
cd C:\Projetos\tapjob
git status
git switch main
git pull
claude
```

Claude works on a scoped branch. Deployment/operations on the Linux server happen through Git/CI/SSH/EasyPanel rather than developing directly in production.

## 3. Recommended skill/tool stack

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

### D. Anthropic security-guidance — baseline security guard
Use from the first implementation PR.

Purpose:
- warn about dangerous code patterns during edits;
- review diffs for high-severity vulnerabilities;
- review commits for cross-file security issues;
- catch classes such as injection, SSRF, hardcoded secrets, IDOR/auth bypass and unsafe deserialization.

It is an assistive guard, not a replacement for CI scanning or independent review.

Official plugin reference:
https://github.com/anthropics/claude-plugins-official/tree/main/plugins/security-guidance

### E. Anthropic frontend-design — install when mobile UI work starts
Use when implementing or reshaping UI.

Purpose:
- deliberate visual direction;
- avoid generic AI-generated interfaces;
- improve typography/layout/detail.

It does not override TapJob design tokens or product UX rules.

Official plugin reference:
https://github.com/anthropics/claude-plugins-official/tree/main/plugins/frontend-design

### F. Expo official skills — install when mobile work starts
Use for every Expo/EAS implementation task.

Install/use official Expo agent skills rather than relying on stale generic React Native knowledge.

Reference:
https://github.com/expo/skills

### G. Superpowers — optional, not baseline
Superpowers provides planning, TDD, debugging and verification workflows.

Do not enable it by default together with overlapping planning workflows unless a concrete gap appears. TapJob already has repository governance plus `grill-me` and `feature-dev`. If installed later, repository documentation always wins on conflicts.

Reference:
https://github.com/obra/superpowers

### Recommended baseline setup

Use the official Anthropic marketplace for Anthropic-authored plugins. Exact CLI behavior can change, so verify with `claude plugin marketplace list` / `/plugins` if a command differs in the installed Claude Code version.

### Baseline now — Foundation / Career Core / WhatsApp

Inside Claude Code:

```text
/plugin marketplace add anthropics/claude-plugins-official
/plugin install feature-dev@claude-plugins-official
/plugin install code-review@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
```

Keep the existing `grill-me` skill installed.

### Add later — Mobile App stage

```text
/plugin install frontend-design@claude-plugins-official
/plugin install expo@claude-plugins-official
```

Do not install a large overlapping skill pack just because it exists. Add a plugin when it closes a concrete workflow gap.

## 4. Standard Claude Code cycle

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

## 5. Prompt to start a new Claude Code session

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

## 6. Upstream code rule

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

## 7. Checkpoint size

Prefer checkpoints that can be independently reviewed.

Examples:
- WP2.1-A: JobSource protocol + test fixtures;
- WP2.1-B: Greenhouse adapter;
- WP2.1-C: Lever adapter;
- WP2.1-D: Ashby adapter;
- WP2.1-E: ingestion/dedupe runner.

Do not ask Claude to implement an entire phase in one unreviewable PR.

## 8. Multi-agent usage

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

## 9. Definition of completion for Claude

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
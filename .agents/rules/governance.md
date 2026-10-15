---
trigger: always_on
description: Core governance rules, authority hierarchy, external review requirement, and anti-patterns
---

# Governance & Authority Invariants

## 1. Orchestrator and Identity

- **Orchestrator**: Google Antigravity 2.0 is the official multi-agent engineering orchestrator for TapJob.
- **Explicit Exclusion**: Maestri is NOT part of TapJob and must never be referenced or used.
- **Project Identity**: TapJob is an independent career platform. Never mix with Chatly, ContatoCerto, Samba, koglim/contato, or any other external project.

## 2. Canonical Source of Truth

- **GitHub Repository (`ricardo081683/tapjob`)**: The `main` branch on GitHub is the canonical source of truth.
- **Documentation Precedence**:
  1. Accepted Architecture Decision Records (`docs/adr/`);
  2. Product Requirements Document (`docs/PRD.md`) and Feature Specs;
  3. Architecture and Integration specifications (`docs/ARCHITECTURE.md`, `docs/INTEGRATIONS.md`);
  4. Factual project state (`docs/PROJECT_STATE.md`);
  5. Code implementation and comments;
  6. Agent summaries or chat transcripts.
- **Evidence Standard**: Agent summaries and status reports are NOT evidence by themselves. Every assertion of completion requires verifiable automated tests, lint/typecheck runs, and green CI at the exact PR HEAD.

## 3. Review and Merge Gates

- **Independent External Review**: All pull requests must undergo genuine independent review. No agent or builder role may approve or merge its own changes.
- **No Direct Main Commits**: Direct commits or pushes to `main` are strictly prohibited.
- **No Automated Merge**: `gh pr merge` and automated self-merging are prohibited during baseline governance.
- **Pre-CI Bootstrap Window Expired**: Because WP0.1 established CI workflows on `main`, the historical pre-CI bootstrap exception has expired and is no longer applicable. All merges require green CI on the exact HEAD commit.

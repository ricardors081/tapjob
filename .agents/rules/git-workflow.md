---
trigger: always_on
description: Branching strategy, commit hygiene, Git worktree isolation, and PR workflow
---

# Git Workflow & Branching Policies

## 1. Branch Strategy

- **Feature Branching**: All work must be conducted on dedicated feature or maintenance branches branched from canonical `main`:
  - `chore/<topic>`
  - `feat/<topic>`
  - `fix/<topic>`
  - `docs/<topic>`
  - `security/<topic>`
- **No Direct Commits**: Never commit directly to `main` or `master`.
- **Protected Push**: Never push directly to `main` or `master` (`git push origin main`, `git push origin HEAD:main`, or equivalent refspecs are strictly blocked).

## 2. Commit & Push Hygiene

- **No Force Push**: `--force`, `-f`, and `--force-with-lease` are strictly prohibited.
- **No Bypass of Verification**: `--no-verify` must not be used to bypass git hooks or safety checks.
- **Atomic Commits**: Keep commits focused and logically grouped with descriptive conventional commit messages.

## 3. Worktree Isolation for Parallelism

- **Independent Specialists**: Concurrent implementation tasks assigned to dynamic specialists should use isolated Git worktrees to prevent workspace collisions.
- **Boundary Separation**: Parallel work is permitted only after interface contracts (APIs, schemas, tokens) are established.
- **No Concurrent Conflicting Edits**: Multiple agents must never edit the same database migration, orchestration state machine, or core schema concurrently.

## 4. Pull Request Standards

- Every PR must include: Objective, Scope, Changes, Tests, Security/Privacy, External Impact, Evidence, Docs, and Rollback strategy.
- Wait for all CI checks to complete on the exact PR HEAD before requesting review.

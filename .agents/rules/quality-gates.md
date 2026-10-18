---
trigger: model_decision
description: Verification gates, local quality checks, CI requirements, and autonomous repair budget
---

# Quality Gates & Verification Standards

## 1. Local Quality Verification

Before opening or updating a PR, run all applicable local checks:
- **Node/TS Workspace**: `npm run lint`, `npm run format:check`, `npm run typecheck`, `npm test`
- **Python Workspace**: `uv run ruff check .`, `uv run ruff format --check .`, `uv run python -m mypy`, `uv run python -m pytest`
- **Governance**: `python tools/agent_guard/validate_governance.py`, `python -m unittest discover -s tools/agent_guard`

## 2. CI Verification & Authoritative Gate

- **CI Job Families**: All established GitHub Actions CI jobs must pass on the exact PR HEAD:
  - Backend (lint, types, unit tests)
  - Backend integration (PostgreSQL migrations, readiness)
  - Contracts (OpenAPI export, client generation, drift check)
  - Shared packages (lint, format, tokens, product config)
  - Mobile (Expo Doctor, bundle export)
  - Docker build and compose smoke test
  - Deployment contract (image-ref validator, compose config)
  - Secret scan and dependency audit
- **No False Success**: Never alter tests, delete assertions, or mock implementations solely to make a failing build green.

## 3. Autonomous Repair Budget

- **Budget Limit**: The autonomous agent team has a budget of up to 5 internal repair iterations (`MAX_INTERNAL_REPAIR_LOOPS=5`) to diagnose and fix lint, type, test, or CI failures.
- **Escalation Trigger**: If a failure cannot be resolved within 5 iterations, or stems from fundamental product/architectural ambiguity, the team must halt and escalate to the human user with a structured root-cause report.

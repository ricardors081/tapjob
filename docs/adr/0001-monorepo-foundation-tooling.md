# ADR-0001 — Monorepo foundation tooling (WP0.1-A)

**Status:** Accepted (merge of the WP0.1-A pull request is the acceptance gate)
**Date:** 2026-09-30
**Decision register:** D-043
**Scope:** repository/tooling choices that WP0.1 was chartered to make. No Accepted decision (D-001…D-042) is changed.

## Context

`IMPLEMENTATION_PLAN.md` WP0.1 requires a monorepo scaffold, reproducible local stack, typed configuration, token/product-config scaffolds, lint/typecheck/tests and CI. `DESIGN_SYSTEM.md` §2 defers the token build tool to WP0.1/WP0.2. The stack itself (FastAPI, PostgreSQL, Redis, Celery, Expo, Docker Compose) is already decided and is not revisited here.

## Decisions

### 1. WP0.1 is delivered in checkpoints

- **WP0.1-A (this ADR):** monorepo structure, Python and JS workspaces, product-config and design-token packages, API and worker skeletons, empty Alembic baseline, local Docker Compose stack, CI, secret scan, dependency audit, Dependabot.
- **WP0.1-B:** minimal Expo mobile shell consuming tokens/product config and a typed public config, plus the remaining WP0.1 items (readiness checks, i18n foundation, Sentry wiring, magic-value lint strategy).

### 2. Python: one uv workspace, one lockfile

Root `pyproject.toml` is a virtual uv workspace (`services/api`, `services/worker`, `packages/product-config`) with a single `uv.lock`. Ruff (lint + format), mypy `--strict` and pytest are configured once at the root.

### 3. Backend is one codebase with several process entrypoints

Consistent with D-007 (modular monolith + workers):

- `tapjob_api` holds the Career Core code, shared runtime foundations (`core.config`, `core.logging`) and Alembic migrations.
- `tapjob_worker` depends on `tapjob_api` instead of duplicating configuration or logging, so future tasks run the same domain code.
- One Docker image (`infra/docker/backend.Dockerfile`) serves the `api`, `worker` and one-shot `migrate` services.

### 4. Configuration

- Environment config is read only through Pydantic Settings classes (`ApiSettings`, `WorkerSettings`). Required values have no defaults and fail at process start. No implicit `.env` loading at runtime.
- Variable names: `APP_ENV`, `LOG_LEVEL`, `DATABASE_URL`, `REDIS_URL`.
- Product identity comes from `packages/product-config/product.json`. The Python loader validates it (Pydantic model, `extra="forbid"`), and the wheel bundles the file. TypeScript reads the same JSON.

### 5. Design tokens: dependency-free generator

`tokens.json` + `themes/*.json` (DTCG) are canonical. `scripts/build.mjs` (Node standard library only) resolves aliases, validates types and writes `generated/native.ts`. CI fails if the generated file is stale. Only documented baseline values are encoded. Undocumented scales (semantic spacing names, type scale, dark theme, …) are left for the design-system workstream. Style Dictionary/Terrazzo can replace the script later without touching the token sources.

### 6. JavaScript: npm workspaces

npm (bundled with Node) avoids adding another tool. Node version is pinned in `.nvmrc`. `packages/contracts` stays a placeholder until OQ-015 is decided in WP0.2.

### 7. CI gates (GitHub Actions)

Backend lint/format/types/unit tests; PostgreSQL migration upgrade/downgrade test; token staleness/typecheck/tests; Docker build plus Compose smoke test hitting `/health`; gitleaks secret scan; `pip-audit` and `npm audit`. Once this workflow exists on `main`, the pre-CI bootstrap exception in `GOVERNANCE.md` expires.

## Consequences

- One Python lockfile and one image keep api/worker versions in lockstep.
- The worker image carries FastAPI dependencies it does not use. This is acceptable at current scale.
- The token generator is small custom code that TapJob must maintain. It is covered by unit tests.
- ESLint/Prettier arrive with the first hand-written TypeScript (WP0.1-B). WP0.1-A has only generated TS and a JSON re-export.

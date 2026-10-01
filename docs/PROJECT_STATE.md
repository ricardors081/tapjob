# Project State — TapJob

**As of:** 2026-10-01  
**Repository:** `ricardoo081/tapjob`  
**Phase:** 0 — Foundation  
**Code status:** WP0.1-A IMPLEMENTED. WP0.1-B IMPLEMENTED / UNDER REVIEW (mobile shell, readiness, Sentry wiring, i18n, lint/format baseline). WP0.1 overall: IN PROGRESS pending review/CI/merge of WP0.1-B.

This file must reflect reality only.

## 1. Completed

### Product discovery
- Product thesis defined.
- Brazil-first strategy defined.
- Mobile + WhatsApp + future web/extension direction accepted.
- AIApply, Huntr and Simplify adopted as product references.
- Ten public repositories analyzed as technical references.
- Second-pass source audit completed against pinned upstream HEAD SHAs.
- Selective code-reuse blueprint completed: primary donor chosen per capability; IMPORT / ADAPT / PORT / REFERENCE rules established.
- Source reuse ledger and Claude Code workflow established.
- Repository-owner reuse authorizations reported as formalized by e-mail and retained privately by the project owner.
- Initial external-platform feasibility research completed for:
  - Gupy;
  - LinkedIn;
  - Indeed;
  - Greenhouse;
  - Lever;
  - Ashby;
  - Gmail.

### Canonical documentation
The canonical foundation includes:
- Project Charter;
- PRD;
- Feature Scope;
- Architecture;
- Data Model;
- Integrations;
- Apply Engine;
- Career Agent;
- Product Surfaces;
- Security/Privacy;
- UX direction;
- Roadmap;
- Delivery Order;
- Foundation Decisions;
- Implementation Plan;
- Governance;
- Decisions;
- References;
- Open Questions;
- Agent operating contract;
- Source Reuse Blueprint;
- Source Reuse Ledger;
- Claude Code Workflow;
- Claude Code root entrypoint (`CLAUDE.md`);
- Design System & Tokens standard;
- Engineering Standards for typed configuration, i18n, domain constants, APIs, errors, observability and no-magic-value rules;
- `TapJob` adopted as the working product identity and the GitHub repository has been renamed to `ricardoo081/tapjob`;
- eight foundation topics resolved in `FOUNDATION_DECISIONS.md`: user journey, infrastructure/auth, design/performance, privacy/security, job-source ordering, monetization/entitlements, analytics/operations and engineering governance;
- product positioning locked: candidate-side job-search execution, not a LinkedIn/social-network clone; AIApply is primary reference, Huntr/Simplify are complementary references, and the 10 audited repos remain implementation accelerators;
- customer-surface order locked: Foundation -> Minimum Career Core -> WhatsApp -> Mobile App -> Browser Extension;
- WhatsApp may operate independently of the app over the shared Career Core;
- Evolution API approved as initial channel transport behind `MessagingChannel`, with official Cloud API migration path;
- n8n limited to auxiliary bridge/orchestration/ops and EasyPanel limited to deployment/operations convenience.

### Engineering foundation — WP0.1-A
See `docs/adr/0001-monorepo-foundation-tooling.md` (D-043).
- monorepo layout: `services/api`, `services/worker`, `packages/{product-config,design-tokens,contracts}`, `apps/mobile` (placeholder), `infra/`;
- uv Python workspace (single `uv.lock`) and npm workspaces;
- `packages/product-config`: canonical `product.json`, validated Python loader, typed TS access;
- `packages/design-tokens`: DTCG `tokens.json` + light theme, dependency-free generator, generated `native.ts`;
- `services/api`: FastAPI app factory, typed fail-fast settings, `/health` liveness endpoint, JSON logs with request IDs, SQLAlchemy base with naming conventions, Alembic with an **empty** baseline migration;
- `services/worker`: Celery app skeleton (JSON-only payloads, late ack), no product tasks;
- `infra/`: backend Dockerfile (non-root) and local Docker Compose stack (postgres, redis, migrate, api, worker) configured only via `infra/.env`;
- GitHub Actions CI: ruff, mypy strict, pytest, PostgreSQL migration test, token checks, Docker/Compose smoke test, gitleaks, pip-audit, npm audit; Dependabot.

When this CI workflow is on `main`, the pre-CI bootstrap exception in `GOVERNANCE.md` no longer applies.

### Mobile & readiness foundation — WP0.1-B (IMPLEMENTED, UNDER REVIEW)
See `docs/adr/0002-mobile-foundation.md` (D-044). Not yet reviewed/merged; the WP0.1 acceptance criteria are not self-certified here.
- `apps/mobile`: Expo SDK 57 + Expo Router shell (two routes) consuming `@tapjob/design-tokens` and `@tapjob/product-config`; proves boot, routing, tokens, product config, i18n and typed public config together; no product screens;
- typed public config (`src/config/public-config.ts`): fail-fast validation of `EXPO_PUBLIC_APP_ENV`/`EXPO_PUBLIC_API_BASE_URL`, optional `EXPO_PUBLIC_SENTRY_DSN`; the only module allowed to read `process.env` in the app;
- i18n: `i18next`/`react-i18next`/`expo-localization`, `pt-BR` implemented, architecture ready for `es`/`en`; the default locale comes from `@tapjob/product-config`'s `defaultLocale` (not a second hardcoded value), fail-fast if it ever has no registered resource;
- design tokens: `spacing`/`typography.fontSize`/`borderWidth` semantic scales added for the shell (`docs/adr/0002-mobile-foundation.md` item 12); the Inter typeface is now actually loaded at runtime (`expo-font` + `@expo-google-fonts/inter`, three weights only, item 13), not just named by a token;
- flat ESLint (`eslint-config-expo` for the app, lighter TS rules for `packages/*`) + Prettier, both wired into CI; magic-value guard rules for raw hex/rgb, hardcoded product identity (`productName`/`shortName`/`deepLinkScheme`), scattered `process.env`, inline URLs, and (in `apps/mobile/**/*.tsx`) raw design values in style properties (`fontSize`, `fontFamily`, spacing/padding/margin/gap, `borderRadius`/`borderWidth`);
- TypeScript realigned to `~6.0.3` workspace-wide (was `^7.0.2` in WP0.1-A) for `eslint-config-expo` compatibility;
- backend `GET /ready`: probes PostgreSQL and Redis concurrently with a bounded timeout (`ApiSettings.readiness_timeout_seconds`, default 2s); 200 when both are reachable, 503 otherwise; response/logs never carry a DSN, host or exception message;
- Sentry: shared `init_sentry` helper for api/worker, no-op unless `SENTRY_DSN` is set (unset in CI/local dev); the Expo config plugin, Metro source-map upload and EAS release wiring are explicitly deferred (documented in `apps/mobile/README.md`);
- CI: new `mobile` job (Expo Doctor, `expo export`); `packages` job gains `lint`/`format:check`; `backend-integration` gains a Redis service; the `docker` job and the `api` container's own healthcheck now call `/ready`;
- CODEOWNERS was not added: no team exists yet.

Known local-environment limitations (do not block CI, see ADR-0002 Consequences):
- `uv run ruff|mypy|pytest` initially could not run on this Windows development machine — endpoint security blocked execution of the `uv`-provisioned Python interpreter, even for a plain `--version` check. Backend verification was then actually run, using the system Python 3.14 already installed/approved on the machine instead of `uv`'s own interpreter (`pip install --user` of the project's pinned dependencies plus editable installs of the three workspace packages, removed again afterward): `ruff check` passed; `ruff format --check` passed after one formatting fix; `mypy --strict` passed after fixing two misplaced `# type: ignore` comments; the 45 non-integration tests passed. This ran Python 3.14, not the project's pinned 3.12. GitHub Actions (Python 3.12) remains the authoritative gate, and it is also the only place PostgreSQL/Redis integration tests and the real Docker build/up/health check run.
- `expo-doctor`'s React Native Directory check failed locally with a network error (sandboxed environment); expected to pass in GitHub Actions, which has full internet access.
- Docker Desktop/WSL remains unavailable on this Windows machine (pre-existing, unrelated to this checkpoint); `docker compose config` was used locally to validate the Compose changes, and the real Docker build/up/health check runs in GitHub Actions.

## 2. Not implemented

No production functionality exists yet. Backend code is limited to the WP0.1-A/WP0.1-B skeleton above (no domain models, tables or business endpoints; the database schema is still an empty baseline). The mobile app is a shell only, no product screens.

Specifically NOT implemented:
- any domain model, table or business endpoint (database schema is an empty baseline);
- Sentry Expo config plugin, Metro source-map upload, EAS release wiring (deferred from WP0.1-B, see `docs/adr/0002-mobile-foundation.md`);
- `es`/`en` translations (architecture is ready; only `pt-BR` strings exist);
- web app;
- browser extension;
- WhatsApp integration;
- auth;
- profile;
- resume parser;
- job ingestion;
- matching;
- AI gateway;
- application tracking;
- apply engine;
- Gmail integration;
- notifications;
- CD / deployment (CI exists; nothing is deployed);
- monitoring.

## 3. Current risks

### R1 — External platform access
Official submission APIs often require employer/partner credentials.

Mitigation:
capability-based Apply Engine and graceful fallback.

### R2 — Gmail restricted scope
Inbox Intelligence may require Google verification/security assessment.

Mitigation:
feature flag and compliance work before public release.

### R3 — Code reuse provenance
Some reference repositories have MIT licenses; others do not expose a root LICENSE file. The project owner reports that authorization from all 10 owners is formalized by e-mail.

Mitigation:
keep private authorization evidence outside the repository; every code-bearing reuse PR must pin upstream repository/SHA/path and record its permission basis, adaptation and security review in `SOURCE_REUSE_LEDGER.md`.

### R4 — Scope size
Vision is broad.

Mitigation:
phase gates and MVP exit criteria.

### R5 — AI hallucination
Career documents can be harmful if facts are fabricated.

Mitigation:
Professional Truth Store + grounded generation + evals.

### R6 — GitHub enforcement capability
Current repository state reports `main` as unprotected, and GitHub ruleset/branch-protection APIs indicate that the current private-repository plan does not expose the intended protection capability.

Mitigation:
WP0.1 still installs CI and the team follows the documented PR/latest-HEAD review process. If the GitHub plan permits branch protection later, enforce required PR/check/conversation rules in GitHub itself. Until then, the governance requirement remains procedural and auditable rather than falsely documented as technically enforced.

## 4. Immediate next work package

### WP0.1 — Repository & Engineering Foundation (IN PROGRESS)

Checkpoints:
- **WP0.1-A — IMPLEMENTED:** repository/monorepo foundation (section 1).
- **WP0.1-B — IMPLEMENTED / UNDER REVIEW:** minimal Expo mobile shell consuming design tokens/product config with a typed public config; ESLint/Prettier for TypeScript; `pt-BR` i18n foundation; readiness checks; Sentry wiring; magic-value lint strategy (see section 1). CODEOWNERS was not added: no team exists yet. Review, CI and merge are still pending; only that gate can mark WP0.1 itself as done.

Original WP0.1 deliverables:
- project monorepo/scaffold decision;
- backend skeleton;
- mobile skeleton;
- shared contracts strategy;
- Docker local stack;
- PostgreSQL;
- Redis;
- environment templates;
- secret handling;
- lint/typecheck/tests;
- GitHub Actions;
- branch/PR conventions;
- CODEOWNERS if team exists;
- dependency/security scanning;
- basic observability;
- contribution docs.

Acceptance:
- clean clone can run local skeleton;
- CI green;
- health endpoints;
- no secrets;
- database migration framework working;
- mobile shell boots;
- docs align with repository reality.

### WP0.2 — Technical Contracts & Remaining Architecture Decisions

Already resolved:
- auth: Firebase Authentication;
- worker: Celery + Redis;
- object storage: Cloudflare R2;
- initial hosting: Linux + Docker Compose behind Cloudflare.

Remaining:
- OpenAPI/client generation strategy;
- AI Gateway abstraction;
- canonical connector/interfaces;
- error/pagination/observability contracts;
- exact deployment automation within the approved topology.

Output:
ADRs/contracts, not production feature claims.

## 5. Phase gates

### Gate 0
Foundation and CI ready.

### Gate 1
Career Core accepted.

### Gate 2
Jobs & Matching accepted.

### Gate 3
Resume & Application Kit accepted.

### Gate 4
Tracking + Agent + WhatsApp accepted.

### Gate 5
Assisted Apply accepted.

### Gate 6
Inbox Intelligence compliant and accepted.

## 6. Documentation maintenance rule

On every significant PR:
- update this file when phase/status changes;
- update DECISIONS when a decision changes;
- update INTEGRATIONS when platform capability changes;
- update ROADMAP only when scope ordering changes.

## 7. Current authoritative conclusion

TapJob has a defined product and architecture baseline and a repository/engineering skeleton, including a minimal mobile shell and backend readiness/observability wiring (WP0.1-A + WP0.1-B), but **no product functionality yet**.

WP0.1-B is implemented and under review; it is not self-certified as merged or as closing WP0.1. The correct next action is independent review, green CI at the PR's latest HEAD and merge under `docs/GOVERNANCE.md` — not feature implementation.
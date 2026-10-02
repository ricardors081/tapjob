# Project State — TapJob

**As of:** 2026-10-02  
**Repository:** `ricardoo081/tapjob`  
**Phase:** 0 — Foundation  
**Code status:** WP0.1 VERIFIED. WP0.2-A VERIFIED. WP0.2-B NOT STARTED. WP0.2 IN PROGRESS.

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

### Mobile & readiness foundation — WP0.1-B (VERIFIED)
See `docs/adr/0002-mobile-foundation.md` (D-044). Independently reviewed and merged in PR #7; CI also passed on the post-merge `main` commit `f8d4bf1ea76b8da2e925ce92ef67f5137b33f04c`.
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

### API contract & HTTP conventions — WP0.2-A (VERIFIED)
See `docs/adr/0003-api-contract-client-generation.md` (D-045) and `docs/adr/0004-http-errors-pagination-correlation-idempotency.md` (D-046); closes `OQ-015`. Independently reviewed and merged in PR #9; CI also passed on the post-merge `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`.
- deterministic, offline OpenAPI export (`tapjob_api.openapi_export`) to `packages/contracts/openapi.json`; structural contract rules (`tapjob_api.http.contract_rules`: explicit camelCase `operationId`, `/api/v1` scoping, non-2xx responses reference `ErrorResponse`) enforced at export time and unit-tested directly;
- `/api/v1` business API root mounted (`tapjob_api.http.api_v1`), empty — no product route exists yet; `/health`/`/ready` stay unversioned and now additionally declare an explicit `operationId` and document their shared `500: ErrorResponse` case;
- canonical `ErrorResponse` envelope and exception handlers (`tapjob_api.http.errors`) for `ApiError`/`RequestValidationError`/`StarletteHTTPException`/unhandled exceptions; `message` is safe-to-display but never a logic input, backend does not localize by `Accept-Language` at this stage;
- a real bug found and fixed in the process: `RequestContextMiddleware` (WP0.1-B) could not attach `X-Request-ID`/`ErrorResponse.requestId` to a truly unhandled exception's response, because Starlette promotes a bare-`Exception`/500 handler to `ServerErrorMiddleware`, outside this middleware; fixed by having the middleware itself build/send that response (then re-raise for Sentry/observability), confirmed with a before/after reproduction;
- cursor pagination codec (`tapjob_api.http.pagination`: `CursorPage`, `decode_cursor`/`encode_cursor`, `fingerprint_filters`), explicitly documented as not tamper-proof; no feed endpoint uses it yet (WP2.3 is the first consumer);
- `Idempotency-Key` convention documented (ADR-0004 §4); no table/middleware yet, by design — added together once a route needs it;
- TypeScript client: `@hey-api/openapi-ts` pinned exact (`0.99.0`), generating types + SDK functions + fetch client runtime into `packages/contracts/src/generated/**` (committed, excluded from Prettier/ESLint); consumed only through a `createApiClient`/`isApiError` facade (`packages/contracts/src/index.ts`) — `src/generated/**` is unreachable from outside the package (`exports` map, not just convention);
- generator regression tests (`packages/contracts/test/generator-fixtures.test.mjs`) cover `$ref`, required/optional fields, `allOf` composition, a discriminated `oneOf` union and a non-2xx error schema, against small hand-built fixture documents independent of TapJob's own (currently minimal) schema;
- `exactOptionalPropertyTypes: false` added, narrowly, to `packages/contracts/tsconfig.json` and `apps/mobile/tsconfig.json` only (not `tsconfig.base.json`): the generated fetch-client runtime does not satisfy that flag as of this Hey API version, and every consumer's own `tsc` re-checks those same generated files (no build/`.d.ts` boundary between workspace packages) — confirmed by reproducing the same errors in `apps/mobile`'s typecheck before the override and seeing them disappear after;
- mobile: `@tapjob/contracts` added as a dependency and exercised only by a technical smoke test (`apps/mobile/__tests__/contracts-smoke.test.ts`, mocked fetch) — no screen/product code consumes it; Metro bundling was separately verified with a temporary, reverted side-effect import (module count rose from 1689 to 1702 modules), proving `package.json#exports` resolution works end to end, but this is not yet an automated CI check;
- CI: new `contracts` job (export OpenAPI + diff check, generate TS client + diff check, typecheck, generator regression tests); a scoped `package.json` `overrides` entry fixes a high-severity `js-yaml` advisory pulled in transitively by `@hey-api/openapi-ts` (`npm audit --audit-level=high` is clean).

Known local-environment limitations for this checkpoint (do not block CI):
- same Windows/`uv` interpreter restriction as WP0.1-B; backend verification ran with the system Python 3.14 instead (`pip install --user` + editable installs, removed afterward): `mypy --strict` passed (40 source files) and `pytest` passed (94 tests: 45 pre-existing + 49 new, across the checkpoint and a follow-up round of fixes from an independent code review). `ruff check`/`ruff format --check` passed earlier in the same session on this exact code; partway through, this machine's endpoint security began blocking execution of the `ruff.exe` binary itself (`PermissionError: Acesso negado`, reproduced even against a freshly reinstalled copy in a throwaway venv -- the same class of issue ADR-0002 documents for `uv`'s own interpreter, now hitting a different tool) and it could not be re-run after that point; `pyflakes` (a pure-Python fallback covering unused imports/names) was run instead and found nothing. GitHub Actions (Linux) remains the authoritative, unaffected gate for all of these.
- an independent code review (`/code-review --level high`) of this checkpoint's diff found and fixed, before this PR was marked ready: a path-prefix boundary bug in `contract_rules` (`/api/v1beta` would have wrongly matched the `/api/v1` scope check), `/ready`'s existing 503/`ReadinessResponse` case was undocumented in the OpenAPI schema (the generated client had no typed way to see it — now documented and present in `GetReadinessErrors`), an un-bounded cursor length in `decode_cursor` (DoS-amplification risk), two defensive/efficiency nits in `contract_rules` (OpenAPI 3.1 wildcard status keys, redundant schema traversal), and `COMMON_OPERATIONAL_ERROR_RESPONSES` moved from being repeated per-route to each router's own constructor. Not fixed, deliberately: the exception-handling logic being split across `errors.py`/`request_context.py` (inherent to the Starlette architecture issue both already document at length) and `exactOptionalPropertyTypes` being scoped per-consumer rather than per-file (TypeScript has no per-file override within one `tsc` invocation; already flagged in ADR-0003 for human review).

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

### WP0.1 — Repository & Engineering Foundation (VERIFIED)

Checkpoints:
- **WP0.1-A — VERIFIED:** repository/monorepo foundation (section 1).
- **WP0.1-B — VERIFIED:** minimal Expo mobile shell consuming design tokens/product config with a typed public config; ESLint/Prettier for TypeScript; `pt-BR` i18n foundation; readiness checks; Sentry wiring; magic-value lint strategy (see section 1). CODEOWNERS was not added because no team exists yet.

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

### WP0.2 — Technical Contracts & Remaining Architecture Decisions (IN PROGRESS)

Checkpoints:
- **WP0.2-A — VERIFIED:** OpenAPI/client generation strategy; `/api/v1` versioning convention; `ErrorResponse` envelope and exception handlers; cursor pagination codec; `Idempotency-Key` convention (documented, not yet implemented); contract CI gates. See `docs/adr/0003-api-contract-client-generation.md` and `docs/adr/0004-http-errors-pagination-correlation-idempotency.md` (section 1 above has the full checkpoint summary). Independently reviewed and merged in PR #9; CI green post-merge on `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`.
- **WP0.2-B — NOT STARTED (next authorized checkpoint):** canonical internal ports (`JobSource`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector`) as `Protocol`s with a capability model; worker-side request correlation; deployment/CD contract (not a real deploy). `MatchingEngine` is not a provider-neutral port here: it remains internal/domain logic for now; interface extraction is deferred to WP2.2 if justified by a real substitution need (not assumed up front).

Already resolved (pre-WP0.2):
- auth: Firebase Authentication;
- worker: Celery + Redis;
- object storage: Cloudflare R2;
- initial hosting: Linux + Docker Compose behind Cloudflare.

Output:
ADRs/contracts, not production feature claims. Product feature work must not start before WP0.2-B is also independently verified.

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

TapJob has a defined product and architecture baseline, a repository/engineering skeleton (WP0.1-A + WP0.1-B) and a first verified technical-contracts checkpoint (WP0.2-A: HTTP/OpenAPI/client-generation conventions), but **no product functionality yet**.

WP0.1 is independently reviewed, merged and verified with green CI on `main`. Gate 0 is satisfied. WP0.2-A is independently reviewed, merged (PR #9) and verified with green CI on `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`. WP0.2-B (canonical ports/capability model, worker correlation, deployment contract) remains not started and is the next authorized checkpoint; WP0.2 as a whole stays IN PROGRESS until it is also independently verified. Product feature work must not start before WP0.2-B is independently verified.
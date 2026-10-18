# Project State — TapJob

**As of:** 2026-10-18  
**Repository:** `ricardors081/tapjob`
**Phase:** 0 — Foundation (VERIFIED, complete); 1 — Career Core (VERIFIED, complete); 2 — Jobs & Matching (WP2.1-A, WP2.1-B, WP2.2, WP2.3 VERIFIED); WhatsApp Alpha (VERIFIED, PR #42); Product Priority: WHATSAPP_LIVE_AND_COMMERCIAL_READINESS; Staging Deployment Gate: WHATSAPP_LIVE_GATE (ADR-0016, D-058) PHASE_A_VERIFIED (PR #46); WhatsApp Phase B = CLOSED / VERIFIED (first real E2E canary = VERIFIED); Mobile Status: MOBILE_PRODUCT_STARTED=YES, MOBILE_PRODUCT_SCOPE=JOBS_DOMAIN_ONLY; FURTHER_MOBILE_PRODUCT_EXPANSION=PAUSED_UNTIL_WHATSAPP_LIVE/PILOT_COMMERCIAL_GATE; Agent Factory Transition: AF-0 = CLOSED_AND_VERIFIED (PR #14); AF-1 = CLOSED_AND_VERIFIED (PR #15); AF-2 = NOT STARTED / NOT AUTHORIZED
**Code status:** WP0.1 VERIFIED. WP0.2 VERIFIED. WP1.1 VERIFIED. WP1.2 VERIFIED. WP1.3 VERIFIED. WP2.1-A VERIFIED. WP2.1-B VERIFIED. WP2.2 VERIFIED. MINIMUM_WHATSAPP_ALPHA_VERTICAL_SLICE=VERIFIED. WP2.3 VERIFIED. PR #45 CLOSED_AND_VERIFIED. PR #46 CLOSED_AND_VERIFIED. PR #47 CLOSED_AND_VERIFIED. WhatsApp Phase B (Live Canary) = CLOSED / VERIFIED. Agent Factory Transition: AF-0 (Governance & Safety Foundation) = CLOSED_AND_VERIFIED (PR #14, squash SHA `5c56526e18928babc9b2b8ea4ecf022d68c2d1a4`, post-merge CI `39143931751`); AF-1 (Formal Team Instantiation & Automated Worktree Dispatch) = CLOSED_AND_VERIFIED (PR #15, squash SHA `f01370e51a7662dd22d3cb7cf0dc8f9f9e29153f`, post-merge CI `39171519229` 8/8 green; historically AF-1 = IN PROGRESS); AF-2 = NOT STARTED / NOT AUTHORIZED. Mobile scope: MOBILE_PRODUCT_STARTED=YES, MOBILE_PRODUCT_SCOPE=JOBS_DOMAIN_ONLY (further mobile expansion PAUSED_UNTIL_WHATSAPP_LIVE/PILOT_COMMERCIAL_GATE). Downstream full WPs (WP3.x, WP4.x, WP5.x) remain unstarted.


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

### Internal ports & capability model — WP0.2-B Checkpoint A (VERIFIED)
See `docs/adr/0005-internal-ports-capability-model.md` (D-048). Independently reviewed across three rounds (four blocking findings, then two micro-findings, then two documentation precision corrections — all resolved), merged via squash into `main` as PR #12 (squash commit `a8bfbe621e5a83ae5f205adb1919ce3250684efb`); CI green at latest HEAD before merge (run `37051633827`, 7/7) and CI green post-merge on `main` (run `37055251128`, 7/7).
- seven provider-neutral `typing.Protocol` contracts under `tapjob_api.ports`: `JobSource`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector` — no adapter, no provider SDK, no product feature;
- `MatchingEngine` is explicitly NOT a port (remains internal/domain logic; interface extraction deferred to WP2.2 only if a real substitution need arises) — asserted by a test, not just documented;
- shared primitives: `CallContext`/`Deadline` (deadline is a budget, not a retry policy), normalized `PortError`/`PortErrorCode` (8 codes, `OUTCOME_UNKNOWN` mandatory and distinct from `UNAVAILABLE`), `AdapterHealth`, generic `ResultMetadata`;
- capability model: one shared `CapabilityStatus` vocabulary, per-port capability-key enums, `CapabilityReport[T]`, and `CapabilityManifest[T]` — a validated, immutable container whose constructor rejects any duplicate capability key outright (`ValueError`), with `status_for`/`reason_for` as the only, unconditionally deterministic lookups (an unreported capability resolves to `UNSUPPORTED`, never an exception or `None`);
- `AiGateway.generate()` proves its structured-output contract with `AiRequest.validate_output: Callable[[object], OutputT]` — the field itself is the validator the caller supplies; a provider's raw response must be validated into `OutputT`, never cast or constructed independently, and a rejection becomes `PortError(PROVIDER_ERROR)`. The module docstring also states explicitly that delimiting untrusted content in a prompt is not, by itself, a sufficient prompt-injection defense; grounding only ever references an already-confirmed `ProfileFact` id, never a fact value;
- `ApplyConnector` covers only the `OFFICIAL_API` execution boundary (fetch form / submit); `SubmissionRequest.answers` only carries already-confirmed answers (`ConfirmedAnswer.confirmation_ref` rejects a blank/whitespace-only value); `SubmissionOutcome(status=SUBMITTED)` requires a non-blank `SubmissionEvidence` (`provider_application_id` or `provider_receipt_ref`) and is unconstructible without it; `SubmissionOutcomeStatus.OUTCOME_UNKNOWN` stays mandatory and needs no evidence;
- every normalized timestamp field across the port DTOs rejects a naive datetime (including a `tzinfo` present but reporting no UTC offset) and normalizes an aware-but-non-UTC datetime to UTC, via one shared rule (`_common.py`'s `require_utc_datetime`/`normalize_utc_field`);
- an AST-based test asserts no port module imports anything outside the standard library and `tapjob_api` itself (no Expo/Evolution/OpenAI/Anthropic/Gmail/ATS/browser-extension types);
- conformance is a static `mypy --strict` check (a fake adapter per port assigned to a Protocol-typed variable), not `@runtime_checkable`/`isinstance`;
- no source reuse in this checkpoint (`docs/SOURCE_REUSE_LEDGER.md` unchanged — nothing to record).

Post-merge CI on `main` (run `37055251128`) confirmed, per job: `ruff check` all checks passed; `ruff format --check` clean (95 files already formatted); `mypy --strict` clean (55 source files); `pytest` 171 passed, 3 deselected; `gitleaks` no leaks found; `pip-audit` no known vulnerabilities; the audit-gate self-test (36/36) and the scoped npm dependency audit gate both passed (temporary exception still limited to `GHSA-86w9-cpqp-85rv` / `node-forge@1.4.0`, expiring `2026-11-02T00:00:00Z`).

Known local-environment limitations during implementation (did not block CI): same Windows/`uv`/`ruff.exe` restrictions as the prior two checkpoints; local backend verification across the review rounds ran with the system Python 3.14 (final local state: `mypy --strict` 55 source files clean, `pytest` 171 passed/3 deselected, `pyflakes` clean, `black --check` clean). `ruff.exe` itself could not run locally at any point (`PermissionError: Acesso negado`), so GitHub Actions remained the sole authoritative `ruff check`/`ruff format --check` gate throughout, confirmed green at both the pre-merge and post-merge HEAD.

### Worker correlation & deployment/CD contract — WP0.2-B Checkpoint B (VERIFIED)
See `docs/adr/0006-worker-correlation-deployment-cd-contract.md` (D-050) and `docs/DEPLOYMENT.md`. Independently reviewed across three rounds (four blocking findings, then two further findings — a `gh api` method bug and a retry-metadata type hole — all resolved), merged via squash into `main` as PR #15 (squash commit `3edaf1cee33b892a5d753b07b9b9946cbcd28887`); CI green at latest HEAD before merge (run `37230556089`, 8/8) and CI green post-merge on `main` (run `37231990603`, 8/8).
- worker-side TapJob correlation id propagated across Celery message boundaries via a custom `tapjob_correlation_id` message header (`tapjob_worker.correlation`) — never Celery's own built-in `correlation_id` message property, which is usually just the task id;
- the shared, bounded, log-injection-safe identifier policy (`tapjob_api.core.correlation`) is extracted from the pre-existing HTTP `X-Request-ID` logic and reused by both HTTP and the worker, each with its own justified fallback (HTTP: fresh random id; worker: the task's own `root_id`, then `task_id`, then a fresh id); `resolve_correlation_id`/`is_valid_correlation_id` accept untyped `object` input, not `str | None`, so a malformed Celery message header (int/list/dict/etc.) degrades to the fallback instead of raising (2026 review, Finding 3);
- `before_task_publish` propagates the active correlation id (including across nested task publication from within a running task); `task_prerun` binds it plus a new, generic `tapjob_api.core.logging.task_context_var` (Celery task id/root id/parent id/task name/retries — never task args/kwargs), with every message-derived field independently validated/normalized before being placed in the logged context, never trusted raw just because it came from `Task.request` (2026 review, Finding 3); `task_postrun`/`task_failure`/`task_retry` all reset it, so no finished task's context survives into whatever the worker executes next;
- signal connection is idempotent (`dispatch_uid`, the same mechanism the pre-existing `setup_logging` hook already used) — `create_celery_app()` being called more than once never registers a duplicate handler;
- deployment/CD contract: build-once/promote-the-same-immutable-digest (`registry/repo@sha256:...`, never `:latest`) across staging/production; one backend image preserved (D-043) for `api`/`worker`/`migrate`; `infra/compose.deploy.yaml` (new, no `build:`, no DB/Redis host ports, migration-before-rollout ordering enforced via `depends_on`) fixes the canonical repository directly in its image template and accepts only a bare `BACKEND_IMAGE_DIGEST=sha256:<digest>` (2026 review, Finding 2 — no repo/tag slot exists, so `:latest`/foreign-repository substitution is structurally impossible, not operator discipline); `tools/deploy/validate_image_ref.mjs` (new, zero-dependency) exports `validateImageRef` (full reference) and `validateImageDigest` (the bare digest form the deploy path uses), rejecting any mutable-tag/malformed/empty value; `.github/workflows/release-image.yml` (new, `workflow_dispatch`-only, no free-form `ref` input — **not executed by this checkpoint**) always builds the tip of `main`, hard-fails a `preflight` job if triggered against any other ref, captures the actual checked-out source SHA for every tag/label/summary value, and requires that exact commit's own CI run to have already completed with success (`tools/deploy/require_ci_success.mjs`, 2026 review, Finding 1) before building/pushing the one backend image to GHCR with minimal `GITHUB_TOKEN`/`actions: read` permissions and full-commit-SHA-pinned third-party actions; rollback is redigest-only, never an automatic database downgrade; EasyPanel/Cloudflare roles documented as bounded (operations/ingress only, never a configuration source of truth); SBOM/provenance attestation explicitly deferred (documented, not implemented);
- no source reuse in this checkpoint (`docs/SOURCE_REUSE_LEDGER.md` unchanged — nothing to record);
- both active temporary security exceptions (node-forge `GHSA-86w9-cpqp-85rv`/D-047, braces `GHSA-vfj7-8cjw-p6xm`/D-049) are untouched by this checkpoint.

Post-merge CI on `main` (run `37231990603`) confirmed, per job: `ruff check` all checks passed; `ruff format --check` clean (102 files already formatted); `mypy` clean (59 source files); `pytest` 222 passed, 3 deselected; the deployment-contract job's image/digest validator self-test (25/25) and release CI-success-gate self-test (30/30) both passed; `gitleaks` no leaks found; `pip-audit` no known vulnerabilities; the audit-gate self-test (94/94) and the real, scoped npm dependency audit gate both passed (both temporary exceptions above still active and unchanged, no third HIGH/CRITICAL advisory). No real GHCR publish, staging/production deploy, or database migration occurred — `.github/workflows/release-image.yml` is `workflow_dispatch`-only and has zero runs. First real execution of that workflow and the first real environment promotion remain a separate, later, operational gate requiring their own explicit authorization (ADR-0006).

### Identity & User Account — WP1.1 (VERIFIED)
See `docs/adr/0009-identity-auth-user-account.md` (D-051). Independently reviewed across three rounds (5 blocking findings, then 6 operational-hardening findings, then one concentrated session-retry-semantics blocker — all resolved), merged via squash into `main` as PR #17 (squash commit `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`); CI green at latest HEAD before merge (run `37254156902`, 8/8) and CI green post-merge on `main` (run `37312677497`, 8/8): backend 270 passed/60 deselected, integration 60 passed/270 deselected against real PostgreSQL/Redis (migration `0002` validated), mobile 91 tests/12 suites, audit-gate 94/94 plus the real scoped npm dependency audit gate passing (both pre-existing temporary exceptions unchanged, no third HIGH/CRITICAL advisory).
- internal `User` UUID is the one canonical identity (never a Firebase UID); a separate `ExternalIdentity` table (`UNIQUE(provider, provider_subject)`) links a Firebase UID, concurrency-safe under real concurrent first-login races against real PostgreSQL, and never auto-linked to an existing `User` by email match (even verified);
- `tapjob_api.identity.firebase` is the sole module importing `firebase_admin`; every SDK exception is normalized behind an `IdentityTokenVerifier` Protocol; every token verification uses `check_revoked=True`; no test or CI job ever contacts a real Firebase project (a `FakeIdentityVerifier` is injected everywhere);
- eight new `ErrorCode` members for the auth trust boundary (`AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID`/`AUTH_TOKEN_REVOKED`/`ACCOUNT_NOT_BOOTSTRAPPED`/`ACCOUNT_DISABLED`/`REAUTHENTICATION_REQUIRED`/`DEVICE_NOT_FOUND`/`IDENTITY_CONFLICT`);
- `POST /api/v1/auth/bootstrap`, `GET`/`PATCH /api/v1/me`, `POST /me/sessions/revoke-all` (global Firebase revoke, audited with the real provider outcome — never claims success before Firebase confirms it), `POST`/`DELETE /me/devices` (idempotent registration, ownership-scoped revoke), `GET`/`POST /me/consents` (append-only), `POST`/`GET`/`DELETE /me/deletion-request` (skeleton only — 202 Accepted, never claims data was deleted; idempotent; cancellable only while still `requested`); a 15-minute recent-authentication policy guards session revoke-all and deletion;
- new schema (migration `0002`, chained off the WP0.1 baseline): `users`, `external_identities`, `user_devices`, `consent_records`, `account_deletion_requests`, `account_audit_events` — every enum-shaped column is a `CHECK`-constrained `VARCHAR`, never a native Postgres `ENUM`;
- a new, generic (not identity-specific) `tapjob_api.core.rate_limit.RedisFixedWindowRateLimiter`, fail-closed on Redis outage, guarding bootstrap/revoke-all/devices/consents/deletion;
- OpenAPI/TypeScript contract regenerated (7 new operations under `/api/v1`); `packages/contracts/src/index.ts`'s facade re-exports the new types, no direct `./generated/*` import anywhere;
- mobile: decision to use React Native Firebase + an Expo Development Build (not the Firebase JS SDK's unencrypted-AsyncStorage persistence, not Expo Go) for Google/Apple/email sign-in; only the minimal technical auth surface is built (auth provider/context, signed-out route, minimal sign-in screen, API bearer-token wiring) — no Career Profile/Jobs/Resume/Agent UI;
- no source reuse in this checkpoint; zero real Firebase project, zero real deploy, zero real OAuth, zero production data.

**Operational prerequisites still pending (not an unverified-code gap — WP1.1's code is VERIFIED; these are external setup steps documented in ADR-0009 §I1/§I2):** the first real Expo Development Build/EAS build has not been executed; a real Firebase project, enabled Google/Apple/email providers, the real (gitignored) native service files and the real `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` value are not configured. Google sign-in remains on the free "Original" API (`GoogleSignin.configure`/`signIn`), a documented limitation — the paid "Universal Sign In" product was investigated and deliberately not purchased/migrated to (ADR-0009 addendum, round 2). Session revocation is global only (`revoke_refresh_tokens`); true per-device auth-session revocation is an explicitly named future gap, not silently worked around.

### Career Profile & Professional Truth Store — WP1.2 (VERIFIED)
See `docs/adr/0010-career-profile-truth-store.md` (D-052). Merged to `main` via squash as PR #19 (`5c06c805ea2a68fcf100ec56cff5eaf715e962c2`), independently reviewed across three rounds before merge. Real post-merge CI (run `37364799439`, attempt 2) then exposed a genuine concurrency defect in `update_career_profile`: a `SELECT ... FOR UPDATE` query could return an already identity-mapped, pre-mutation `CareerProfile` object even after PostgreSQL correctly serialized two concurrent transactions at the SQL level — the row lock and the ORM attribute refresh are two separate guarantees, and the first draft conflated them (`docs/adr/0010-...md` "Addendum — round 4"). A structural fix (scalar-only existence check + `execution_options(populate_existing=True)` on the lock query, plus a deterministic two-`Session` regression test proving the exact stale-identity scenario) was independently reviewed and merged via squash as corrective PR #20 (`f30590577cf25733de63025f987c251ebe0e89af`). Post-merge CI on the resulting `main` (run `37397784331`) is green 8/8 — `Backend integration` specifically re-ran and passed the 6-thread non-zero-version race that originally exposed the defect, alongside the first-write race, the virtual-no-op race, and the new deterministic test; security sub-checks (gitleaks, pip-audit, audit-gate 94/94, both pre-existing temporary exceptions unchanged, no third HIGH/CRITICAL) all passed. **WP1.2 is VERIFIED.** This records the incident, not just the outcome: the merge of PR #19 alone was not sufficient to call WP1.2 verified — real concurrent load on GitHub's runners was required to surface this defect, and only the corrective PR #20's own independent review and green post-merge CI complete verification.
- one `CareerProfile` per `User` (`UNIQUE(user_id)`), lazily created on first mutation only — `GET` never creates a row; a `User` with none yet receives a virtual `profileVersion: 0` response;
- `ProfileFact` is typed (nine `FactValue` shapes: experience/education/skill/certification/language/project/link/preference/work_authorization), immutable (a changed claim creates a new fact in the same lineage and supersedes the old one — the old row's original value stays permanently readable), and never hard-deleted on retraction;
- `source_kind`/`user_confirmed` are always server-assigned (`user_direct`) for the public add-fact endpoint — no client can spoof provenance or confirmation;
- optimistic concurrency via one atomic conditional `UPDATE ... WHERE profile_version = :expected ... RETURNING` per logical mutation, `expectedProfileVersion` in every mutating request, conflict → `409 PROFILE_VERSION_CONFLICT`, verified under real concurrent PostgreSQL load (first-write race and a non-zero-version conflict, real threads);
- `get_truth_snapshot` is the one read path any future AI/resume/matching/WhatsApp consumer may ground on — confirmed, active, not retracted/superseded, owned-only, with historical `profile_version` reconstruction;
- audit reuses WP1.1's `AccountAuditEvent` (four new event types), allowlisted metadata only — never fact content, salary figures, or work-authorization answers;
- new schema (migration `0003`, chained off `0002`): `career_profiles`, `profile_facts`, with a partial unique index (`lineage_id` WHERE `status='active'`) and two `CHECK` constraints;
- OpenAPI/TypeScript contract regenerated (8 new operations under `/api/v1/me/...`); facade re-exports the new types, no direct `./generated/*` import;
- source reuse: `career-ops-hq/career-ops` (provenance/evidence concept, PORT — in-place mutation explicitly rejected) and `roysahar11/appliable` (Source-of-Truth-Discipline concept, ADAPT — filesystem-as-database explicitly rejected), both recorded in `docs/SOURCE_REUSE_LEDGER.md`;
- zero AI/LLM calls, zero Resume Import, zero WhatsApp adapter, zero full mobile profile UI, zero real deploy, zero real migration.

### Resume Import — WP1.3 (VERIFIED)
See `docs/adr/0011-resume-import.md` (D-053). Implemented, independently reviewed across two rounds (10 findings in Round 1, 7 findings + 2 hardening items in Round 2), rebased cleanly over Expo maintenance PR #23, merged via squash as PR #22 (squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, approved PR HEAD `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`). Pre-merge CI on approved HEAD: run `37538366658` (8/8 green); post-merge CI on squash commit: run `37551974662` (8/8 green). Tree equivalence confirmed: approved HEAD tree `af7d26f4544f573f4d93b212b4c11b070f3f2052` equals squash commit tree `af7d26f4544f573f4d93b212b4c11b070f3f2052`. **WP1.3 is VERIFIED.**
- PDF/DOCX upload, streamed in bounded chunks; real binary signature/container validation (never
  `Content-Type`/extension alone); ZIP-bomb-style DOCX archives rejected from archive metadata
  alone, before any decompression; legacy OLE/encrypted containers detected explicitly; hard extension allowlist;
- private Cloudflare R2 object storage behind a local, internal `ObjectStoragePort` (not a WP0.2-B
  canonical port), random server-generated `storage_key` never derived from the original filename;
  in-memory fake adapter in tests, genuinely shared directory adapter (`LocalFileObjectStorage`)
  for API/worker in local compose, zero runtime InMemory fallback (`ObjectStorageConfigError`),
  zero real R2 mutation in tests; R2 connect/read timeouts and standard bounded retry configured;
  object integrity (`byte_size`, `checksum_sha256`) verified on retrieval;
- bounded, non-OCR `pypdf`/`python-docx` text extraction; resource budget checked before each page extraction;
  scanned/image-only documents detected and reported, never silently presented as a successful empty extraction;
- deterministic, non-AI heuristic parser produces `ports.resume_parser.ExtractedFact` candidates
  (`AiPurpose.RESUME_EXTRACTION` already exists on canonical `AiGateway` port, but no provider adapter
  is implemented in this checkpoint); parser source-level bounds and remaining-budget propagation enforced;
  `ports.resume_parser.ExtractedFact.value` widened to a bounded recursive JSON type without coupling
  the port to `career_profile`'s own typed `FactValue` union;
- every candidate is fail-closed validated against WP1.2's canonical `FactValue` union before ever
  being persisted as a reviewable, UNCONFIRMED `ResumeImportCandidate` (composite FK ownership on candidate/import);
  zero confirmed `ProfileFact` before explicit user review and finalization; terminal handling for zero extracted candidates;
- `FactSourceKind.RESUME_IMPORT` joins `user_direct` (migration `0004` extends the existing CHECK
  constraint); never infers `target_roles`, salary/location preference, or work authorization from
  resume text; never builds a contact-profile model from extracted contact information;
- finalization is one atomic `profile_version` transition producing confirmed facts plus at most
  one master `Resume`/immutable `ResumeVersion` per user (a real partial-unique-index DB invariant),
  grounded via `fact_ids` back to confirmed `ProfileFact`s; idempotent under both sequential retry
  and a genuine 6-thread concurrent race (`SELECT ... FOR UPDATE`, verified refresh-safe by construction);
- async Celery processing (`resume_import.process`) with random `processing_lease_token` fencing,
  dedicated lease retry wait countdown and budget, soft/hard task time limits (`soft=120 < hard=180 < lease=300`);
- durable dispatch recovery via self-rescheduling worker sweep task (`sweep_stale_resume_import_dispatches_task`),
  removing reliance on client polling;
- complete DB/storage compensation on failure (compensating delete covers audit write and commit);
- migration `0004` downgrade is data-safe (fails fast with `RuntimeError` if `resume_import` facts exist);
- new schema (migration `0004`, chained off `0003`): `resumes`, `resume_versions`,
  `resume_imports`, `resume_import_candidates`;
- OpenAPI/TypeScript contract regenerated (4 new operations under `/api/v1/me/resume-imports`,
  `/api/v1/me/resumes/master`);
- source reuse: `Gsync/jobsync` (resume-import prompt/eval discipline, PORT — schema/code
  explicitly rejected) and `AbhishekMandapmalvi/AutoApply` (heading-synonym-map concept, ADAPT —
  Markdown/regex machinery explicitly rejected), both recorded in `docs/SOURCE_REUSE_LEDGER.md`;
- no malware scanner integrated (documented limitation, `docs/SECURITY_PRIVACY.md` §12);
- zero real AI/LLM provider calls, zero PDF/resume rendering, zero signed-URL/download endpoint,
  zero Resume Studio/ATS/tailoring/applications/jobs/matching/WhatsApp/Career Agent/browser
  extension, zero full mobile import UI, zero real deploy, zero real R2 mutation, zero real
  migration execution.

**Open pre-production gate / known limitation:** Raw uploaded source blob retention/deletion lifecycle is not yet implemented end-to-end. Stored blobs in R2 (staging/production) and local storage (development) persist indefinitely; WP1.1's account deletion skeleton does not yet reach into resume-import storage blobs. Before production launch or handling real personal data, an approved retention policy, orphan reconciliation, and end-to-end blob deletion must be implemented (see ADR-0011).

### Jobs Domain, Manual Intake & Normalization Foundation — WP2.1-A (VERIFIED)
See `docs/adr/0012-jobs-domain-manual-intake.md` (D-054). Independently reviewed, merged via squash into `main` as PR #25 (`main` commit `38da12050d9613f502bce956edc7167c8427f3df`), post-merge CI run `37716255293` 8/8 green. **WP2.1-A is VERIFIED.**
- Canonical `Company`, `Job`, `JobOrigin`, `IdempotencyKey` entities (migration `0005`);
- Strong identity via `(provider, external_id)` with nullable `external_id` for manual intakes;
- Corporate domain identity protection against multi-tenant ATS hostnames;
- Deterministic text normalization, workplace type classification, and similarity fingerprint;
- `SafeHttpClient` SSRF security boundary (DNS pre-resolution, IP allowlist, connection pinning, SNI/TLS preservation, 2 MiB streaming decoded limit, manual redirects);
- Authenticated manual paste intake (`POST /api/v1/jobs/intake/paste`), authenticated safe URL intake (`POST /api/v1/jobs/intake/url`), authenticated job detail retrieval (`GET /api/v1/jobs/{jobId}`).

### External Hosted ATS Connectors & Ingestion Scheduler — WP2.1-B (VERIFIED)
See `docs/adr/0013-external-ats-connectors-ingestion.md` (D-055). Independently reviewed across two rounds and two concurrency/idempotency hardening findings, merged via squash into `main` as PR #36 (squash commit `fccc14bcdf581245bdefd7b67395ca96eafcf2f7`, approved PR HEAD `8586c759f8ee28cfcead9310f4740f479cf4dfde`). CI green pre-merge (run `37800774060`, 8/8) and post-merge on `main` (run `37822195467`, 8/8). Tree equivalence confirmed (`e86f20fb132438725128a582638dfc8cf338e8d6`). **WP2.1-B is VERIFIED.**
- Greenhouse, Lever, and Ashby adapters conforming strictly to `ports.JobSource` protocol;
- Shared bounded SSRF-safe ATS HTTP transport with per-hop deadline budgeting, 5s connect / 10s read timeouts, 5 MiB payload bounds;
- Canonical ingestion pipeline (`ingest_job_source`), source-scoped company identity without name merging, mutable posting updates, complete-discovery liveness guard, 3-strike absence closure with recovery;
- Celery worker tasks with stable task-ID-derived `cycle_id` ensuring redelivery idempotency, dedicated-connection PostgreSQL advisory locking with 64-bit key, and centralized Celery Beat scheduling.

### Matching Engine — WP2.2 (VERIFIED)
See `docs/adr/0014-matching-engine.md` (D-056). Independently reviewed across multiple rounds (CRITICAL=0, HIGH=0, MEDIUM_BLOCKING=0: APPROVED), merged via squash into `main` as PR #40 (squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, approved implementation HEAD `55744b6872b49854cad45c2ef69cd3c132f3c073`). Post-merge CI run `37997452936` on `main` is green 8/8. **WP2.2 is VERIFIED.**
- Deterministic `MatchingEngine` under `tapjob_api.matching` (internal domain logic, not a port protocol), consuming `TruthSnapshot` (confirmed facts only) and canonical `Job`;
- Integer score scale 0..100; hard-filter knock-out gate (`WORK_AUTHORIZATION`, `WORKPLACE_TYPE`, `LOCATION`, `SALARY_MINIMUM`, `ROLE_ALIGNMENT`) with strict distinction between `HARD_BLOCKER`, `SOFT_MISMATCH`, and `UNKNOWN` (undisclosed job attributes never block; hard blocker forces `overall_score = 0` and `eligibility = INELIGIBLE`);
- Deterministic 5-component lexical scoring (title 25%, skills 35% with 75/25 required vs preferred weight, experience 20%, seniority 10%, location/workplace 10%) using pure integer arithmetic (basis points fixed-point arithmetic 0..10,000, round-half-up to integer 0..100, zero float);
- Grounded deterministic template explainability linking strengths directly to `ProfileFact.id` values; no LLM dependency in the WP2.2 runtime baseline (optional presentation-layer LLM synthesis via `AiGateway` `AiPurpose.JOB_MATCH_EXPLANATION` remains an architectural capability for future work packages);
- Reproducibility invariant bound to `matching_policy_version` (`2026.1`), `profile_version`, `job_match_fingerprint`, and `evaluation_as_of_date`;
- PostgreSQL canonical `match_results` persistence with unique constraint `uq_match_results_user_job_versions` (migration `0006`) + volatile Redis cache (24h TTL) with visibility pre-check; concurrency handling via `INSERT ... ON CONFLICT DO NOTHING` asserting `deterministic_result_digest` equality;
- Synchronous single match (< 50ms p95) via authenticated `POST /api/v1/jobs/{job_id}/match` endpoint; untrusted job descriptions treated as inert text (prompt-injection impervious); 15 golden deterministic eval scenarios and 6 architectural invariant test suites passing.

## 2. Not implemented

No production feature beyond WP1.1's identity/account foundation, WP1.2's Career Profile/Truth Store, WP1.3's Resume Import, WP2.1's Jobs Domain & Connectors, and WP2.2's Matching Engine exists yet. The mobile app has only a minimal auth technical surface, no product screens.

### Canonical Principle of Channels
TapJob is ONE product with ONE Career Core. WhatsApp, Mobile App, and Browser Extension are surfaces/channels over the same canonical state and business rules:

```text
WhatsApp ──┐
Mobile ────┼──> TapJob Core
Extension ─┘
```

Nenhum canal pode possuir:
- profile truth próprio;
- matching próprio;
- application state próprio;
- resume truth próprio;
- Career Agent policy própria;
- business rule própria.

O backend/Core permanece channel-neutral. Compartilham a mesma:
- User identity;
- Truth Store;
- Jobs;
- Matching;
- Resume;
- Application Kit;
- Applications/Tracker;
- Career Agent;
- Notifications policy.

Canais variam APRESENTAÇÃO e CAPACIDADES DE INTERFACE, nunca a verdade canônica.

Specifically NOT implemented:
- Full downstream work packages (WP3.x Resume & Application Kit, WP4.x Tracking & Agent, WP5.x Apply Engine, etc.);
- WhatsApp Alpha beyond vertical slice (full WhatsApp feature suite deferred);
- Mobile Product Experience beyond Jobs surface (Resume Studio, Kanban/Application Tracker, Analytics, Interviews — deferred);
- Full mobile profile UI (WP1.2 and WP1.3 are backend-verified; mobile UI is deferred to Mobile Product stage);
- AI-backed resume parser (deterministic parser implemented in WP1.3; AI extraction deferred);
- Semantic layer (embeddings) and LLM re-score for matching (deferred from WP2.2);
- Apply Engine (Phase 5), Browser Extension (Phase 5);
- Gmail integration / Inbox Intelligence (Phase 6);
- Notifications delivery / FCM push delivery (device push-token storage exists; nothing is ever sent);
- A real deletion orchestrator (the skeleton records/tracks a request only; no data is ever actually deleted);
- CD / deployment (CI exists; nothing is deployed);
- Monitoring/production infrastructure.

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

### WP0.2 — Technical Contracts & Remaining Architecture Decisions (VERIFIED)

Checkpoints:
- **WP0.2-A — VERIFIED:** OpenAPI/client generation strategy; `/api/v1` versioning convention; `ErrorResponse` envelope and exception handlers; cursor pagination codec; `Idempotency-Key` convention (documented, not yet implemented); contract CI gates. See `docs/adr/0003-api-contract-client-generation.md` and `docs/adr/0004-http-errors-pagination-correlation-idempotency.md` (section 1 above has the full checkpoint summary). Independently reviewed and merged in PR #9; CI green post-merge on `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`.
- **WP0.2-B — VERIFIED:** canonical internal ports (`JobSource`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector`) as `Protocol`s with a capability model; worker-side request correlation; deployment/CD contract (not a real deploy). `MatchingEngine` is not a provider-neutral port here: it remains internal/domain logic for now; interface extraction is deferred to WP2.2 if justified by a real substitution need (not assumed up front).
  - **Checkpoint A — VERIFIED:** the seven ports, capability model, `CallContext`/`PortError`/`AdapterHealth`. Independently reviewed and merged via squash in PR #12 (`main` commit `a8bfbe621e5a83ae5f205adb1919ce3250684efb`); CI green post-merge (run `37055251128`, 7/7). See section 1 above and `docs/adr/0005-internal-ports-capability-model.md`.
  - **Checkpoint B — VERIFIED:** worker-side TapJob correlation id propagated across Celery message boundaries (custom `tapjob_correlation_id` header, never Celery's own built-in `correlation_id`), bound into structured worker logs alongside Celery task/root/parent id and never task args/kwargs; deployment/CD contract establishing build-once/promote-the-same-immutable-digest, one backend image (D-043 preserved), `infra/compose.deploy.yaml`, `tools/deploy/validate_image_ref.mjs` digest-format gate, and a `workflow_dispatch`-only `.github/workflows/release-image.yml` GHCR publish workflow (zero runs to date). Independently reviewed and merged via squash in PR #15 (`main` commit `3edaf1cee33b892a5d753b07b9b9946cbcd28887`); CI green pre-merge (run `37230556089`, 8/8) and post-merge (run `37231990603`, 8/8). See section 1 above and `docs/adr/0006-worker-correlation-deployment-cd-contract.md`/`docs/DEPLOYMENT.md`. No real deploy, no GHCR publish, no product feature, no migration.

Already resolved (pre-WP0.2):
- auth: Firebase Authentication;
- worker: Celery + Redis;
- object storage: Cloudflare R2;
- initial hosting: Linux + Docker Compose behind Cloudflare.

Output:
ADRs/contracts, not production feature claims. Both WP0.2-B checkpoints are independently reviewed, merged and post-merge CI verified; WP0.2-B — and WP0.2 as a whole — is VERIFIED.

### WP1.1 — Identity & User Account (VERIFIED)
The first authorized product work package. See section 1 above and `docs/adr/0009-identity-auth-user-account.md` (D-051). Independently reviewed and merged via squash (PR #17, `main` commit `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`); CI green pre-merge (run `37254156902`, 8/8) and post-merge (run `37312677497`, 8/8).

### WP1.2 — Career Profile & Truth Store (VERIFIED)
See section 1 above and `docs/adr/0010-career-profile-truth-store.md` (D-052). Implemented end-to-end (CareerProfile, ProfileFact, typed fact values, optimistic concurrency, truth snapshot, provenance, audit, migration `0003`, OpenAPI/contracts regenerated), independently reviewed across three rounds, merged via squash as PR #19 (`main` commit `5c06c805ea2a68fcf100ec56cff5eaf715e962c2`). Real post-merge CI (run `37364799439`, attempt 2) exposed a genuine concurrency defect (identity-map staleness under `SELECT ... FOR UPDATE`, ADR-0010 "Addendum — round 4"); the structural fix was independently reviewed and merged via squash as corrective PR #20 (`main` commit `f30590577cf25733de63025f987c251ebe0e89af`); post-merge CI on the resulting `main` (run `37397784331`) is green 8/8, with `Backend integration` re-proving the previously-failing concurrency test alongside a new deterministic regression test. **WP1.2 is VERIFIED.**

### WP1.3 — Resume Import (VERIFIED)
See section 1 above and `docs/adr/0011-resume-import.md` (D-053). Implemented end-to-end, independently reviewed across two rounds (10 findings in Round 1, 7 findings + 2 hardening items in Round 2), rebased cleanly over Expo maintenance PR #23, merged via squash as PR #22 (squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, approved PR HEAD `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`). CI green pre-merge (run `37538366658`, 8/8) and post-merge (run `37551974662`, 8/8). Tree equivalence confirmed (`af7d26f4544f573f4d93b212b4c11b070f3f2052`). **WP1.3 is VERIFIED.** Phase 1 Career Core baseline (WP1.1, WP1.2, WP1.3) is complete to the extent defined by those work packages. Raw source blob retention/deletion lifecycle is documented as an open pre-production gate.

### Phase 2: WP2.1-A — Jobs Domain, Manual Intake & Normalization Foundation (VERIFIED)
Implemented slices A1 (canonical `Company`, `Job`, `JobOrigin`, `IdempotencyKey` models, Alembic migration `0005`, deterministic normalization, manual paste intake `POST /api/v1/jobs/intake/paste`, `GET /api/v1/jobs/{jobId}`) and A2 (Safe URL Intake `POST /api/v1/jobs/intake/url`, `SafeHttpClient` SSRF security boundary with DNS pre-resolution, IP validation, connection pinning, TLS SNI & certificate verification preservation, `trust_env=False`, 2 MiB streaming decoded bounds, max 3 manual redirects, HTML/JSON-LD extraction, JS-only dynamic page fallback). Merged via squash into `main` as PR #25 (`main` commit `38da12050d9613f502bce956edc7167c8427f3df`), post-merge CI run `37716255293` 8/8 green. **WP2.1-A is VERIFIED.** See `docs/adr/0012-jobs-domain-manual-intake.md` (D-054).

### Phase 2: WP2.1-B — External Hosted ATS Connectors & Ingestion Scheduler (VERIFIED)
Implemented adapters for Greenhouse, Lever, and Ashby conforming strictly to `ports.JobSource` protocol (`capabilities`, `discover`, `fetch_details`, `health`); Ashby adapter strictly aligns with documented official public contract (no reliance on undocumented `id`, bounded deterministic SHA-256 `external_id` from canonical `jobUrl`, `isListed == False` direct-link filter, `secondaryLocations` object parsing, workplace hints, cached listing details); Greenhouse adapter filters prospect posts (`internal_job_id is None`) and caches details with `content="true"`; Lever adapter parses `allLocations` and workplace type; shared base ATS HTTP client with per-hop deadline budgeting, 5s connect / 10s read timeouts, 5 MiB bounded responses, `trust_env=False`, SSRF protection with connection pinning and DNS pre-resolution, and normalized `PortError` mapping; `JobSourceRegistry` and operational health probes with fail-closed configuration (`extra="forbid"`); canonical ingestion pipeline (`ingest_job_source`) persisting global discovery into `Company`, `Job` (`created_by_user_id=None`), and `JobOrigin` (`(provider, external_id)`) with corporate domain sanitization, source-scoped company reuse without name merging, and mutable ATS posting updates on re-observation; non-destructive liveness reconciliation with stable task-ID-derived `cycle_id` (`sweep:{request.id}` / `task:{request.id}`) guaranteeing idempotency under Celery redelivery (`acks_late=True`) and retries, dedicated-connection PostgreSQL advisory locking per source board with 64-bit bigint key and verified release; transitioning to `CLOSED` after 3-strike threshold with automatic status recovery; Celery worker tasks `jobs.ingest_source` and periodic `jobs.sweep_scheduled_ingestion` in `tapjob_worker` with centralized Celery Beat scheduler. See `docs/adr/0013-external-ats-connectors-ingestion.md` (D-055). Independently reviewed across two rounds and two additional concurrency/idempotency hardening findings, merged via squash into `main` as PR #36 (squash commit `fccc14bcdf581245bdefd7b67395ca96eafcf2f7`, approved PR HEAD `8586c759f8ee28cfcead9310f4740f479cf4dfde`). CI green pre-merge (run `37800774060`, 8/8) and post-merge on `main` (run `37822195467`, 8/8). Tree equivalence confirmed (`e86f20fb132438725128a582638dfc8cf338e8d6`). Known blocking findings = none. Regressions on WP2.1-A = none. **WP2.1-B is VERIFIED.**

### Phase 2: WP2.2 — Matching Engine (VERIFIED)
Architecture, contracts, scoring models, explainability, cache, concurrency, evals, and implementation plan established in ADR-0014 (D-056). Implemented under `tapjob_api.matching` (pure internal domain logic, not a port protocol), consuming `TruthSnapshot` (confirmed facts only) and canonical `Job`; integer score scale 0..100; hard-filter knock-out gate (`WORK_AUTHORIZATION`, `WORKPLACE_TYPE`, `LOCATION`, `SALARY_MINIMUM`, `ROLE_ALIGNMENT`) with strict distinction between `HARD_BLOCKER`, `SOFT_MISMATCH`, and `UNKNOWN` (undisclosed job attributes never trigger blockers; hard blocker forces `overall_score = 0` and `eligibility = INELIGIBLE`); deterministic 5-component lexical scoring (title 25%, skills 35% with 75/25 required vs preferred weight, experience 20%, seniority 10%, location/workplace 10%) with pure basis points integer arithmetic and round-half-up, bilingual Portuguese/English normalization and technology alias map; neutral imputation (50/100, UNKNOWN) for omitted non-mandatory attributes; minimum eligible score threshold at 50; semantic layer (embeddings) and LLM re-score deferred to WP2.3+; grounded deterministic template explainability linking strengths directly to `ProfileFact.id` values with no LLM dependency in the WP2.2 runtime baseline (optional presentation-layer LLM synthesis via `AiGateway` `AiPurpose.JOB_MATCH_EXPLANATION` remains an architectural capability for future work packages); reproducibility invariant via `matching_policy_version` (`2026.1`), `profile_version`, `job_match_fingerprint`, and `evaluation_as_of_date`; PostgreSQL canonical `match_results` persistence with unique constraint `uq_match_results_user_job_versions` on `(user_id, job_id, profile_version, job_match_fingerprint, policy_version, evaluation_as_of_date)` (migration `0006`) + volatile Redis cache (24h TTL) with visibility pre-check; concurrency handling via `INSERT ... ON CONFLICT DO NOTHING` asserting equality of `deterministic_result_digest`; single match synchronous (< 50ms p95, benchmarked at < 1ms), bulk match deferred to WP2.3 (asynchronous Celery); authenticated `POST /api/v1/jobs/{job_id}/match` API endpoint; untrusted job descriptions treated as inert text (prompt injection impervious); 15 golden deterministic eval scenarios and 6 invariant suites passing. Independently reviewed across multiple rounds and approved with zero blocking findings (CRITICAL=0, HIGH=0, MEDIUM_BLOCKING=0); merged via squash into `main` as PR #40 (squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, approved implementation HEAD `55744b6872b49854cad45c2ef69cd3c132f3c073`). Post-merge CI run `37997452936` on `main` is green 8/8. **WP2.2 is VERIFIED.**

### Minimum WhatsApp Alpha Vertical Slice (VERIFIED)
The cross-WP minimum vertical slice delivering an end-to-end user journey on WhatsApp over the channel-neutral Core was implemented, independently reviewed across multiple rounds (CRITICAL=0, HIGH=0, MEDIUM_BLOCKING=0: APPROVED), merged via squash into `main` as PR #42 (approved HEAD `b47426eb648a1e2fb2f1e076102e803c61391558`, squash/main commit `ad055e3516ada08cc7e5fc60eab3a439b28ef346`). Post-merge CI run `38165824859` on `main` is 8/8 green. Tree equivalence confirmed. Closeout: `CLOSED_AND_VERIFIED`.

Included capabilities:
- Channel identity models and linkage (`channel_providers`, `channel_identities`, `channel_identity_links`, migration `0007`);
- Provider envelope lease fencing, inbound idempotency, and domain effects (`provider_event_envelopes`, `inbound_effects`, migrations `0008`, `0011`, `0012`);
- Outbound message queue with retry, backoff, and outbox service (`outbound_message_queue`);
- Minimal application tracking & reminders (`applications`, `application_kits`, `reminders`, migration `0009`);
- Reviewable candidate drafts (`candidate_review_drafts`, migration `0010`);
- Conversational intent routing, resume intake, tailored claims, grounded explanation, and follow-up tools;
- Evolution API adapter isolated behind `MessagingChannel` port with echo dropping and webhook authentication;
- Celery worker tasks with durable claims, soft time limits, and recovery sweeper.

Current product stage: **MOBILE APP / MOBILE PRODUCT EXPERIENCE**.  
Current authorized capability WP: **WP2.3 — Job Feed & Alerts (IN PROGRESS)**.

## 5. Phase gates

### Gate 0
Foundation and CI ready. (SATISFIED)

### Gate 1
Career Core accepted. (SATISFIED)

### Gate 2
Jobs & Matching accepted. (WP2.1-A, WP2.1-B, WP2.2 SATISFIED; WP2.3 IN PROGRESS)

### Gate 3
Resume & Application Kit accepted.

### Gate 4
Tracking + Agent + WhatsApp accepted. (WhatsApp Alpha VERIFIED)

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

TapJob has a defined product and architecture baseline, a repository/engineering skeleton (WP0.1-A + WP0.1-B), and three verified technical-contracts checkpoints (WP0.2-A, WP0.2-B Checkpoint A, WP0.2-B Checkpoint B) — **Phase 0 / Foundation is VERIFIED and complete**.

Phase 1 — Career Core baseline is **VERIFIED**:
- WP1.1 — Identity & User Account (`docs/adr/0009-identity-auth-user-account.md`, D-051) is **VERIFIED**;
- WP1.2 — Career Profile & Professional Truth Store (`docs/adr/0010-career-profile-truth-store.md`, D-052) is **VERIFIED**;
- WP1.3 — Resume Import (`docs/adr/0011-resume-import.md`, D-053) is **VERIFIED**.

Phase 2 — Jobs & Matching intake and deterministic matching foundation is **VERIFIED**:
- WP2.1-A — Jobs Domain, Manual Intake & Normalization Foundation (`docs/adr/0012-jobs-domain-manual-intake.md`, D-054) is **VERIFIED**;
- WP2.1-B — External Hosted ATS Connectors & Ingestion Scheduler (`docs/adr/0013-external-ats-connectors-ingestion.md`, D-055) is **VERIFIED**;
- WP2.2 — Matching Engine (`docs/adr/0014-matching-engine.md`, D-056) is **VERIFIED** (PR #40, squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, approved implementation HEAD `55744b6872b49854cad45c2ef69cd3c132f3c073`, post-merge CI run `37997452936` 8/8 green).

Channel Surface Slice:
- **Minimum WhatsApp Alpha Vertical Slice is VERIFIED** (PR #42, approved HEAD `b47426eb648a1e2fb2f1e076102e803c61391558`, squash/main commit `ad055e3516ada08cc7e5fc60eab3a439b28ef346`, post-merge CI run `38165824859` 8/8 green, tree equivalence YES, independent closeout CLOSED_AND_VERIFIED).

Current Product Stage & Priority:
- **CURRENT_PRODUCT_PRIORITY = WHATSAPP_LIVE_AND_COMMERCIAL_READINESS**: The immediate strategic objective is to achieve commercial readiness and live validation for the WhatsApp product before further expansion of the mobile app.
- **MOBILE STATUS**: `MOBILE_PRODUCT_STARTED = YES`, `MOBILE_PRODUCT_SCOPE = JOBS_DOMAIN_ONLY`. Further expansion of the mobile client is paused (`FURTHER_MOBILE_PRODUCT_EXPANSION = PAUSED_UNTIL_WHATSAPP_LIVE/PILOT_COMMERCIAL_GATE`).
- **ACTIVE_EXECUTION = WHATSAPP_LIVE_GATE (Phase B First Real End-to-End Canary Verified)**: Phase A verified and merged via PR #46 (commit `b43dd71f39dfdbd72f1dd48f5c64a8a84f22dd74`). Dependency patch drift resolved via PR #47 (commit `f9c9dc621b610ab254e43ccb33c85b3bdbb20ea8`, post-merge CI 8/8 green). On branch `ops/whatsapp-live-canary-phase-b`, Phase B first real end-to-end canary was executed between dedicated staging identities: Evolution instance `TapJob` (`********8445`, state `open`) and `TapJob-Canary-Client` (`********8733`, state `open`). Execution-time allowlist strictly locked to that pair (`CUSTOMER_OR_THIRD_PARTY_SENDS=0`). Canary dispatched "oi" via `POST /message/sendText/TapJob-Canary-Client` (HTTP 201, ID `3EB0F08E1C1E850D2D8D21`). Real Evolution webhook `messages.upsert` delivered to TapJob public Traefik endpoint (HTTP 202, `X-Webhook-Secret` authenticated). Processed by Celery worker, resolved Canary user/identity, executed first-contact greeting orchestration, created outbox entry. Dispatched real reply from TapJob to Canary via Evolution `sendText` (HTTP 201, ID `3EB0CAC4CDF3F4B420A546`). Reply verified on Canary client (`fromMe=false`, 160 chars greeting text, end-to-end latency ~10.1s). Provider boundary `fromMe=true` echo filtered with zero recursive outbox entries and zero loops. Live replay idempotency verified: replaying captured real payload returned HTTP 202 with exactly 0 duplicate envelopes, users, outbox intents, or WhatsApp sends. Total real send operations: exactly 2 (`CANARY_CLIENT_TO_TAPJOB_SENDS=1`, `TAPJOB_TO_CANARY_SENDS=1`). Runtime services healthy. PR open for independent review.

Verified Work Package:
- **WP2.3 — Job Feed & Alerts is VERIFIED** (PR #43, squash/main commit `812b6200e125b14acabbd992b5e759f1ff0d879a`, post-merge CI run `38400363321` 8/8 green). Downstream full work packages (WP3.x, WP4.x, WP5.x) remain unstarted and are NOT automatically authorized.

Agent Factory Transition:
- **AF-0 — Governance & Safety Foundation is CLOSED_AND_VERIFIED** (PR #14 merged into canonical `main` with squash commit `5c56526e18928babc9b2b8ea4ecf022d68c2d1a4`, post-merge CI `39143931751` 8/8 green). Establishes Google Antigravity 2.0 multi-agent orchestrator governance, root `AGENTS.md` invariants, modular `.agents/rules/`, PreToolUse safety guards (`tools/agent_guard/pre_tool_guard.py`), runtime smoke validation, local git hooks (`tools/agent_guard/validate_governance.py`), and CI quality gates.
- **AF-1 — Formal Team Instantiation & Automated Worktree Dispatch is CLOSED_AND_VERIFIED** (PR #15 merged into canonical `main` with squash commit `f01370e51a7662dd22d3cb7cf0dc8f9f9e29153f`, post-merge CI run `39171519229` 8/8 green). Establishes 14 formal role profiles (`tools/agent_factory/profiles/`), Antigravity `define_subagent` and `agent.md` generation (`tools/agent_factory/team.py`), deterministic task contracts (`tools/agent_factory/task_contract.py`), automated worktree dispatch with dirty cleanup prevention and strict isolation safeguards (`tools/agent_factory/worktree_dispatcher.py`), persistent multi-process task registry with mutex locking, role ownership and write permission enforcement, TOCTOU exact HEAD pinning lifecycle gates, internal repair loop budget manager with separation of duties enforcement (`tools/agent_factory/repair_loop.py`), operational CLI bootstrap tool, and 30 unit tests wired into CI.
- **AF-2 — Multi-Agent Parallel Execution is NOT STARTED / NOT AUTHORIZED**: Downstream Agent Factory checkpoints remain unstarted and require explicit human authorization before initiation.


# Decision Register — TapJob

This is the current high-level decision register. Structural decisions may later be expanded into dedicated ADRs.

| ID | Decision | Status | Date |
|---|---|---|---|
| D-001 | Build one Career Core exposed by mobile, WhatsApp and later web/extension | Accepted | 2026-09-29 |
| D-002 | Brazil is the initial market | Accepted | 2026-09-29 |
| D-003 | AIApply + Huntr + Simplify are product references, not dependencies | Accepted | 2026-09-29 |
| D-004 | Use public reference repos as accelerators with provenance | Accepted | 2026-09-29 |
| D-005 | React Native + Expo is mobile baseline | Accepted | 2026-09-29 |
| D-006 | Python/FastAPI + PostgreSQL is backend baseline | Accepted | 2026-09-29 |
| D-007 | Start as modular monolith + async workers | Accepted | 2026-09-29 |
| D-008 | Professional Truth Store is mandatory for generated career artifacts | Accepted | 2026-09-29 |
| D-009 | No universal auto-apply promise | Accepted | 2026-09-29 |
| D-010 | Apply Engine supports OFFICIAL_API / ASSISTED_EXTENSION / DEEP_LINK / MANUAL / DISABLED | Accepted | 2026-09-29 |
| D-011 | User retains control over sensitive/legal application answers | Accepted | 2026-09-29 |
| D-012 | LLM provider calls happen through backend AI Gateway | Accepted | 2026-09-29 |
| D-013 | Gmail Inbox Intelligence is optional/feature-gated until compliance ready | Accepted | 2026-09-29 |
| D-014 | Gupy official integration/partnership is a Brazil priority | Accepted | 2026-09-29 |
| D-015 | LinkedIn/Indeed headless auto-apply is not production foundation | Accepted | 2026-09-29 |
| D-016 | GitHub PR review is mandatory; latest-HEAD green CI becomes mandatory once WP0.1 installs CI, with only the documented pre-CI bootstrap exception before that | Accepted | 2026-09-29 |
| D-017 | GitHub documentation is canonical handoff for future agents | Accepted | 2026-09-29 |
| D-018 | TapJob remains the sovereign architecture; no upstream repository becomes the whole-product base | Accepted | 2026-09-30 |
| D-019 | Reuse is selective and classified as IMPORT / ADAPT / PORT / REFERENCE, with one primary runtime donor per capability | Accepted | 2026-09-30 |
| D-020 | Every code-bearing upstream reuse must pin repo + SHA + source path and be recorded in SOURCE_REUSE_LEDGER before merge | Accepted | 2026-09-30 |
| D-021 | Python donors (JobBot/AutoApply) are preferred for backend runtime; Expo donors (Basafy/Resumint/CareerTrack) are preferred for mobile runtime; Flutter donors are reference/port only | Accepted | 2026-09-30 |
| D-022 | `TapJob` is the working product/repository identity; runtime product identity must be centrally configured rather than hardcoded | Accepted | 2026-09-30 |
| D-023 | Visual decisions use centralized semantic design tokens with a DTCG-compatible canonical source where practical; feature screens do not own raw colors/typography/spacing | Accepted | 2026-09-30 |
| D-024 | Configuration is separated into product config, typed environment config, secrets and feature flags; no direct scattered environment/magic-value access | Accepted | 2026-09-30 |
| D-025 | Initial compute runs on the owner's Linux server with Docker Compose; Cloudflare Tunnel/edge protects public ingress; AWS is a scale/migration option, not an initial dependency | Accepted | 2026-09-30 |
| D-026 | Cloudflare R2 is initial private object storage for resumes/documents; PostgreSQL remains canonical business storage and Redis is non-durable infrastructure | Accepted | 2026-09-30 |
| D-027 | Firebase Authentication is initial identity provider with Google, Apple and e-mail; phone/SMS auth is deferred | Accepted | 2026-09-30 |
| D-028 | FCM is mobile push transport; Firebase Analytics measures product behavior; Sentry provides technical/AI observability | Accepted | 2026-09-30 |
| D-029 | Initial UI baseline uses Inter, semantic design tokens, blue primary/orange accent, green success and red error; actual screens never hardcode those values | Accepted | 2026-09-30 |
| D-030 | P0 job sources are manual URL/paste + Greenhouse + Lever + Ashby; Gupy is first Brazil strategic integration; LinkedIn/Indeed are initially assisted/external | Accepted | 2026-09-30 |
| D-031 | Controlled beta can expose implemented capabilities without payment; centralized entitlements/usage limits are designed before billing, while exact pricing remains data-driven | Accepted | 2026-09-30 |
| D-032 | Product analytics uses a canonical event taxonomy and server-confirmed domain events; analytics payloads never contain resume/e-mail bodies or sensitive answers | Accepted | 2026-09-30 |
| D-033 | After WP0.1 installs CI, PR + latest-HEAD green checks and resolved review threads are mandatory by governance. Enforce them with GitHub branch protection/rulesets when the repository plan supports it; until then, use the same gate procedurally. Squash merge is preferred | Accepted | 2026-09-30 |
| D-034 | Celery + Redis is the initial async worker/queue baseline unless an implementation spike proves a material reason to supersede it | Accepted | 2026-09-30 |
| D-035 | TapJob is a candidate-side job-search execution platform, not a LinkedIn-style social network; job feed means opportunity feed and networking means private contacts/outreach/follow-up | Accepted | 2026-09-30 |
| D-036 | AIApply is the primary product archetype; Huntr and Simplify are complementary product references; the 10 audited repositories are implementation accelerators/code donors rather than product-direction authorities | Accepted | 2026-09-30 |
| D-037 | Initial geographic/product focus is Brazil; architecture/i18n/integration boundaries must support later Latin America and broader international expansion without changing the core product thesis | Accepted | 2026-09-30 |
| D-038 | Customer-surface delivery order is Foundation -> Minimum Career Core -> WhatsApp -> Mobile App -> Browser Extension; this changes sequencing only, not approved product scope | Accepted | 2026-09-30 |
| D-039 | WhatsApp is an independent first-class channel over the same Career Core and does not depend on the mobile app; channel identities link to one internal User | Accepted | 2026-09-30 |
| D-040 | Existing Evolution API is the initial WhatsApp transport for development/controlled early validation behind the provider-neutral MessagingChannel adapter; official WhatsApp Cloud API remains the migration target for broad commercial scale | Accepted | 2026-09-30 |
| D-041 | n8n is allowed only as auxiliary bridge/orchestration/ops tooling; it cannot own canonical user state, domain rules, authorization, idempotency, agent policy or application state | Accepted | 2026-09-30 |
| D-042 | EasyPanel may be used as a deployment/operations UI on the existing Linux server but is not an architectural dependency or hidden source of runtime configuration | Accepted | 2026-09-30 |
| D-043 | WP0.1 foundation tooling: uv workspace (single lockfile) for Python, npm workspaces for JS, one backend image for api/worker/migrate, dependency-free DTCG token generator, WP0.1 split into checkpoints A/B — see `docs/adr/0001-monorepo-foundation-tooling.md` | Accepted | 2026-09-30 |
| D-044 | WP0.1-B mobile foundation: Expo SDK 57 shell under `apps/mobile` consuming design tokens/product config; TypeScript realigned to `~6.0.3` workspace-wide for `eslint-config-expo` compatibility; flat ESLint + Prettier baseline with magic-value guard rules extended to raw design values in mobile style properties; `pt-BR`-only i18n architecture ready for `es`/`en`, default locale sourced from product-config (not hardcoded); typed public config (`EXPO_PUBLIC_APP_ENV`, `EXPO_PUBLIC_API_BASE_URL`, optional `EXPO_PUBLIC_SENTRY_DSN`); minimal spacing/typography/border-width design tokens and the Inter typeface actually loaded (`expo-font` + `@expo-google-fonts/inter`, three weights); backend `/ready` readiness endpoint probing PostgreSQL/Redis; Sentry init wiring for api/worker (config plugin/source maps deferred); CI expanded (mobile job, lint/format, Redis in integration job) — see `docs/adr/0002-mobile-foundation.md`. Intended to close out WP0.1 pending review/CI/merge | Accepted | 2026-10-01 |
| D-045 | WP0.2-A API contract: FastAPI/OpenAPI remains canonical; deterministic offline export to `packages/contracts/openapi.json`; business routes under `/api/v1` via `tapjob_api.http.api_v1`, `/health`/`/ready` stay unversioned; explicit camelCase `operationId` per route; TypeScript client generated by `@hey-api/openapi-ts` pinned exact (`0.99.0`) behind a `createApiClient`/`isApiError` facade, `src/generated/**` unreachable from outside the package (`exports` map); structural contract rules (`tapjob_api.http.contract_rules`) enforced at export time and CI-diffed — see `docs/adr/0003-api-contract-client-generation.md` | Accepted | 2026-10-01 |
| D-046 | WP0.2-A HTTP conventions: canonical `ErrorResponse` envelope (`code`/`message`/`requestId`/`errors`/`details`) for every non-2xx response, `code` is the only field logic may branch on; cursor-based `CursorPage`/pagination codec (not offset), explicitly undocumented as tamper-proof; `Idempotency-Key` convention recorded without middleware yet; `X-Request-ID` now guaranteed to equal `ErrorResponse.requestId`, including for a truly unhandled exception (a real header/requestId gap in `RequestContextMiddleware` found and fixed in this checkpoint); OpenTelemetry stays deferred — see `docs/adr/0004-http-errors-pagination-correlation-idempotency.md` | Accepted | 2026-10-01 |
| D-047 | Temporary, scoped, fail-closed, auto-expiring CI exception (`tools/security/audit-gate.mjs`) for a single HIGH advisory (`GHSA-86w9-cpqp-85rv` / `CVE-2026-85393`, `node-forge@1.4.0`, resolved only through Expo's own CLI/build tooling chain, not a TapJob backend/worker dependency); replaces the bare `npm audit --audit-level=high` CI step with a gate that still runs the real audit and still fails on any other/unknown HIGH or CRITICAL finding; expires `2026-11-02T00:00:00Z` or earlier once a compatible official fix lands; `npm audit fix --force`'s proposed `expo@44.0.6` downgrade is explicitly rejected — see `docs/adr/0007-temporary-node-forge-audit-exception.md` (ADR-0005/0006 are reserved for WP0.2-B). Security/CI-tooling decision only; does not change WP0.2-A/B status | Accepted (temporary) | 2026-10-02 |
| D-048 | WP0.2-B Checkpoint A internal ports: seven provider-neutral `typing.Protocol` contracts (`JobSource`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector`) under `tapjob_api.ports` (shared by api/worker, no circular dependency since the worker already depends on the api package); `MatchingEngine` is explicitly NOT a port (remains internal/domain logic, interface extraction deferred to WP2.2 only if a real substitution need arises); shared `CallContext`/`Deadline`, normalized `PortError`/`PortErrorCode` (8 codes, including mandatory `OUTCOME_UNKNOWN`), `AdapterHealth`, and a capability model (`CapabilityStatus` + per-port capability enums, held in a validated, duplicate-rejecting `CapabilityManifest` with deterministic `status_for`); no adapter, no provider, no deploy — see `docs/adr/0005-internal-ports-capability-model.md` | Accepted | 2026-10-02 |
| D-049 | Second temporary, scoped, fail-closed, auto-expiring CI exception (`tools/security/audit-gate.mjs`), added alongside the still-active node-forge exception (D-047), for a single HIGH advisory (`GHSA-vfj7-8cjw-p6xm` / `CVE-2026-93687`, `braces@3.0.3`, observed only in the current Expo/Metro build-tooling and Jest test-tooling dependency paths via `micromatch@4.0.8`, with no direct TapJob application-code import found, and not resolvable to any patched version since none exists upstream); `EXCEPTIONS` now validated as a list (`validateExceptionsConfig`, fail-closed on any duplicate/contradictory GHSA+package entry) so a second exception could be added without weakening the first; expires `2026-11-04T00:00:00Z` or earlier once a compatible official fix lands; pinning to the unreleased upstream fix commit (`micromatch/braces#72`) and `npm audit fix`'s major-version-bump suggestions are both explicitly rejected — see `docs/adr/0008-temporary-braces-audit-exception.md`. Security/CI-tooling decision only; does not change WP0.2-B status | Accepted (temporary) | 2026-10-04 |
| D-050 | WP0.2-B Checkpoint B: (A) worker-side TapJob correlation id propagated via a custom Celery message header (`tapjob_correlation_id`, never Celery's own built-in `correlation_id`, which is usually just the task id), bound into the same `request_id_var` HTTP logging already uses plus a new generic `task_context_var` (Celery task id/root id/parent id/task name/retries, never args/kwargs), via `before_task_publish`/`task_prerun` and reset via `task_postrun`/`task_failure`/`task_retry`, with the validation regex extracted into shared `tapjob_api.core.correlation` instead of duplicated; (B) deployment/CD contract establishing build-once/promote-the-same-immutable-digest (`registry/repo@sha256:...`, never `:latest`) across staging/production, one backend image (D-043 preserved) for api/worker/migrate, a new `infra/compose.deploy.yaml` (no `build:`, no DB/Redis host ports), a small zero-dependency `tools/deploy/validate_image_ref.mjs` digest-format gate, a `workflow_dispatch`-only (not executed) `.github/workflows/release-image.yml` GHCR publish workflow, migration-before-rollout ordering, rollback-by-redigest (never automatic DB downgrade), and EasyPanel/Cloudflare roles bounded to operations/ingress only, never configuration source of truth; SBOM/provenance attestation explicitly deferred — see `docs/adr/0006-worker-correlation-deployment-cd-contract.md`. No real deploy, no GHCR publish, no product feature, no migration | Accepted | 2026-10-04 |

## Pending decisions

### P-001 Auth provider — RESOLVED 2026-09-30
Firebase Authentication by D-027.

### P-002 Worker — RESOLVED 2026-09-30
Celery + Redis baseline by D-034.

### P-003 Hosting — RESOLVED 2026-09-30
Initial staging/production compute uses the owner's Linux server + Docker Compose behind Cloudflare; migration to managed cloud remains an operational trigger.

### P-004 Object storage — RESOLVED 2026-09-30
Cloudflare R2 by D-026.

### P-005 Web timing — RESOLVED 2026-09-30
Mobile + WhatsApp are first-class V1 surfaces. End-user web is complementary and can follow mobile beta; browser extension enters assisted-apply phase.

### P-006 Gmail launch timing — RESOLVED 2026-09-30
Inbox Intelligence is V1.x/feature-gated and only launches publicly after OAuth verification/security obligations are satisfied.

### P-007 Brand/design tokens — RESOLVED 2026-09-30
Resolved by D-022/D-023. Product working name is `TapJob`; exact visual token values/logo are still free to evolve inside the centralized design-system contract.

### P-008 Monetization — PARTIALLY RESOLVED 2026-09-30
Entitlement architecture and Free/Pro/Automation direction are approved by D-031. Exact plan names, prices and quotas remain data-driven commercial decisions.

## Decision change process

Do not edit an Accepted decision to rewrite history.

Instead:
1. add a superseding decision;
2. mark previous as Superseded;
3. explain rationale in PR/ADR.
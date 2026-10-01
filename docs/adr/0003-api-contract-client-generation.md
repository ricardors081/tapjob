# ADR-0003 — API contract source of truth & TypeScript client generation (WP0.2-A)

**Status:** Accepted (merge of the WP0.2-A pull request is the acceptance gate)
**Date:** 2026-10-01
**Decision register:** D-045
**Scope:** OpenAPI export/versioning conventions and the TypeScript client generation strategy WP0.2-A was chartered to decide (`docs/IMPLEMENTATION_PLAN.md` WP0.2, closing `OQ-015`). No Accepted decision (D-001…D-044) is changed, and no product/business endpoint is added by this checkpoint.

## Context

`docs/ENGINEERING_STANDARDS.md` §7 already named FastAPI/OpenAPI as the contract source of truth and left the generated-client strategy open (`OQ-015`). `docs/IMPLEMENTATION_PLAN.md` WP0.2 asked for an OpenAPI/client-generation decision, API versioning, and contract CI gates before product work (jobs/matching/resume/applications) starts.

## Decisions

### 1. Deterministic, offline OpenAPI export

`tapjob_api.openapi_export.build_openapi_schema()` constructs the FastAPI app with synthetic PostgreSQL/Redis DSNs (same pattern as `services/api/tests/conftest.py`) and reads `app.openapi()`. This never enters the app's lifespan (no connection is ever attempted) and does not depend on `openapi_url` being enabled — that setting only controls whether FastAPI serves the schema over HTTP in a given environment, not whether the schema itself can be generated. `export_openapi_schema()` additionally validates the result against `tapjob_api.http.contract_rules` (item 2) before writing it.

Output: `packages/contracts/openapi.json`, serialized with `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False)` plus one trailing `\n`, written with `newline="\n"` explicitly so a Windows checkout cannot locally disagree with the LF `.gitattributes` normalizes everything to. Two consecutive runs against unchanged code produce a byte-identical file (`test_openapi_export.py`). CI regenerates it and runs `git diff --exit-code` to catch drift between the committed contract and the code that actually defines it.

### 2. Structural contract rules, enforced at export time and unit-tested directly

`tapjob_api.http.contract_rules` checks, over the raw schema dict:
- every operation has an explicit, unique, camelCase `operationId` (FastAPI's own auto-generated ids always contain an underscore, so the camelCase pattern alone also catches a missing explicit one);
- every path is under `API_V1_PREFIX` ("/api/v1") except the exempt operational endpoints `/health` and `/ready` (`docs/ENGINEERING_STANDARDS.md` §7: these stay outside versioning);
- every documented non-2xx response of a versioned (`/api/v1/...`) route references `#/components/schemas/ErrorResponse` (ADR-0004). "Under `/api/v1`" is boundary-aware (`path == "/api/v1"` or `path.startswith("/api/v1/")`), not a bare string-prefix check — `/api/v1beta` is not under `/api/v1` even though it shares the string prefix.

`export_openapi_schema()` raises `ContractViolationError` (listing every violation, not just the first) if any rule fails, so a violation fails the export step itself rather than surfacing later as a confusing codegen diff. The rules are also unit-tested directly against small constructed schemas (`test_contract_rules.py`), independent of whether a real business route exists yet — WP0.2 ships contracts, not product endpoints.

### 3. `/api/v1` business API; `/health`/`/ready` stay unversioned

`tapjob_api.http.api_v1` defines `router = APIRouter(prefix="/api/v1")`, mounted in `create_app()` with no routes yet. The first real business endpoint (a later work package) is added to this router and inherits the prefix automatically; `contract_rules.validate_api_v1_scoping` fails the export if anything else drifts outside it. Breaking changes within v1 require an ADR; `/api/v2` is not created speculatively. `docs.expo.dev`'s own SDK versioning precedent and this repository's D-033 (latest-HEAD governance) both already assume this kind of explicit, reviewed versioning discipline.

`/health` and `/ready` keep their pre-existing, already-accepted response shapes (`HealthResponse`, `ReadinessResponse`; ADR-0002) rather than being retrofitted to ErrorResponse: they predate this ADR and have their own richer, already-tested contract. Both now additionally declare an explicit `operationId` (`getHealth`, `getReadiness` — needed for a stable, readable generated SDK function name) and document their shared `500: ErrorResponse` case (`COMMON_OPERATIONAL_ERROR_RESPONSES`, set once per router rather than repeated per route, ADR-0004) — truthful documentation of what `RequestContextMiddleware`'s catch-all now guarantees (ADR-0004 §2), not a behavior change. `/ready`'s own pre-existing 503/degraded case (ADR-0002) is likewise now documented (`503: ReadinessResponse`, additive to the router default) rather than left implicit in the handler alone — the generated client previously had no typed way to represent it; it now appears correctly in `GetReadinessErrors`.

### 4. TypeScript client: `@hey-api/openapi-ts`, pinned exact

Compared against `openapi-typescript` + `openapi-fetch` (`docs/OPEN_QUESTIONS.md` OQ-015's original framing):

| | `openapi-typescript` + `openapi-fetch` | `@hey-api/openapi-ts` |
|---|---|---|
| License | MIT / MIT | MIT |
| `typescript` peer range | `^5.x` (verified 2026-10-01) | `>=5.5.3 \|\| >=6.0.0` (verified 2026-10-01) |
| Last stable release | 2026-02-11, ~8 months stale at evaluation time; its own TypeScript 6 support PRs (#2774, #2818) are open and unmerged | Active (weekly-ish releases through 2026-09-30) |
| Generated surface | Types only | Types + SDK functions (one per `operationId`) + fetch client runtime |
| Runtime dependency | `openapi-fetch` (small, separate package) | None — the fetch client is bundled into the generated output since v0.73 |
| TanStack Query | Separate `openapi-react-query` package | Official plugin, not used yet (deliberately out of scope, item 5) |

`openapi-typescript`'s `typescript: ^5.x` peer range does not accept the `typescript@~6.0.3` ADR-0002 already pinned workspace-wide, and its codegen hasn't shipped a release addressing that in eight months. `@hey-api/openapi-ts` resolved cleanly, so it is the decision — not chosen for novelty, but because the alternative does not install under this repository's existing, already-Accepted TypeScript version.

**Pin:** `@hey-api/openapi-ts` is `"0.99.0"` exact (no `^`/`~`) in `packages/contracts/package.json`'s `devDependencies`; it is a build-time tool, not a runtime dependency of the generated client. An upgrade is a deliberate PR that re-runs generation and reviews the resulting diff — the committed `src/generated/**` output makes that diff visible in review rather than silent.

### 5. Minimal generation scope today

`packages/contracts/openapi-ts.config.ts` runs exactly three plugins: `@hey-api/typescript`, `@hey-api/sdk`, `@hey-api/client-fetch`. TanStack Query and Zod/Valibot schema-validator plugins are available but not enabled — no consumer needs them yet (`docs/IMPLEMENTATION_PLAN.md` WP0.2 explicitly ships contracts, not product screens). Output is committed (`packages/contracts/src/generated/**`), excluded from Prettier/ESLint (same treatment as `packages/design-tokens/generated`, documented at each exclusion site) since it has its own deterministic formatting from the generator.

### 6. Public facade; `src/generated/**` is unreachable from outside the package

`packages/contracts/package.json`'s `"exports"` map publishes only `"."` → `./src/index.ts`. This is a structural (module-resolution), not merely lint, boundary: an app importing `@tapjob/contracts/src/generated/...` gets a resolution error under both Node's and Metro's `exports`-aware resolvers (verified: Metro bundled the facade successfully via a temporary, reverted import — see Consequences). `src/index.ts` re-exports:
- `createApiClient({ baseUrl, getAccessToken?, fetch? })`, building a configured `Client` via Hey API's own `createClient`/`createConfig`. No endpoint declares a security requirement yet, so `getAccessToken` is wired as a request *interceptor* (attaches `Authorization: Bearer <token>` to every outgoing request when a token is returned) rather than through Hey API's per-operation `security`/`auth` config — this does not depend on which future operation eventually declares one;
- `isApiError(value): value is ErrorResponse`, a structural type guard (checks `code`/`message`/`requestId` are strings) for narrowing an SDK call's `error` field;
- `endpoints` (a namespace re-export of the generated SDK functions — `getHealth`, `getReadiness` today) and the generated request/response/error types. A caller passes the client explicitly per call (`endpoints.getHealth({ client })`), per Hey API's own documented pattern, rather than this facade maintaining a mutable global singleton client.

### 7. Wire conventions

- JSON payload fields are camelCase (Pydantic `alias`, e.g. `ErrorResponse.requestId`); Python identifiers stay snake_case.
- Timestamps: ISO-8601 UTC. UUIDs: string. Money: `{ amountMinor, currency }` (`docs/ENGINEERING_STANDARDS.md` §10/§11) — not exercised by any endpoint yet, recorded here as the binding convention for the first one that is.
- The backend does not localize `message` by `Accept-Language` at this stage (ADR-0004 §1); clients branch/localize on `code`.

## Consequences

- **`exactOptionalPropertyTypes: false` is required wherever `@tapjob/contracts` is consumed.** `@hey-api/openapi-ts@0.99.0`'s generated fetch-client *runtime* (`src/generated/client/**`, `src/generated/core/**` — not the facade or the generated *types*) assigns an explicit `undefined` to several optional fields internally and does not satisfy `exactOptionalPropertyTypes: true`, which `tsconfig.base.json` sets workspace-wide (ADR-0001). Verified against this exact generated output, not a general claim about the library. Because this monorepo has no build/`.d.ts` boundary between workspace packages (raw `.ts` source is consumed directly, same as `packages/design-tokens`/`product-config`), a consumer's own `tsc` re-parses and re-checks those same generated files when it follows the import graph — confirmed empirically: `apps/mobile`'s typecheck reproduced the identical errors once it imported the facade. The override is therefore added narrowly, with an explanatory comment, in `packages/contracts/tsconfig.json` (so the package's own typecheck passes) **and** in `apps/mobile/tsconfig.json` (so the one real consumer's typecheck passes); any future consumer (web, extension) will need the same override, which the comment in each file calls out. `tsconfig.base.json` itself is unchanged, so every package that does not import `@tapjob/contracts` keeps full strictness.
- **Metro bundling was verified but is not an automated gate yet.** A temporary, reverted side-effect import (`import "@tapjob/contracts"` in `_layout.tsx`) proved `expo export --platform android` bundles the facade and its generated dependencies without a resolution error (module count rose from 1689 to 1702; Metro's `package.json#exports` support, default-on since Expo SDK 52, correctly resolved the package's single published entry point). This was not left in the committed app, per this checkpoint's "no functional consumption" constraint, so CI's `mobile` job does not yet exercise this path automatically. Re-verify (or make it a permanent, automated check) once a real screen imports `@tapjob/contracts`.
- `@hey-api/openapi-ts`'s own dependency chain pinned a vulnerable `js-yaml` (`@hey-api/json-schema-ref-parser@1.4.4` → `js-yaml@4.2.0`, three advisories, high severity — `GHSA-52cp-r559-cp3m`, `GHSA-5p4m-2wfm-xmqj`, `GHSA-2883-xcg3-v3hh`). Resolved with a scoped `package.json` `overrides` entry (`"@hey-api/json-schema-ref-parser": { "js-yaml": "4.3.2" }`) rather than a blanket `js-yaml` override, so `babel-plugin-istanbul`'s own `js-yaml@3.x` dependency (a different major, different API) is left untouched. `npm audit --audit-level=high` is clean after the override; 15 pre-existing moderate findings remain in the unrelated `@expo/config-plugins` chain (not introduced by this checkpoint).
- `packages/contracts/openapi.json` and `src/generated/**` are excluded from Prettier (`.prettierignore`) and ESLint (`eslint.config.mjs` `globalIgnores`): both already have deterministic formatting from their own generators, and running a third formatter over them would make `git diff --exit-code` fail in CI for no code reason.
- A generic future SDK call's non-2xx response is only typed as `ErrorResponse` in the generated client for status codes the OpenAPI document actually enumerates per operation; an undocumented status at runtime still deserializes as `ErrorResponse` at the HTTP layer (the handlers in ADR-0004 guarantee that), but the generated TypeScript type for that call site will not list it until the route's `responses=` is updated to document it — a reminder that documenting error responses (`contract_rules.validate_error_responses`) is not optional per-route hygiene, it is what makes the generated client's error types accurate.

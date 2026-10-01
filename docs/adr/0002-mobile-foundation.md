# ADR-0002 — Mobile foundation & WP0.1 close-out (WP0.1-B)

**Status:** Accepted (merge of the WP0.1-B pull request is the acceptance gate)
**Date:** 2026-10-01
**Decision register:** D-044
**Scope:** tooling/implementation choices WP0.1-B was chartered to make. No Accepted decision (D-001…D-043) is changed.

## Context

ADR-0001 split WP0.1 into two checkpoints. WP0.1-A delivered the monorepo/backend skeleton. WP0.1-B is chartered to close out WP0.1 with the minimal Expo mobile shell, i18n foundation, typed public config, ESLint/Prettier, magic-value guards, readiness checks, initial Sentry wiring and the corresponding CI expansion, per `docs/IMPLEMENTATION_PLAN.md` and `docs/DELIVERY_ORDER.md`. Whether WP0.1 is actually complete is a governance call made at review/merge, not asserted here.

## Decisions

### 1. Expo SDK 57

Confirmed as the current stable release against `docs.expo.dev/versions/latest` and npm's `latest` dist-tag at implementation time (`expo@57.0.26`; React Native 0.86.3, React 19.2.3, Node ≥22.13). `expo@58` existed only as a pre-release (`next` dist-tag) and was not used.

### 2. Single TypeScript version across the JS/TS workspace

`eslint-config-expo`'s `typescript-eslint` dependency only supports TypeScript `<6.1.0`, while the SDK 57 template itself uses `~6.0.3`. The workspace root is realigned from `typescript@^7.0.2` (WP0.1-A) to `typescript@~6.0.3`, the version Expo's own template ships. One version across `packages/*` and `apps/mobile`; no per-workspace override.

### 3. Monorepo dependency resolution

`apps/mobile` joins the existing npm workspace (no separate lockfile/`node_modules`, consistent with `packages/*`). Expo auto-configures Metro for npm-workspace monorepos from SDK 52 onward, so no manual `metro.config.js` is added. A root `overrides.react = "19.2.3"` pins the one React version SDK 57 bundles: without it, a transitive dependency resolves a newer patch (`19.3.0`), which `expo-doctor` flags as a duplicate native-adjacent dependency.

### 4. Flat ESLint config + Prettier, scoped by workspace

One root `eslint.config.mjs`: `packages/**` gets core JS rules plus non-type-checked `typescript-eslint` rules (type correctness is `tsc --noEmit`'s job, not ESLint's); `apps/mobile/**` extends `eslint-config-expo/flat` via the `defineConfig`/`extends` helper, scoped with `files`, so the Expo ruleset does not leak onto `packages/*`. A handful of `no-restricted-syntax`/`no-restricted-properties` rules enforce the magic-value guards (item 5). Prettier formats `apps/**` and `packages/**` TS/JS/JSON only (not docs, not `packages/design-tokens/generated/**`, which is generated and would drift from its own deterministic serialization).

### 5. Magic-value guards: lint rules, not a bespoke plugin

Four `no-restricted-syntax` patterns (raw hex/rgb color literals, inline `http(s)://` URLs, hardcoded product-identity strings built from `product.json`'s `productName`/`shortName`/`deepLinkScheme` at config-load time) plus one `no-restricted-properties` rule (`process.env` outside `apps/mobile/src/config/public-config.ts`). Import/require module specifiers and test/config files are explicitly excluded from the product-identity pattern so `from "@tapjob/design-tokens"` itself doesn't trip it. `apps/mobile/**/*.tsx` additionally gets a fifth pattern matching a raw number/string literal assigned directly to a design-sensitive style property (`fontSize`, `fontFamily`, spacing/padding/margin/gap, `borderRadius`/`borderWidth`); functional layout properties such as `flex`/`flexShrink` are deliberately not covered, since they aren't visual-design values (item 12).

### 6. Typed public config: fail-fast, two required variables

`apps/mobile/src/config/public-config.ts` is the only module allowed to read `process.env`. `EXPO_PUBLIC_APP_ENV` and `EXPO_PUBLIC_API_BASE_URL` are required and validated (allowed environment values; absolute `http`/`https` URL) at import time; `EXPO_PUBLIC_SENTRY_DSN` is optional. All three are, by `EXPO_PUBLIC_*` convention, public.

### 7. i18n: `pt-BR` only, architecture ready for `es`/`en`

`i18next` + `react-i18next` + `expo-localization`. `SUPPORTED_LOCALES` (which locales have a registered resource) lives in `apps/mobile/src/i18n/locales.ts`; `DEFAULT_LOCALE` is **not** a second hardcoded `"pt-BR"` there. It is derived from `@tapjob/product-config`'s `defaultLocale` through `resolveDefaultLocale()`, which throws at import time if that value has no matching entry in `SUPPORTED_LOCALES` — one source of truth for the default, with a fail-fast guard instead of silent drift if product.json's default ever changes ahead of the mobile resources. No translation work for `es`/`en` in this checkpoint, matching the approved scope.

### 8. Readiness (`/ready`) design

- `/health` stays liveness-only; `/ready` is new and probes PostgreSQL and Redis.
- One pooled `Engine` is created in `create_app`'s FastAPI `lifespan` and stored on `app.state`; `/ready` reuses it instead of opening a new connection per call. `pool_pre_ping=True`; `connect_args={"connect_timeout": ...}` from the new `ApiSettings.readiness_timeout_seconds` (named default `2.0s`, not a literal).
- Redis is checked with `redis-py`'s sync client (`socket_connect_timeout`/`socket_timeout`), matching the existing sync `psycopg` driver rather than introducing an async driver pair.
- Both checks run concurrently via `asyncio.gather(asyncio.to_thread(...), asyncio.to_thread(...))` under one overall `asyncio.wait_for` per check — real parallelism without adding an async DB/Redis driver.
- Any failure (timeout, connection error, auth error, …) is reported as `unavailable`; the response body and logs carry only the dependency name and the exception's class name, never a message, DSN or host (`AGENTS.md` §8).
- `200` when every dependency is reachable, `503` otherwise.

### 9. Sentry: init-only this checkpoint

Both services call one shared `tapjob_api.core.observability.init_sentry`, a no-op unless `SENTRY_DSN` is set (never set in CI/local dev). `RuntimeSettings` gains `sentry_dsn`/`sentry_traces_sample_rate` so the API and the worker share the same fields. The `@sentry/react-native/expo` config plugin, Metro source-map upload and EAS release wiring are **deferred**: `expo install` adds the plugin to `app.json` automatically, and it was removed for this checkpoint because it requires org/project configuration this WP does not set up. `apps/mobile/README.md` documents the deferral.

### 10. CI expansion

New `mobile` job (Expo Doctor, `expo export --platform android --platform ios`). `packages` job gains `lint`/`format:check`. `backend-integration` gains a Redis service and `REDIS_URL` so readiness integration tests and the (now Redis-required) `ApiSettings` can run. `docker` job's health check calls `/ready` instead of `/health`, and the `api` container's own healthcheck was switched to `/ready` too, so `docker compose up --wait` only reports healthy once Postgres and Redis are actually reachable.

### 11. Screen tests live outside `src/app/`

Expo Router treats every file under `src/app/` as a route or layout. Co-locating `*.test.tsx` there made Metro bundle `@testing-library/react-native` into the shipped app and fail to resolve Node's `console` module. Per Expo's own testing guidance, screen tests moved to `apps/mobile/__tests__/`.

### 12. Design tokens: spacing, typography scale and border width for the shell

Independent review of this checkpoint found the two screens using raw visual constants (`padding: 24`, `fontSize: 28`, `borderWidth: 1`, …) despite `packages/design-tokens` already existing — exactly the "arbitrary font sizes/spacing/radius" `docs/DESIGN_SYSTEM.md` §10 calls merge-blocking. `tokens.json` gains the minimal set the shell actually needs, each a semantic alias over the existing primitive scale or a small new scale, not a one-off per screen:

- `spacing.{xs,sm,md,lg,xl}` (4/8/12/16/24), aliasing `primitive.space`;
- `typography.fontSize.{body,title,heading}` (16/24/28);
- `borderWidth.hairline` (1).

Both screens were rewritten to consume these instead of the literals; the lint guard in item 5 now blocks the raw literals from coming back.

### 13. Loading the Inter typeface

The `typography.fontFamily.primary` token named "Inter", but no font was ever loaded, so every screen silently rendered the system font. Fixed with the approach `docs.expo.dev/develop/user-interface/fonts` recommends for Expo-Go-compatible runtime loading: `expo-font`'s `useFonts` hook in `src/app/_layout.tsx`, with `expo-splash-screen` keeping the splash screen up until loading settles, loading only the three weights the shell uses from `@expo-google-fonts/inter`'s **per-weight subpath exports** (`@expo-google-fonts/inter/400Regular` etc., not the package's main barrel, which would pull in all 18 weights — confirmed by `expo export` only bundling the three `.ttf` files, ~342–344KB each).

Expo SDK 57 does not support `useFonts`' "load several weights under one family name" form (SDK 58+ only), so each loaded weight is its own family name (`Inter_400Regular`, `Inter_600SemiBold`, `Inter_700Bold`) rather than one `"Inter"` family combined with `fontWeight`. `tokens.typography.fontFamily` therefore exposes `primaryRegular`/`primarySemibold`/`primaryBold` (the loadable names) alongside `primary` (the typeface's identity, `"Inter"`); the mapping from token to font asset lives in one place, `apps/mobile/src/theme/fonts.ts`. Screens reference only the `primary*` tokens, never a literal `"Inter_700Bold"` string — the lint guard in item 5 now covers `fontFamily` too. While fonts are loading, `_layout.tsx` renders `null`; if loading errors, it still renders the app (falling back to the system font) instead of hanging, and in both cases the splash screen is released. `expo-font`/`expo-splash-screen` were also added to `app.json`'s `plugins`, matching `expo install`'s own guidance for these two packages.

## Consequences

- TypeScript is one version behind the latest major (6.x vs. 7.x) across the whole JS/TS workspace until `eslint-config-expo`/`typescript-eslint` support 7.x; revisit when the Expo SDK template moves.
- `eslint@9.x` is used instead of `10.x` because `eslint-plugin-import`'s peer range caps at `^9`; same revisit trigger.
- `@types/node` is a dev dependency of `apps/mobile` solely so `app.config.ts` can read `product.json` via `node:fs` (Expo CLI loads `app.config.ts` through Node's native ESM loader, which requires a JSON import attribute `@tapjob/product-config`'s own package doesn't declare); this does not change what Metro bundles into the shipped app.
- `expo-doctor`'s "React Native Directory" check depends on an external network call and could not be verified from this development sandbox; it is expected to run normally in GitHub Actions.
- Local backend verification (`ruff`, `mypy`, `pytest`) was initially blocked on this Windows machine because endpoint security rejected execution of the `uv`-provisioned Python interpreter. It was then run for real using the system Python 3.14 already installed and approved on the machine (a fresh `pip install --user` of the project's pinned backend dependencies plus editable installs of the three workspace packages, removed again afterward): `ruff check` passed, `ruff format --check` passed after one fix, `mypy --strict` passed after fixing two misplaced `# type: ignore` comments, and the 45 non-integration tests passed. This local run used Python 3.14, not the project's pinned 3.12; GitHub Actions (Python 3.12, and the only place PostgreSQL/Redis/Docker integration is exercised) remains the authoritative gate. Docker Desktop/WSL remains separately unavailable on this machine, unrelated to this checkpoint.

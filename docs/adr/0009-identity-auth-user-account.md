# ADR-0009 — Identity, Auth & User Account (WP1.1)

**Status:** Accepted
**Date:** 2026-10-04
**Decision:** D-051 (`docs/DECISIONS.md`)

## Context

WP0.1/WP0.2 established the engineering foundation and technical contracts; Phase 0 is VERIFIED
and no product feature exists yet. WP1.1 — Identity & User Account is the first authorized product
work package (`docs/IMPLEMENTATION_PLAN.md`). Firebase Authentication was already decided as the
identity provider, with Google/Apple/email as initial providers
(`docs/FOUNDATION_DECISIONS.md`), and the core identity invariant was already recorded there
verbatim: *"Backend stores Firebase UID as external identity, while TapJob owns its internal User
UUID."* This ADR turns that prior decision into a concrete schema, server-side trust boundary,
session/revocation model, and the minimal mobile technical surface needed to prove it end to end.

WP1.2+ (Career Profile, Jobs, Matching, Resume, Agent, WhatsApp, Apply Engine, Browser Extension)
are explicitly **not authorized** by this ADR.

## A. Identity model

### A1. Canonical User, external identity as a separate table

```text
User (TapJob UUID, canonical)
   ├── ExternalIdentity (provider="firebase", provider_subject=<Firebase UID>)
   ├── future: ExternalIdentity (provider="whatsapp", ...)
   └── future: other identities
```

`users.id` (a TapJob-minted UUID, `uuid.uuid4()` client-side default) is the one canonical
identity primary key. It is never a Firebase UID, never derived from one, and a `User` may exist
with **zero** external identities — this is required, not incidental: a future WhatsApp-first
onboarding can create a `User` before that person ever opens the mobile app, with Firebase linked
only later through an explicit flow this ADR does not need to build yet (§A4). `external_identities`
is a separate table, `UNIQUE(provider, provider_subject)`, `provider` is `firebase` today and is
designed to admit `whatsapp`/future providers later without an architectural migration (it is a
`CHECK`-constrained, not a native-enum, column — see §C1).

### A2. Never auto-link by email

**Security-critical, tested directly (`test_identity_bootstrap_integration.py`):** a new Firebase
UID is never merged into an existing `User` merely because `email` (or `email_verified=true`)
matches. Two different Firebase UIDs reporting the identical, verified email resolve to two
**separate** TapJob Users. `email_snapshot`/`email_verified` on `ExternalIdentity` are a snapshot
for display/audit only, never a lookup key for identity resolution (`service.bootstrap_user` only
ever queries by `(provider, provider_subject)`). Merging a pre-existing channel-only `User` with a
later Firebase identity is a **future, explicit, confirmed-ownership account-linking flow**
(§A4) — out of scope for WP1.1, but the schema does not block building it later.

### A3. Concurrency

Two requests racing to bootstrap the same Firebase UID for the first time must never create two
Users. `UNIQUE(provider, provider_subject)` is the actual backstop, not `service.bootstrap_user`'s
own read-then-write sequence: the loser of the race catches the resulting `IntegrityError`,
rolls back, and re-queries the now-committed row rather than retrying the insert or raising.
Verified under real concurrent load against real PostgreSQL
(`test_concurrent_first_bootstraps_for_the_same_identity_create_exactly_one_user`, 8 concurrent
threads, real DB, not just the single-threaded case the other bootstrap tests exercise).

### A4. Future account-linking boundary (not built here)

Firebase itself already supports linking multiple sign-in methods (Google + Apple + password)
into one Firebase account, normally preserving a single Firebase UID — that is a Firebase-side
concern this ADR does not touch. A **different** operation — linking a pre-existing, channel-only
TapJob `User` (e.g. created via a future WhatsApp-first onboarding) to a Firebase identity created
later — is a TapJob-side account-linking decision, and is explicitly **not** implemented by WP1.1.
The schema must permit building it later without a destructive migration: a `User` with zero
`ExternalIdentity` rows already exists as a valid state (§A1), and `IDENTITY_CONFLICT` (§B4) is
reserved in the error taxonomy for the day this flow needs to report "this Firebase identity is
already linked to a different account" rather than silently doing nothing or auto-merging. The
complete confirmed-ownership linking flow itself (its UX, its verification requirements) is left
for whichever future work package actually needs it.

## B. Token verification & trust boundary

### B1. Firebase Admin adapter, isolated

`tapjob_api.identity.firebase` is the **only** module allowed to import `firebase_admin`. It
exposes `IdentityTokenVerifier` (a `Protocol`: `verify_id_token`/`revoke_refresh_tokens`) and
`FirebaseIdentityProvider` (the real adapter). Every SDK exception
(`InvalidIdTokenError`/`ExpiredIdTokenError`/`RevokedIdTokenError`/`UserDisabledError`/
`CertificateFetchError`/a bare `ValueError`/`FirebaseError`) is normalized into
`FirebaseTokenError(reason)` — the real exception's own type/message never crosses this boundary
(AGENTS.md §8). No test in this repository ever contacts a real Firebase project: every test uses
`FakeIdentityVerifier` (`services/api/tests/identity_fakes.py`), injected via
`app.dependency_overrides[get_identity_verifier]`.

### B2. `check_revoked=True`, always

Every `verify_id_token` call passes `check_revoked=True`. Per Firebase's own documentation,
`revoke_refresh_tokens` only blocks **new** ID tokens minted after the call; an ID token a client
already holds remains individually valid until its own (~1 hour) natural expiry regardless of
revocation. `check_revoked=True` is what actually rejects an already-issued token once its user's
session has been revoked — without it, `/me/sessions/revoke-all` (§D2) would be silently
ineffective against any token minted before the call. This is Firebase's documented revocation
model, not a gap in this implementation; it is recorded here so it is never "rediscovered" as a bug
later.

### B3. Bearer parsing, strict

`Authorization: Bearer <token>` is parsed strictly (`tapjob_api.identity.dependencies`): wrong
scheme, missing token, empty token and an oversized token (bounded above any real Firebase ID
token's observed length, ~900–1200 chars) all resolve to `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID`
before any verification work runs — the same defensive shape
`tapjob_api.http.request_context` already applies to `X-Request-ID`. `uid`/`email`/`provider` are
**never** read from the request body or query string to determine identity; the verified token is
the only source.

### B4. Error taxonomy

New `ErrorCode` members (`tapjob_api.http.errors`), each with its own default message:
`AUTH_TOKEN_MISSING`, `AUTH_TOKEN_INVALID`, `AUTH_TOKEN_REVOKED`, `ACCOUNT_NOT_BOOTSTRAPPED`,
`ACCOUNT_DISABLED`, `REAUTHENTICATION_REQUIRED`, `DEVICE_NOT_FOUND`, `IDENTITY_CONFLICT` (reserved
for the future account-linking-conflict flow, §A6; not raised by any WP1.1 endpoint).
`RATE_LIMITED`/`SERVICE_UNAVAILABLE` already existed (D-046) and are reused, not duplicated.

### B5. Firebase config, server-side

`create_firebase_app()` uses **Application Default Credentials only**
(`firebase_admin.credentials.ApplicationDefault()`) — never a committed service-account key file,
private key, or other secret blob (`docs/DEPLOYMENT.md` §7 convention). Staging/production
eagerly create the real adapter during the app's lifespan (`tapjob_api/main.py`) and fail fast at
process start if ADC is not configured; local/development leave `app.state.identity_verifier`
unset, falling back to a lazy, process-wide singleton only if a route that actually needs it is
hit outside a test (tests always override the dependency). No CI job and no test in this
repository ever calls a real Firebase project.

## C. Data model

### C1. Tables

`users`, `external_identities`, `user_devices`, `consent_records`, `account_deletion_requests`,
`account_audit_events` — full column/constraint detail in
`services/api/src/tapjob_api/identity/models.py` and the migration
(`services/api/src/tapjob_api/migrations/versions/20261004_0002_identity_user_account.py`, revision
`0002`, chained off the WP0.1 baseline `0001`). Every status/provider/platform/decision column is
`sa.Enum(..., native_enum=False, create_constraint=True)` — a `VARCHAR` plus a `CHECK` constraint,
not a PostgreSQL native `ENUM` type: adding a new value later (e.g. a `whatsapp` provider) is an
ordinary `CHECK`-constraint migration, never the destructive `ALTER TYPE ... ADD VALUE` dance a
native enum would require. The migration hand-writes every table/constraint/index rather than
importing the live ORM models (the standard Alembic caution: importing models risks a future
WP1.2+ column leaking retroactively into this migration's `upgrade()`).

### C2. Timezone/locale defaults

`timezone` defaults to `"Etc/UTC"` — a neutral, valid, DST-free IANA zone, never inferred from the
server's own location or the request's apparent origin; the device/user supplies a real value
later (mobile may suggest the device's own timezone, §F). `locale` is seeded from
`tapjob_product_config.get_product_config().default_locale` at `User` creation time (not
hardcoded, not a DB `server_default`, since product config is read at the application layer) and
is independently re-validatable by the same simplified BCP-47 pattern product-config itself uses.
Timezone updates are validated against Python's real `zoneinfo.available_timezones()` — not an
arbitrary string.

### C3. Audit is append-only, allowlisted

`account_audit_events.metadata` (JSONB) only ever receives fields the service layer explicitly
constructs (`tapjob_api.identity.router._audit`) — never `args`/`kwargs`, never a raw request body,
never a password, Firebase token, or `Authorization` header value. `correlation_id` is populated
from the existing `request_id_var` (shared with HTTP/worker correlation, ADR-0006) so an audit row
can be joined back to its originating request's logs.

## D. Session lifecycle

### D1. Firebase owns the client session

TapJob does not invent a second refresh-token system. The client obtains a fresh Firebase ID
token through the Firebase SDK; TapJob's backend only ever verifies that token (§B). There is no
TapJob-issued JWT and no TapJob-side session store beyond `ExternalIdentity`'s own
`last_seen_at`.

### D2. Global revoke-all, not per-device

`POST /api/v1/me/sessions/revoke-all` calls `revoke_refresh_tokens` on the account's linked
Firebase UID — global, not per-device. `UserDevice` is a device **registration** record
(install-time metadata + an optional future push token), not an auth-session object; this ADR
deliberately does **not** invent a custom per-device session/token architecture just to allow
per-device Firebase revocation. If product later requires true per-device session revocation, that
is a separate, later design decision (a real gap worth naming, not silently working around).

### D3. Audit reflects the real provider outcome

The audit row for revoke-all is written **after** the Firebase call returns, and reflects what
actually happened: a `SUCCESS` row is only ever written once Firebase confirms the revocation
(or there was genuinely nothing to revoke, e.g. a channel-only `User` with no Firebase identity
yet); a provider failure is recorded as a `FAILURE` row with the failure reason, never silently
dropped or misreported as success (`test_provider_failure_during_revoke_all_is_service_unavailable_
and_never_claims_success`).

## E. Recent authentication

Security-sensitive actions — session revoke-all, account deletion request/cancel — require the
verified token's own `auth_time` claim to be within `REAUTH_MAX_AGE_SECONDS` (15 minutes,
`tapjob_api.identity.service`) of "now." A stale `auth_time` fails closed with
`REAUTHENTICATION_REQUIRED` (401); the mobile client's only valid response is to request a fresh
provider credential (re-authenticate), never a TapJob-side reauthentication mechanism. This is a
named constant with direct tests at both the boundary (exactly at the max age: still valid) and
just past it (rejected), not a magic number repeated at each call site.

## F. Account settings, devices, consent, deletion (skeletons where noted)

- **Settings:** `locale`/`timezone` only, validated (§C2); a `PATCH` with no fields changes
  nothing.
- **Devices:** `UserDevice.installation_id` is a TapJob-minted, privacy-preserving random
  identifier the app generates and persists (mobile: via `expo-secure-store`, §I) — never an IMEI,
  hardware serial, advertising ID or other invasive fingerprint. Re-registering the same
  `(user, installation_id)` is idempotent (metadata refresh, not a duplicate row) but never
  silently un-revokes an already-revoked device. A push token, if stored, is never sent (FCM
  delivery is explicitly not WP1.1) and never logged.
- **Consent:** `consent_records` is append-only — a changed decision is a new row, never an
  overwrite. No fixed purpose-key catalog is enforced at the DB level: no final legal-purpose list
  has been approved for the product yet (`docs/OPEN_QUESTIONS.md`); this is deliberately the
  recording infrastructure, not a legal conclusion, and no consent is force-recorded at sign-up.
- **Deletion (skeleton):** `POST /api/v1/me/deletion-request` is `202 Accepted` — it only
  schedules/records a request, never claims data was deleted. Idempotent (a second request while
  one is `REQUESTED`/`PROCESSING` returns the existing row; a partial unique index is the
  structural backstop, not just application logic). Cancellable only while still `REQUESTED` —
  once `PROCESSING`, cancellation is refused rather than racing a future deletion orchestrator.
  **Explicit future boundary:** a real deletion orchestrator must eventually revoke tokens,
  disconnect integrations, delete files, delete/anonymize DB data, remove the Firebase account, and
  preserve only legally-required minimal audit data — none of that is implemented here, and no
  automatic Firebase user deletion or hard data delete happens anywhere in this checkpoint.

## G. Rate limiting

`tapjob_api.core.rate_limit.RedisFixedWindowRateLimiter` — generic, Redis-backed, one key per
`(policy, principal, window)`, reused from a small shared module (not identity-specific; a later
feature needing its own rate limit reuses this instead of building a second implementation).
Named policies (`tapjob_api.identity.dependencies`): bootstrap, session revoke-all, device
mutation, consent mutation, deletion request — each with its own limit/window, no magic numbers at
call sites. Principal key is the verified Firebase UID (bootstrap, pre-`User`) or the internal
`User` UUID (every other endpoint) — never a raw client IP or `X-Forwarded-For` (proxy trust
semantics are not configured). **Fail-closed, deliberately:** every policy here guards a
security-sensitive mutation, so a Redis outage (`RateLimiterUnavailableError`) fails the request
with `SERVICE_UNAVAILABLE` rather than silently disabling the limit — availability of these
specific endpoints is less important than not letting abuse protection quietly vanish during an
infrastructure incident.

## H. Mobile Firebase SDK decision

**Decision: React Native Firebase + an Expo Development Build — not the Firebase JS SDK, not Expo
Go.**

Firebase JS SDK's only supported React Native persistence
(`getReactNativePersistence(AsyncStorage)`) stores the raw ID/refresh token in **unencrypted**
AsyncStorage. This is a documented property of that SDK, not a configuration mistake, and it
directly conflicts with `docs/SECURITY_PRIVACY.md`'s secure-token-storage expectation. React
Native Firebase instead rides on each platform's native Firebase Auth SDK, which persists session
state via the OS's own secure storage (Keychain on iOS, Keystore-backed on Android) — materially
stronger, and consistent with `docs/ENGINEERING_STANDARDS.md` §15's guidance that
`expo-secure-store` is for TapJob-owned secrets, not a justification to accept a weaker
provider-session store elsewhere. Expo Go compatibility is explicitly **not** a production
requirement (`docs/ENGINEERING_STANDARDS.md`/this review): TapJob is already production-oriented,
and Expo's own current guidance is that a development build is the normal path once custom native
code is required — which React Native Firebase is. This checkpoint therefore adds
`expo-dev-client` and the native Firebase/Google/Apple auth packages (§I) and accepts that Expo Go
no longer runs this app; it was already heading that direction for any real native integration.

**Not reconsidered without new facts:** if a future review finds Expo's or React Native Firebase's
own guidance has materially changed, or a worse operational problem appears, this decision should
be revisited explicitly — it is not treated as permanently closed by this ADR.

## I. Mobile technical auth surface (minimal, structural)

WP1.1 ships only the minimal mobile surface needed to prove the auth boundary end to end — not the
full product:

- `apps/mobile/src/auth/` — a thin `AuthService` abstraction wrapping React Native Firebase Auth
  (`onAuthStateChanged`, email sign-up/sign-in, sign-out), with Google
  (`@react-native-google-signin/google-signin`) and Apple (`expo-apple-authentication` bridged
  into `auth().signInWithCredential`) orchestration feeding the same Firebase credential flow —
  never a distinct TapJob-side credential path. No Google/Apple provider access token is sent to or
  persisted by TapJob; the backend only ever receives the resulting Firebase ID token. Android has
  no supported Apple flow and does not attempt one. Every provider flow here is structurally
  implemented (real request shape, real credential hand-off into Firebase); none of it is
  exercised against a real provider network in any test — every mobile test mocks the native auth
  modules instead.
- An `AuthProvider`/context + session observer, a signed-out route, a minimal sign-in screen, and
  the existing shell wrapped behind an auth guard — no Career Profile, Jobs, Resume, Agent, or full
  onboarding UI is built here. `@tapjob/contracts`'s `createApiClient`'s existing `getAccessToken`
  hook (built in WP0.2-A anticipating exactly this) is wired to read the current Firebase ID
  token — no change to the facade itself was needed.
- Session persistence is entirely provider-owned (React Native Firebase's native session store);
  this module never manually writes a Firebase ID/refresh token into `AsyncStorage` itself. The
  one TapJob-owned secret on-device is the device's own `installation_id`, held via
  `expo-secure-store`.

### I1. Operational prerequisites (not part of this PR)

The repository-side CNG (Continuous Native Generation) config IS wired (2026 independent-review
round 1): `apps/mobile/app.json`'s `plugins` lists `@react-native-firebase/app`,
`@react-native-firebase/auth`, `@react-native-google-signin/google-signin` and
`expo-apple-authentication`; `apps/mobile/app.config.ts` sets `ios.usesAppleSignIn` and references
`android.googleServicesFile`/`ios.googleServicesFile` at fixed, gitignored paths
(`apps/mobile/google-services.json`/`GoogleService-Info.plist`). `__tests__/nativeAuthConfig.test.ts`
proves this deterministically by resolving the real config through `expo config --json` (the same
resolution `expo export`/`expo-doctor`/a real `expo prebuild` use) — zero side effects, no real
service file required to exist, no Firebase/Google/Apple network contact. This is a config
*contract* proof, not a real native build: the first real Expo Development Build is still an
operational gate (below), and Expo's/React Native Firebase's own CNG plugin is what will fail
loudly, at real-build time, if the real service file is actually missing then — this ADR does not
claim to have executed or reproduced that failure here (no native tooling in this environment).

Still genuinely not configured by this checkpoint, and required before any of this runs against a
real Firebase project: a real Firebase project/app, enabled Google/Apple/email providers, the iOS
bundle identifier and Android package already derived from `product-config` (`com.tapjob.mobile`),
Google provider OAuth client configuration (including the real `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID`
value `GoogleSignin.configure()` now requires — see §I2), Apple Sign-In capability/provider setup,
the real (gitignored) `google-services.json`/`GoogleService-Info.plist` content, and a server-side
ADC-compatible credential for the backend (§B5). The first real Expo Development Build / EAS build
is also an operational prerequisite, not something this checkpoint can execute (no device, no EAS
credentials, no real Firebase project in this environment).

### I2. `GoogleSignin.configure()` is a real, required runtime step

`firebaseAuthService.ts`'s `signInWithGoogle()` calls `GoogleSignin.configure({ webClientId })`
exactly once per process, before the first `GoogleSignin.signIn()` (2026 independent-review
finding 2 — a real gap the first implementation left open: `signIn()` without a prior `configure()`
either fails or silently returns no ID token on the real SDK). `webClientId` is the Google OAuth
"Web client" ID from the real Firebase project's console — not a secret (it identifies an OAuth
client, not a credential), but environment-specific, so it is typed public config
(`EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` via `src/config/public-config.ts`), never a literal in feature
code. Left unset (the default until a real Firebase project exists for an environment), Google
sign-in fails closed with an explicit, typed error instead of calling an unconfigured SDK.

## Testing implications

- Backend unit (no DB, no real Firebase): `tapjob_api.identity.firebase` adapter (SDK exception
  normalization, claim validation), `tapjob_api.core.rate_limit` (fixed-window policy against a
  fake Redis pipeline), `tapjob_api.identity.schemas` (locale/timezone/device-field validation),
  recent-authentication boundary.
- Backend integration (real PostgreSQL/Redis): the full auth-token matrix (missing/malformed/
  invalid/expired/revoked/disabled/unavailable), bootstrap idempotency and concurrency, the
  never-auto-link-by-email invariant, `/me` get/patch and ownership, devices (register/idempotent
  re-register/revoke/IDOR), consents (append-only/ordering/ownership), deletion (request/duplicate/
  cancel/cancel-after-processing-refused/recent-auth/ownership), sessions (revoke-all success/
  failure/recent-auth/subsequent-request-rejected), and the bootstrap rate limit against real
  Redis.
- Contracts: `packages/contracts/openapi.json` regenerated and re-validated against
  `tapjob_api.http.contract_rules`; the TypeScript client regenerated; the facade
  (`packages/contracts/src/index.ts`) re-exports the new identity types/operations, never a direct
  `./generated/*` import.
- Mobile: native Firebase/Google/Apple auth modules are mocked in every test — no real provider
  network in CI or locally.

## Relationship to other decisions

- Builds directly on the identity invariant already recorded in `docs/FOUNDATION_DECISIONS.md`
  ("Backend stores Firebase UID as external identity, while TapJob owns its internal User UUID")
  and the Firebase Authentication choice itself (same document) — neither is reopened here.
- Reuses, rather than duplicates, two existing WP0.2 foundations: `packages/contracts`'s
  `createApiClient`'s `getAccessToken` hook (built in WP0.2-A anticipating exactly this auth
  boundary, never modified by this ADR) and `tapjob_api.core.logging`'s `request_id_var`
  correlation convention (reused as-is for `account_audit_events.correlation_id`).
- Does not reuse or collide with ADR-0007/ADR-0008 (the two active temporary security exceptions,
  D-047/D-049) — neither is touched, weakened or reinterpreted by this PR.
- Preserves WP0.2-B's deployment contract (ADR-0006) unchanged; this checkpoint performs no real
  deploy, no GHCR publish, and introduces no new deployment-time configuration beyond the
  server-side ADC prerequisite already documented in §B5/§I1 (which `docs/DEPLOYMENT.md` must
  eventually record as an operational prerequisite, not a code change).

## Consequences

- `services/api/src/tapjob_api/identity/` is a new, self-contained package: models, schemas,
  the Firebase adapter, the rate limiter's identity-specific policies, the service/use-case layer,
  FastAPI dependencies, and the router. `tapjob_api.http.errors` gains eight new `ErrorCode`
  members. `tapjob_api.db.session` is a new, generic (not identity-specific) DB-session dependency
  future features reuse. `tapjob_api.core.rate_limit` is likewise generic infrastructure.
- `firebase-admin` is a new backend dependency (and its transitive dependencies, including
  `google-cloud-firestore` — pulled in by the SDK itself; TapJob does not use Firestore and does
  not treat Firebase as a canonical datastore anywhere, per `docs/FOUNDATION_DECISIONS.md`).
- The mobile app moves from Expo-Go-compatible to requiring an Expo Development Build (§H) — a
  real, accepted architectural shift for this checkpoint, not an incidental side effect.
- WP1.1 does not, by itself, make itself VERIFIED — that requires independent review, CI green at
  the latest HEAD, merge, and post-merge CI green, the same pattern every prior checkpoint followed.
  WP1.2 and every downstream work package remain **not authorized** by this ADR.

## Addendum — 2026 independent review, round 1

An independent review of the as-opened PR approved the overall architecture but found 5 blocking
findings plus 2 additional review items, all resolved on the same PR/branch (no new ADR number —
this is a correction/completion pass on the decision already recorded above, not a new decision):

1. **Native auth config wasn't wired** — `app.json`/`app.config.ts` installed the native auth
   packages as JS dependencies but never declared their Expo config plugins or iOS capability.
   Fixed; see §I1 above and `__tests__/nativeAuthConfig.test.ts`.
2. **`GoogleSignin.configure()` was never called** — `signInWithGoogle()` called `signIn()`
   directly. Fixed; see §I2 above.
3. **`cancelAccountDeletion` had no recent-auth/rate-limit guard** — unlike
   `requestAccountDeletion`, the cancel route took no `VerifiedIdentity`/rate-limiter dependency and
   enforced neither, despite being equally security-sensitive. Fixed: it now requires recent
   authentication and shares `DELETION_REQUEST_RATE_LIMIT`'s budget with the request route (same
   resource family, one combined counter, not a second near-duplicate policy).
4. **Mobile bootstrap failure was a silent dead end** — a failed `bootstrapAccount` call reset an
   internal ref to `null` without any state change, so nothing re-triggered the effect and the
   protected shell could still render for a Firebase-signed-in-but-never-TapJob-bootstrapped user.
   Fixed: `AuthContext.tsx` now models bootstrap readiness as its own explicit state
   (`signedInBootstrapping` / `signedInReady` / `signedInBootstrapError`), `_layout.tsx`'s
   `AuthGate` renders a minimal message (with a retry action on error) instead of the protected
   shell for the two non-ready states, and `retryBootstrap()` is the only thing that starts another
   attempt — never an automatic/looping retry.
5. **Revocation/`check_revoked` semantics were documented backwards** — comments in
   `tapjob_api/identity/firebase.py` and `router.py` (and this PR's own body) said an already-issued
   ID token "remains valid... regardless of `check_revoked`" / "regardless of this call". That is
   backwards: *without* `check_revoked`, the token verifies on signature/expiry alone and would
   stay valid; `check_revoked=True` (which this adapter always passes) is exactly what makes
   Firebase Admin additionally check the token's issue time against the account's server-side
   revocation boundary and reject it immediately — `test_a_token_revoked_by_the_provider_is_
   rejected_on_the_next_request` already proved the correct (immediate) behavior; only the prose
   was wrong. Fixed in both docstrings; the runtime (`check_revoked=True`) was never changed.
6. **Extra review: the `ACCOUNT_CREATED` audit event wasn't atomic with the `User` row** —
   `bootstrap_user()` called `session.commit()` itself (both on create and on conflict), before the
   router ever staged the audit event, so a new `User` could durably exist with no audit row if the
   router's own later commit failed. Fixed: `bootstrap_user()` now uses a SAVEPOINT
   (`session.begin_nested()`) for its speculative insert instead of a full commit/rollback, never
   commits itself, and the router commits exactly once, after staging the audit event — preserving
   the same real-concurrent-threads safety guarantee (`test_concurrent_first_bootstraps_for_the_
   same_identity_create_exactly_one_user`) while closing the atomicity gap. A dedicated test
   (`test_bootstrap_commits_the_new_user_and_its_account_created_audit_event_together`) asserts
   exactly one `ACCOUNT_CREATED` row exists after creation, and that a repeat login never adds a
   second one.

## Addendum — 2026 independent review, round 2

A second independent review again approved the overall architecture and found 6 operational-
hardening blockers plus a stale-doc item, resolved on this same PR/branch:

1. **iOS static framework config was missing** — `expo-build-properties` wasn't installed, so
   neither static nor dynamic framework linking was explicitly configured. Reconfirmed directly
   against the current official React Native Firebase docs before editing (not assumed from the
   reviewer's suggestion): on React Native 0.75+ (this project is on 0.86.3), React Native Firebase
   defaults to Swift Package Manager, which the docs state explicitly **requires dynamic
   frameworks** ("do not combine RNFB's default SPM mode with static frameworks") — `"static"` is
   only correct for the opt-in `disableSPM`/CocoaPods path this project does not use. Fixed:
   installed `expo-build-properties` (`~57.0.22`, the SDK-57-compatible version `npx expo install`
   resolved) with `ios.useFrameworks: "dynamic"`. `__tests__/nativeAuthConfig.test.ts` asserts this
   exact value, not `"static"`.
2. **EAS file-environment-variable wiring was undocumented/unreal** — `app.config.ts` referenced
   fixed local paths with no actual mechanism for a real EAS Build to supply the real files.
   Reconfirmed against Expo's own EAS environment-variables docs (docs.expo.dev/eas/environment-
   variables/manage, "Secrets and file variables"), which show the EXACT pattern now used:
   `android.googleServicesFile = process.env.GOOGLE_SERVICES_JSON ?? '<local fallback>'`. Fixed:
   `app.config.ts` reads `GOOGLE_SERVICES_JSON` (Android) and `GOOGLE_SERVICE_INFO_PLIST` (iOS, the
   same naming convention applied to the second file, documented as this project's choice) --
   build-time-only variables, never `EXPO_PUBLIC_*`/`publicConfig`, since these configure app-config
   resolution, not the client bundle. Falls back to the local gitignored paths when unset. 3 new
   tests prove: no-env-var fallback, an env-var path used verbatim, and no file content/credential
   ever appearing in the resolved config.
3. **Google Auth API reconsideration ("Original" vs "Universal")** — investigated per the
   mandate's own instruction to confirm official docs before migrating, rather than copying a
   suggested name blindly. Finding: `@react-native-google-signin/google-signin`'s own installed
   README (v16.1.5, read directly from the package, not summarized) states in its own words that
   the free, installed package is "the free (public) version... built on the deprecated legacy
   Google Sign-In SDK on Android," and that **"Universal Sign In is the premium version of this
   package"** (separate paid product, "Get a license | See what's included"), not a free upgrade
   within the same package. `GoogleOneTapSignIn` appears only in example JSDoc comments inside the
   free package's source, with no actual export backing it in this installed version.
   **Decision: do not migrate.** Purchasing a paid, separately-licensed package is a procurement
   decision outside engineering authority in this session, not a code change this review round can
   make unilaterally — and the mandate's own instruction was conditional ("se a documentação
   oficial atual continuar igual, migre"; investigation shows the premise does not hold for a free
   migration path). A free, actively-maintained alternative exists in the wider ecosystem
   (`react-native-nitro-google-signin`, using Android Credential Manager) but swapping the entire
   Google auth package is a materially larger architecture change than this hardening round's
   scope ("não redesenhe WP1.1") — named here explicitly as a future consideration, not silently
   worked around. `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` (round 1, §I2) remains necessary and correct
   for the API actually in use: the free "Original" surface has no documented free auto-detection
   from the native Firebase config.
4. **Invalid/revoked mobile session handling was missing** — the mobile client attached a bearer
   token but had no reaction to `AUTH_TOKEN_REVOKED`/`AUTH_TOKEN_INVALID`, and `REAUTHENTICATION_
   REQUIRED` (also HTTP 401) risked being conflated with those. Investigated the generated Hey API
   client's actual interceptor architecture (`client.interceptors.response.use`,
   `ResolvedRequestOptions.serializedBody`) before implementing, rather than inventing a parallel
   wrapper. Fixed in `@tapjob/contracts`'s `createApiClient` (`packages/contracts/src/index.ts`):
   branches on the canonical `ErrorResponse.code`, never on "any 401 means logout" --
   `AUTH_TOKEN_REVOKED` calls the new `onSessionInvalid` hook immediately (no retry, terminal);
   `AUTH_TOKEN_INVALID` triggers exactly one bounded forced-token-refresh retry (`getAccessToken({
   forceRefresh: true })`, which `apps/mobile` wires to `AuthService.getIdToken`'s new
   `forceRefresh` option, itself forwarded to the real `getIdToken(user, forceRefresh)` SDK call) --
   structurally bounded to at most 2 fetches total since the retry's own `fetch()` call never
   re-enters the interceptor chain. **Round 3 correction:** the retry only invalidates the global
   session when ITS OWN canonical `ErrorResponse.code` is `AUTH_TOKEN_INVALID` or
   `AUTH_TOKEN_REVOKED` -- never merely "the retry's status is still 401" (an earlier version of
   this logic conflated the two: a security-sensitive endpoint can legitimately answer
   `REAUTHENTICATION_REQUIRED` to a retry carrying a now-valid refreshed token, which must never be
   misreported as a dead session). A forced refresh that itself throws (a real provider/network
   error, not merely "no token") is handled the same as "no refreshed token available": terminal,
   with the caller still seeing the ORIGINAL canonical `AUTH_TOKEN_INVALID` response, never the
   provider's own error shape. `REAUTHENTICATION_REQUIRED` and `AUTH_TOKEN_MISSING` never call
   `onSessionInvalid` at all, at either the first response or the retry (the former is a
   per-operation condition a future screen handles itself via `isApiError`/`.code`; the latter
   means there was never a session to invalidate). `apps/mobile/src/auth/apiClient.ts` wires `onSessionInvalid` to the
   existing `AuthService.signOut()` rather than inventing a parallel "force signed-out" mechanism:
   signing out already updates `auth.currentUser`/fires `onAuthStateChanged`, which `AuthContext`
   already observes, and `AuthGate` (round 1) already redirects a `signedOut` state to `/sign-in` --
   no new state machine needed. 7 new tests in `contracts-smoke.test.ts` plus 2 in
   `apiClient.test.ts` cover the full matrix (revoked, invalid-then-success, invalid-then-still-
   failing, invalid-with-no-refreshed-token, reauth-required-never-triggers-it, missing-never-
   triggers-it, token-never-in-a-thrown-error).
5. **The signed-out `AuthGate` had a render gap** — on the SAME render that scheduled a redirect
   (`signedOut` while not on `/sign-in`, or the reverse `signedInReady` while still on `/sign-in`),
   the function still fell through to `return children`, so the protected `Stack` (or the sign-in
   screen) could be mounted for one frame before the redirect effect actually navigated away. Fixed:
   both conditions are computed once (`redirectingToSignIn`/`redirectingHome`) and gate the render
   (`return null`) exactly like the existing `loading` case, symmetrically for both directions. Also
   added a sign-out action alongside retry in the `signedInBootstrapError` state (`_layout.tsx`): a
   user must never be stuck permanently in a Firebase session that keeps failing to bootstrap.
6. **`auth_time`/deletion-cancel hardening (two related findings):**
   - **6a.** `is_reauthentication_recent` computed `current - auth_time` with no lower bound, so a
     FUTURE `auth_time` produced a negative age that satisfied `<= REAUTH_MAX_AGE_SECONDS` and
     wrongly passed as "recent." Fixed with an explicit, named, tested tolerance
     (`MAX_CLOCK_SKEW_SECONDS = 60`): an `auth_time` more than 60 seconds in the future is now
     rejected, never silently accepted. 3 new tests cover the exact boundary, one second past it,
     and a far-future timestamp.
   - **6b.** `cancel_deletion_request` read the row (`SELECT ... WHERE status = REQUESTED`) and then
     wrote it in a separate statement — a future deletion orchestrator transitioning the same row to
     `PROCESSING` between that read and write could have its transition silently overwritten back to
     `CANCELLED`. Fixed with a single atomic conditional `UPDATE ... WHERE user_id = ... AND status
     = REQUESTED ... RETURNING` (SQLAlchemy 2.0's ORM-enabled `UPDATE`/`RETURNING`): the `WHERE`
     clause itself is the concurrency guarantee, matching (and updating) a row only if it is STILL
     `REQUESTED` at the instant the `UPDATE` executes. No deletion orchestrator exists yet (out of
     scope for this WP); a new real-PostgreSQL integration test moves a row to `PROCESSING` directly
     via SQL to stand in for that future transition, and proves the atomic `UPDATE` refuses to touch
     it, leaving it exactly as `PROCESSING`.

**Stale documentation fixed:** `packages/contracts/src/index.ts`'s `getAccessToken` doc comment
still said "No endpoint requires one yet (docs/IMPLEMENTATION_PLAN.md WP0.2)" — stale since WP1.1
added seven authenticated endpoints in round 1 itself. Corrected in the same edit that added the
round-2 interceptor logic above.

## Addendum — 2026 independent review, round 3

A third independent review approved rounds 1-2's fixes and found one concentrated blocker, with
two distinct bugs, in round 2's new session-retry interceptor (`packages/contracts/src/index.ts`),
both resolved on this same PR/branch:

1. **A throwing forced refresh never invalidated the session.** `await getAccessToken({
   forceRefresh: true })` had no `try`/`catch`; if the provider call itself rejected (a real
   network/Firebase error, distinct from "resolved with `null`"), the exception propagated out of
   the response interceptor into the generated client's own error path, which never calls
   `onSessionInvalid` and surfaces the provider's raw error object to the caller instead of the
   canonical `ErrorResponse` -- violating "a refresh failure must end in a terminal/reauth state."
   Fixed: the call is now wrapped in `try`/`catch`; a throw is treated identically to "no refreshed
   token available" (`onSessionInvalid("AUTH_TOKEN_INVALID")`, then the ORIGINAL response is
   returned to the caller unchanged, never the provider's own error shape, which could otherwise
   leak unsafe detail).
2. **Any still-401 retry was reported as `AUTH_TOKEN_INVALID`, regardless of its own code.** The
   retry's status alone (`retried.status === 401`) decided the classification, so a retry that
   legitimately came back `REAUTHENTICATION_REQUIRED` (e.g. a security-sensitive endpoint behind
   the NOW-VALID refreshed token correctly asking for recent authentication) or `AUTH_TOKEN_REVOKED`
   was misreported, the former wrongly ending the global session, the latter wrongly classified as
   merely "invalid." **Corrected rule, the only one that was ever intended:** a retry invalidates
   the global session ONLY when its OWN canonical `ErrorResponse.code` is `AUTH_TOKEN_INVALID` or
   `AUTH_TOKEN_REVOKED` -- never "the retry is still a 401." `REAUTHENTICATION_REQUIRED`,
   `AUTH_TOKEN_MISSING`, and any unrecognized/future 401 code reaching the retry are left alone and
   delivered to the caller exactly as received, never silently reclassified. The retry bound itself
   (at most 2 fetches total; the retry's own `fetch()` never re-enters this interceptor chain) was
   already correct and is unchanged.

5 new tests in `contracts-smoke.test.ts` cover: a throwing refresh (terminal, no second fetch,
original `ErrorResponse` preserved), a retry resolving to `AUTH_TOKEN_REVOKED`, a retry resolving
to `REAUTHENTICATION_REQUIRED` (never terminal), a retry resolving to `AUTH_TOKEN_MISSING` (never
terminal), and a retry resolving to an unrecognized code (never silently reclassified) -- 16 tests
total in that file, all passing against the real fetch/Request/Response globals this Jest
environment provides (no mocked DOM layer).

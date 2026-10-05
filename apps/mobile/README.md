# apps/mobile

**Status:** Minimal Expo shell (WP0.1-B, see `docs/adr/0002-mobile-foundation.md`) plus a minimal Firebase auth technical surface (WP1.1, see `docs/adr/0009-identity-auth-user-account.md`). No full product screens yet; the full customer mobile experience follows the WhatsApp-first stage (`docs/DELIVERY_ORDER.md`).

This shell exists only to prove that the following work together: app boot, Expo Router, `@tapjob/design-tokens`, `@tapjob/product-config`, i18n, the typed public config, and (as of WP1.1) the Firebase auth boundary.

## Stack

- Expo SDK 57, React Native 0.86, React 19.2 (New Architecture).
- Expo Router (`src/app`: `/`, `/about`, `/sign-in`).
- TypeScript `~6.0.3` (workspace-wide; see `docs/adr/0002-mobile-foundation.md` for why).
- `i18next` + `react-i18next` + `expo-localization` (`pt-BR` only today; architecture ready for `es`/`en`). The default locale comes from `@tapjob/product-config`'s `defaultLocale`, not a second hardcoded value (`src/i18n/locales.ts`).
- `expo-font` + `@expo-google-fonts/inter` (three weights only; see Typography below).
- `@sentry/react-native` (init-only; see Observability below).
- **Auth (WP1.1):** React Native Firebase (`@react-native-firebase/app`/`auth`) + `@react-native-google-signin/google-signin` + `expo-apple-authentication`, with `expo-dev-client`. This requires an **Expo Development Build** -- Expo Go no longer runs this app, by deliberate decision (`docs/adr/0009-identity-auth-user-account.md` §H: Firebase JS SDK's Expo-Go-compatible persistence stores raw tokens in unencrypted AsyncStorage, which this project rejects). See `src/auth/` and the ADR's §I1 for the operational prerequisites (real Firebase project, provider configuration, `google-services.json`/`GoogleService-Info.plist`) a real build still needs -- none of that is configured by this repository.

## Setup

```bash
cp apps/mobile/.env.example apps/mobile/.env   # PowerShell: Copy-Item apps/mobile/.env.example apps/mobile/.env
npm run start --workspace @tapjob/mobile
```

`npm install`/`npm ci` at the repository root installs this workspace too (one npm workspace, one lockfile).

## Configuration

`src/config/public-config.ts` is the **only** module allowed to read `process.env`. It validates fail-fast at import time:

| Variable | Required | Notes |
|---|---|---|
| `EXPO_PUBLIC_APP_ENV` | yes | `local` \| `development` \| `staging` \| `production` |
| `EXPO_PUBLIC_API_BASE_URL` | yes | absolute `http(s)://` URL |
| `EXPO_PUBLIC_SENTRY_DSN` | no | Sentry stays disabled when unset |
| `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` | no | Google OAuth "Web client" ID (not a secret, but environment-specific); unset until a real Firebase project exists -- Google sign-in then fails closed with an explicit error instead of calling an unconfigured SDK |

Every `EXPO_PUBLIC_*` value is bundled into the client and is public by convention (docs/ENGINEERING_STANDARDS.md §3.3): never put a secret here.

### Native auth configuration (WP1.1)

`app.json`'s `plugins` and `app.config.ts`'s `ios`/`android` blocks wire the Expo config plugins
React Native Firebase, Google Sign-In and Sign in with Apple each need (`@react-native-firebase/
app`/`auth`, `@react-native-google-signin/google-signin`, `expo-apple-authentication`, plus
`ios.usesAppleSignIn`), plus `expo-build-properties` with `ios.useFrameworks: "dynamic"` --
required because React Native Firebase defaults to Swift Package Manager on RN 0.75+ (we are on
0.86.3), and RNFirebase's own docs state SPM requires dynamic frameworks (confirmed directly
against the official docs, not assumed; `"static"` only applies to the opt-in `disableSPM`/
CocoaPods path this project does not use). `__tests__/nativeAuthConfig.test.ts` proves all of this
deterministically via Expo's own config resolution (`expo config --json`) -- no real native build
runs in this test; it validates the **config contract**, not a built app.

The real native Firebase service files are **never committed** (see `.gitignore` for
`apps/mobile/google-services.json`/`GoogleService-Info.plist`). `app.config.ts` reads their paths
from two build-time-only environment variables, `GOOGLE_SERVICES_JSON` (Android) and
`GOOGLE_SERVICE_INFO_PLIST` (iOS) -- deliberately **not** `EXPO_PUBLIC_*`/`publicConfig`, since
these are app-config-resolution inputs, never bundled into the client. This is the same pattern
Expo's own EAS environment-variables docs show for this exact file
(docs.expo.dev/eas/environment-variables/manage: a "file" type environment variable is uploaded
once to EAS, then exposed to a build job as a real path on the build runner, which the named
variable points to). A real EAS Build supplies these two variables; with neither set (local dev,
CI, this test suite), `app.config.ts` falls back to the fixed local paths above. See
`docs/adr/0009-identity-auth-user-account.md` §I1/§I2 for the full operational-prerequisites list,
including the `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` value above (which IS public/bundled, unlike
these two).

## Typography / fonts

`packages/design-tokens` names the brand typeface (`typography.fontFamily.primary = "Inter"`) and the loadable weights the shell uses (`primaryRegular`/`primarySemibold`/`primaryBold` → `Inter_400Regular`/`Inter_600SemiBold`/`Inter_700Bold`). `src/theme/fonts.ts` maps each loadable token to its `@expo-google-fonts/inter` asset (imported from that package's per-weight subpaths, e.g. `@expo-google-fonts/inter/700Bold`, so only those three `.ttf` files are bundled — not all 18 weights in the package). `src/app/_layout.tsx` loads them with `useFonts` and keeps the splash screen up (`expo-splash-screen`) until loading settles; a load error still renders the app, falling back to the system font, instead of hanging. Screens reference only the `fontFamily` tokens, never a literal font name.

Expo SDK 57 doesn't support combining one family name with the `fontWeight` style prop for a *loaded custom* font (SDK 58+ does), which is why each weight has its own token/family name here instead of one `"Inter"` family plus `fontWeight`.

## Observability

`src/observability/sentry.ts` calls `Sentry.init()` only when `EXPO_PUBLIC_SENTRY_DSN` is set; CI and local development never set it, so no event is ever sent from those runs.

**Deferred to a later checkpoint** (not implemented here):
- the `@sentry/react-native/expo` config plugin (it was added to `app.json` automatically by `expo install` and then removed, because it needs an org/project configured before it does anything useful);
- Metro source-map upload for crash symbolication;
- EAS Build/Update release wiring.

`SENTRY_AUTH_TOKEN` (needed for that later work) is a build-time-only secret and must never be committed or embedded in the app.

## Commands

- `npm run start --workspace @tapjob/mobile` (or `cd apps/mobile && npm start`): Expo dev server.
- `npm run lint` / `npm run format:check` (repo root): ESLint (`eslint-config-expo` + magic-value guards) and Prettier cover this workspace.
- `npm run typecheck --workspace @tapjob/mobile`: `tsc --noEmit`.
- `npm run test --workspace @tapjob/mobile`: Jest (`jest-expo` preset). Screen-rendering tests live in `__tests__/`, not under `src/app/`, because Expo Router treats every file in `src/app/` as a route or layout.
- `npx expo-doctor` (from `apps/mobile`): dependency/config diagnostics, as run in CI.
- `npx expo export --platform android --platform ios` (from `apps/mobile`): proves the shell actually bundles, as run in CI.

## Not implemented here

Per the WP0.1-B scope: no Home/feed/resume/login/jobs/agent/WhatsApp/tabs/tracker/Application Kit screens, no Firebase Auth, and no `es`/`en` translations (the locale architecture supports adding them later without touching consuming code).

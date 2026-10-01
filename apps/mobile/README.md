# apps/mobile

**Status:** Minimal Expo shell (WP0.1-B, see `docs/adr/0002-mobile-foundation.md`). No product screens yet; the full customer mobile experience follows the WhatsApp-first stage (`docs/DELIVERY_ORDER.md`).

This shell exists only to prove that the following work together: app boot, Expo Router, `@tapjob/design-tokens`, `@tapjob/product-config`, i18n and the typed public config.

## Stack

- Expo SDK 57, React Native 0.86, React 19.2 (New Architecture).
- Expo Router (`src/app`, two routes: `/` and `/about`).
- TypeScript `~6.0.3` (workspace-wide; see `docs/adr/0002-mobile-foundation.md` for why).
- `i18next` + `react-i18next` + `expo-localization` (`pt-BR` only today; architecture ready for `es`/`en`). The default locale comes from `@tapjob/product-config`'s `defaultLocale`, not a second hardcoded value (`src/i18n/locales.ts`).
- `expo-font` + `@expo-google-fonts/inter` (three weights only; see Typography below).
- `@sentry/react-native` (init-only; see Observability below).

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

Every `EXPO_PUBLIC_*` value is bundled into the client and is public by convention (docs/ENGINEERING_STANDARDS.md §3.3): never put a secret here.

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

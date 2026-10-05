/**
 * Typed, validated public configuration (docs/ENGINEERING_STANDARDS.md §3.2, §5).
 *
 * This is the ONLY module allowed to read `process.env` in the mobile app. Every
 * `EXPO_PUBLIC_*` variable is bundled into the client and must be treated as public: never read
 * a secret through this module (docs/ENGINEERING_STANDARDS.md §3.3, AGENTS.md §4).
 *
 * Validation is fail-fast: a missing or malformed required value throws when this module is
 * first imported, instead of surfacing as an obscure failure later.
 */

export type AppEnvironment = "local" | "development" | "staging" | "production";

const APP_ENVIRONMENTS: readonly AppEnvironment[] = [
  "local",
  "development",
  "staging",
  "production",
];

export interface PublicConfig {
  readonly appEnv: AppEnvironment;
  readonly apiBaseUrl: string;
  readonly sentryDsn: string | undefined;
  /** The Google OAuth "Web client" ID (Firebase/Google Cloud console) used by
   * `GoogleSignin.configure()` to obtain an ID token Firebase can verify -- NOT a secret (it
   * identifies the OAuth client, not a credential), but environment-specific (a different real
   * Firebase project per app-env), so it is config, never a literal in feature code
   * (docs/adr/0009-identity-auth-user-account.md §I, 2026 review finding 2). `undefined` until a
   * real Firebase project is configured for this environment -- `signInWithGoogle()` then fails
   * closed with a clear error instead of silently calling an unconfigured SDK. */
  readonly googleWebClientId: string | undefined;
}

function isAppEnvironment(value: string): value is AppEnvironment {
  return (APP_ENVIRONMENTS as readonly string[]).includes(value);
}

function readRequired(name: string, value: string | undefined): string {
  if (!value || value.trim().length === 0) {
    throw new Error(
      `Missing required public config value: ${name}. Set it in apps/mobile/.env (see apps/mobile/.env.example).`,
    );
  }
  return value;
}

function parseAppEnv(value: string): AppEnvironment {
  if (!isAppEnvironment(value)) {
    throw new Error(
      `Invalid EXPO_PUBLIC_APP_ENV "${value}". Expected one of: ${APP_ENVIRONMENTS.join(", ")}.`,
    );
  }
  return value;
}

function parseApiBaseUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`Invalid EXPO_PUBLIC_API_BASE_URL "${value}": must be an absolute URL.`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`Invalid EXPO_PUBLIC_API_BASE_URL "${value}": must use http or https.`);
  }
  return value;
}

function parseOptionalString(value: string | undefined): string | undefined {
  return value && value.trim().length > 0 ? value : undefined;
}

function loadPublicConfig(): PublicConfig {
  return Object.freeze({
    appEnv: parseAppEnv(readRequired("EXPO_PUBLIC_APP_ENV", process.env.EXPO_PUBLIC_APP_ENV)),
    apiBaseUrl: parseApiBaseUrl(
      readRequired("EXPO_PUBLIC_API_BASE_URL", process.env.EXPO_PUBLIC_API_BASE_URL),
    ),
    sentryDsn: parseOptionalString(process.env.EXPO_PUBLIC_SENTRY_DSN),
    googleWebClientId: parseOptionalString(process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID),
  });
}

export const publicConfig: PublicConfig = loadPublicConfig();

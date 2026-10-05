/**
 * The module under test reads `process.env` at import time, so every test case resets the
 * module registry and the environment before requiring it fresh. A synchronous `require()` is
 * used instead of dynamic `import()` because jest-expo transpiles to CommonJS, where a fresh
 * per-call dynamic import isn't supported without `--experimental-vm-modules`.
 */

import type { PublicConfig } from "./public-config";

const ORIGINAL_ENV = { ...process.env };
const VALID_ENV = {
  EXPO_PUBLIC_APP_ENV: "local",
  EXPO_PUBLIC_API_BASE_URL: "http://localhost:8000",
};

function loadPublicConfig(env: Record<string, string | undefined>): PublicConfig {
  jest.resetModules();
  process.env = { ...ORIGINAL_ENV };
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- fresh module instance per test case
  return (require("./public-config") as typeof import("./public-config")).publicConfig;
}

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe("publicConfig", () => {
  test("loads and freezes a valid configuration", () => {
    const publicConfig = loadPublicConfig(VALID_ENV);

    expect(publicConfig.appEnv).toBe("local");
    expect(publicConfig.apiBaseUrl).toBe("http://localhost:8000");
    expect(publicConfig.sentryDsn).toBeUndefined();
    expect(Object.isFrozen(publicConfig)).toBe(true);
  });

  test("exposes an optional Sentry DSN when configured", () => {
    const publicConfig = loadPublicConfig({
      ...VALID_ENV,
      EXPO_PUBLIC_SENTRY_DSN: "https://example@sentry.local/1",
    });

    expect(publicConfig.sentryDsn).toBe("https://example@sentry.local/1");
  });

  test("treats a blank optional Sentry DSN as absent", () => {
    const publicConfig = loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_SENTRY_DSN: "   " });

    expect(publicConfig.sentryDsn).toBeUndefined();
  });

  test("exposes no Google web client ID by default", () => {
    const publicConfig = loadPublicConfig(VALID_ENV);

    expect(publicConfig.googleWebClientId).toBeUndefined();
  });

  test("exposes an optional Google web client ID when configured", () => {
    const publicConfig = loadPublicConfig({
      ...VALID_ENV,
      EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: "123-abc.apps.googleusercontent.com",
    });

    expect(publicConfig.googleWebClientId).toBe("123-abc.apps.googleusercontent.com");
  });

  test("treats a blank optional Google web client ID as absent", () => {
    const publicConfig = loadPublicConfig({
      ...VALID_ENV,
      EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: "   ",
    });

    expect(publicConfig.googleWebClientId).toBeUndefined();
  });

  test("throws when EXPO_PUBLIC_APP_ENV is missing", () => {
    expect(() => loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_APP_ENV: undefined })).toThrow(
      /EXPO_PUBLIC_APP_ENV/,
    );
  });

  test("throws when EXPO_PUBLIC_APP_ENV is not a recognized environment", () => {
    expect(() => loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_APP_ENV: "production-ish" })).toThrow(
      /Invalid EXPO_PUBLIC_APP_ENV/,
    );
  });

  test("throws when EXPO_PUBLIC_API_BASE_URL is missing", () => {
    expect(() => loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_API_BASE_URL: undefined })).toThrow(
      /EXPO_PUBLIC_API_BASE_URL/,
    );
  });

  test("throws when EXPO_PUBLIC_API_BASE_URL is not an absolute URL", () => {
    expect(() => loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_API_BASE_URL: "not-a-url" })).toThrow(
      /must be an absolute URL/,
    );
  });

  test("throws when EXPO_PUBLIC_API_BASE_URL uses an unsupported protocol", () => {
    expect(() =>
      loadPublicConfig({ ...VALID_ENV, EXPO_PUBLIC_API_BASE_URL: "ftp://files.example.com" }),
    ).toThrow(/must use http or https/);
  });
});

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { productConfig } from "@tapjob/product-config";

/**
 * Deterministic proof that the native auth modules added for WP1.1 (React Native Firebase, Google
 * Sign-In, Sign in with Apple) are actually WIRED into Expo's Continuous Native Generation (CNG)
 * config, not just installed as JS dependencies (2026 independent-review round 1 finding 1:
 * `expo export`/`expo-doctor` alone never prove this, since neither reads the native config
 * plugins at all). Round 2 extends this to the iOS static/dynamic-framework linking mode (finding
 * 1) and the EAS file-environment-variable wiring for the real Firebase service files (finding 2).
 *
 * This resolves the real app.config.ts/app.json through Expo's own config resolution
 * (`expo config --json` -- the same module `expo export`, `expo-doctor`, and a real
 * `expo prebuild` all read from) with zero side effects: no native project is generated, no real
 * (gitignored) Firebase service file is required to exist, and no Firebase/Google/Apple service is
 * ever contacted. **This proves the CNG config CONTRACT, not a real native build** -- the first
 * real Expo Development Build remains an operational gate (docs/adr/0009-identity-auth-user-
 * account.md §I1).
 */
function resolveExpoConfig(
  envOverrides: Record<string, string | undefined> = {},
): Record<string, unknown> {
  const mobileRoot = resolve(__dirname, "..");
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const [key, value] of Object.entries(envOverrides)) {
    if (value === undefined) {
      delete env[key];
    } else {
      env[key] = value;
    }
  }
  // A fixed literal command string (never built from external/untrusted input, so there is no
  // injection surface) -- `execSync` runs it through a shell, which is what makes `npx` resolve to
  // `npx.cmd` on Windows without a platform-specific binary name or Node's shell-escaping
  // deprecation warning (triggered only by combining an args array with `shell: true`).
  const output = execSync("npx expo config --json", {
    cwd: mobileRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env,
  });
  return JSON.parse(output) as Record<string, unknown>;
}

describe("native auth config (Expo CNG)", () => {
  let config: Record<string, unknown>;

  beforeAll(() => {
    config = resolveExpoConfig();
  }, 60_000);

  test("React Native Firebase app + auth plugins are configured", () => {
    expect(config.plugins).toEqual(
      expect.arrayContaining(["@react-native-firebase/app", "@react-native-firebase/auth"]),
    );
  });

  test("the Google Sign-In plugin is configured", () => {
    expect(config.plugins).toEqual(
      expect.arrayContaining(["@react-native-google-signin/google-signin"]),
    );
  });

  test("the Apple Authentication plugin and iOS capability are both configured", () => {
    expect(config.plugins).toEqual(expect.arrayContaining(["expo-apple-authentication"]));
    expect((config.ios as Record<string, unknown>).usesAppleSignIn).toBe(true);
  });

  test("expo-dev-client is configured (React Native Firebase requires a Development Build, never Expo Go)", () => {
    expect(config.plugins).toEqual(expect.arrayContaining(["expo-dev-client"]));
  });

  test("expo-build-properties configures iOS with dynamic frameworks (2026 review round 2 finding 1)", () => {
    // NOT "static": React Native Firebase defaults to Swift Package Manager on RN 0.75+ (we are on
    // 0.86.3), and RNFirebase's own docs state SPM requires dynamic frameworks ("do not combine
    // RNFB's default SPM mode with static frameworks") -- confirmed directly against the official
    // docs before writing this, not assumed. Static is only correct for the opt-in
    // `disableSPM`/CocoaPods path this project does not use.
    const plugins = config.plugins as unknown[];
    const buildPropertiesEntry = plugins.find(
      (entry) => Array.isArray(entry) && entry[0] === "expo-build-properties",
    ) as [string, { ios?: { useFrameworks?: string } }] | undefined;
    expect(buildPropertiesEntry).toBeDefined();
    expect(buildPropertiesEntry?.[1].ios?.useFrameworks).toBe("dynamic");
  });

  test("bundle identifier and package name still derive from the canonical product config, not a literal", () => {
    expect(config.ios).toMatchObject({
      bundleIdentifier: `com.${productConfig.deepLinkScheme}.mobile`,
    });
    expect(config.android).toMatchObject({
      package: `com.${productConfig.deepLinkScheme}.mobile`,
    });
  });

  test("no secret-shaped value appears anywhere in the resolved config", () => {
    const serialized = JSON.stringify(config);
    expect(serialized).not.toMatch(/AIza[0-9A-Za-z_-]{20,}/); // Firebase/Google API key shape
    expect(serialized).not.toMatch(/-----BEGIN (RSA )?PRIVATE KEY-----/);
  });
});

describe("native Firebase service file paths (2026 review round 2 finding 2)", () => {
  test("without an EAS file environment variable, resolution falls back to the local gitignored path", () => {
    const config = resolveExpoConfig({
      GOOGLE_SERVICES_JSON: undefined,
      GOOGLE_SERVICE_INFO_PLIST: undefined,
    });
    const ios = config.ios as Record<string, unknown>;
    const android = config.android as Record<string, unknown>;

    expect(android.googleServicesFile).toBe("./google-services.json");
    expect(ios.googleServicesFile).toBe("./GoogleService-Info.plist");
  });

  test("an EAS file environment variable's path is used verbatim when set", () => {
    // Synthetic paths standing in for what EAS Build actually provides at build time (per
    // docs.expo.dev/eas/environment-variables/manage: "an environment variable such as
    // GOOGLE_SERVICES_JSON ... made available to jobs as file paths on the build runner") -- no
    // real file is created or read here; `expo config` only echoes the path string back.
    const config = resolveExpoConfig({
      GOOGLE_SERVICES_JSON: "/synthetic/eas/path/google-services.json",
      GOOGLE_SERVICE_INFO_PLIST: "/synthetic/eas/path/GoogleService-Info.plist",
    });
    const ios = config.ios as Record<string, unknown>;
    const android = config.android as Record<string, unknown>;

    expect(android.googleServicesFile).toContain("google-services.json");
    expect(android.googleServicesFile).toContain("synthetic");
    expect(ios.googleServicesFile).toContain("GoogleService-Info.plist");
    expect(ios.googleServicesFile).toContain("synthetic");
  });

  test("resolution never embeds file content or a credential, only a path string", () => {
    const config = resolveExpoConfig({
      GOOGLE_SERVICES_JSON: "/synthetic/eas/path/google-services.json",
      GOOGLE_SERVICE_INFO_PLIST: "/synthetic/eas/path/GoogleService-Info.plist",
    });
    const ios = config.ios as Record<string, unknown>;
    const android = config.android as Record<string, unknown>;

    expect(typeof android.googleServicesFile).toBe("string");
    expect(typeof ios.googleServicesFile).toBe("string");
    const serialized = JSON.stringify(config);
    expect(serialized).not.toMatch(/"client_id"|"private_key"|"api_key"/);
  });

  test("the real service file paths (both fallback names) are gitignored", () => {
    // Checked against .gitignore itself rather than "does the file exist on disk", since a
    // developer may have a real copy locally for an actual Development Build without that being a
    // repository problem.
    const gitignore = readFileSync(resolve(__dirname, "../../..", ".gitignore"), "utf8");
    expect(gitignore).toMatch(/apps\/mobile\/google-services\.json/);
    expect(gitignore).toMatch(/apps\/mobile\/GoogleService-Info\.plist/);
  });
});

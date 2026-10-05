import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ConfigContext, ExpoConfig } from "expo/config";

// Expo CLI loads app.config.ts through Node's native ESM loader, which (unlike Metro/tsc/Jest)
// requires an import attribute for JSON that @tapjob/product-config's package doesn't declare.
// Reading the canonical file directly here avoids a build-tool-only workaround inside the shared
// package; product.json (docs/ENGINEERING_STANDARDS.md §3.1) remains the single source of truth.
interface ProductConfig {
  readonly productName: string;
  readonly shortName: string;
  readonly deepLinkScheme: string;
}

const PRODUCT_CONFIG_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../packages/product-config/product.json",
);
const productConfig = JSON.parse(readFileSync(PRODUCT_CONFIG_PATH, "utf8")) as ProductConfig;

// Real native Firebase service file paths (docs/adr/0009-identity-auth-user-account.md §I1/§I2:
// "operational prerequisites"). Neither file is ever committed -- see .gitignore. A real EAS Build
// supplies them via build-time "file" environment variables (`GOOGLE_SERVICES_JSON`/
// `GOOGLE_SERVICE_INFO_PLIST`, the exact pattern Expo's own EAS environment-variables docs use for
// this same file, docs.expo.dev/eas/environment-variables/manage): EAS writes the uploaded file to
// a path on the build runner and exposes that path through the named variable, which this file
// reads directly (NOT via `publicConfig`/`EXPO_PUBLIC_*` -- this is build-time-only app config
// resolution, never bundled into the client, 2026 independent-review round 2 finding 2). With
// neither variable set (local dev, CI, this test suite), it falls back to the fixed, gitignored
// local paths below -- safe with no file present either way: CNG only reads this value during a
// real `expo prebuild`/native build, never during `expo export`/`expo-doctor`/this module itself,
// which never touches the filesystem at these paths.
const ANDROID_GOOGLE_SERVICES_FILE = process.env.GOOGLE_SERVICES_JSON ?? "./google-services.json";
const IOS_GOOGLE_SERVICES_FILE =
  process.env.GOOGLE_SERVICE_INFO_PLIST ?? "./GoogleService-Info.plist";

// Dynamic config layered over app.json (docs/ENGINEERING_STANDARDS.md §3.1, §6 of
// docs/DESIGN_SYSTEM.md): app identity/scheme/bundle ids derive from the single canonical
// product-config file instead of being duplicated here.
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: productConfig.productName,
  slug: `${productConfig.deepLinkScheme}-mobile`,
  scheme: productConfig.deepLinkScheme,
  version: "0.1.0",
  orientation: "portrait",
  userInterfaceStyle: "automatic",
  ios: {
    ...config.ios,
    supportsTablet: true,
    bundleIdentifier: `com.${productConfig.deepLinkScheme}.mobile`,
    // Required for expo-apple-authentication's "Sign In with Apple" capability
    // (docs.expo.dev/versions/latest/sdk/apple-authentication: "Setup iOS project").
    usesAppleSignIn: true,
    googleServicesFile: IOS_GOOGLE_SERVICES_FILE,
  },
  android: {
    ...config.android,
    package: `com.${productConfig.deepLinkScheme}.mobile`,
    googleServicesFile: ANDROID_GOOGLE_SERVICES_FILE,
  },
  experiments: {
    ...config.experiments,
    typedRoutes: true,
  },
});

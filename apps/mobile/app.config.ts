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
  },
  android: {
    ...config.android,
    package: `com.${productConfig.deepLinkScheme}.mobile`,
  },
  experiments: {
    ...config.experiments,
    typedRoutes: true,
  },
});

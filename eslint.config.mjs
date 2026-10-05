// Flat ESLint config for the whole JS/TS workspace (docs/ENGINEERING_STANDARDS.md §29).
//
// - packages/**: hand-written Node/TypeScript tooling and library code gets core JS rules plus
//   non-type-checked TypeScript rules (type correctness itself is `tsc --noEmit`'s job).
// - apps/mobile/**: the Expo app extends the official `eslint-config-expo` flat preset.
// - Both layers share a small set of magic-value guards that encode
//   docs/ENGINEERING_STANDARDS.md §2/§3/§5 and docs/DESIGN_SYSTEM.md §10: no raw hex/rgb colors
//   outside packages/design-tokens, no scattered `process.env` access, no inline HTTP(S) URLs and
//   no hardcoded product identity. These are intentionally plain `no-restricted-*` rules rather
//   than a bespoke plugin.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import expoConfig from "eslint-config-expo/flat.js";
import tseslint from "typescript-eslint";

const ROOT_DIR = dirname(fileURLToPath(import.meta.url));
const productConfig = JSON.parse(
  readFileSync(join(ROOT_DIR, "packages/product-config/product.json"), "utf8"),
);

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const HEX_COLOR_PATTERN = "^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$";
const RGB_FUNCTION_PATTERN = "^rgba?\\(";
// Escaped for embedding inside an esquery `/pattern/` selector literal, where an unescaped "/"
// would terminate the pattern early.
const HTTP_URL_PATTERN = "^https?:\\/\\/";
const productIdentityPattern = [
  productConfig.productName,
  productConfig.shortName,
  productConfig.deepLinkScheme,
]
  .filter((value, index, all) => Boolean(value) && all.indexOf(value) === index)
  .map(escapeRegExp)
  .join("|");

// Style properties a design token now covers (packages/design-tokens tokens.json: spacing,
// typography.fontSize/fontFamily, radius, borderWidth). Layout-functional properties like `flex`
// or `flexShrink` are intentionally not in this list: they aren't visual-design values.
const DESIGN_VALUE_STYLE_PROPERTIES = [
  "fontSize",
  "fontFamily",
  "borderRadius",
  "borderWidth",
  "borderTopWidth",
  "borderBottomWidth",
  "borderLeftWidth",
  "borderRightWidth",
  "padding",
  "paddingTop",
  "paddingBottom",
  "paddingLeft",
  "paddingRight",
  "paddingHorizontal",
  "paddingVertical",
  "margin",
  "marginTop",
  "marginBottom",
  "marginLeft",
  "marginRight",
  "marginHorizontal",
  "marginVertical",
  "gap",
  "rowGap",
  "columnGap",
].join("|");

const hexColorGuard = {
  selector: `Literal[value=/${HEX_COLOR_PATTERN}/]`,
  message:
    "Raw hex colors belong in packages/design-tokens, not feature code (docs/DESIGN_SYSTEM.md §10).",
};
const rgbFunctionGuard = {
  selector: `Literal[value=/${RGB_FUNCTION_PATTERN}/]`,
  message:
    "Raw rgb()/rgba() colors belong in packages/design-tokens, not feature code (docs/DESIGN_SYSTEM.md §10).",
};
const httpUrlGuard = {
  selector: `Literal[value=/${HTTP_URL_PATTERN}/]`,
  message:
    "API/base URLs must come from typed config, not an inline literal (docs/ENGINEERING_STANDARDS.md §5).",
};
const productIdentityGuard = {
  // Excludes import/require module specifiers so `from "@tapjob/design-tokens"` itself
  // doesn't trip this guard; it still catches the product name/scheme in ordinary code.
  selector: `Literal[value=/\\b(${productIdentityPattern})\\b/]:not(ImportDeclaration > Literal):not(ExportNamedDeclaration > Literal):not(ExportAllDeclaration > Literal):not(ImportExpression > Literal):not(CallExpression[callee.name="require"] > Literal:first-child)`,
  message:
    "Product identity must come from @tapjob/product-config, not a hardcoded literal (docs/ENGINEERING_STANDARDS.md §3.1).",
};
// Catches a raw number/string assigned directly to a design-sensitive style property anywhere
// (StyleSheet.create(...) or an inline `style={{ ... }}` prop); a token reference such as
// `tokens.spacing.lg` is a MemberExpression, not a Literal, so it never matches.
const designValueStylePropertyGuard = {
  selector: `Property[key.name=/^(${DESIGN_VALUE_STYLE_PROPERTIES})$/][value.type="Literal"]`,
  message:
    "Use a @tapjob/design-tokens value here, not a raw number/string (docs/DESIGN_SYSTEM.md §10).",
};
const processEnvGuard = {
  object: "process",
  property: "env",
  message: "Only apps/mobile/src/config/public-config.ts may read process.env (docs/ENGINEERING_STANDARDS.md §5).",
};

/** @type {import("eslint").Linter.RulesRecord} */
const magicValueGuardRules = {
  "no-restricted-syntax": ["error", hexColorGuard, rgbFunctionGuard, httpUrlGuard, productIdentityGuard],
  "no-restricted-properties": ["error", processEnvGuard],
};

/** Screens/components additionally may not hardcode design values (docs/DESIGN_SYSTEM.md §10:
 * "arbitrary font sizes/spacing/radius are merge-blocking" once design tokens exist).
 * @type {import("eslint").Linter.RulesRecord} */
const screenGuardRules = {
  "no-restricted-syntax": [
    "error",
    hexColorGuard,
    rgbFunctionGuard,
    httpUrlGuard,
    productIdentityGuard,
    designValueStylePropertyGuard,
  ],
  "no-restricted-properties": ["error", processEnvGuard],
};

export default defineConfig([
  globalIgnores([
    "**/node_modules/**",
    "**/dist/**",
    "**/.expo/**",
    "**/coverage/**",
    "packages/design-tokens/generated/**",
    "packages/contracts/src/generated/**",
  ]),

  // packages/* and tools/*: Node-run TypeScript/JavaScript tooling (scripts, typed accessors, CI
  // gates).
  {
    files: ["packages/**/*.{mjs,ts,mts}", "tools/**/*.{mjs,ts,mts}"],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: {
      globals: { process: "readonly", console: "readonly" },
    },
  },
  {
    files: ["packages/**/*.{mjs,ts,mts}", "tools/**/*.{mjs,ts,mts}"],
    rules: magicValueGuardRules,
  },

  // apps/mobile: the Expo app.
  {
    files: ["apps/mobile/**/*.{js,jsx,ts,tsx}"],
    extends: [expoConfig],
  },
  // .ts (no JSX/styles): the base magic-value guards.
  { files: ["apps/mobile/**/*.ts"], rules: magicValueGuardRules },
  // .tsx (screens/components): the base guards plus the design-value-in-styles guard.
  { files: ["apps/mobile/**/*.tsx"], rules: screenGuardRules },
  {
    // User-facing screens must route copy through i18n (docs/ENGINEERING_STANDARDS.md §9)
    // rather than scattering untracked literal strings.
    files: ["apps/mobile/src/app/**/*.tsx"],
    rules: {
      "react/jsx-no-literals": ["error", { noStrings: true, ignoreProps: true }],
    },
  },

  // Manual Jest mocks for node_modules packages (docs/adr/0009-identity-auth-user-account.md §I):
  // applied automatically by Jest itself (no per-test `jest.mock()` call), so `eslint-config-expo`'s
  // own test-file detection (matching `*.test.*`/`__tests__/**`) never covers these -- `jest` must
  // be declared as a global here explicitly instead.
  {
    files: ["apps/mobile/__mocks__/**/*.js"],
    languageOptions: {
      globals: { jest: "readonly" },
    },
  },

  // Config/tooling files legitimately need the patterns the guards above forbid elsewhere.
  {
    files: [
      "eslint.config.mjs",
      "apps/mobile/app.config.ts",
      "apps/mobile/src/config/public-config.ts",
      "packages/product-config/**/*.{ts,mjs}",
      "**/*.test.{ts,tsx,mjs}",
    ],
    rules: {
      "no-restricted-syntax": "off",
      "no-restricted-properties": "off",
    },
  },
]);

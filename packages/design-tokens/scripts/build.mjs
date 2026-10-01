// Generates platform token files from the canonical DTCG sources (tokens.json + themes/*.json).
//
//   node scripts/build.mjs          write generated/native.ts
//   node scripts/build.mjs --check  exit 1 if generated/native.ts is stale (used by CI)
//
// Deliberately dependency-free: token tooling (Style Dictionary, Terrazzo, ...) can replace this
// script later without changing the canonical token files.
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PACKAGE_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const BASE_FILE = "tokens.json";
const THEMES_DIR = "themes";
const OUTPUT_FILE = join("generated", "native.ts");
// Raw palette/scale values stay out of platform output so feature code can only reach semantic tokens.
const PRIMITIVE_GROUP = "primitive";
const ALIAS_PATTERN = /^\{([^{}]+)\}$/;
const HEX_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const UNIT_BY_TYPE = { dimension: "px", duration: "ms" };
// DTCG Color Module 2025.10. Only sRGB is supported until a platform needs wide-gamut output.
const SUPPORTED_COLOR_SPACE = "srgb";
const SRGB_COMPONENT_COUNT = 3;
const OPAQUE_ALPHA = 1;
const BYTE_MAX = 255;
const HEX_RADIX = 16;

/**
 * Converts a DTCG color value ({ colorSpace, components, alpha?, hex? }) to "#RRGGBB".
 * The optional `hex` fallback must agree with the components so the two can never drift.
 */
export function dtcgColorToHex(path, value) {
  const fail = (reason) => {
    throw new Error(`Token "${path}" is not a valid DTCG sRGB color: ${reason}`);
  };
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(`expected { colorSpace, components, alpha?, hex? }, got ${JSON.stringify(value)}`);
  }
  if (value.colorSpace !== SUPPORTED_COLOR_SPACE) {
    fail(`colorSpace must be "${SUPPORTED_COLOR_SPACE}", got ${JSON.stringify(value.colorSpace)}`);
  }
  const { components } = value;
  if (
    !Array.isArray(components) ||
    components.length !== SRGB_COMPONENT_COUNT ||
    !components.every((c) => typeof c === "number" && c >= 0 && c <= 1)
  ) {
    fail(`components must be ${SRGB_COMPONENT_COUNT} numbers in [0, 1] ("none" is not renderable)`);
  }
  // Native output is 6-digit hex, so translucent colors are rejected until an output format needs them.
  if (value.alpha !== undefined && value.alpha !== OPAQUE_ALPHA) {
    fail(`alpha must be omitted or ${OPAQUE_ALPHA}`);
  }
  const hex = `#${components
    .map((c) =>
      Math.round(c * BYTE_MAX)
        .toString(HEX_RADIX)
        .padStart(2, "0"),
    )
    .join("")}`.toUpperCase();
  if (value.hex !== undefined) {
    if (typeof value.hex !== "string" || !HEX_COLOR_PATTERN.test(value.hex)) {
      fail(`hex fallback must be 6-digit #RRGGBB, got ${JSON.stringify(value.hex)}`);
    }
    if (value.hex.toUpperCase() !== hex) {
      fail(`hex fallback ${value.hex} does not match components (${hex})`);
    }
  }
  return hex;
}

function isToken(node) {
  return node !== null && typeof node === "object" && "$value" in node;
}

/** Flattens a DTCG tree into a Map of dotted path -> { type, value }, applying group $type inheritance. */
export function flattenTokens(tree, prefix = [], inheritedType = undefined, out = new Map()) {
  for (const [key, node] of Object.entries(tree)) {
    if (key.startsWith("$")) continue;
    const path = [...prefix, key];
    const type = node?.$type ?? inheritedType;
    if (isToken(node)) {
      if (!type) throw new Error(`Token "${path.join(".")}" has no $type`);
      out.set(path.join("."), { type, value: node.$value });
    } else if (node !== null && typeof node === "object") {
      flattenTokens(node, path, type, out);
    } else {
      throw new Error(`Invalid token node at "${path.join(".")}"`);
    }
  }
  return out;
}

/**
 * Resolves whole-value aliases such as "{primitive.color.blue.600}", rejecting cycles and dangling
 * refs. Only string values can be aliases; object values (DTCG colors, dimensions) are literals.
 */
export function resolveToken(path, tokens, stack = []) {
  const token = tokens.get(path);
  if (!token) throw new Error(`Unknown token reference "${path}" (from ${stack.at(-1) ?? "root"})`);
  if (stack.includes(path))
    throw new Error(`Circular token reference: ${[...stack, path].join(" -> ")}`);
  const alias = typeof token.value === "string" ? ALIAS_PATTERN.exec(token.value) : null;
  if (!alias) return token;
  const target = resolveToken(alias[1], tokens, [...stack, path]);
  if (target.type !== token.type) {
    throw new Error(`Token "${path}" (${token.type}) aliases "${alias[1]}" of type ${target.type}`);
  }
  return target;
}

/** Converts a resolved DTCG value into its React Native representation. */
export function toNativeValue(path, { type, value }) {
  switch (type) {
    case "color":
      return dtcgColorToHex(path, value);
    case "dimension":
    case "duration":
      if (value?.unit !== UNIT_BY_TYPE[type] || typeof value.value !== "number") {
        throw new Error(`Token "${path}" must be { value: number, unit: "${UNIT_BY_TYPE[type]}" }`);
      }
      return value.value;
    case "fontFamily":
      if (typeof value !== "string" || value.length === 0) {
        throw new Error(`Token "${path}" must be a non-empty font family name`);
      }
      return value;
    default:
      throw new Error(`Token "${path}" has unsupported $type "${type}"`);
  }
}

function setPath(target, path, value) {
  const keys = path.split(".");
  let node = target;
  for (const key of keys.slice(0, -1)) node = node[key] ??= {};
  node[keys.at(-1)] = value;
}

/** Builds the exported semantic object for one source tree, resolving against all known tokens. */
export function buildSemanticTree(sourceTokens, allTokens) {
  const result = {};
  for (const path of sourceTokens.keys()) {
    if (path === PRIMITIVE_GROUP || path.startsWith(`${PRIMITIVE_GROUP}.`)) continue;
    setPath(result, path, toNativeValue(path, resolveToken(path, allTokens)));
  }
  return result;
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

export function loadSources(packageDir = PACKAGE_DIR) {
  const base = readJson(join(packageDir, BASE_FILE));
  const themes = Object.fromEntries(
    readdirSync(join(packageDir, THEMES_DIR))
      .filter((file) => file.endsWith(".json"))
      .sort()
      .map((file) => [file.replace(/\.json$/, ""), readJson(join(packageDir, THEMES_DIR, file))]),
  );
  return { base, themes };
}

/** Resolves the canonical sources into the platform-neutral { tokens, themes } output tree. */
export function buildNativeTree({ base, themes }) {
  const baseTokens = flattenTokens(base);
  const tokens = buildSemanticTree(baseTokens, baseTokens);
  const themeOutput = {};
  for (const [name, tree] of Object.entries(themes)) {
    const themeTokens = flattenTokens(tree);
    for (const path of themeTokens.keys()) {
      if (baseTokens.has(path)) throw new Error(`Theme "${name}" redefines base token "${path}"`);
    }
    themeOutput[name] = buildSemanticTree(themeTokens, new Map([...baseTokens, ...themeTokens]));
  }
  return { tokens, themes: themeOutput };
}

export function renderNative(sources) {
  const { tokens, themes: themeOutput } = buildNativeTree(sources);
  const json = (value) => JSON.stringify(value, null, 2);
  return [
    "// AUTO-GENERATED by packages/design-tokens/scripts/build.mjs. Do not edit by hand.",
    "// Sources: packages/design-tokens/tokens.json and packages/design-tokens/themes/*.json",
    "",
    `export const tokens = ${json(tokens)} as const;`,
    "",
    `export const themes = ${json(themeOutput)} as const;`,
    "",
    "export type Tokens = typeof tokens;",
    "export type ThemeName = keyof typeof themes;",
    "export type Theme = (typeof themes)[ThemeName];",
    "",
  ].join("\n");
}

function main(argv) {
  const outputPath = join(PACKAGE_DIR, OUTPUT_FILE);
  const rendered = renderNative(loadSources());
  if (argv.includes("--check")) {
    let current = "";
    try {
      current = readFileSync(outputPath, "utf8");
    } catch {
      // Missing output is reported as stale below.
    }
    if (current !== rendered) {
      console.error(`${OUTPUT_FILE} is out of date. Run: npm run tokens:build`);
      process.exitCode = 1;
      return;
    }
    console.log(`${OUTPUT_FILE} is up to date.`);
    return;
  }
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, rendered, "utf8");
  console.log(`Wrote ${OUTPUT_FILE}`);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2));
}

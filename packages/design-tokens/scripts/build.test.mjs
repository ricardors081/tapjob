import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildNativeTree,
  dtcgColorToHex,
  flattenTokens,
  loadSources,
  renderNative,
  resolveToken,
  toNativeValue,
} from "./build.mjs";

const px = (value) => ({ value, unit: "px" });
const srgb = (components, extra = {}) => ({ colorSpace: "srgb", components, ...extra });
const BLUE_600 = srgb([0.1451, 0.3882, 0.9216], { hex: "#2563EB" });

describe("flattenTokens", () => {
  it("applies group $type inheritance", () => {
    const tokens = flattenTokens({ radius: { $type: "dimension", sm: { $value: px(8) } } });
    assert.deepEqual(tokens.get("radius.sm"), { type: "dimension", value: px(8) });
  });

  it("rejects tokens without a type", () => {
    assert.throws(() => flattenTokens({ orphan: { $value: "#FFFFFF" } }), /has no \$type/);
  });
});

describe("resolveToken", () => {
  const tokens = flattenTokens({
    primitive: { color: { $type: "color", blue: { $value: BLUE_600 } } },
    action: { $type: "color", primary: { $value: "{primitive.color.blue}" } },
  });

  it("follows aliases to the primitive value", () => {
    assert.deepEqual(resolveToken("action.primary", tokens).value, BLUE_600);
  });

  it("rejects dangling references", () => {
    const broken = flattenTokens({ a: { $type: "color", $value: "{missing.token}" } });
    assert.throws(() => resolveToken("a", broken), /Unknown token reference "missing.token"/);
  });

  it("rejects circular references", () => {
    const cyclic = flattenTokens({
      a: { $type: "color", $value: "{b}" },
      b: { $type: "color", $value: "{a}" },
    });
    assert.throws(() => resolveToken("a", cyclic), /Circular token reference/);
  });

  it("rejects aliases across token types", () => {
    const mixed = flattenTokens({
      size: { $type: "dimension", $value: px(4) },
      color: { $type: "color", $value: "{size}" },
    });
    assert.throws(() => resolveToken("color", mixed), /aliases "size" of type dimension/);
  });
});

describe("toNativeValue", () => {
  it("converts dimensions and durations to unitless numbers", () => {
    assert.equal(toNativeValue("r", { type: "dimension", value: px(12) }), 12);
    assert.equal(toNativeValue("d", { type: "duration", value: { value: 160, unit: "ms" } }), 160);
  });

  it("converts DTCG sRGB colors to uppercase #RRGGBB", () => {
    assert.equal(toNativeValue("c", { type: "color", value: BLUE_600 }), "#2563EB");
  });

  it("rejects unexpected units", () => {
    assert.throws(() =>
      toNativeValue("r", { type: "dimension", value: { value: 1, unit: "rem" } }),
    );
  });
});

describe("dtcgColorToHex", () => {
  it("accepts colors without the optional hex fallback and with opaque alpha", () => {
    assert.equal(dtcgColorToHex("c", srgb([1, 1, 1])), "#FFFFFF");
    assert.equal(dtcgColorToHex("c", srgb([0, 0, 0], { alpha: 1 })), "#000000");
  });

  const invalid = [
    ["a legacy hex string value", "#2563EB", /expected { colorSpace/],
    [
      "an unsupported color space",
      { ...BLUE_600, colorSpace: "display-p3" },
      /colorSpace must be "srgb"/,
    ],
    ["the none keyword", srgb(["none", 0, 0]), /components must be 3 numbers/],
    ["out-of-range components", srgb([37, 99, 235]), /components must be 3 numbers/],
    ["the wrong number of components", srgb([0.1, 0.2]), /components must be 3 numbers/],
    ["a translucent alpha", srgb([0, 0, 0], { alpha: 0.5 }), /alpha must be omitted or 1/],
    ["a malformed hex fallback", srgb([1, 1, 1], { hex: "#FFF" }), /hex fallback must be 6-digit/],
    [
      "a hex fallback that disagrees",
      srgb([1, 1, 1], { hex: "#000000" }),
      /does not match components/,
    ],
  ];
  for (const [label, value, message] of invalid) {
    it(`rejects ${label}`, () => {
      assert.throws(() => dtcgColorToHex("c", value), message);
    });
  }
});

describe("renderNative", () => {
  const output = renderNative(loadSources());

  it("exports semantic tokens and never the primitive palette", () => {
    assert.match(output, /"canvas": "#F8FAFC"/);
    assert.doesNotMatch(output, /"primitive"/);
  });

  it("rejects themes that redefine base tokens", () => {
    const sources = loadSources();
    const clash = { radius: { $type: "dimension", sm: { $value: px(1) } } };
    assert.throws(
      () => renderNative({ ...sources, themes: { clash } }),
      /redefines base token "radius.sm"/,
    );
  });

  it("preserves the documented baseline colors", () => {
    const light = buildNativeTree(loadSources()).themes.light;
    assert.deepEqual(light.color, {
      background: { canvas: "#F8FAFC", surface: "#FFFFFF" },
      text: { primary: "#0F172A", secondary: "#475569", inverse: "#FFFFFF" },
      border: { default: "#E2E8F0" },
      brand: { accent: "#F97316" },
      action: { primary: { background: "#2563EB", pressed: "#1D4ED8", foreground: "#FFFFFF" } },
      feedback: {
        success: { foreground: "#16A34A" },
        warning: { foreground: "#D97706" },
        error: { foreground: "#DC2626" },
        info: { foreground: "#0284C7" },
      },
    });
  });

  it("is deterministic", () => {
    assert.equal(renderNative(loadSources()), output);
  });

  it("exposes the spacing, border-width and typography scales the mobile shell consumes", () => {
    const { tokens } = buildNativeTree(loadSources());
    assert.deepEqual(tokens.spacing, { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 });
    assert.deepEqual(tokens.borderWidth, { hairline: 1 });
    assert.deepEqual(tokens.typography.fontSize, { body: 16, title: 24, heading: 28 });
    assert.deepEqual(tokens.typography.fontFamily, {
      primary: "Inter",
      primaryRegular: "Inter_400Regular",
      primarySemibold: "Inter_600SemiBold",
      primaryBold: "Inter_700Bold",
    });
  });
});

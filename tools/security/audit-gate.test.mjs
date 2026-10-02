// Deterministic coverage for the audit gate's internal logic (docs/adr/0007-temporary-node-forge-
// audit-exception.md). Nothing here touches the live npm registry or `npm audit` itself -- every
// case feeds a hand-built audit/lockfile JSON shape and an injected `now` into the pure
// `evaluateAudit`. The real `npm audit` still runs for real in CI (tools/security/audit-gate.mjs's
// `main`); this file only proves the accept/reject *logic* behaves correctly.
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  EXCEPTIONS,
  buildInstalledVersionIndex,
  collectLeafAdvisories,
  evaluateAudit,
  extractGhsaId,
  isExceptionActive,
  parseJsonSafely,
  validateAuditShape,
} from "./audit-gate.mjs";

const PERMITTED = EXCEPTIONS[0];
const BEFORE_EXPIRY = new Date("2026-10-15T00:00:00Z");
const AFTER_EXPIRY = new Date("2026-11-03T00:00:00Z");

function lockfileWith(packageVersions) {
  const packages = {};
  for (const [name, version] of Object.entries(packageVersions)) {
    packages[`node_modules/${name}`] = { version };
  }
  return { packages };
}

const PERMITTED_LOCKFILE = lockfileWith({ [PERMITTED.package]: PERMITTED.pinnedVersion });

function leaf({ ghsaPath, severity, name, title = "test advisory" }) {
  return {
    url: `https://github.com/advisories/${ghsaPath}`,
    severity,
    name,
    title,
  };
}

function evaluate(auditVulnerabilities, { lockfileJson = PERMITTED_LOCKFILE, now = BEFORE_EXPIRY } = {}) {
  return evaluateAudit({
    auditJson: { vulnerabilities: auditVulnerabilities },
    lockfileJson,
    exceptions: EXCEPTIONS,
    now,
  });
}

describe("extractGhsaId", () => {
  test("extracts and uppercases a GHSA id from an advisory URL", () => {
    assert.equal(
      extractGhsaId("https://github.com/advisories/ghsa-86w9-cpqp-85rv"),
      "GHSA-86W9-CPQP-85RV",
    );
  });

  test("returns null for a non-matching or non-string value", () => {
    assert.equal(extractGhsaId("https://example.com/not-an-advisory"), null);
    assert.equal(extractGhsaId(undefined), null);
  });
});

describe("parseJsonSafely", () => {
  test("invalid JSON fails instead of throwing", () => {
    const result = parseJsonSafely("{not valid json");
    assert.equal(result.ok, false);
    assert.ok(result.error);
  });

  test("valid JSON parses through", () => {
    const result = parseJsonSafely('{"a":1}');
    assert.deepEqual(result, { ok: true, value: { a: 1 } });
  });
});

describe("validateAuditShape", () => {
  // Regression coverage for the 2026-10-02 independent review's Finding 2: `typeof x === "object"`
  // is true for both `null` and arrays, so a shallow check previously let a structurally invalid
  // payload fall through `Object.entries(...)` as zero entries and read as a clean (passing)
  // audit. Each case below must be rejected, not reinterpreted as "no vulnerabilities".
  test("a well-formed payload is valid", () => {
    const result = validateAuditShape({
      vulnerabilities: { pkg: { severity: "high", via: [] } },
    });
    assert.equal(result.ok, true);
    assert.deepEqual(result.problems, []);
  });

  test("a non-object top level is invalid", () => {
    assert.equal(validateAuditShape("not an object").ok, false);
    assert.equal(validateAuditShape(42).ok, false);
    assert.equal(validateAuditShape(null).ok, false);
    assert.equal(validateAuditShape(undefined).ok, false);
  });

  test("`vulnerabilities` missing is invalid", () => {
    assert.equal(validateAuditShape({}).ok, false);
  });

  test("`vulnerabilities: null` is invalid (not an empty/clean audit)", () => {
    const result = validateAuditShape({ vulnerabilities: null });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /"vulnerabilities" must be a JSON object, got null/);
  });

  test("`vulnerabilities: []` is invalid (arrays are not the expected map shape)", () => {
    const result = validateAuditShape({ vulnerabilities: [] });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /got array/);
  });

  test("`vulnerabilities` as a string is invalid", () => {
    assert.equal(validateAuditShape({ vulnerabilities: "clean" }).ok, false);
  });

  test("`vulnerabilities` as a number is invalid", () => {
    assert.equal(validateAuditShape({ vulnerabilities: 0 }).ok, false);
  });

  test("an individual entry that is not an object is invalid, not silently skipped", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: "high" } });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /vulnerabilities\["pkg"\] must be a JSON object/);
  });

  test("an individual entry missing a string `severity` is invalid", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: { via: [] } } });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /severity is missing or not a string/);
  });
});

describe("isExceptionActive", () => {
  test("true strictly before the expiry instant", () => {
    assert.equal(isExceptionActive(PERMITTED, BEFORE_EXPIRY), true);
  });

  test("false at/after the expiry instant", () => {
    assert.equal(isExceptionActive(PERMITTED, AFTER_EXPIRY), false);
    assert.equal(isExceptionActive(PERMITTED, new Date(PERMITTED.expiresAt)), false);
  });
});

describe("buildInstalledVersionIndex", () => {
  test("indexes scoped and nested packages by their final path segment", () => {
    const index = buildInstalledVersionIndex({
      packages: {
        "node_modules/@expo/cli": { version: "57.0.27" },
        "node_modules/@expo/cli/node_modules/node-forge": { version: "1.4.0" },
        "node_modules/node-forge": { version: "1.4.0" },
      },
    });
    assert.deepEqual(index["@expo/cli"], ["57.0.27"]);
    assert.deepEqual(index["node-forge"], ["1.4.0", "1.4.0"]);
  });
});

describe("evaluateAudit", () => {
  test("a clean audit (no vulnerabilities) passes", () => {
    const result = evaluate({});
    assert.equal(result.ok, true);
    assert.deepEqual(result.violations, []);
    assert.deepEqual(result.usedExceptions, []);
  });

  // Finding 2 (2026-10-02 review): `typeof null === "object"` and arrays are objects too, so a
  // shallow `typeof auditJson.vulnerabilities !== "object"` check previously let these payloads
  // fall through as "zero entries" and PASS. Every one of these must fail instead.
  test("`vulnerabilities` entirely absent fails, never reads as a clean audit", () => {
    const result = evaluateAudit({
      auditJson: {},
      lockfileJson: PERMITTED_LOCKFILE,
      exceptions: EXCEPTIONS,
      now: BEFORE_EXPIRY,
    });
    assert.equal(result.ok, false);
  });

  test("`vulnerabilities: null` fails, never reads as a clean audit", () => {
    const result = evaluate(null);
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid audit payload shape/);
  });

  test("`vulnerabilities: []` (an array) fails, never reads as a clean audit", () => {
    const result = evaluate([]);
    assert.equal(result.ok, false);
  });

  test("`vulnerabilities` as a string fails", () => {
    assert.equal(evaluate("clean").ok, false);
  });

  test("`vulnerabilities` as a number fails", () => {
    assert.equal(evaluate(0).ok, false);
  });

  test("a structurally invalid HIGH-looking entry is not silently skipped", () => {
    const result = evaluate({ "sneaky-package": "high" });
    assert.equal(result.ok, false);
  });

  test("moderate-only findings never fail the gate", () => {
    const result = evaluate({
      "some-package": {
        severity: "moderate",
        via: [leaf({ ghsaPath: "GHSA-xxxx-xxxx-xxxx", severity: "moderate", name: "some-package" })],
      },
    });
    assert.equal(result.ok, true);
  });

  test("only the permitted GHSA, before expiry: passes and records the exception as used", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: {
          severity: "high",
          via: [leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package })],
        },
      },
      { now: BEFORE_EXPIRY },
    );
    assert.equal(result.ok, true);
    assert.equal(result.violations.length, 0);
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, PERMITTED.ghsa);
  });

  test("the same permitted GHSA after its expiry date fails", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: {
          severity: "high",
          via: [leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package })],
        },
      },
      { now: AFTER_EXPIRY },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], new RegExp(PERMITTED.ghsa, "i"));
  });

  test("a different HIGH advisory (unpermitted) fails", () => {
    const result = evaluate({
      "other-package": {
        severity: "high",
        via: [leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "other-package" })],
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  test("a CRITICAL advisory (unpermitted) fails", () => {
    const result = evaluate({
      "other-package": {
        severity: "critical",
        via: [leaf({ ghsaPath: "GHSA-dddd-eeee-ffff", severity: "critical", name: "other-package" })],
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /critical/);
  });

  test("the permitted GHSA alongside a second, different HIGH advisory still fails", () => {
    const result = evaluate({
      [PERMITTED.package]: {
        severity: "high",
        via: [
          leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package }),
          leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "unrelated-package" }),
        ],
      },
    });
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  test("node-forge resolved to a different version does not get the exception", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: {
          severity: "high",
          via: [leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package })],
        },
      },
      { lockfileJson: lockfileWith({ [PERMITTED.package]: "1.5.0" }) },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], new RegExp(PERMITTED.ghsa, "i"));
  });

  test("the exception package missing from the lockfile entirely does not get the exception", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: {
          severity: "high",
          via: [leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package })],
        },
      },
      { lockfileJson: { packages: {} } },
    );
    assert.equal(result.ok, false);
  });

  test("a transitive chain caused exclusively by the permitted GHSA passes", () => {
    // Mirrors the real shape: @expo/cli -> via: ["node-forge"] -> node-forge -> via: [leaf].
    const result = evaluate({
      "@expo/cli": {
        severity: "high",
        via: [PERMITTED.package],
      },
      [PERMITTED.package]: {
        severity: "high",
        via: [leaf({ ghsaPath: PERMITTED.ghsa, severity: "high", name: PERMITTED.package })],
      },
    });
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 1);
  });

  test("a transitive chain containing a different HIGH/CRITICAL advisory fails", () => {
    const result = evaluate({
      "some-root-package": {
        severity: "high",
        via: ["intermediate-package"],
      },
      "intermediate-package": {
        severity: "high",
        via: [leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "intermediate-package" })],
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  test("a high-severity entry with no resolvable advisory leaf fails closed", () => {
    const result = evaluate({
      "weird-package": { severity: "high", via: [] },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /no underlying HIGH\/CRITICAL advisory leaf/);
  });

  test("a cyclic via chain does not infinite-loop and still fails closed on an unpermitted advisory", () => {
    const result = evaluate({
      a: { severity: "high", via: ["b"] },
      b: { severity: "high", via: ["a", leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "b" })] },
    });
    assert.equal(result.ok, false);
  });
});

describe("collectLeafAdvisories", () => {
  test("returns [] for an unknown package name", () => {
    assert.deepEqual(collectLeafAdvisories("does-not-exist", {}), []);
  });
});

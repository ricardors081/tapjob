// Deterministic coverage for the audit gate's internal logic (docs/adr/0007-temporary-node-forge-
// audit-exception.md, docs/adr/0008-temporary-braces-audit-exception.md). Nothing here touches the
// live npm registry or `npm audit` itself -- every case feeds a hand-built audit/lockfile JSON shape
// and an injected `now` into the pure `evaluateAudit`. The real `npm audit` still runs for real in CI
// (tools/security/audit-gate.mjs's `main`); this file only proves the accept/reject *logic* behaves
// correctly, including now that `EXCEPTIONS` holds two independent entries.
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
  validateExceptionsConfig,
} from "./audit-gate.mjs";

const NODE_FORGE = EXCEPTIONS[0];
const BRACES = EXCEPTIONS[1];
// Kept as `PERMITTED` for the pre-existing node-forge-focused tests below, unchanged.
const PERMITTED = NODE_FORGE;

// node-forge expires 2026-11-02T00:00:00Z; braces expires 2026-11-04T00:00:00Z.
const BEFORE_EXPIRY = new Date("2026-10-15T00:00:00Z"); // before both
const AFTER_EXPIRY = new Date("2026-11-03T00:00:00Z"); // after node-forge's expiry, before braces'
const AFTER_BRACES_EXPIRY = new Date("2026-11-05T00:00:00Z"); // after both

function lockfileWith(packageVersions) {
  const packages = {};
  for (const [name, version] of Object.entries(packageVersions)) {
    packages[`node_modules/${name}`] = { version };
  }
  return { packages };
}

const PERMITTED_LOCKFILE = lockfileWith({ [PERMITTED.package]: PERMITTED.pinnedVersion });
const BRACES_LOCKFILE = lockfileWith({ [BRACES.package]: BRACES.pinnedVersion });
const BOTH_LOCKFILE = lockfileWith({
  [NODE_FORGE.package]: NODE_FORGE.pinnedVersion,
  [BRACES.package]: BRACES.pinnedVersion,
});

function leaf({ ghsaPath, severity, name, title = "test advisory" }) {
  return {
    url: `https://github.com/advisories/${ghsaPath}`,
    severity,
    name,
    title,
  };
}

function evaluate(
  auditVulnerabilities,
  { lockfileJson = PERMITTED_LOCKFILE, now = BEFORE_EXPIRY, exceptions = EXCEPTIONS } = {},
) {
  return evaluateAudit({
    auditJson: { vulnerabilities: auditVulnerabilities },
    lockfileJson,
    exceptions,
    now,
  });
}

function leafFor(exception) {
  return leaf({ ghsaPath: exception.ghsa, severity: "high", name: exception.package });
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

  // Regression coverage for the 2026-10-04 independent review's Finding 1: a malformed `via` item
  // (not a string, not a well-formed advisory-leaf object) previously ended up with a `severity`
  // of `null` inside `collectLeafAdvisories`, got silently filtered out of "concerning" leaves by
  // `evaluateAudit`, and so disappeared without a trace -- never a leaf, never a violation. If a
  // DIFFERENT, legitimately-permitted leaf sat alongside it in the same `via` array, the whole
  // entry could still read as fully explained and PASS. Each case below must make the payload
  // shape itself invalid, independent of whatever else is in the same `via` array.
  test("a `via` item that is a string is valid (a reference to another vulnerabilities key)", () => {
    const result = validateAuditShape({
      vulnerabilities: { pkg: { severity: "high", via: ["other-pkg"] } },
    });
    assert.equal(result.ok, true);
  });

  test("a `via` item that is a well-formed advisory-leaf object is valid", () => {
    const result = validateAuditShape({
      vulnerabilities: {
        pkg: {
          severity: "high",
          via: [{ url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc", severity: "high", name: "pkg" }],
        },
      },
    });
    assert.equal(result.ok, true);
  });

  test("a `via` item that is an object without a string `severity` is invalid", () => {
    const result = validateAuditShape({
      vulnerabilities: {
        pkg: { severity: "high", via: [{ url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc" }] },
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /via\[0\] is an advisory-leaf object but its severity/);
  });

  test("a `via` item that is `null` is invalid", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: { severity: "high", via: [null] } } });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /via\[0\] must be a string reference or an advisory-leaf object, got null/);
  });

  test("a `via` item that is a number is invalid", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: { severity: "high", via: [42] } } });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /via\[0\] must be a string reference or an advisory-leaf object, got number/);
  });

  test("a `via` item that is a boolean is invalid", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: { severity: "high", via: [true] } } });
    assert.equal(result.ok, false);
  });

  test("a `via` item that is a nested array is invalid", () => {
    const result = validateAuditShape({ vulnerabilities: { pkg: { severity: "high", via: [[]] } } });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /via\[0\] must be a string reference or an advisory-leaf object, got array/);
  });

  test("a malformed `via` item at a later index is still caught (not just index 0)", () => {
    const result = validateAuditShape({
      vulnerabilities: {
        pkg: {
          severity: "high",
          via: [
            { url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc", severity: "high", name: "pkg" },
            null,
          ],
        },
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /via\[1\]/);
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

  test("braces exception: true strictly before its own (later) expiry instant", () => {
    assert.equal(isExceptionActive(BRACES, AFTER_EXPIRY), true);
  });

  test("braces exception: false at/after its own expiry instant", () => {
    assert.equal(isExceptionActive(BRACES, AFTER_BRACES_EXPIRY), false);
    assert.equal(isExceptionActive(BRACES, new Date(BRACES.expiresAt)), false);
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

// A minimal, otherwise-valid exception fixture, so a test exercising ONE invariant (duplicate
// detection, a single malformed field, ...) does not also have to satisfy every other field's
// requirement by hand or accidentally trip a DIFFERENT validation rule than the one under test.
function fakeException(overrides = {}) {
  return {
    ghsa: "GHSA-aaaa-bbbb-cccc",
    package: "foo",
    pinnedVersion: "1.0.0",
    expiresAt: "2099-01-01T00:00:00Z",
    reference: "docs/adr/0000-fake.md",
    ...overrides,
  };
}

describe("validateExceptionsConfig", () => {
  test("the real EXCEPTIONS list (node-forge + braces) is itself valid", () => {
    const result = validateExceptionsConfig(EXCEPTIONS);
    assert.equal(result.ok, true);
    assert.deepEqual(result.problems, []);
  });

  test("an empty exceptions list is valid", () => {
    assert.equal(validateExceptionsConfig([]).ok, true);
  });

  test("two distinct GHSA+package entries are valid", () => {
    const result = validateExceptionsConfig([
      fakeException({ ghsa: "GHSA-aaaa-bbbb-cccc", package: "foo" }),
      fakeException({ ghsa: "GHSA-dddd-eeee-ffff", package: "bar", pinnedVersion: "2.0.0" }),
    ]);
    assert.equal(result.ok, true);
  });

  test("two entries with the same GHSA+package and the SAME version is a rejected duplicate", () => {
    const result = validateExceptionsConfig([fakeException(), fakeException()]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /duplicate exception entries/);
  });

  test("two entries with the same GHSA+package but DIFFERENT versions is a rejected contradiction", () => {
    const result = validateExceptionsConfig([
      fakeException({ pinnedVersion: "1.0.0" }),
      fakeException({ pinnedVersion: "2.0.0" }),
    ]);
    assert.equal(result.ok, false);
  });

  test("same GHSA but a different package is not a duplicate (distinct scope)", () => {
    const result = validateExceptionsConfig([
      fakeException({ package: "foo" }),
      fakeException({ package: "bar" }),
    ]);
    assert.equal(result.ok, true);
  });

  test("GHSA comparison is case-insensitive", () => {
    const result = validateExceptionsConfig([
      fakeException({ ghsa: "GHSA-aaaa-bbbb-cccc" }),
      fakeException({ ghsa: "ghsa-AAAA-BBBB-CCCC" }),
    ]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /duplicate exception entries/);
  });

  test("evaluateAudit fails closed when given a duplicate-config exceptions list", () => {
    const result = evaluate({}, { exceptions: [fakeException(), fakeException()] });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid exceptions configuration/);
  });

  // -- Extra hardening (2026-10-04 review): malformed individual fields must fail deterministically,
  // never crash with an accidental TypeError (e.g. `.toUpperCase()` on a non-string `ghsa`). ------

  test("a non-string `ghsa` is rejected explicitly, not thrown", () => {
    assert.doesNotThrow(() => validateExceptionsConfig([fakeException({ ghsa: 42 })]));
    const result = validateExceptionsConfig([fakeException({ ghsa: 42 })]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /ghsa must be a non-empty string/);
  });

  test("an empty-string `ghsa` is rejected", () => {
    const result = validateExceptionsConfig([fakeException({ ghsa: "" })]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /ghsa must be a non-empty string/);
  });

  test("a missing `package` is rejected explicitly, not thrown", () => {
    const withoutPackage = fakeException();
    delete withoutPackage.package;
    assert.doesNotThrow(() => validateExceptionsConfig([withoutPackage]));
    const result = validateExceptionsConfig([withoutPackage]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /package must be a non-empty string/);
  });

  test("a non-string `pinnedVersion` is rejected", () => {
    const result = validateExceptionsConfig([fakeException({ pinnedVersion: 100 })]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /pinnedVersion must be a non-empty string/);
  });

  test("an unparseable `expiresAt` is rejected", () => {
    const result = validateExceptionsConfig([fakeException({ expiresAt: "not-a-date" })]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /expiresAt must be a parseable date string/);
  });

  test("a missing `reference` is rejected", () => {
    const withoutReference = fakeException();
    delete withoutReference.reference;
    const result = validateExceptionsConfig([withoutReference]);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /reference must be a non-empty string/);
  });

  test("a malformed entry does not prevent a properly-formed sibling entry from being checked for duplicates", () => {
    const result = validateExceptionsConfig([
      fakeException({ ghsa: 42 }),
      fakeException({ ghsa: "GHSA-aaaa-bbbb-cccc", package: "foo" }),
      fakeException({ ghsa: "GHSA-aaaa-bbbb-cccc", package: "foo" }),
    ]);
    assert.equal(result.ok, false);
    assert.ok(result.problems.some((p) => /ghsa must be a non-empty string/.test(p)));
    assert.ok(result.problems.some((p) => /duplicate exception entries/.test(p)));
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

  // -- node-forge only --------------------------------------------------------------------------

  test("only the permitted node-forge GHSA, before expiry: passes and records the exception as used", () => {
    const result = evaluate(
      { [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] } },
      { now: BEFORE_EXPIRY },
    );
    assert.equal(result.ok, true);
    assert.equal(result.violations.length, 0);
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, PERMITTED.ghsa);
  });

  test("the same permitted node-forge GHSA after its expiry date fails", () => {
    const result = evaluate(
      { [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] } },
      { now: AFTER_EXPIRY },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], new RegExp(PERMITTED.ghsa, "i"));
  });

  test("node-forge resolved to a different version does not get the exception", () => {
    const result = evaluate(
      { [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] } },
      { lockfileJson: lockfileWith({ [PERMITTED.package]: "1.5.0" }) },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], new RegExp(PERMITTED.ghsa, "i"));
  });

  test("the node-forge exception package missing from the lockfile entirely does not get the exception", () => {
    const result = evaluate(
      { [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] } },
      { lockfileJson: { packages: {} } },
    );
    assert.equal(result.ok, false);
  });

  test("a transitive chain caused exclusively by the permitted node-forge GHSA passes", () => {
    // Mirrors the real shape: @expo/cli -> via: ["node-forge"] -> node-forge -> via: [leaf].
    const result = evaluate({
      "@expo/cli": { severity: "high", via: [PERMITTED.package] },
      [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] },
    });
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 1);
  });

  test("the permitted node-forge GHSA alongside a second, different HIGH advisory still fails", () => {
    const result = evaluate({
      [PERMITTED.package]: {
        severity: "high",
        via: [leafFor(PERMITTED), leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "unrelated-package" })],
      },
    });
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  // -- braces only --------------------------------------------------------------------------------

  test("only the permitted braces GHSA, before its expiry: passes and records the exception as used", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] } },
      { lockfileJson: BRACES_LOCKFILE, now: BEFORE_EXPIRY },
    );
    assert.equal(result.ok, true);
    assert.equal(result.violations.length, 0);
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, BRACES.ghsa);
  });

  test("the permitted braces GHSA after ITS OWN expiry date fails", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] } },
      { lockfileJson: BRACES_LOCKFILE, now: AFTER_BRACES_EXPIRY },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], new RegExp(BRACES.ghsa, "i"));
  });

  test("braces resolved to a different version does not get the exception", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] } },
      { lockfileJson: lockfileWith({ [BRACES.package]: "3.0.2" }) },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], new RegExp(BRACES.ghsa, "i"));
  });

  test("the braces exception's GHSA attached to a different package does not get the exception", () => {
    // Same GHSA string but a different leaf package name must not be silently treated as covered.
    const result = evaluate(
      { "some-other-package": { severity: "high", via: [leaf({ ghsaPath: BRACES.ghsa, severity: "high", name: "some-other-package" })] } },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
  });

  test("the permitted braces GHSA alongside a second, different HIGH advisory still fails", () => {
    const result = evaluate(
      {
        [BRACES.package]: {
          severity: "high",
          via: [leafFor(BRACES), leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "unrelated-package" })],
        },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  test("the permitted braces GHSA alongside an unrelated CRITICAL advisory still fails", () => {
    const result = evaluate(
      {
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
        "other-package": {
          severity: "critical",
          via: [leaf({ ghsaPath: "GHSA-dddd-eeee-ffff", severity: "critical", name: "other-package" })],
        },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], /critical/);
  });

  test("a transitive chain caused exclusively by the permitted braces GHSA passes", () => {
    // Mirrors the real shape: @expo/metro-file-map -> via: ["micromatch"] -> micromatch ->
    // via: ["braces"] -> braces -> via: [leaf].
    const result = evaluate(
      {
        "@expo/metro-file-map": { severity: "high", via: ["micromatch"] },
        micromatch: { severity: "high", via: [BRACES.package] },
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, BRACES.ghsa);
  });

  test("a transitive chain rooted in braces but containing a different HIGH advisory fails", () => {
    const result = evaluate(
      {
        "@expo/metro-file-map": { severity: "high", via: ["micromatch"] },
        micromatch: {
          severity: "high",
          via: [BRACES.package, leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "micromatch" })],
        },
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  // -- both exceptions present simultaneously --------------------------------------------------

  test("both the node-forge and braces exceptions present and valid: passes with both recorded", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] },
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BOTH_LOCKFILE, now: BEFORE_EXPIRY },
    );
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 2);
    const usedGhsas = result.usedExceptions.map((e) => e.ghsa).sort();
    assert.deepEqual(usedGhsas, [BRACES.ghsa, PERMITTED.ghsa].sort());
  });

  test("both present, but node-forge has already expired while braces has not: fails on node-forge only", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] },
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BOTH_LOCKFILE, now: AFTER_EXPIRY },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], new RegExp(PERMITTED.ghsa, "i"));
    // braces is still covered, so it is NOT in violations and IS recorded as used.
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, BRACES.ghsa);
  });

  test("both present and both covered, plus a third unrelated HIGH advisory: still fails on the third", () => {
    const result = evaluate(
      {
        [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED)] },
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
        "other-package": {
          severity: "high",
          via: [leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "other-package" })],
        },
      },
      { lockfileJson: BOTH_LOCKFILE, now: BEFORE_EXPIRY },
    );
    assert.equal(result.ok, false);
    assert.equal(result.violations.length, 1);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
    assert.equal(result.usedExceptions.length, 2);
  });

  // -- generic / shared logic --------------------------------------------------------------------

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

  test("a transitive chain containing a different HIGH/CRITICAL advisory fails", () => {
    const result = evaluate({
      "some-root-package": { severity: "high", via: ["intermediate-package"] },
      "intermediate-package": {
        severity: "high",
        via: [leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "intermediate-package" })],
      },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /GHSA-AAAA-BBBB-CCCC/);
  });

  test("a high-severity entry with no resolvable advisory leaf fails closed", () => {
    const result = evaluate({ "weird-package": { severity: "high", via: [] } });
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

  // -- Finding 1 (2026-10-04 review): a malformed `via` item cannot hide behind a valid leaf -----

  test("allowed braces leaf + a sibling via object with no severity still fails", () => {
    const result = evaluate(
      {
        [BRACES.package]: {
          severity: "high",
          via: [leafFor(BRACES), { url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc", name: "sneaky" }],
        },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid audit payload shape/);
  });

  test("allowed braces leaf + a sibling null via item still fails", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES), null] } },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid audit payload shape/);
  });

  test("allowed braces leaf + a sibling number via item still fails", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES), 42] } },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid audit payload shape/);
  });

  test("allowed braces leaf + a sibling advisory object with an unparseable URL still fails closed (unpermitted, no GHSA)", () => {
    // This object IS structurally valid (string severity present), so it is not rejected by shape
    // validation -- but its GHSA cannot be extracted, so it can never match any exception and
    // must surface as its own, separate unpermitted-advisory violation.
    const result = evaluate(
      {
        [BRACES.package]: {
          severity: "high",
          via: [leafFor(BRACES), { url: "https://example.com/not-an-advisory", severity: "high", name: "sneaky" }],
        },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /no GHSA in url/);
  });

  test("a malformed via item alone (no other leaf) fails on invalid shape, not on 'no leaf resolved'", () => {
    const result = evaluate({ "weird-package": { severity: "high", via: [null] } });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /invalid audit payload shape/);
  });

  // -- Finding 2 (2026-10-04 review): an unresolved via reference cannot hide behind a valid leaf --

  test("a lone missing via reference (no other leaf) fails closed", () => {
    const result = evaluate({ root: { severity: "high", via: ["missing-package"] } });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /references package\(s\) not present/);
  });

  test("allowed braces leaf + a sibling missing string reference still fails", () => {
    const result = evaluate(
      { [BRACES.package]: { severity: "high", via: [leafFor(BRACES), "missing-package"] } },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /references package\(s\) not present/);
  });

  test("allowed node-forge leaf + a sibling missing string reference still fails", () => {
    const result = evaluate({
      [PERMITTED.package]: { severity: "high", via: [leafFor(PERMITTED), "missing-package"] },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations[0], /references package\(s\) not present/);
  });

  test("an allowed braces leaf elsewhere does not mask a SEPARATE, genuinely unexplained cyclic entry", () => {
    // Two independent top-level findings in the same payload: one fully explained by the braces
    // exception, and one that is a pure true cycle (each name still on its own active recursion
    // path when revisited) with no leaf anywhere in it. Each entry is evaluated on its own terms --
    // the braces entry passing must never let the cyclic entry's own lack of any explanation slip
    // through. The true-cycle detector now catches this directly (reported as an unresolved causal
    // reference), rather than falling through to the generic "no leaf resolved" message.
    const result = evaluate(
      {
        [BRACES.package]: { severity: "high", via: [leafFor(BRACES)] },
        "cyclic-a": { severity: "high", via: ["cyclic-b"] },
        "cyclic-b": { severity: "high", via: ["cyclic-a"] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations.join("\n"), /references package\(s\) not present|no underlying HIGH\/CRITICAL advisory leaf/);
  });

  test("revisiting the same package from two different parents (a diamond) does not spuriously fail", () => {
    // The real shape confirmed directly against this repository's own `npm audit --json`: the
    // same shared package reached from more than one parent in one traversal. This must remain
    // safe and must not be confused with Finding 2's "missing reference" case.
    const result = evaluate(
      {
        "parent-a": { severity: "high", via: ["shared"] },
        "parent-b": { severity: "high", via: ["shared"] },
        shared: { severity: "high", via: [leafFor(BRACES)] },
        root: { severity: "high", via: ["parent-a", "parent-b"] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, true);
  });

  // -- Review round 3: true cycle (active recursion path) vs. diamond (memoized completion) ------
  //
  // A revisit of a name still on the CURRENT recursion path (a true cycle: a -> b -> a) must never
  // be treated the same as a revisit of a name already fully resolved earlier in the SAME
  // traversal (a diamond: two parents both reaching one shared, already-completed node). Collapsing
  // both into one `visited` set let a true, unexplained causal cycle disappear silently whenever a
  // separately-permitted leaf sat anywhere in the same `via` array or even in the same strongly
  // connected component.

  test("a pure diamond (no cycle) passes", () => {
    const result = evaluate(
      {
        root: { severity: "high", via: ["parent-a", "parent-b"] },
        "parent-a": { severity: "high", via: ["shared"] },
        "parent-b": { severity: "high", via: ["shared"] },
        shared: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, true);
  });

  test("a simple true cycle with no leaf anywhere fails", () => {
    const result = evaluate({
      a: { severity: "high", via: ["b"] },
      b: { severity: "high", via: ["a"] },
    });
    assert.equal(result.ok, false);
  });

  test("permitted braces leaf on root + a sibling true cycle WITH NO LEAF OF ITS OWN still fails overall", () => {
    // `root` itself resolves fine (its own direct braces leaf is permitted) -- but `cycle-a`/
    // `cycle-b` are themselves separately flagged HIGH entries in this audit payload, and neither
    // has any leaf reachable from it anywhere (a true dead end, invariant A). `evaluateAudit`
    // evaluates every flagged entry as its own root, so the cyclic pair's own lack of any
    // explanation still fails the gate overall -- without needing `root`'s valid leaf to be
    // artificially invalidated by the unrelated sibling cycle.
    const result = evaluate(
      {
        root: { severity: "high", via: [leafFor(BRACES), "cycle-a"] },
        "cycle-a": { severity: "high", via: ["cycle-b"] },
        "cycle-b": { severity: "high", via: ["cycle-a"] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations.join("\n"), /no underlying HIGH\/CRITICAL advisory leaf/);
    // root's own entry must not itself be reported as a violation -- its leaf is genuinely permitted.
    assert.ok(!result.violations.some((violation) => violation.startsWith("root:")));
  });

  test("permitted node-forge leaf on root + a sibling true cycle WITH NO LEAF OF ITS OWN still fails overall", () => {
    const result = evaluate({
      root: { severity: "high", via: [leafFor(PERMITTED), "cycle-a"] },
      "cycle-a": { severity: "high", via: ["cycle-b"] },
      "cycle-b": { severity: "high", via: ["cycle-a"] },
    });
    assert.equal(result.ok, false);
    assert.match(result.violations.join("\n"), /no underlying HIGH\/CRITICAL advisory leaf/);
    assert.ok(!result.violations.some((violation) => violation.startsWith("root:")));
  });

  test("a cycle whose own strongly-connected component contains a permitted leaf passes", () => {
    // a <-> b, with the permitted braces leaf attached directly to b: the cycle itself is just
    // graph structure (npm audit reports real mutual package relationships this way -- confirmed
    // directly against this repository's own metro/metro-config/metro-transform-worker chain).
    // Both `a` and `b` are independently flagged HIGH entries and BOTH reach the same leaf, so
    // both resolve and the whole payload passes.
    const result = evaluate(
      {
        a: { severity: "high", via: ["b"] },
        b: { severity: "high", via: ["a", leafFor(BRACES)] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 1);
    assert.equal(result.usedExceptions[0].ghsa, BRACES.ghsa);
  });

  test("a cycle whose SCC reaches both the node-forge leaf and the braces leaf passes", () => {
    const result = evaluate(
      {
        a: { severity: "high", via: ["b", leafFor(PERMITTED)] },
        b: { severity: "high", via: ["a", leafFor(BRACES)] },
      },
      { lockfileJson: BOTH_LOCKFILE },
    );
    assert.equal(result.ok, true);
    assert.equal(result.usedExceptions.length, 2);
  });

  test("a cycle whose SCC has a permitted leaf PLUS a missing via reference still fails", () => {
    const result = evaluate(
      {
        a: { severity: "high", via: ["b"] },
        b: { severity: "high", via: ["a", leafFor(BRACES), "missing-package"] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations.join("\n"), /references package\(s\) not present/);
  });

  test("a cycle whose SCC has a permitted leaf PLUS an unrelated unpermitted leaf still fails", () => {
    // Same shape as above, but `b` also carries a second, unrelated, unpermitted HIGH leaf -- the
    // cycle being harmless does not make every leaf reachable from it automatically permitted.
    const result = evaluate(
      {
        a: { severity: "high", via: ["b"] },
        b: {
          severity: "high",
          via: [
            "a",
            leafFor(BRACES),
            leaf({ ghsaPath: "GHSA-aaaa-bbbb-cccc", severity: "high", name: "unrelated-package" }),
          ],
        },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, false);
    assert.match(result.violations.join("\n"), /GHSA-AAAA-BBBB-CCCC/);
  });

  test("a diamond nested inside another diamond still passes", () => {
    const result = evaluate(
      {
        root: { severity: "high", via: ["mid-a", "mid-b"] },
        "mid-a": { severity: "high", via: ["inner-a", "inner-b"] },
        "mid-b": { severity: "high", via: ["inner-a", "inner-b"] },
        "inner-a": { severity: "high", via: ["shared"] },
        "inner-b": { severity: "high", via: ["shared"] },
        shared: { severity: "high", via: [leafFor(BRACES)] },
      },
      { lockfileJson: BRACES_LOCKFILE },
    );
    assert.equal(result.ok, true);
  });

  test("a cycle reached by two different parents still fails (not treated as a safe diamond)", () => {
    const result = evaluate(
      {
        root: { severity: "high", via: ["parent-a", "parent-b"] },
        "parent-a": { severity: "high", via: ["cycle-a"] },
        "parent-b": { severity: "high", via: ["cycle-a"] },
        "cycle-a": { severity: "high", via: ["cycle-b"] },
        "cycle-b": { severity: "high", via: ["cycle-a"] },
      },
    );
    assert.equal(result.ok, false);
  });
});

describe("collectLeafAdvisories", () => {
  test("a name absent from `vulnerabilities` entirely is reported as unresolved, not silently empty", () => {
    assert.deepEqual(collectLeafAdvisories("does-not-exist", {}), {
      leaves: [],
      unresolved: ["does-not-exist"],
    });
  });

  test("a cycle back to a name still on the current recursion path is silent, not unresolved", () => {
    // A cycle is graph structure (confirmed in real npm audit output: metro <-> metro-config),
    // never an unresolved reference by itself -- only a name entirely absent from
    // `vulnerabilities` is. `collectLeafAdvisories` must never add a real, present key's own name
    // to `unresolved` merely because it is reached again while its own frame is still open.
    const vulnerabilities = {
      a: { severity: "high", via: ["b"] },
      b: { severity: "high", via: ["a"] },
    };
    const result = collectLeafAdvisories("a", vulnerabilities);
    assert.deepEqual(result, { leaves: [], unresolved: [] });
  });

  test("revisiting an already-visited name (a diamond, not a true cycle) is silent, not unresolved", () => {
    // Mirrors a real pattern confirmed directly against this repository's own `npm audit --json`:
    // the same package name reached from two different parents in one traversal (e.g. `metro`).
    const vulnerabilities = {
      parentA: { severity: "high", via: ["shared"] },
      parentB: { severity: "high", via: ["shared"] },
      shared: { severity: "high", via: [] },
      root: { severity: "high", via: ["parentA", "parentB"] },
    };
    const result = collectLeafAdvisories("root", vulnerabilities);
    assert.deepEqual(result.unresolved, []);
  });
});

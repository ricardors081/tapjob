#!/usr/bin/env node
// Scoped, fail-closed replacement for the bare `npm audit --audit-level=high` CI step
// (docs/adr/0007-temporary-node-forge-audit-exception.md, D-047).
//
// Runs the REAL `npm audit`, parses its structured JSON, and fails on any HIGH/CRITICAL advisory
// EXCEPT the one(s) explicitly listed below -- and only while that exception is (a) not expired
// and (b) the affected package is pinned to the exact version the exception was written against.
// Every other HIGH/CRITICAL finding -- known or not yet discovered -- still fails the build. This
// is not a blanket `--audit-level` downgrade and not a generic allowlist: each exception is one
// GHSA, one package, one pinned version, with an automatic expiry.
//
// This script intentionally has zero new dependencies: plain Node (child_process, fs, path).

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");

/**
 * Active exceptions. Each one is scoped to a single advisory, a single package and a single
 * pinned version, with a hard expiry. Remove an entry as soon as its condition for removal is
 * met -- do not wait for the expiry date if a compatible official fix lands sooner.
 *
 * @typedef {{
 *   ghsa: string,
 *   cve: string,
 *   package: string,
 *   pinnedVersion: string,
 *   expiresAt: string,
 *   reference: string,
 * }} AuditException
 * @type {ReadonlyArray<AuditException>}
 */
export const EXCEPTIONS = Object.freeze([
  Object.freeze({
    ghsa: "GHSA-86w9-cpqp-85rv",
    cve: "CVE-2026-85393",
    package: "node-forge",
    pinnedVersion: "1.4.0",
    // Remove this entry (not just let it expire) the moment a compatible, non-breaking Expo
    // tooling release stops resolving a vulnerable node-forge, whichever comes first.
    expiresAt: "2026-11-02T00:00:00Z",
    reference: "docs/adr/0007-temporary-node-forge-audit-exception.md",
  }),
]);

const GHSA_PATTERN = /GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}/i;
const CONCERNING_SEVERITIES = new Set(["high", "critical"]);

export function extractGhsaId(url) {
  if (typeof url !== "string") return null;
  const match = GHSA_PATTERN.exec(url);
  return match ? match[0].toUpperCase() : null;
}

function describeType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/** A "plain object" here means: a real JSON object -- not null, not an array, not a primitive.
 * `typeof null === "object"` and arrays are also objects in JavaScript, so a bare `typeof x ===
 * "object"` check is NOT sufficient to validate audit JSON shape (that gap is exactly what Finding
 * 2 of the 2026-10-02 review caught: `{"vulnerabilities": null}` or `{"vulnerabilities": []}`
 * would otherwise fall through `Object.entries(...)` as zero entries and read as a clean audit). */
function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Validates the exact shape `evaluateAudit` depends on, BEFORE any accept/reject logic runs, so an
 * invalid payload can never be silently reinterpreted as "zero vulnerabilities". Every problem is
 * collected (not just the first) for a clearer failure message.
 *
 * Rejects, among other things: a non-object top level; `vulnerabilities` missing, `null`, an
 * array, a string, a number, or a boolean; and any individual `vulnerabilities[name]` entry that
 * is not itself an object with a string `severity` (a malformed entry could be hiding a real
 * HIGH/CRITICAL finding, so it is fail-closed rather than skipped).
 *
 * @returns {{ ok: boolean, problems: string[] }}
 */
export function validateAuditShape(auditJson) {
  const problems = [];

  if (!isPlainObject(auditJson)) {
    problems.push(`npm audit payload must be a JSON object, got ${describeType(auditJson)}`);
    return { ok: false, problems };
  }

  const vulnerabilities = auditJson.vulnerabilities;
  if (!isPlainObject(vulnerabilities)) {
    problems.push(
      `npm audit payload's "vulnerabilities" must be a JSON object, got ${describeType(vulnerabilities)}`,
    );
    return { ok: false, problems };
  }

  for (const [name, entry] of Object.entries(vulnerabilities)) {
    if (!isPlainObject(entry)) {
      problems.push(`vulnerabilities["${name}"] must be a JSON object, got ${describeType(entry)}`);
      continue;
    }
    if (typeof entry.severity !== "string") {
      problems.push(`vulnerabilities["${name}"].severity is missing or not a string`);
    }
    if (entry.via !== undefined && !Array.isArray(entry.via)) {
      problems.push(`vulnerabilities["${name}"].via is present but not an array`);
    }
  }

  return { ok: problems.length === 0, problems };
}

export function parseJsonSafely(raw) {
  try {
    return { ok: true, value: JSON.parse(raw) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * Walks `via` recursively. A string entry points at another key in `vulnerabilities` (an
 * indirect/transitive cause); an object entry is a real advisory leaf. Cycle-safe via `visited`.
 */
export function collectLeafAdvisories(name, vulnerabilities, visited = new Set()) {
  if (visited.has(name)) return [];
  visited.add(name);
  const entry = vulnerabilities[name];
  if (!entry || !Array.isArray(entry.via)) return [];

  const leaves = [];
  for (const item of entry.via) {
    if (typeof item === "string") {
      leaves.push(...collectLeafAdvisories(item, vulnerabilities, visited));
    } else if (item && typeof item === "object") {
      leaves.push({
        ghsa: extractGhsaId(item.url),
        severity: item.severity ?? null,
        name: item.name ?? name,
        title: item.title ?? null,
      });
    }
  }
  return leaves;
}

/** Maps package name -> every version resolved for it anywhere in the lockfile, read directly
 * from `package-lock.json`'s `packages` map rather than trusting npm audit's own version text. */
export function buildInstalledVersionIndex(lockfileJson) {
  const index = {};
  const packages = lockfileJson?.packages ?? {};
  const marker = "node_modules/";
  for (const [path, info] of Object.entries(packages)) {
    const lastIndex = path.lastIndexOf(marker);
    if (lastIndex === -1) continue; // the root workspace entry ("") etc.
    const name = path.slice(lastIndex + marker.length);
    if (!info || typeof info.version !== "string") continue;
    (index[name] ??= []).push(info.version);
  }
  return index;
}

export function isExceptionActive(exception, now) {
  const expiry = Date.parse(exception.expiresAt);
  return Number.isFinite(expiry) && now.getTime() < expiry;
}

/** An exception only neutralizes a leaf when the GHSA, the package name, the expiry AND the
 * exact installed version all agree -- any mismatch means the exception does not apply, and the
 * finding falls through to fail-closed. */
export function findPermittedException(leaf, exceptions, installedVersionsByPackage, now) {
  for (const exception of exceptions) {
    // extractGhsaId() always uppercases; exception.ghsa is written in GitHub's own mixed-case
    // display form (e.g. "GHSA-86w9-..."), so compare case-insensitively rather than require the
    // exception list to be written in a less-recognizable all-caps form.
    if (leaf.ghsa !== exception.ghsa.toUpperCase()) continue;
    if (leaf.name !== exception.package) continue;
    if (!isExceptionActive(exception, now)) continue;
    const versions = installedVersionsByPackage[exception.package] ?? [];
    if (versions.length === 0) continue;
    if (!versions.every((version) => version === exception.pinnedVersion)) continue;
    return exception;
  }
  return null;
}

/**
 * Pure evaluation: no process/filesystem access, fully deterministic given its inputs -- this is
 * what tools/security/audit-gate.test.mjs exercises without touching the live npm registry.
 *
 * @returns {{ ok: boolean, violations: string[], usedExceptions: AuditException[] }}
 */
export function evaluateAudit({ auditJson, lockfileJson, exceptions, now }) {
  const shape = validateAuditShape(auditJson);
  if (!shape.ok) {
    return {
      ok: false,
      violations: shape.problems.map((problem) => `invalid audit payload shape: ${problem}`),
      usedExceptions: [],
    };
  }

  const vulnerabilities = auditJson.vulnerabilities;
  const installedVersionsByPackage = buildInstalledVersionIndex(lockfileJson);
  const violations = [];
  const usedExceptions = new Map();

  for (const [name, entry] of Object.entries(vulnerabilities)) {
    if (!CONCERNING_SEVERITIES.has(entry?.severity)) continue;

    const leaves = collectLeafAdvisories(name, vulnerabilities);
    const concerning = leaves.filter((leaf) => CONCERNING_SEVERITIES.has(leaf.severity));

    if (concerning.length === 0) {
      violations.push(
        `${name}: severity "${entry.severity}" but no underlying HIGH/CRITICAL advisory leaf ` +
          "could be resolved from `via` (fail-closed: cannot prove this is covered by an exception)",
      );
      continue;
    }

    for (const leaf of concerning) {
      const permitted = findPermittedException(leaf, exceptions, installedVersionsByPackage, now);
      if (permitted) {
        usedExceptions.set(permitted.ghsa, permitted);
      } else {
        violations.push(
          `${name}: unpermitted ${leaf.severity} advisory ${leaf.ghsa ?? "(no GHSA in url)"} ` +
            `(${leaf.title ?? "no title"}) in package "${leaf.name}"`,
        );
      }
    }
  }

  return { ok: violations.length === 0, violations, usedExceptions: [...usedExceptions.values()] };
}

function runNpmAudit() {
  try {
    // execSync (not execFileSync): the whole point here is to run a shell command, and the
    // command string is a fixed literal with no interpolated/untrusted input, so there is no
    // injection surface -- this avoids both execFileSync's Windows "npm.cmd" resolution failure
    // and the args-array-plus-shell:true combination Node flags as DEP0190.
    return execSync("npm audit --json", {
      cwd: REPO_ROOT,
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
    });
  } catch (error) {
    // `npm audit` exits non-zero whenever it finds ANY vulnerability -- that is the expected,
    // normal case here (we do our own accept/reject logic below), and Node still attaches stdout
    // to the thrown error in that case. Only the absence of usable stdout is a real operational
    // failure (network/registry error, npm itself missing, etc.).
    if (typeof error.stdout === "string" && error.stdout.trim().length > 0) {
      return error.stdout;
    }
    throw new Error(
      `npm audit produced no usable output (operational failure, not an advisory): ${error.message}`,
    );
  }
}

function printExceptionBanner(exception) {
  console.warn(
    [
      "TEMPORARY SECURITY EXCEPTION ACTIVE",
      `  GHSA: ${exception.ghsa} (${exception.cve})`,
      `  package: ${exception.package}@${exception.pinnedVersion}`,
      `  expires: ${exception.expiresAt}`,
      `  reference: ${exception.reference}`,
      "  any other HIGH/CRITICAL advisory (known or new) still fails this gate.",
    ].join("\n"),
  );
}

export function main() {
  let rawAudit;
  try {
    rawAudit = runNpmAudit();
  } catch (error) {
    console.error(`AUDIT GATE: ${error.message}`);
    process.exitCode = 1;
    return;
  }

  const parsedAudit = parseJsonSafely(rawAudit);
  if (!parsedAudit.ok) {
    console.error(`AUDIT GATE: could not parse npm audit output as JSON: ${parsedAudit.error}`);
    process.exitCode = 1;
    return;
  }
  // Shape validation (null/array/string/number "vulnerabilities", malformed entries, ...) lives
  // entirely in evaluateAudit -> validateAuditShape, not duplicated here, so there is exactly one
  // place that can turn an invalid payload into a false "clean audit" -- and it is unit-tested
  // directly (tools/security/audit-gate.test.mjs).
  const auditJson = parsedAudit.value;

  let lockfileJson;
  try {
    lockfileJson = JSON.parse(readFileSync(resolve(REPO_ROOT, "package-lock.json"), "utf8"));
  } catch (error) {
    console.error(`AUDIT GATE: could not read/parse package-lock.json: ${error.message}`);
    process.exitCode = 1;
    return;
  }

  const result = evaluateAudit({ auditJson, lockfileJson, exceptions: EXCEPTIONS, now: new Date() });

  for (const exception of result.usedExceptions) {
    printExceptionBanner(exception);
  }

  if (!result.ok) {
    console.error("AUDIT GATE: FAIL");
    for (const violation of result.violations) {
      console.error(`  - ${violation}`);
    }
    process.exitCode = 1;
    return;
  }

  console.log("AUDIT GATE: PASS");
}

const isDirectlyExecuted = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectlyExecuted) {
  main();
}

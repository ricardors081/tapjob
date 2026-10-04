#!/usr/bin/env node
// Scoped, fail-closed replacement for the bare `npm audit --audit-level=high` CI step
// (docs/adr/0007-temporary-node-forge-audit-exception.md, D-047;
// docs/adr/0008-temporary-braces-audit-exception.md, D-049).
//
// Runs the REAL `npm audit`, parses its structured JSON, and fails on any HIGH/CRITICAL advisory
// EXCEPT the individually documented, scoped, exact-version, auto-expiring exceptions listed below
// -- and only while a given exception is (a) not expired and (b) the affected package is pinned to
// the exact version that exception was written against. Every other HIGH/CRITICAL finding -- known
// or not yet discovered -- still fails the build. This is not a blanket `--audit-level` downgrade
// and not a generic allowlist: each exception is one GHSA, one package, one pinned version, with an
// automatic expiry. `EXCEPTIONS` may hold more than one entry; it stays a short, individually-
// justified list (see `validateExceptionsConfig` below), never a growing suppression file.
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
  Object.freeze({
    ghsa: "GHSA-vfj7-8cjw-p6xm",
    cve: "CVE-2026-93687",
    package: "braces",
    pinnedVersion: "3.0.3",
    // Remove this entry (not just let it expire) the moment EITHER: braces publishes an official
    // patched release compatible with the current dependency chain, OR an official Expo/Jest/Metro
    // update stops resolving a vulnerable braces -- whichever comes first. As of this exception's
    // approval, braces@3.0.3 is still npm's published "latest" and the advisory itself lists no
    // patched version (docs/adr/0008-temporary-braces-audit-exception.md).
    expiresAt: "2026-11-04T00:00:00Z",
    reference: "docs/adr/0008-temporary-braces-audit-exception.md",
  }),
]);

/**
 * Validates the `EXCEPTIONS` list itself, before it is ever used to evaluate an audit.
 *
 * Each entry's own fields are checked first (non-empty strings for `ghsa`/`package`/
 * `pinnedVersion`/`reference`, a parseable `expiresAt`): a malformed entry must surface as an
 * explicit, deterministic problem here, never as an accidental `TypeError` (e.g. calling
 * `.toUpperCase()` on a non-string `ghsa`) part-way through evaluating a real audit.
 *
 * Two entries naming the same GHSA + package is always an ambiguous, fail-closed configuration
 * error -- whether they agree on `pinnedVersion` (redundant, and a sign an entry was duplicated by
 * mistake) or disagree (contradictory: at most one of the two can be the real pinned version).
 * This guards the exception list the same way `validateAuditShape` guards the audit payload: a
 * configuration mistake must surface as a hard failure, never as "whichever entry the loop happens
 * to reach first".
 *
 * @returns {{ ok: boolean, problems: string[] }}
 */
export function validateExceptionsConfig(exceptions) {
  const problems = [];
  const firstIndexByKey = new Map();

  exceptions.forEach((exception, index) => {
    const hasValidGhsa = typeof exception?.ghsa === "string" && exception.ghsa.length > 0;
    if (!hasValidGhsa) {
      problems.push(`exceptions[${index}].ghsa must be a non-empty string`);
    }
    const hasValidPackage = typeof exception?.package === "string" && exception.package.length > 0;
    if (!hasValidPackage) {
      problems.push(`exceptions[${index}].package must be a non-empty string`);
    }
    if (typeof exception?.pinnedVersion !== "string" || exception.pinnedVersion.length === 0) {
      problems.push(`exceptions[${index}].pinnedVersion must be a non-empty string`);
    }
    if (
      typeof exception?.expiresAt !== "string" ||
      !Number.isFinite(Date.parse(exception.expiresAt))
    ) {
      problems.push(`exceptions[${index}].expiresAt must be a parseable date string`);
    }
    if (typeof exception?.reference !== "string" || exception.reference.length === 0) {
      problems.push(`exceptions[${index}].reference must be a non-empty string`);
    }

    // The duplicate-pair check below needs a valid string ghsa/package to build a key from; an
    // entry already flagged above is skipped here rather than risking a second, confusing error
    // (or a crash) from keying on a malformed value.
    if (!hasValidGhsa || !hasValidPackage) return;

    const key = `${exception.ghsa.toUpperCase()}|${exception.package}`;
    if (firstIndexByKey.has(key)) {
      problems.push(
        `duplicate exception entries for ${exception.ghsa} / ${exception.package} at indices ` +
          `${firstIndexByKey.get(key)} and ${index} -- each GHSA+package pair must appear at most once`,
      );
    } else {
      firstIndexByKey.set(key, index);
    }
  });

  return { ok: problems.length === 0, problems };
}

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
 * array, a string, a number, or a boolean; any individual `vulnerabilities[name]` entry that is
 * not itself an object with a string `severity` (a malformed entry could be hiding a real
 * HIGH/CRITICAL finding, so it is fail-closed rather than skipped); and, per `via` item, anything
 * that is not EITHER a string reference OR a plain object carrying a string `severity`.
 *
 * This last check closes a real fail-open gap (2026-10-04 independent review, Finding 1):
 * `collectLeafAdvisories` only ever recognizes a `via` item as a leaf when it is a plain object,
 * and only ever counts a leaf as "concerning" when its `severity` is a recognized HIGH/CRITICAL
 * string. Before this check existed, a malformed item -- `null`, a number, an array, or an object
 * with a missing/non-string `severity` -- was silently skipped by `collectLeafAdvisories` and
 * silently filtered out of "concerning" by `evaluateAudit`, contributing neither a leaf NOR a
 * violation. If a *different*, legitimately-permitted leaf happened to sit alongside it in the
 * same `via` array, the whole entry could still read as fully explained and PASS -- a malformed
 * leaf must never be able to hide behind a valid one. Validating every item's shape up front, and
 * failing the whole audit on any violation, removes that gap entirely.
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
    if (entry.via === undefined) continue;
    if (!Array.isArray(entry.via)) {
      problems.push(`vulnerabilities["${name}"].via is present but not an array`);
      continue;
    }
    entry.via.forEach((item, index) => {
      if (typeof item === "string") return; // a reference to another vulnerabilities key
      if (!isPlainObject(item)) {
        problems.push(
          `vulnerabilities["${name}"].via[${index}] must be a string reference or an ` +
            `advisory-leaf object, got ${describeType(item)}`,
        );
        return;
      }
      if (typeof item.severity !== "string") {
        problems.push(
          `vulnerabilities["${name}"].via[${index}] is an advisory-leaf object but its severity ` +
            "is missing or not a string",
        );
      }
    });
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
 * Walks `via` to collect every HIGH/CRITICAL advisory leaf reachable from `name` -- the full
 * reachable set, not a single path -- treating `vulnerabilities` as a directed graph where a
 * string `via` item is an edge to another key and an object `via` item is a leaf attached to the
 * node currently being visited (its shape is already guaranteed by `validateAuditShape`, which
 * `evaluateAudit` always runs first).
 *
 * A cycle in this graph is NOT, by itself, an unresolved finding (2026-10-04 independent review:
 * real `npm audit --json` output for this repository contains genuine cycles, not just diamonds --
 * e.g. `metro` <-> `metro-config` <-> `metro-transform-worker` all name each other in `via` because
 * npm reports their shared root cause this way -- and an earlier version of this function that
 * failed closed on ANY revisited name broke the real gate even though no new, unpermitted advisory
 * was present). The only two things that make a causal branch "unresolved" are: a `via` STRING
 * naming a key that is not present in `vulnerabilities` at all (reported here), or -- enforced by
 * `evaluateAudit`, not here -- a HIGH/CRITICAL root for which this function finds no leaf at all
 * anywhere in its reachable set.
 *
 * `visited` is one set per top-level traversal (fresh for each root `evaluateAudit` evaluates): a
 * name is marked visited the moment its own frame starts, so revisiting it -- whether through a
 * diamond (a different parent reaching an already-completed node) or a true cycle (a back-edge to
 * a node whose own frame is still being explored further up the same call stack) -- simply means
 * "nothing further to add from this edge," never a double-count and never a dropped leaf: the
 * node's own leaves are collected once, from whichever call first reaches it, and every other edge
 * into the same strongly-connected component eventually reaches those same leaves through the
 * surviving, still-in-progress frame instead. Each node that genuinely has no leaf reachable from
 * it anywhere (a true dead end, e.g. a pure cycle with nothing in it) still makes that node's OWN
 * entry fail when `evaluateAudit` later evaluates that node as its own HIGH/CRITICAL root -- the
 * cycle does not need to be flagged a second time here to make that happen.
 *
 * @returns {{ leaves: Array<{ghsa: string|null, severity: string|null, name: string, title:
 *   string|null}>, unresolved: string[] }}
 */
export function collectLeafAdvisories(name, vulnerabilities, visited = new Set()) {
  if (visited.has(name)) return { leaves: [], unresolved: [] };
  visited.add(name);

  const entry = vulnerabilities[name];
  if (!entry) return { leaves: [], unresolved: [name] };
  if (!Array.isArray(entry.via)) return { leaves: [], unresolved: [] };

  const leaves = [];
  const unresolved = [];
  for (const item of entry.via) {
    if (typeof item === "string") {
      const nested = collectLeafAdvisories(item, vulnerabilities, visited);
      leaves.push(...nested.leaves);
      unresolved.push(...nested.unresolved);
    } else if (isPlainObject(item)) {
      leaves.push({
        ghsa: extractGhsaId(item.url),
        severity: item.severity ?? null,
        name: item.name ?? name,
        title: item.title ?? null,
      });
    }
    // Any other item shape is rejected earlier by `validateAuditShape`; `evaluateAudit` never
    // calls this function with a payload that still contains one.
  }
  return { leaves, unresolved };
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
  const exceptionsShape = validateExceptionsConfig(exceptions);
  if (!exceptionsShape.ok) {
    return {
      ok: false,
      violations: exceptionsShape.problems.map(
        (problem) => `invalid exceptions configuration: ${problem}`,
      ),
      usedExceptions: [],
    };
  }

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

    const { leaves, unresolved } = collectLeafAdvisories(name, vulnerabilities);
    const concerning = leaves.filter((leaf) => CONCERNING_SEVERITIES.has(leaf.severity));

    if (unresolved.length > 0) {
      const uniqueUnresolved = [...new Set(unresolved)];
      violations.push(
        `${name}: \`via\` references package(s) not present in this audit payload ` +
          `(${uniqueUnresolved.join(", ")}) -- cannot prove this ${entry.severity} finding is ` +
          "fully explained (fail-closed), regardless of any other leaf found alongside it",
      );
    }

    if (concerning.length === 0) {
      if (unresolved.length === 0) {
        violations.push(
          `${name}: severity "${entry.severity}" but no underlying HIGH/CRITICAL advisory leaf ` +
            "could be resolved from `via` (fail-closed: cannot prove this is covered by an exception)",
        );
      }
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

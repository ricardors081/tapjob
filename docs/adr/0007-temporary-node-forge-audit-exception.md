# ADR-0007 — Temporary, scoped CI exception for GHSA-86w9-cpqp-85rv (node-forge)

**Status:** Accepted (temporary; this is a security/CI-tooling decision, not an architectural one)
**Date:** 2026-10-02
**Decision register:** D-047
**Scope:** `tools/security/audit-gate.mjs` and the `Secret scan and dependency audit` CI job's `npm audit` step only. Does not change, supersede or depend on any WP0.2-B decision; WP0.2-B remains not started. Does not change WP0.2-A status; PR #10 owns the documentation close-out for that checkpoint. Numbered 0007, not 0005/0006: those two numbers are reserved for WP0.2-B's own ADRs (internal ports/capability model) and are intentionally left free by this document.

## Context

Independent review of PR #10 (a documentation-only WP0.2-A close-out) found `Secret scan and dependency audit` failing: `npm audit --audit-level=high` reports a new **HIGH** advisory, [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv) / CVE-2026-85393, against `node-forge` (RSA PKCS#1 v1.5 signature verification accepts extra nested `DigestAlgorithm` elements — CWE-347).

**Severity, reported inconsistently across sources at review time (2026-10-02):** GitHub's own advisory page currently shows only a CVSS v4 score, **8.7** (vector `CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N`); npm's own advisory data, as returned by `npm audit --json` run directly against this repository, reports CVSS v3.1, **7.5** (`CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N`). Both are "High" severity under either scheme; this ADR does not resolve why the two sources disagree on the numeric score (different scoring snapshot/methodology between GitHub's page and npm's registry advisory mirror is the likely explanation, not independently confirmed here) and records both rather than picking one.

Confirmed directly from `npm audit --json` and `package-lock.json` before writing this (not assumed from the advisory alone):

- resolved version: `node-forge@1.4.0`, the single node in the lockfile (`node_modules/node-forge`);
- npm's own advisory data reports a vulnerable range of `<=1.4.0` with no fixed version (`fixAvailable` only names a major `expo` bump, not a patched `node-forge`). The advisory's own description text states the issue affects node-forge "through 1.4.0". GitHub's advisory page's structured "Affected versions"/"Patched versions" fields were read inconsistently across checks during this review (its version-range metadata appears to load client-side); do not treat either a specific range or "Unknown" as this ADR's confirmed claim about those two specific UI fields — re-check the live page directly if that distinction matters;
- dependency chain: `apps/mobile` → `expo@57.0.26` → `@expo/cli@57.0.27` → `node-forge`, both directly and via `@expo/code-signing-certificates@0.0.6` → `node-forge`;
- every other **HIGH** entry `npm audit` reports (`@expo/cli`, `@expo/code-signing-certificates`, `@sentry/react-native`, `expo`) is high *only* because each depends, transitively, on this same `node-forge` advisory — none introduces a second, independent HIGH/CRITICAL advisory. The separate **MODERATE**-only advisories already present (`decode-uri-component`, `uuid`, `xcode`, and the `@expo/config*`/`expo-router`/`expo-splash-screen` chain depending on them) are unrelated and were already below the `--audit-level=high` threshold before this change;
- `npm audit`'s own suggested fix (`npm audit fix --force`) resolves to `expo@44.0.6` — a major, breaking downgrade of the entire Expo toolchain, which is explicitly rejected below;
- this is newly surfacing now, not something this repository previously ignored: the identical `package-lock.json` passed this same CI step cleanly on `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b` (WP0.2-A's post-merge CI, 2026-10-01). The advisory was evidently published/indexed to the npm advisory database between that run and this one; `npm audit` queries live advisory data, so an unchanged lockfile can start failing without any code change on this repository's side.

## Decision

Add one **temporary, scoped, fail-closed, auditable, auto-expiring** exception, implemented as plain Node.js with no new dependency, and keep it out of the already-separate WP0.2-A documentation PR (#10).

### Rejected alternatives (and why)

- **`npm audit fix --force`:** forces `expo@44.0.6`, a major breaking downgrade of the entire mobile toolchain (Expo SDK 57-era dependencies → Expo SDK 44-era dependencies, per the fix plan's own output). Unacceptable blast radius for a dependency-audit fix.
- **Downgrade or replace Expo:** same problem; Expo is an accepted baseline (D-005, ADR-0002), not up for revision here.
- **Disable the security gate / lower `--audit-level`:** would hide every future HIGH/CRITICAL finding, not just this one — the opposite of fail-closed.
- **Generic allowlist (e.g. ignore all `node-forge` or all `@expo/*` findings):** would silently swallow an unrelated future advisory in the same package or chain.
- **Fork/unofficial `node-forge` replacement, or pinning to an unreleased commit:** introduces unvetted supply-chain risk strictly worse than the advisory being mitigated. Rejected per explicit instruction.
- **Do nothing / mark the check "expected failure" in CI config:** not auditable, not scoped, no expiry, and would mask a real signal for every future PR indefinitely.

### What the exception actually is

`tools/security/audit-gate.mjs` replaces the bare `npm audit --audit-level=high` CI step. It:

1. still runs the real `npm audit --json` (nothing is mocked/skipped in CI);
2. parses the structured JSON (never regexes the human-readable text output);
3. for every package npm audit marks HIGH or CRITICAL, recursively resolves `via` down to its real advisory leaves (an entry's `via` array mixes plain package-name strings — "caused transitively by this other package's finding" — with advisory-leaf objects carrying a GHSA URL/severity; the walk follows the strings and collects the leaves);
4. a leaf only counts as covered when **all** of (a) its GHSA equals `GHSA-86w9-cpqp-85rv`, (b) its package name equals `node-forge`, (c) the exception has not passed its expiry, and (d) **every** resolved installed version of `node-forge` in `package-lock.json` equals exactly `1.4.0` — read from the lockfile directly, not trusted from npm audit's own text;
5. any HIGH/CRITICAL leaf that fails any one of those four conditions — a different GHSA, a different package, an expired exception, or `node-forge` having moved to a different version while the advisory somehow still applies — fails the gate. So does an unparseable npm audit output, a missing `vulnerabilities` object, or a HIGH/CRITICAL entry whose `via` chain resolves no advisory leaf at all (nothing here is a blanket `exit 0`);
6. MODERATE-only findings are left alone, same practical behavior as the `--audit-level=high` step it replaces;
7. when the exception is actually used, the job log prints an explicit `TEMPORARY SECURITY EXCEPTION ACTIVE` banner naming the GHSA, CVE, package@version, expiry and this ADR — never a secret.

`tools/security/audit-gate.test.mjs` covers the logic deterministically (clean audit; the permitted GHSA before/after expiry; a different HIGH; a different CRITICAL; the permitted GHSA alongside an unrelated HIGH; `node-forge` resolved to a different version; the package missing from the lockfile; an exclusively-permitted transitive chain; a transitive chain containing an unrelated HIGH; an unexplained HIGH; a cyclic `via` chain) against hand-built JSON fixtures and an injected `now` — no live registry access, so these tests run the same way every time regardless of what the npm advisory database currently contains. CI additionally runs the real `npm audit` through the gate, so both the logic and the live result are verified on every run.

### Exposure assessment

**Confirmed by this review:**
- TapJob's lockfile resolves `node-forge@1.4.0`;
- the entire dependency chain to it is Expo's own CLI/build tooling (`expo` → `@expo/cli` → `node-forge`, both directly and via `@expo/code-signing-certificates`), not a package TapJob depends on directly;
- the real `npm audit` run against this repository's lockfile flags this installed version against this GHSA;
- no import of `node-forge` by TapJob's own Career Core backend/worker code (`services/api`, `services/worker`) was found in this review;
- this review found no evidence that the vulnerable code path is invoked as part of TapJob's own business/runtime behavior (as opposed to Expo's own CLI tooling operations, e.g. `expo export`, `expo-doctor`).

**Explicitly not claimed (avoiding overclaim):**
- this ADR does **not** assert that no external input can ever reach `node-forge`'s vulnerable signature-verification code path — that was not tested;
- this ADR does **not** assert that `@expo/code-signing-certificates` is the only consumer of `node-forge` in this chain: `npm audit`'s own `via` listing shows `@expo/cli` depends on `node-forge` directly as well, not only through `@expo/code-signing-certificates`;
- this ADR does **not** make any claim about whether `node-forge`'s code is or is not included in a built mobile app bundle — that was not verified here, and the scope of this ADR is the CI dependency-audit gate, not a bundle-contents audit.

The exception is scoped to exactly one package, one version, one advisory; it cannot silently cover a future, different `node-forge` advisory or a different package's HIGH/CRITICAL finding (tested).

## Expiry and removal

- **Hard expiry:** `2026-11-02T00:00:00Z`, enforced in code (`isExceptionActive`), not by a comment someone has to remember. After this instant, if the advisory is still present, the gate fails closed automatically — no manual step required to re-enable enforcement.
- **Earlier removal condition (whichever comes first):** remove the exception entry from `tools/security/audit-gate.mjs` as soon as a compatible, non-breaking Expo tooling release resolves a patched `node-forge` (or drops the dependency). Re-run `npm audit` after any Expo-related dependency bump to confirm before removing.
- **If the advisory disappears from `npm audit` before the expiry** (e.g. Dependabot/a manual bump already fixes it), the gate simply finds no HIGH/CRITICAL entry to evaluate and passes normally — the exception sits unused, harmlessly, until removed.
- **Rollback:** delete the single entry from the `EXCEPTIONS` array in `tools/security/audit-gate.mjs` (and, optionally, revert the CI step back to a bare `npm audit --audit-level=high`) — the gate script itself can stay as reusable infrastructure for any future scoped exception.

## Governance record

- GHSA: `GHSA-86w9-cpqp-85rv`
- CVE: `CVE-2026-85393`
- Package / version: `node-forge@1.4.0`
- Severity: High. GitHub Advisory currently reports CVSS v4 8.7 (verified 2026-10-02); npm's own advisory data reports CVSS v3.1 7.5 for the same advisory — recorded as-is, not reconciled, see Context
- Owner: TapJob maintainers / Product Owner
- Approved: 2026-10-02, by the Product Owner (per the instruction authorizing this ADR)
- Review/expiry date: `2026-11-02T00:00:00Z`
- Removal condition: compatible official fix available, or expiry, whichever is first
- Upstream reference: https://github.com/advisories/GHSA-86w9-cpqp-85rv

## Consequences

- This is intentionally **not** a precedent for a general-purpose "ignore this advisory" mechanism: `EXCEPTIONS` in `tools/security/audit-gate.mjs` is meant to stay a short, individually-justified, individually-expiring list, each entry documented the way this one is — not a growing suppression file.
- Does not touch `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` or WP0.2-A/B status; those are governed entirely by PR #9/#10 and remain unaffected by this security-tooling PR.
- Does not reuse or collide with ADR-0005/ADR-0006, reserved for WP0.2-B's internal-ports decision; this is ADR-0007, the next free number after those two reservations, and is explicitly a temporary CI/security-tooling exception, not a product architecture decision.

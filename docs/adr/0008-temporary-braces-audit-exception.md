# ADR-0008 — Temporary, scoped CI exception for GHSA-vfj7-8cjw-p6xm (braces)

**Status:** Accepted (temporary; this is a security/CI-tooling decision, not an architectural one)
**Date:** 2026-10-04
**Decision register:** D-049
**Scope:** `tools/security/audit-gate.mjs` and the `Secret scan and dependency audit` CI job's `npm audit` step only. Does not change, supersede or depend on any WP0.2-B decision; Checkpoint B remains NOT STARTED. Does not reuse or collide with ADR-0006 (reserved for WP0.2-B Checkpoint B's deployment/CD contract) or ADR-0007 (the existing, separate, still-active node-forge exception, D-047) — this is a second, independent entry in the same `EXCEPTIONS` array, not a replacement for it.

## Context

Independent review of PR #13 (a documentation-only WP0.2-B Checkpoint A close-out) found the post-merge CI run on `main` (`66f418df438f6eb9f6bf33fed84535240542f59b`, run `37215698801`) failing: the `npm dependency audit` step correctly recognized the existing node-forge exception as still active, but failed on a **new, previously unpermitted** HIGH advisory against `braces`.

This was **not introduced by PR #13** — that PR touched only `README.md`, `docs/IMPLEMENTATION_PLAN.md` and `docs/PROJECT_STATE.md`. The audit-gate design is working exactly as documented in ADR-0007: `npm audit` queries live advisory data, so an unchanged lockfile can start failing without any code change on this repository's side, the same way the node-forge advisory itself first surfaced during PR #10.

### The advisory

- **GHSA:** [`GHSA-vfj7-8cjw-p6xm`](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
- **CVE:** `CVE-2026-93687`
- **Title:** "braces vulnerable to stack-exhaustion denial of service through deeply nested patterns"
- **Severity:** High. CVSS v4 **8.7** (vector `CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:N` — availability-impact High, consistent with a DoS), per GitHub's advisory page, independently re-fetched and confirmed on 2026-10-04.
- **CWE:** `CWE-674` (Uncontrolled Recursion).
- **Published:** 2026-09-18 (per the advisory page).
- **Affected versions:** `<= 3.0.3`. **Patched versions:** none listed.
- **Mechanism:** recursive AST walkers in `braces` (`lib/compile.js`, `lib/expand.js`, `lib/parse.js`) lack a depth guard; a sufficiently deeply nested brace-expansion pattern exhausts the call stack and crashes the Node.js process with an uncaught `RangeError` — a process-crash denial of service, not remote code execution or data exposure.

### Confirmed directly from this repository's own `npm audit --json` and `package-lock.json` (not assumed from the advisory alone)

- `package-lock.json` resolves exactly one installed version of `braces`: **`braces@3.0.3`** (single flattened `node_modules/braces` entry — no scoped/nested duplicates to reconcile);
- the only direct dependent of `braces` anywhere in the lockfile is `micromatch@4.0.8` (`node_modules/micromatch`'s `dependencies.braces` = `^3.0.3`), itself resolved to exactly one version;
- the direct dependents of `micromatch` in the lockfile are: `@expo/metro-file-map`, `@jest/core`, `@jest/transform`, `jest-config`, `jest-haste-map`, `jest-message-util`, `metro-file-map` — i.e. every path to `braces` runs through either Expo's own CLI/bundler tooling (`@expo/metro-file-map`, consumed by `@expo/cli`, consumed by `expo`) or Jest's test-runner internals (`jest`/`jest-cli`/`jest-config`, declared as `apps/mobile`'s own `devDependencies`), never through a package TapJob's product code imports directly;
- `npm audit --json` run directly against this repository resolves exactly **two** distinct HIGH/CRITICAL advisory leaves across all flagged packages: the already-permitted `GHSA-86w9-cpqp-85rv` (node-forge) and this new `GHSA-vfj7-8cjw-p6xm` (braces). No third, unexplained HIGH/CRITICAL advisory is present (independently verified by walking every flagged package's `via` chain to its advisory leaves and collecting the distinct set);
- `npm audit`'s own `fixAvailable` for every `braces`-chain entry points at a **major, breaking** bump (`jest@30.5.2` or, via the Expo chain, `expo@44.0.6` — the same breaking downgrade ADR-0007 already rejected for node-forge). None of these proposed "fixes" actually resolve to a patched `braces`: the advisory's own data states no patched version exists at all, so any `npm audit fix` path still installs the same vulnerable `braces@3.0.3` — it only changes which major version of an unrelated tool (Jest, Expo) gets pulled in along the way.

### Upstream status (independently re-confirmed on 2026-10-04, not assumed from this task's prompt)

- `braces@3.0.3` is still npm's published `latest` (`npm view braces version` → `3.0.3`); `npm view braces versions` lists no version beyond `3.0.3`.
- `micromatch@4.0.8` (npm's published `latest`) still declares `braces: "^3.0.3"` — there is no newer, compatible `micromatch` that resolves a non-vulnerable `braces`.
- [`micromatch/braces#73`](https://github.com/micromatch/braces/issues/73) ("CVE-2026-93687 affects 3.0.3 with no fixed release available"): **open**, confirms 3.0.3 remains latest and states "There is no release to upgrade to, so every consumer reaching braces through `micromatch` is left with a permanent high-severity finding and no remediation path."
- [`micromatch/braces#72`](https://github.com/micromatch/braces/pull/72) ("fix: prevent stack overflow from deeply nested patterns (CVE-2026-93687)"): **open, not merged, not released to npm** as of 2026-10-04. The fix author reports having e-mailed the maintainers who can approve the merge, "no answer so far in the past 9 days" (as of 2026-09-30/10-03 comments on the PR).
- No compatible, non-breaking official release exists anywhere in this dependency chain that resolves a patched `braces`.

## Decision

**Route B** (no official fix available): add a **second, independent, temporary, scoped, fail-closed, auditable, auto-expiring** exception to the already-existing `EXCEPTIONS` array in `tools/security/audit-gate.mjs` — the same mechanism ADR-0007/D-047 established, not a new gate, not a modification to how the node-forge exception is evaluated.

### Rejected alternatives (and why)

- **`npm audit fix --force` / accepting the suggested major bump:** every `fixAvailable` entry in this chain is `isSemVerMajor: true` (a `jest@30.5.2` or `expo@44.0.6` bump) and, per the advisory's own "no patched version" statement, would still resolve the same vulnerable `braces@3.0.3` underneath — it trades an unrelated breaking upgrade for zero actual remediation. Rejected, same reasoning as ADR-0007.
- **Downgrade or pin a different `braces`/`micromatch` version via `overrides`:** impossible to do safely — every published version of `braces` (verified via `npm view braces versions`) is `<= 3.0.3`, and the advisory's affected range is `<= 3.0.3` with no patched version at any point in that history. There is no non-vulnerable version to override to.
- **Pin to the unreleased upstream fix commit/branch (`micromatch/braces#72`) directly from GitHub:** explicitly rejected per standing instruction — an unreleased, unpublished, unreviewed-by-maintainers commit is unvetted supply-chain risk, strictly worse than a documented, scoped, expiring audit exception against a known, widely-used release.
- **Disable the security gate / lower `--audit-level`:** would hide every future HIGH/CRITICAL finding, not just this one — the opposite of fail-closed, and would also silently remove the still-needed node-forge coverage.
- **Generic allowlist (e.g. ignore all `braces` or all `micromatch`/`@expo/metro-file-map` findings):** would silently swallow an unrelated future advisory in the same package or chain — exactly what `validateExceptionsConfig` (added in this change) and the exact-GHSA/exact-package/exact-version matching already guard against.
- **Do nothing / mark the check "expected failure" in CI config:** not auditable, not scoped, no expiry, and would mask a real signal — including any future, different advisory in the same package — for every subsequent PR indefinitely.

### What changed in `tools/security/audit-gate.mjs`

1. A second entry was added to `EXCEPTIONS` (see Governance record below for the exact fields) — the array now holds two independent exceptions, each still scoped to one GHSA, one package, one pinned version, with its own expiry.
2. A new pure function, `validateExceptionsConfig(exceptions)`, validates the `EXCEPTIONS` list itself before it is ever used: two entries naming the same GHSA + package — whether they agree or disagree on `pinnedVersion` — is always a fail-closed configuration error (ambiguous/contradictory, never silently resolved by "whichever the loop reaches first"). `evaluateAudit` calls this before anything else and fails the whole gate if it finds a problem.
3. `findPermittedException`'s existing per-leaf matching logic (GHSA, package name, expiry, exact installed version must all agree) is **unchanged** — it already worked generically over however many exceptions `EXCEPTIONS` holds; it did not need new logic to support a second entry, only the new list-level validation above.
4. The node-forge exception entry (ADR-0007, D-047) is **untouched**: same GHSA, same package, same pinned version, same expiry (`2026-11-02T00:00:00Z`), same reference. This ADR does not weaken, extend or reinterpret it.

### Exposure assessment

**Confirmed by this review:**
- `package-lock.json` resolves exactly `braces@3.0.3`, reached through exactly one path: `micromatch@4.0.8` → `braces@3.0.3`;
- the direct consumers of `micromatch` in the lockfile are `@expo/metro-file-map` (pulled in by `@expo/cli`, pulled in by `expo`) and a cluster of Jest internals (`@jest/core`, `@jest/transform`, `jest-config`, `jest-haste-map`, `jest-message-util`, pulled in by `jest`/`jest-cli`, which `apps/mobile/package.json` declares only as `devDependencies`, alongside `jest-expo`/`@types/jest`);
- a repository-wide search (`micromatch`/`braces` imported directly, outside `node_modules`) found **zero** matches: no file in TapJob's own source imports either package directly;
- `metro-file-map`/`jest-haste-map` are file-system-crawling tooling that runs in a Node.js process on the build/CI machine during `expo export`/`expo-doctor`/`npm test` — not code that TapJob's own application logic calls at request-handling time; the brace/glob patterns they process come from static, developer-authored configuration (Metro/Jest config files), not from any external or user-supplied input;
- TapJob currently has **no production functionality** (`docs/PROJECT_STATE.md` §2: "No production functionality exists yet... no domain model, table or business endpoint") and, specifically, no implemented feature anywhere in this repository that accepts an external actor's string and forwards it into a glob/brace-expansion pattern evaluated by `micromatch`/`braces`.

**Explicitly not claimed (avoiding overclaim):**
- this ADR does **not** assert that `metro-file-map`'s or Jest's internal file-crawling code can never execute in a context exposed to attacker-influenced input in some future configuration — only that no such path exists in this repository today, and that was checked by direct source search, not inferred;
- this ADR does **not** assert that no part of the `@expo/cli`/Metro toolchain's output artifacts ever embeds `braces`' code in a built bundle — bundle-contents inspection was out of scope here (same limitation ADR-0007 already states for `node-forge`);
- this ADR does **not** claim the DoS is impossible to trigger in CI itself: a crafted, extremely deeply nested filename or ignore-pattern placed in this repository's own tree could in principle crash a local `jest`/`expo export` run. This is a contributor-trust question (anyone who can add such a file already has write access to the repository), not an external-attacker surface, and is unchanged by this exception either way.

The exception is scoped to exactly one package, one version, one advisory; `validateExceptionsConfig` plus the existing per-leaf matching (tested exhaustively in `tools/security/audit-gate.test.mjs`) prove it cannot silently cover a future, different `braces` advisory, a different package's HIGH/CRITICAL finding, or a version drift away from `3.0.3`.

## Expiry and removal

- **Hard expiry:** `2026-11-04T00:00:00Z`, enforced in code (`isExceptionActive`), independent of the node-forge exception's own `2026-11-02T00:00:00Z` expiry. After this instant, if the advisory is still present, the gate fails closed automatically — no manual step required to re-enable enforcement.
- **Earlier removal condition (whichever comes first):** remove this entry from `tools/security/audit-gate.mjs` as soon as EITHER (a) `braces` (or a `micromatch` release that no longer depends on a vulnerable `braces`) publishes an official, compatible patched release, OR (b) an official Expo/Jest/Metro update stops resolving a vulnerable `braces` in this repository's dependency graph. Re-run `npm audit` after any such bump to confirm before removing.
- **If the advisory disappears from `npm audit` before the expiry** (e.g. a dependency bump already resolves it), the gate simply finds no HIGH/CRITICAL entry to evaluate for this package and passes normally — the exception sits unused, harmlessly, until removed.
- **Rollback:** delete this single entry from the `EXCEPTIONS` array in `tools/security/audit-gate.mjs`; the node-forge exception and the gate script itself are unaffected and remain in place.

## Governance record

- GHSA: `GHSA-vfj7-8cjw-p6xm`
- CVE: `CVE-2026-93687`
- Package / version: `braces@3.0.3`
- Severity: High. CVSS v4 **8.7** (verified 2026-10-04, re-fetched independently from GitHub's advisory page)
- CWE: `CWE-674` (Uncontrolled Recursion)
- Owner: TapJob maintainers / Product Owner
- Approved: 2026-10-04, by the Product Owner (per the instruction authorizing this ADR)
- Review/expiry date: `2026-11-04T00:00:00Z`
- Removal condition: compatible official fix available (either in `braces` itself or in the Expo/Jest/Metro chain that resolves it), or expiry, whichever is first
- Upstream references: https://github.com/advisories/GHSA-vfj7-8cjw-p6xm, https://github.com/micromatch/braces/issues/73, https://github.com/micromatch/braces/pull/72

## Consequences

- `EXCEPTIONS` now holds two entries. This is intentionally still **not** a precedent for a general-purpose "ignore findings" mechanism: each entry remains individually scoped, individually justified and individually expiring, and `validateExceptionsConfig` now enforces — as a tested, fail-closed invariant, not just a convention — that the list can never contain an ambiguous or contradictory pair of entries for the same GHSA + package.
- Does not touch `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` or WP0.2-B status; Checkpoint A remains VERIFIED (PR #12/#13) and Checkpoint B remains NOT STARTED, governed entirely by those separate PRs.
- Does not reuse or collide with ADR-0006 (reserved for WP0.2-B Checkpoint B's deployment/CD contract) or renumber/modify ADR-0007; this is ADR-0008, the next free number.

#!/usr/bin/env node
// Proves the EXACT commit SHA about to be released already had its own CI run complete with
// SUCCESS -- not "main was merged, so it must be green" (2026 review, Finding 1; the real incident
// this guards against: PR #13 merged cleanly, but a post-merge audit-gate failure on a LATER commit
// was discovered only afterward -- "main is green right now" never implied "this specific commit's
// own CI run succeeded"). `.github/workflows/release-image.yml` always builds whatever commit is at
// the tip of `main` at run time (no free-form ref input, Finding 1A/1B); this gate is the one check
// standing between that commit and a real GHCR push if its own CI run was never green.
//
// `evaluateCiRequirement` is pure (no network/process access) so it is fully unit-testable without
// executing the release workflow itself (`tools/deploy/require_ci_success.test.mjs`) -- mirroring
// `tools/security/audit-gate.mjs`'s evaluate-then-fetch split. `buildWorkflowRunsApiArgs` is
// likewise pure, so the exact `gh api` invocation (method, endpoint, fields) is unit-tested too,
// not just the decision logic (2026 review round 3: an earlier version omitted `--method GET`,
// which `gh api` would have silently turned into a POST the moment `-f` fields were added --
// read-only by intent, but operationally broken against a GET-only endpoint).

import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const CI_WORKFLOW_FILE = "ci.yml";

function describeType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/**
 * Validates the exact shape `evaluateCiRequirement` depends on for ONE workflow-run entry, before
 * any accept/reject decision (2026 review round 3, mirroring `tools/security/audit-gate.mjs`'s
 * `validateAuditShape`). A matching run is never trusted just because `status`/`conclusion`
 * happen to read "completed"/"success" -- `run_number` must be a real integer (it decides which
 * run is "latest", so a malformed one could silently pick the wrong run as authoritative) and
 * `status`/`conclusion` must be the types the GitHub API actually documents (`conclusion` is
 * `null` until a run completes, a string afterward). A structurally malformed matching entry
 * fails the whole gate closed, rather than being skipped or silently out-ranked by `-Infinity`.
 *
 * @returns {{ ok: boolean, problems: string[] }}
 */
export function validateWorkflowRunShape(run, index) {
  const problems = [];
  if (typeof run?.head_sha !== "string" || run.head_sha.length === 0) {
    problems.push(`workflow_runs[${index}].head_sha must be a non-empty string, got ${describeType(run?.head_sha)}`);
  }
  if (!Number.isInteger(run?.run_number)) {
    problems.push(`workflow_runs[${index}].run_number must be an integer, got ${describeType(run?.run_number)}`);
  }
  if (typeof run?.status !== "string" || run.status.length === 0) {
    problems.push(`workflow_runs[${index}].status must be a non-empty string, got ${describeType(run?.status)}`);
  }
  if (run?.conclusion !== null && typeof run?.conclusion !== "string") {
    problems.push(`workflow_runs[${index}].conclusion must be a string or null, got ${describeType(run?.conclusion)}`);
  }
  return { ok: problems.length === 0, problems };
}

/**
 * @param {unknown} workflowRuns - the `workflow_runs` array from GitHub's "List workflow runs"
 *   API response (`GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs`).
 * @param {{ sha: string }} options
 * @returns {{ ok: boolean, reason: string }}
 */
export function evaluateCiRequirement(workflowRuns, { sha } = {}) {
  if (typeof sha !== "string" || sha.length === 0) {
    return { ok: false, reason: "sha must be a non-empty string" };
  }
  if (!Array.isArray(workflowRuns)) {
    return {
      ok: false,
      reason: `workflow runs payload must be an array, got ${describeType(workflowRuns)}`,
    };
  }

  const matching = workflowRuns
    .map((run, index) => ({ run, index }))
    .filter(({ run }) => run != null && run.head_sha === sha);
  if (matching.length === 0) {
    return { ok: false, reason: `no CI run found for commit ${sha}` };
  }

  for (const { run, index } of matching) {
    const shape = validateWorkflowRunShape(run, index);
    if (!shape.ok) {
      return {
        ok: false,
        reason: `malformed CI run payload for commit ${sha} (fail-closed): ${shape.problems.join("; ")}`,
      };
    }
  }

  // Several runs can exist for the same commit (e.g. a manual re-run after a transient failure).
  // The LATEST one by run_number is what must be green -- an earlier success followed by a later
  // failure for the same commit is a regression, never satisfied by the earlier run alone. Every
  // entry's run_number is already confirmed a real integer above, so this comparison needs no
  // further defensive fallback.
  const latest = matching.reduce((best, entry) =>
    entry.run.run_number > best.run.run_number ? entry : best,
  ).run;

  if (latest.status !== "completed") {
    return {
      ok: false,
      reason: `latest CI run for ${sha} (#${latest.run_number}) is not completed (status: ${latest.status})`,
    };
  }
  if (latest.conclusion !== "success") {
    return {
      ok: false,
      reason: `latest CI run for ${sha} (#${latest.run_number}) did not succeed (conclusion: ${latest.conclusion})`,
    };
  }

  return {
    ok: true,
    reason: `latest CI run #${latest.run_number} for ${sha} completed with conclusion "success"`,
  };
}

/**
 * Builds the exact argument array passed to `gh api` to list this repository's `ci.yml` runs for
 * one commit. Pure and unit-tested (2026 review round 3, Finding: `gh api` silently switches its
 * HTTP method from GET to POST the moment any `-f`/`-F` field is given, UNLESS `--method GET` is
 * explicit -- the endpoint this gate calls,
 * `GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs`, is GET-only and would 404/405
 * under POST, which would have made this gate fail-closed but operationally broken on its first
 * real invocation. Extracting this as its own pure function means the exact call shape -- method,
 * endpoint, and which fields are sent -- is a deterministic test away from being caught, rather
 * than only surfacing the first time `release-image.yml` actually runs.
 *
 * @returns {string[]}
 */
export function buildWorkflowRunsApiArgs(sha, repo, { perPage = 100 } = {}) {
  return [
    "api",
    "--method",
    "GET",
    `repos/${repo}/actions/workflows/${CI_WORKFLOW_FILE}/runs`,
    "-f",
    `head_sha=${sha}`,
    "-f",
    "event=push",
    "-f",
    `per_page=${perPage}`,
  ];
}

function fetchWorkflowRuns(sha, repo) {
  // `gh api` picks up GH_TOKEN/GITHUB_TOKEN from the environment automatically -- no PAT, no
  // token handling in this script itself. `event=push` scopes this to the same trigger CI itself
  // runs on for a commit landing on `main` (docs/adr/0006-...md); a workflow_dispatch/pull_request
  // run of CI for the same SHA does not satisfy this gate. execFileSync with an argument array (not
  // execSync with an interpolated string) so `sha` is never parsed by a shell, regardless of what
  // it contains.
  const raw = execFileSync("gh", buildWorkflowRunsApiArgs(sha, repo), {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  const parsed = JSON.parse(raw);
  return parsed.workflow_runs;
}

export function main() {
  // Both values are taken as CLI arguments, not via `process.env` (docs/ENGINEERING_STANDARDS.md
  // §5: only apps/mobile's own typed public config may read `process.env` directly) -- the caller
  // (the release workflow) passes its own `github.repository`/captured source SHA in via ordinary
  // shell substitution instead, the same convention `tools/deploy/validate_image_ref.mjs` follows.
  const sha = process.argv[2];
  const repo = process.argv[3];
  if (typeof sha !== "string" || sha.length === 0 || typeof repo !== "string" || repo.length === 0) {
    console.error(
      "REQUIRE CI SUCCESS: usage: node tools/deploy/require_ci_success.mjs <commit-sha> <owner/repo>",
    );
    process.exitCode = 1;
    return;
  }

  let workflowRuns;
  try {
    workflowRuns = fetchWorkflowRuns(sha, repo);
  } catch (error) {
    console.error(`REQUIRE CI SUCCESS: could not fetch CI workflow runs: ${error.message}`);
    process.exitCode = 1;
    return;
  }

  const result = evaluateCiRequirement(workflowRuns, { sha });
  if (!result.ok) {
    console.error(`REQUIRE CI SUCCESS: FAIL (${result.reason})`);
    process.exitCode = 1;
    return;
  }

  console.log(`REQUIRE CI SUCCESS: PASS (${result.reason})`);
}

const isDirectlyExecuted = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectlyExecuted) {
  main();
}

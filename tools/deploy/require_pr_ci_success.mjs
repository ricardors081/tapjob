#!/usr/bin/env node
// Verifies that a specific Pull Request belongs to the canonical repository, is currently open,
// resolves its exact remote HEAD commit SHA, and proves that exact commit's own CI run completed
// with SUCCESS.
//
// Designed strictly for .github/workflows/preview-image.yml to build non-production preview images
// for controlled staging validation.
//
// Evaluates pure logic in evaluatePrCiRequirement and validatePrPayload for deterministic unit testing
// without live network calls.

import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const CI_WORKFLOW_FILE = "ci.yml";

function describeType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/**
 * Validates the exact shape of a GitHub Pull Request payload.
 *
 * @param {unknown} pr - parsed JSON from GET /repos/{owner}/{repo}/pulls/{pull_number}
 * @param {{ expectedRepo?: string, expectedPrNumber?: number }} [options]
 * @returns {{ ok: boolean, problems: string[], headSha?: string }}
 */
export function validatePrPayload(pr, { expectedRepo, expectedPrNumber } = {}) {
  const problems = [];
  if (typeof pr !== "object" || pr === null) {
    return { ok: false, problems: [`PR payload must be an object, got ${describeType(pr)}`] };
  }

  const prNum = pr.number;
  if (!Number.isInteger(prNum)) {
    problems.push(`pr.number must be an integer, got ${describeType(prNum)}`);
  } else if (expectedPrNumber !== undefined && prNum !== expectedPrNumber) {
    problems.push(`pr.number (${prNum}) does not match expected PR number (${expectedPrNumber})`);
  }

  if (pr.state !== "open") {
    problems.push(`PR #${prNum ?? "unknown"} must be in 'open' state, got '${pr.state}'`);
  }

  const head = pr.head;
  if (typeof head !== "object" || head === null) {
    problems.push(`pr.head must be an object, got ${describeType(head)}`);
  } else {
    if (typeof head.sha !== "string" || !/^[0-9a-f]{40}$/i.test(head.sha)) {
      problems.push(`pr.head.sha must be a 40-character commit hex string, got ${describeType(head?.sha)}`);
    }

    const headRepo = head.repo;
    if (typeof headRepo !== "object" || headRepo === null) {
      problems.push(`pr.head.repo must be an object, got ${describeType(headRepo)}`);
    } else if (expectedRepo && headRepo.full_name !== expectedRepo) {
      problems.push(
        `PR #${prNum} head repository ('${headRepo.full_name}') does not match canonical repository ('${expectedRepo}'). Fork builds are rejected for preview security.`,
      );
    }
  }

  if (problems.length > 0) {
    return { ok: false, problems };
  }

  return { ok: true, problems: [], headSha: pr.head.sha };
}

/**
 * Validates the exact shape evaluatePrCiRequirement depends on for ONE workflow-run entry.
 *
 * @param {any} run
 * @param {number} index
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
 * Pure evaluation function combining PR validation and exact commit CI verification.
 *
 * @param {unknown} prPayload
 * @param {unknown} workflowRuns
 * @param {{ expectedRepo?: string, expectedPrNumber?: number }} [options]
 * @returns {{ ok: boolean, reason: string, prNumber?: number, headSha?: string, runNumber?: number, runId?: number }}
 */
export function evaluatePrCiRequirement(prPayload, workflowRuns, { expectedRepo, expectedPrNumber } = {}) {
  const prValidation = validatePrPayload(prPayload, { expectedRepo, expectedPrNumber });
  if (!prValidation.ok) {
    return {
      ok: false,
      reason: `PR validation failed (fail-closed): ${prValidation.problems.join("; ")}`,
    };
  }

  const sha = prValidation.headSha;
  const prNum = prPayload.number;

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
    return { ok: false, reason: `no CI run found for commit ${sha} on PR #${prNum}` };
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
    prNumber: prNum,
    headSha: sha,
    runNumber: latest.run_number,
    runId: latest.id,
    reason: `latest CI run #${latest.run_number} for PR #${prNum} HEAD ${sha} completed with conclusion "success"`,
  };
}

export function buildPrApiArgs(prNumber, repo) {
  return [
    "api",
    "--method",
    "GET",
    `repos/${repo}/pulls/${prNumber}`,
  ];
}

export function buildWorkflowRunsApiArgs(sha, repo, { perPage = 100 } = {}) {
  return [
    "api",
    "--method",
    "GET",
    `repos/${repo}/actions/workflows/${CI_WORKFLOW_FILE}/runs`,
    "-f",
    `head_sha=${sha}`,
    "-f",
    `per_page=${perPage}`,
  ];
}

function fetchPr(prNumber, repo) {
  const raw = execFileSync("gh", buildPrApiArgs(prNumber, repo), {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  return JSON.parse(raw);
}

function fetchWorkflowRuns(sha, repo) {
  const raw = execFileSync("gh", buildWorkflowRunsApiArgs(sha, repo), {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  const parsed = JSON.parse(raw);
  return parsed.workflow_runs;
}

export function main() {
  const prNumberArg = process.argv[2];
  const repo = process.argv[3];
  const prNumber = Number.parseInt(prNumberArg, 10);

  if (Number.isNaN(prNumber) || typeof repo !== "string" || repo.length === 0) {
    console.error(
      "REQUIRE PR CI SUCCESS: usage: node tools/deploy/require_pr_ci_success.mjs <pr-number> <owner/repo>",
    );
    process.exitCode = 1;
    return;
  }

  let pr;
  try {
    pr = fetchPr(prNumber, repo);
  } catch (err) {
    console.error(`REQUIRE PR CI SUCCESS: failed to fetch PR #${prNumber} from ${repo}: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  const prValidation = validatePrPayload(pr, { expectedRepo: repo, expectedPrNumber: prNumber });
  if (!prValidation.ok) {
    console.error(`REQUIRE PR CI SUCCESS: FAIL (${prValidation.problems.join("; ")})`);
    process.exitCode = 1;
    return;
  }

  const sha = prValidation.headSha;

  let workflowRuns;
  try {
    workflowRuns = fetchWorkflowRuns(sha, repo);
  } catch (err) {
    console.error(`REQUIRE PR CI SUCCESS: failed to fetch workflow runs for commit ${sha} from ${repo}: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  const evaluation = evaluatePrCiRequirement(pr, workflowRuns, { expectedRepo: repo, expectedPrNumber: prNumber });
  if (!evaluation.ok) {
    console.error(`REQUIRE PR CI SUCCESS: FAIL (${evaluation.reason})`);
    process.exitCode = 1;
    return;
  }

  console.log(`REQUIRE PR CI SUCCESS: PASS (${evaluation.reason})`);
  console.log(`PR_NUMBER=${evaluation.prNumber}`);
  console.log(`PR_HEAD_SHA=${evaluation.headSha}`);
  console.log("CI_VERIFIED=YES");
  console.log("PREVIEW_ONLY=YES");
  console.log("PRODUCTION_PROMOTION_ALLOWED=NO");

  const outputPath = process.argv[4];
  if (outputPath && typeof outputPath === "string" && outputPath.length > 0) {
    const lines = [
      `pr_number=${evaluation.prNumber}`,
      `pr_head_sha=${evaluation.headSha}`,
      "ci_verified=true",
      `ci_run_number=${evaluation.runNumber}`,
      `ci_run_id=${evaluation.runId}`,
    ].join("\n") + "\n";
    appendFileSync(outputPath, lines, "utf8");
  }
}

const isEntrypoint =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (isEntrypoint) {
  main();
}

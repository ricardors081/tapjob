import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildPrApiArgs,
  buildWorkflowRunsApiArgs,
  evaluatePrCiRequirement,
  validatePrPayload,
  validateWorkflowRunShape,
  verifyPrHeadMatch,
} from "./require_pr_ci_success.mjs";

const REPO = "ricardors081/tapjob";
const PR_NUM = 20;
const SHA = "a".repeat(40);
const OTHER_SHA = "b".repeat(40);

function makePr({
  number = PR_NUM,
  state = "open",
  sha = SHA,
  headRepo = REPO,
  baseRepo = REPO,
  baseRef = "main",
} = {}) {
  return {
    number,
    state,
    head: {
      sha,
      repo: { full_name: headRepo },
    },
    base: {
      ref: baseRef,
      repo: { full_name: baseRepo },
    },
  };
}

function makeRun({ runNumber, status, conclusion, headSha = SHA, id = 1000 + runNumber }) {
  return { head_sha: headSha, run_number: runNumber, status, conclusion, id };
}

describe("validatePrPayload", () => {
  test("a well-formed open PR from canonical repo passes", () => {
    const pr = makePr();
    const res = validatePrPayload(pr, { expectedRepo: REPO, expectedPrNumber: PR_NUM });
    assert.equal(res.ok, true);
    assert.equal(res.headSha, SHA);
  });

  test("non-object payload fails", () => {
    const res = validatePrPayload("invalid", { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /must be an object/);
  });

  test("mismatched PR number fails", () => {
    const pr = makePr({ number: 99 });
    const res = validatePrPayload(pr, { expectedRepo: REPO, expectedPrNumber: PR_NUM });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /does not match expected PR number/);
  });

  test("closed PR fails", () => {
    const pr = makePr({ state: "closed" });
    const res = validatePrPayload(pr, { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /must be in 'open' state/);
  });

  test("external fork PR fails closed for preview security", () => {
    const pr = makePr({ headRepo: "attacker/tapjob" });
    const res = validatePrPayload(pr, { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /Fork builds are rejected for preview security/);
  });

  test("invalid head commit SHA fails", () => {
    const pr = makePr({ sha: "not-a-40-hex-sha" });
    const res = validatePrPayload(pr, { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /must be a 40-character commit hex string/);
  });

  test("non-main base branch fails closed", () => {
    const pr = makePr({ baseRef: "develop" });
    const res = validatePrPayload(pr, { expectedRepo: REPO, expectedBaseRef: "main" });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /base branch must be 'main', got 'develop'/);
  });

  test("mismatched base repository fails closed", () => {
    const pr = makePr({ baseRepo: "attacker/tapjob" });
    const res = validatePrPayload(pr, { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /base repository \('attacker\/tapjob'\) does not match canonical repository/);
  });

  test("missing pr.base fails closed", () => {
    const pr = makePr();
    delete pr.base;
    const res = validatePrPayload(pr, { expectedRepo: REPO });
    assert.equal(res.ok, false);
    assert.match(res.problems[0], /pr\.base must be an object/);
  });
});

describe("validateWorkflowRunShape", () => {
  test("a well-formed completed/success run passes", () => {
    const shape = validateWorkflowRunShape(
      { head_sha: SHA, run_number: 1, status: "completed", conclusion: "success" },
      0,
    );
    assert.equal(shape.ok, true);
    assert.deepEqual(shape.problems, []);
  });

  test("missing head_sha fails", () => {
    const shape = validateWorkflowRunShape({ run_number: 1, status: "completed", conclusion: "success" }, 0);
    assert.equal(shape.ok, false);
  });

  test("non-integer run_number fails", () => {
    const shape = validateWorkflowRunShape(
      { head_sha: SHA, run_number: "one", status: "completed", conclusion: "success" },
      0,
    );
    assert.equal(shape.ok, false);
  });
});

describe("evaluatePrCiRequirement", () => {
  test("passes when PR is valid and exact commit has successful CI", () => {
    const pr = makePr();
    const runs = [makeRun({ runNumber: 1, status: "completed", conclusion: "success" })];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, true);
    assert.equal(res.headSha, SHA);
    assert.equal(res.prNumber, PR_NUM);
    assert.equal(res.runNumber, 1);
    assert.match(res.reason, /conclusion "success"/);
  });

  test("fails if PR validation fails (e.g. closed PR)", () => {
    const pr = makePr({ state: "closed" });
    const runs = [makeRun({ runNumber: 1, status: "completed", conclusion: "success" })];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, false);
    assert.match(res.reason, /must be in 'open' state/);
  });

  test("fails if no CI run exists for the exact PR head SHA", () => {
    const pr = makePr();
    const runs = [makeRun({ runNumber: 1, status: "completed", conclusion: "success", headSha: OTHER_SHA })];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, false);
    assert.match(res.reason, /no CI run found for commit/);
  });

  test("fails if latest CI run for the commit is in-progress", () => {
    const pr = makePr();
    const runs = [makeRun({ runNumber: 1, status: "in_progress", conclusion: null })];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, false);
    assert.match(res.reason, /is not completed/);
  });

  test("fails if latest CI run for the commit failed", () => {
    const pr = makePr();
    const runs = [makeRun({ runNumber: 1, status: "completed", conclusion: "failure" })];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, false);
    assert.match(res.reason, /did not succeed/);
  });

  test("latest run_number is authoritative: earlier failure + later success passes", () => {
    const pr = makePr();
    const runs = [
      makeRun({ runNumber: 1, status: "completed", conclusion: "failure" }),
      makeRun({ runNumber: 2, status: "completed", conclusion: "success" }),
    ];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, true);
    assert.equal(res.runNumber, 2);
  });

  test("latest run_number is authoritative: earlier success + later failure fails", () => {
    const pr = makePr();
    const runs = [
      makeRun({ runNumber: 1, status: "completed", conclusion: "success" }),
      makeRun({ runNumber: 2, status: "completed", conclusion: "failure" }),
    ];
    const res = evaluatePrCiRequirement(pr, runs, { expectedRepo: REPO, expectedPrNumber: PR_NUM });

    assert.equal(res.ok, false);
    assert.match(res.reason, /did not succeed/);
  });
});

describe("buildPrApiArgs & buildWorkflowRunsApiArgs", () => {
  test("buildPrApiArgs uses explicit GET and targets pulls endpoint", () => {
    const args = buildPrApiArgs(PR_NUM, REPO);
    assert.deepEqual(args, ["api", "--method", "GET", `repos/${REPO}/pulls/${PR_NUM}`]);
  });

  test("buildWorkflowRunsApiArgs uses explicit GET and scopes to head_sha", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    assert.ok(args.includes("--method"));
    assert.ok(args.includes("GET"));
    assert.ok(args.includes(`repos/${REPO}/actions/workflows/ci.yml/runs`));
    assert.ok(args.includes(`head_sha=${SHA}`));
  });
});

describe("verifyPrHeadMatch (TOCTOU guard)", () => {
  test("passes when current remote PR HEAD equals preflight SHA", () => {
    const pr = makePr({ sha: SHA });
    const res = verifyPrHeadMatch(pr, SHA, {
      expectedRepo: REPO,
      expectedPrNumber: PR_NUM,
      expectedBaseRef: "main",
    });
    assert.equal(res.ok, true);
    assert.equal(res.headsMatch, true);
    assert.equal(res.preflightVerifiedHead, SHA);
    assert.equal(res.buildTimeRemoteHead, SHA);
    assert.match(res.reason, /remote HEAD matches preflight-verified commit/);
  });

  test("fails closed when current remote PR HEAD moved after preflight", () => {
    const pr = makePr({ sha: OTHER_SHA });
    const res = verifyPrHeadMatch(pr, SHA, {
      expectedRepo: REPO,
      expectedPrNumber: PR_NUM,
      expectedBaseRef: "main",
    });
    assert.equal(res.ok, false);
    assert.equal(res.headsMatch, false);
    assert.equal(res.preflightVerifiedHead, SHA);
    assert.equal(res.buildTimeRemoteHead, OTHER_SHA);
    assert.match(res.reason, /TOCTOU check failed: PR #20 remote HEAD moved/);
  });

  test("fails closed if PR was closed after preflight", () => {
    const pr = makePr({ state: "closed", sha: SHA });
    const res = verifyPrHeadMatch(pr, SHA, { expectedRepo: REPO, expectedPrNumber: PR_NUM });
    assert.equal(res.ok, false);
    assert.equal(res.headsMatch, false);
    assert.match(res.reason, /must be in 'open' state/);
  });

  test("fails closed if PR base branch is not main", () => {
    const pr = makePr({ baseRef: "feature-branch", sha: SHA });
    const res = verifyPrHeadMatch(pr, SHA, {
      expectedRepo: REPO,
      expectedPrNumber: PR_NUM,
      expectedBaseRef: "main",
    });
    assert.equal(res.ok, false);
    assert.equal(res.headsMatch, false);
    assert.match(res.reason, /base branch must be 'main'/);
  });

  test("fails closed on repo mismatch", () => {
    const pr = makePr({ headRepo: "other/repo", sha: SHA });
    const res = verifyPrHeadMatch(pr, SHA, { expectedRepo: REPO, expectedPrNumber: PR_NUM });
    assert.equal(res.ok, false);
    assert.equal(res.headsMatch, false);
    assert.match(res.reason, /Fork builds are rejected for preview security/);
  });
});

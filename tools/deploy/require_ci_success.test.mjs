import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildWorkflowRunsApiArgs,
  evaluateCiRequirement,
  validateWorkflowRunShape,
} from "./require_ci_success.mjs";

const SHA = "a".repeat(40);
const OTHER_SHA = "b".repeat(40);

function run({ runNumber, status, conclusion, headSha = SHA }) {
  return { head_sha: headSha, run_number: runNumber, status, conclusion };
}

describe("evaluateCiRequirement", () => {
  test("no matching run for the given SHA fails", () => {
    const result = evaluateCiRequirement(
      [run({ runNumber: 1, status: "completed", conclusion: "success", headSha: OTHER_SHA })],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /no CI run found/);
  });

  test("a completed, successful run for the exact SHA passes", () => {
    const result = evaluateCiRequirement(
      [run({ runNumber: 1, status: "completed", conclusion: "success" })],
      { sha: SHA },
    );
    assert.equal(result.ok, true);
  });

  test("a completed but failed run fails", () => {
    const result = evaluateCiRequirement(
      [run({ runNumber: 1, status: "completed", conclusion: "failure" })],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /did not succeed/);
  });

  test("an in-progress run (not yet completed) fails", () => {
    const result = evaluateCiRequirement(
      [run({ runNumber: 1, status: "in_progress", conclusion: null })],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /not completed/);
  });

  test("multiple runs for the same SHA: an earlier failure followed by a later success passes (re-run case)", () => {
    const result = evaluateCiRequirement(
      [
        run({ runNumber: 1, status: "completed", conclusion: "failure" }),
        run({ runNumber: 2, status: "completed", conclusion: "success" }),
      ],
      { sha: SHA },
    );
    assert.equal(result.ok, true);
  });

  test("multiple runs for the same SHA: an earlier success followed by a later failure fails (regression case, must use latest not any)", () => {
    const result = evaluateCiRequirement(
      [
        run({ runNumber: 1, status: "completed", conclusion: "success" }),
        run({ runNumber: 2, status: "completed", conclusion: "failure" }),
      ],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /did not succeed/);
  });

  test("run order in the input array does not matter -- the highest run_number always wins", () => {
    const result = evaluateCiRequirement(
      [
        run({ runNumber: 5, status: "completed", conclusion: "failure" }),
        run({ runNumber: 3, status: "completed", conclusion: "success" }),
      ],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
  });

  test("a non-array payload fails", () => {
    const result = evaluateCiRequirement({ workflow_runs: [] }, { sha: SHA });
    assert.equal(result.ok, false);
    assert.match(result.reason, /must be an array/);
  });

  test("null payload fails", () => {
    const result = evaluateCiRequirement(null, { sha: SHA });
    assert.equal(result.ok, false);
  });

  test("an empty array fails", () => {
    const result = evaluateCiRequirement([], { sha: SHA });
    assert.equal(result.ok, false);
    assert.match(result.reason, /no CI run found/);
  });

  test("a missing sha fails without raising", () => {
    const result = evaluateCiRequirement([run({ runNumber: 1, status: "completed", conclusion: "success" })], {});
    assert.equal(result.ok, false);
    assert.match(result.reason, /sha must be a non-empty string/);
  });

  test("a non-string sha fails without raising", () => {
    const result = evaluateCiRequirement(
      [run({ runNumber: 1, status: "completed", conclusion: "success" })],
      { sha: 123 },
    );
    assert.equal(result.ok, false);
  });

  test("a malformed (null) entry in the runs array does not crash evaluation", () => {
    const result = evaluateCiRequirement(
      [null, run({ runNumber: 1, status: "completed", conclusion: "success" })],
      { sha: SHA },
    );
    assert.equal(result.ok, true);
  });

  // 2026 review round 3: a matching run must never be accepted as success just because
  // status/conclusion happen to read "completed"/"success" -- every other field this gate relies
  // on (run_number, head_sha, conclusion's null-vs-string shape) must also be well-formed, or the
  // whole gate fails closed.

  test("a matching run with a non-integer run_number fails closed even though status/conclusion look fine", () => {
    const result = evaluateCiRequirement(
      [{ head_sha: SHA, run_number: "1", status: "completed", conclusion: "success" }],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /malformed CI run payload/);
    assert.match(result.reason, /run_number/);
  });

  test("a matching run with a float run_number fails closed", () => {
    const result = evaluateCiRequirement(
      [{ head_sha: SHA, run_number: 1.5, status: "completed", conclusion: "success" }],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /run_number/);
  });

  test("a matching run with a non-string status fails closed", () => {
    const result = evaluateCiRequirement(
      [{ head_sha: SHA, run_number: 1, status: 1, conclusion: "success" }],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /status/);
  });

  test("a matching run with a non-string, non-null conclusion fails closed", () => {
    const result = evaluateCiRequirement(
      [{ head_sha: SHA, run_number: 1, status: "completed", conclusion: 1 }],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /conclusion/);
  });

  test("a null conclusion is still valid shape (in-progress runs carry it)", () => {
    const shape = validateWorkflowRunShape(
      { head_sha: SHA, run_number: 1, status: "in_progress", conclusion: null },
      0,
    );
    assert.equal(shape.ok, true);
  });

  test("a malformed earlier run and a well-formed later run for the same SHA still fails closed", () => {
    // The malformed entry could, in principle, actually be the true "latest" run -- its own
    // run_number cannot be trusted, so the whole evaluation must refuse to pick a winner.
    const result = evaluateCiRequirement(
      [
        { head_sha: SHA, run_number: "not-a-number", status: "completed", conclusion: "failure" },
        run({ runNumber: 2, status: "completed", conclusion: "success" }),
      ],
      { sha: SHA },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /malformed CI run payload/);
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

  test("a missing head_sha fails", () => {
    const shape = validateWorkflowRunShape({ run_number: 1, status: "completed", conclusion: "success" }, 0);
    assert.equal(shape.ok, false);
  });

  test("an empty head_sha fails", () => {
    const shape = validateWorkflowRunShape(
      { head_sha: "", run_number: 1, status: "completed", conclusion: "success" },
      0,
    );
    assert.equal(shape.ok, false);
  });
});

describe("buildWorkflowRunsApiArgs", () => {
  const REPO = "ricardors081/tapjob";

  test("uses an explicit --method GET (gh api would otherwise silently switch to POST with -f fields)", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    const methodIndex = args.indexOf("--method");
    assert.ok(methodIndex !== -1, "expected --method to be present");
    assert.equal(args[methodIndex + 1], "GET");
  });

  test("never includes a mutating HTTP method anywhere in the args", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    for (const mutatingMethod of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.ok(!args.includes(mutatingMethod), `did not expect ${mutatingMethod} in args`);
    }
  });

  test("targets the ci.yml workflow runs endpoint for the given repo", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    assert.ok(args.includes(`repos/${REPO}/actions/workflows/ci.yml/runs`));
  });

  test("passes head_sha as a -f field", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    assert.ok(args.includes(`head_sha=${SHA}`));
  });

  test("passes event=push as a -f field", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    assert.ok(args.includes("event=push"));
  });

  test("defaults per_page to 100", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO);
    assert.ok(args.includes("per_page=100"));
  });

  test("accepts a custom per_page", () => {
    const args = buildWorkflowRunsApiArgs(SHA, REPO, { perPage: 30 });
    assert.ok(args.includes("per_page=30"));
    assert.ok(!args.includes("per_page=100"));
  });

  test("a different repo is reflected verbatim in the endpoint path", () => {
    const args = buildWorkflowRunsApiArgs(SHA, "someone-else/other-repo");
    assert.ok(args.includes("repos/someone-else/other-repo/actions/workflows/ci.yml/runs"));
  });
});

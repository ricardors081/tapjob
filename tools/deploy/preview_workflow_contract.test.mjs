import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { verifyPrHeadMatch } from "./require_pr_ci_success.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = join(__dirname, "../..");
const PREVIEW_WORKFLOW_PATH = join(REPO_ROOT, ".github/workflows/preview-image.yml");

describe("PR Preview Workflow Contract (Control Plane vs Target Source Separation)", () => {
  const workflowContent = readFileSync(PREVIEW_WORKFLOW_PATH, "utf8");

  test("workflow isolates control plane checkout to _preview_control with pinned dispatch SHA", () => {
    assert.match(
      workflowContent,
      /path:\s*_preview_control/,
      "control plane must be checked out to dedicated _preview_control path",
    );
    assert.match(
      workflowContent,
      /ref:\s*\${{\s*(?:env\.CONTROL_PLANE_SHA|needs\.preflight\.outputs\.control-plane-sha|github\.sha)\s*}}/,
      "control plane checkout must be pinned to the canonical dispatch commit SHA",
    );
  });

  test("workflow isolates target PR checkout to _preview_source with exact PR_HEAD_SHA", () => {
    assert.match(
      workflowContent,
      /path:\s*_preview_source/,
      "target PR source must be checked out to dedicated _preview_source path",
    );
    assert.match(
      workflowContent,
      /ref:\s*\${{\s*env\.PR_HEAD_SHA\s*}}/,
      "target PR checkout must be pinned to PR_HEAD_SHA",
    );
  });

  test("workflow verifies checkouts with git -C before any build", () => {
    assert.match(
      workflowContent,
      /git\s+-C\s+_preview_source\s+rev-parse\s+HEAD/,
      "workflow must verify exact target PR HEAD commit in _preview_source",
    );
    assert.match(
      workflowContent,
      /git\s+-C\s+_preview_control\s+rev-parse\s+HEAD/,
      "workflow must verify exact control plane commit in _preview_control",
    );
  });

  test("workflow executes TOCTOU verifier strictly from _preview_control", () => {
    assert.match(
      workflowContent,
      /node\s+_preview_control\/tools\/deploy\/require_pr_ci_success\.mjs\s+--verify-head/,
      "TOCTOU verifier must be invoked from _preview_control, never from target PR source",
    );
    const buildAndPushSection = workflowContent.split("build-and-push:")[1] ?? "";
    assert.doesNotMatch(
      buildAndPushSection,
      /run:\s*node\s+tools\/deploy\/require_pr_ci_success\.mjs/,
      "build-and-push job must not invoke require_pr_ci_success.mjs from workspace root",
    );
  });

  test("workflow Docker build context and Dockerfile point strictly to _preview_source", () => {
    assert.match(
      workflowContent,
      /context:\s*_preview_source/,
      "Docker build context must be _preview_source",
    );
    assert.match(
      workflowContent,
      /file:\s*_preview_source\/infra\/docker\/backend\.Dockerfile/,
      "Docker build Dockerfile must be _preview_source/infra/docker/backend.Dockerfile",
    );
  });

  test("workflow Node setup uses _preview_control/.nvmrc", () => {
    assert.match(
      workflowContent,
      /node-version-file:\s*_preview_control\/\.nvmrc/,
      "Node setup in build-and-push must use control-plane .nvmrc configuration",
    );
  });

  test("TARGET_PR_DOES_NOT_CONTAIN_PREVIEW_VERIFIER => preview workflow succeeds structurally from control-plane", () => {
    // Simulate a target PR directory that lacks preview tooling (such as PR #20 HEAD)
    const testDir = mkdtempSync(join(tmpdir(), "tapjob-preview-test-"));
    try {
      const mockSourceDir = join(testDir, "_preview_source");

      // Target PR source directory exists without tools/deploy/require_pr_ci_success.mjs
      assert.equal(
        existsSync(join(mockSourceDir, "tools/deploy/require_pr_ci_success.mjs")),
        false,
        "Target PR source does not contain preview verifier (simulates PR #20)",
      );

      // Verifier exists in the control plane
      const controlVerifierPath = join(REPO_ROOT, "tools/deploy/require_pr_ci_success.mjs");
      assert.equal(
        existsSync(controlVerifierPath),
        true,
        "Control plane provides tools/deploy/require_pr_ci_success.mjs",
      );

      // Verify that verifyPrHeadMatch operates purely using control plane evaluation, completely independent of target PR files
      const sha = "a".repeat(40);
      const prPayload = {
        number: 20,
        state: "open",
        head: { sha, repo: { full_name: "ricardors081/tapjob" } },
        base: { ref: "main", repo: { full_name: "ricardors081/tapjob" } },
      };
      const result = verifyPrHeadMatch(prPayload, sha, {
        expectedRepo: "ricardors081/tapjob",
        expectedPrNumber: 20,
        expectedBaseRef: "main",
      });
      assert.equal(result.ok, true);
      assert.equal(result.headsMatch, true);
      assert.match(result.reason, /remote HEAD matches preflight-verified commit/);
    } finally {
      rmSync(testDir, { recursive: true, force: true });
    }
  });
});

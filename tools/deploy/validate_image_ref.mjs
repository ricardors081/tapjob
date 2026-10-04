#!/usr/bin/env node
// Deterministic, zero-dependency validator for one requirement of the build-once/promote-same-
// digest deployment contract (docs/adr/0006-worker-correlation-deployment-cd-contract.md, D-050):
// the unit of promotion between environments is an IMMUTABLE image digest, never a mutable tag.
//
// Accepts only `<name>@sha256:<64 lowercase hex characters>` -- e.g.
// `ghcr.io/ricardoo081/tapjob-backend@sha256:<64 hex>`. Rejects, among other things, a bare
// `:latest` tag, any other mutable tag, a tag combined with a digest, a malformed/short/uppercase
// digest, and an empty value. This intentionally does not implement the full OCI reference grammar
// (docs/adr/0006-...md): it is a small, auditable allowlist of the one shape this deployment
// contract actually needs to accept.

import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DIGEST_BODY_PATTERN = "sha256:[0-9a-f]{64}";
const DIGEST_SUFFIX_PATTERN = new RegExp(`@${DIGEST_BODY_PATTERN}$`);
const DIGEST_PATTERN = new RegExp(`^${DIGEST_BODY_PATTERN}$`);
const NAME_COMPONENT_PATTERN = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const PORT_SUFFIX_PATTERN = /:[0-9]+$/;

/**
 * @returns {{ ok: boolean, problems: string[] }}
 */
export function validateImageRef(ref) {
  const problems = [];

  if (typeof ref !== "string" || ref.length === 0) {
    problems.push("image reference must be a non-empty string");
    return { ok: false, problems };
  }

  const digestMatch = DIGEST_SUFFIX_PATTERN.exec(ref);
  if (!digestMatch) {
    problems.push(
      "image reference must end with an immutable '@sha256:<64 lowercase hex characters>' " +
        "digest -- a mutable tag (e.g. ':latest') or a missing digest is not an acceptable " +
        "promotion unit",
    );
    return { ok: false, problems };
  }

  const namePart = ref.slice(0, ref.length - digestMatch[0].length);
  if (namePart.length === 0) {
    problems.push("image reference is missing the repository name before '@sha256:...'");
    return { ok: false, problems };
  }
  if (namePart.includes("@")) {
    problems.push("image reference must contain exactly one '@' (the digest separator)");
    return { ok: false, problems };
  }

  const components = namePart.split("/");
  const lastComponent = components[components.length - 1];
  if (lastComponent.includes(":")) {
    problems.push(
      "image reference must not combine a mutable tag with the digest (e.g. 'name:tag@sha256:" +
        "...') -- the digest alone is the promotion identity; use it without a tag",
    );
  }

  components.forEach((component, index) => {
    // Only the first path component (the registry host) may carry a `:port` suffix.
    const candidate = index === 0 ? component.replace(PORT_SUFFIX_PATTERN, "") : component;
    if (!NAME_COMPONENT_PATTERN.test(candidate)) {
      problems.push(`image reference component "${component}" is not a valid name/registry component`);
    }
  });

  return { ok: problems.length === 0, problems };
}

/**
 * Validates a BARE digest -- `sha256:<64 lowercase hex characters>` -- with no repository name,
 * no `@` separator and no tag slot at all (2026 review, Finding 2). This is the shape
 * `infra/compose.deploy.yaml`'s `BACKEND_IMAGE_DIGEST` variable must hold: the canonical
 * repository (`ghcr.io/ricardoo081/tapjob-backend`) is fixed directly in that file's image
 * template, not interpolated from an environment variable, so there is no slot left for an
 * operator to substitute a foreign repository or a mutable tag into -- only this bare digest is
 * ever accepted as the variable part. `validateImageRef` above remains the full-reference
 * validator for contexts that still need one (e.g. the release workflow's own promotion
 * reference); the two are deliberately separate functions for two deliberately different shapes.
 *
 * @returns {{ ok: boolean, problems: string[] }}
 */
export function validateImageDigest(value) {
  const problems = [];

  if (typeof value !== "string" || value.length === 0) {
    problems.push("image digest must be a non-empty string");
    return { ok: false, problems };
  }

  if (!DIGEST_PATTERN.test(value)) {
    problems.push(
      "image digest must be exactly 'sha256:<64 lowercase hex characters>' -- no repository " +
        "name, no '@' separator, and no tag (e.g. ':latest') is ever accepted here",
    );
  }

  return { ok: problems.length === 0, problems };
}

export function main() {
  // Takes the value as a CLI argument, not via `process.env` (docs/ENGINEERING_STANDARDS.md §5:
  // only apps/mobile's own typed public config may read `process.env` directly) -- a caller
  // passes its environment variable in via ordinary shell substitution instead, e.g.
  // `node tools/deploy/validate_image_ref.mjs "$BACKEND_IMAGE"`, or, for the bare-digest form,
  // `node tools/deploy/validate_image_ref.mjs --digest "$BACKEND_IMAGE_DIGEST"`.
  const isDigestMode = process.argv[2] === "--digest";
  const value = isDigestMode ? process.argv[3] : process.argv[2];
  const result = isDigestMode ? validateImageDigest(value) : validateImageRef(value);
  const label = isDigestMode ? "IMAGE DIGEST VALIDATION" : "IMAGE REFERENCE VALIDATION";

  if (!result.ok) {
    console.error(`${label}: FAIL (checked: ${JSON.stringify(value ?? null)})`);
    for (const problem of result.problems) {
      console.error(`  - ${problem}`);
    }
    process.exitCode = 1;
    return;
  }

  console.log(`${label}: PASS (${value})`);
}

const isDirectlyExecuted = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectlyExecuted) {
  main();
}

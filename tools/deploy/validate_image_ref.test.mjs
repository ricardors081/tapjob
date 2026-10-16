import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { validateImageDigest, validateImageRef } from "./validate_image_ref.mjs";

const VALID_DIGEST = "a".repeat(64);
const REF = `ghcr.io/ricardos081/tapjob-backend@sha256:${VALID_DIGEST}`;

describe("validateImageRef", () => {
  test("a valid sha256 digest reference passes", () => {
    const result = validateImageRef(REF);
    assert.equal(result.ok, true);
    assert.deepEqual(result.problems, []);
  });

  test("a registry host with a port is accepted", () => {
    const result = validateImageRef(`localhost:5000/tapjob-backend@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, true);
  });

  test("a bare ':latest' tag (no digest) fails", () => {
    const result = validateImageRef("ghcr.io/ricardos081/tapjob-backend:latest");
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /immutable/);
  });

  test("a mutable tag with no digest at all fails", () => {
    const result = validateImageRef("ghcr.io/ricardos081/tapjob-backend:v1.2.3");
    assert.equal(result.ok, false);
  });

  test("a bare repository name with no tag and no digest fails", () => {
    const result = validateImageRef("ghcr.io/ricardos081/tapjob-backend");
    assert.equal(result.ok, false);
  });

  test("a malformed digest (too short) fails", () => {
    const result = validateImageRef("ghcr.io/ricardos081/tapjob-backend@sha256:abc123");
    assert.equal(result.ok, false);
  });

  test("a digest with uppercase hex characters fails", () => {
    const result = validateImageRef(`ghcr.io/ricardos081/tapjob-backend@sha256:${"A".repeat(64)}`);
    assert.equal(result.ok, false);
  });

  test("a non-hex digest body fails", () => {
    const result = validateImageRef(`ghcr.io/ricardos081/tapjob-backend@sha256:${"g".repeat(64)}`);
    assert.equal(result.ok, false);
  });

  test("an empty string fails", () => {
    const result = validateImageRef("");
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /non-empty string/);
  });

  test("undefined fails", () => {
    const result = validateImageRef(undefined);
    assert.equal(result.ok, false);
  });

  test("a non-string value fails", () => {
    const result = validateImageRef(42);
    assert.equal(result.ok, false);
  });

  test("a tag combined with a digest is rejected (digest alone is the promotion identity)", () => {
    const result = validateImageRef(`ghcr.io/ricardos081/tapjob-backend:v1@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /mutable tag with the digest/);
  });

  test("a second '@' anywhere in the reference is rejected", () => {
    const result = validateImageRef(`ghcr.io/weird@name/tapjob-backend@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /exactly one '@'/);
  });

  test("a reference with no name before the digest is rejected", () => {
    const result = validateImageRef(`@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /missing the repository name/);
  });

  test("a name component containing a space is rejected", () => {
    const result = validateImageRef(`ghcr.io/ricardos081/tapjob backend@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
  });
});

// Finding 2 (2026 review): BACKEND_IMAGE_DIGEST holds a BARE digest, never a full reference --
// the canonical repository is fixed directly in infra/compose.deploy.yaml's image template, not
// interpolated, so this variable has no slot for a repository name, a tag, or an '@' at all.
describe("validateImageDigest", () => {
  test("a valid bare sha256 digest passes", () => {
    const result = validateImageDigest(`sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, true);
    assert.deepEqual(result.problems, []);
  });

  test("':latest' is rejected", () => {
    const result = validateImageDigest("latest");
    assert.equal(result.ok, false);
  });

  test("a mutable tag is rejected", () => {
    const result = validateImageDigest("v1.2.3");
    assert.equal(result.ok, false);
  });

  test("a full name@sha256:<digest> reference is rejected (no repo slot here)", () => {
    const result = validateImageDigest(`ghcr.io/ricardos081/tapjob-backend@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
  });

  test("a foreign repository prefixed onto a digest is rejected", () => {
    const result = validateImageDigest(`ghcr.io/attacker/other-image@sha256:${VALID_DIGEST}`);
    assert.equal(result.ok, false);
  });

  test("a malformed digest (too short) is rejected", () => {
    const result = validateImageDigest("sha256:abc123");
    assert.equal(result.ok, false);
  });

  test("a digest with uppercase hex characters is rejected", () => {
    const result = validateImageDigest(`sha256:${"A".repeat(64)}`);
    assert.equal(result.ok, false);
  });

  test("an empty string is rejected", () => {
    const result = validateImageDigest("");
    assert.equal(result.ok, false);
    assert.match(result.problems[0], /non-empty string/);
  });

  test("undefined is rejected", () => {
    const result = validateImageDigest(undefined);
    assert.equal(result.ok, false);
  });

  test("a non-string value is rejected", () => {
    const result = validateImageDigest(42);
    assert.equal(result.ok, false);
  });
});

// Regression guard for the GENERATOR itself (docs/adr/0003-api-contract-client-generation.md),
// independent of TapJob's own OpenAPI schema (see test_openapi_export.py's tests for that). Each
// fixture is a minimal, hand-built OpenAPI document isolating one construct: $ref, required vs.
// optional fields and a non-2xx error-response schema are constructs the real backend contract
// already uses today; allOf and a discriminated oneOf union are included pre-emptively because a
// future port/capability/messaging-content contract (docs/IMPLEMENTATION_PLAN.md WP0.2's
// remaining ports work) is expected to need composed/polymorphic schemas, and Hey API documents
// support for both today. If a future @hey-api/openapi-ts upgrade silently drops a field or
// mishandles one of these constructs, these tests fail here instead of surfacing as a confusing
// bug in generated product code.
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";

import { createClient } from "@hey-api/openapi-ts";

async function generateTypes(paths, schemas) {
  const dir = await mkdtemp(join(tmpdir(), "hey-api-fixture-"));
  try {
    await createClient({
      input: {
        openapi: "3.1.0",
        info: { title: "fixture", version: "1.0.0" },
        paths,
        components: { schemas },
      },
      output: dir,
      plugins: ["@hey-api/typescript"],
      logs: { level: "silent" },
    });
    return await readFile(join(dir, "types.gen.ts"), "utf8");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

// Each test does its own isolated mkdtemp + codegen pass + cleanup and shares no state, so they
// can run concurrently rather than paying five sequential codegen round trips.
describe(
  "Hey API generator: structural constructs TapJob's contract relies on",
  { concurrency: true },
  () => {
    test("$ref resolves to a named type alias, not an inlined duplicate", async () => {
      const generated = await generateTypes(
        {
          "/widgets/{id}": {
            get: {
              operationId: "getWidget",
              responses: {
                200: {
                  content: {
                    "application/json": { schema: { $ref: "#/components/schemas/Widget" } },
                  },
                },
              },
            },
          },
        },
        { Widget: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
      );

      assert.match(generated, /export type Widget = \{/);
      assert.match(generated, /200: Widget/);
    });

    test("required fields have no `?:` and optional fields do", async () => {
      const generated = await generateTypes(
        {},
        {
          Widget: {
            type: "object",
            properties: { id: { type: "string" }, nickname: { type: "string" } },
            required: ["id"],
          },
        },
      );

      assert.match(generated, /\bid: string;/);
      assert.doesNotMatch(generated, /\bid\?: string;/);
      assert.match(generated, /\bnickname\?: string;/);
    });

    test("allOf composes every member schema's fields, not just the last one", async () => {
      const generated = await generateTypes(
        {},
        {
          Timestamped: {
            type: "object",
            properties: { createdAt: { type: "string" } },
            required: ["createdAt"],
          },
          Widget: {
            allOf: [
              { $ref: "#/components/schemas/Timestamped" },
              { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
            ],
          },
        },
      );

      assert.match(generated, /createdAt: string/);
      assert.match(generated, /id: string/);
    });

    test("a discriminated oneOf union keeps every variant and its own fields", async () => {
      const generated = await generateTypes(
        {},
        {
          TextContent: {
            type: "object",
            properties: { kind: { type: "string", enum: ["text"] }, text: { type: "string" } },
            required: ["kind", "text"],
          },
          DocumentContent: {
            type: "object",
            properties: {
              kind: { type: "string", enum: ["document"] },
              storageKey: { type: "string" },
            },
            required: ["kind", "storageKey"],
          },
          MessageContent: {
            oneOf: [
              { $ref: "#/components/schemas/TextContent" },
              { $ref: "#/components/schemas/DocumentContent" },
            ],
            discriminator: {
              propertyName: "kind",
              mapping: {
                text: "#/components/schemas/TextContent",
                document: "#/components/schemas/DocumentContent",
              },
            },
          },
        },
      );

      assert.match(generated, /export type TextContent = \{/);
      assert.match(generated, /export type DocumentContent = \{/);
      // The union intersects each variant with its own discriminator literal (narrower/more
      // precise than a plain `TextContent | DocumentContent`), so assert on the pieces that matter:
      // both variants are referenced, and the discriminator value is preserved per variant.
      assert.match(generated, /export type MessageContent = /);
      assert.match(generated, /& TextContent\)/);
      assert.match(generated, /& DocumentContent\)/);
      assert.match(generated, /kind: 'text'/);
      assert.match(generated, /kind: 'document'/);
      assert.match(generated, /text: string/);
      assert.match(generated, /storageKey: string/);
    });

    test("a non-2xx error response schema is kept per status code, never dropped", async () => {
      const generated = await generateTypes(
        {
          "/widgets": {
            get: {
              operationId: "listWidgets",
              responses: {
                200: {
                  content: {
                    "application/json": {
                      schema: { type: "array", items: { $ref: "#/components/schemas/Widget" } },
                    },
                  },
                },
                404: {
                  content: {
                    "application/json": { schema: { $ref: "#/components/schemas/ApiError" } },
                  },
                },
              },
            },
          },
        },
        {
          Widget: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
          ApiError: {
            type: "object",
            properties: { code: { type: "string" }, requestId: { type: "string" } },
            required: ["code", "requestId"],
          },
        },
      );

      assert.match(generated, /export type ApiError = \{/);
      assert.match(generated, /404: ApiError/);
    });
  },
);

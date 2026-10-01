// Hey API codegen config (docs/adr/0003-api-contract-client-generation.md). Run with
// `npm run generate --workspace @tapjob/contracts`; CI re-runs it and diffs src/generated against
// the committed copy to catch drift the same way it diffs openapi.json itself.
//
// Deliberately minimal: only types + SDK functions + the fetch client runtime. TanStack Query and
// schema validators (Zod/Valibot) are available as additional Hey API plugins but are out of scope
// until a consumer actually needs them (docs/IMPLEMENTATION_PLAN.md WP0.2).
import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi.json",
  output: "./src/generated",
  plugins: ["@hey-api/typescript", "@hey-api/sdk", "@hey-api/client-fetch"],
});

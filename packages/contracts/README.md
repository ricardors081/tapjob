# packages/contracts

TypeScript client generated from the backend's OpenAPI contract (`docs/adr/0003-api-contract-client-generation.md`, closes `OQ-015`).

## Source of truth

FastAPI/OpenAPI is canonical (`docs/ENGINEERING_STANDARDS.md` §7). `openapi.json` in this package is a **deterministic, offline export** of that schema — regenerate it from the repository root with:

```sh
uv run python -m tapjob_api.openapi_export
```

## Generating the client

```sh
npm run generate --workspace @tapjob/contracts
```

This runs `@hey-api/openapi-ts` (pinned exact in `package.json`, never `^`/`~`) against `openapi.json` and writes `src/generated/**`. CI regenerates both the schema and the client and fails on any `git diff` — the committed output must always match what the code that produced it would generate again.

## Consuming this package

Import only from `@tapjob/contracts` (this package's `"."` export, `src/index.ts`). `src/generated/**` is not published by the `exports` map in `package.json`, so a deep import is a module-resolution error for both Node and Metro, not just a lint warning.

```ts
import { createApiClient, endpoints, isApiError } from "@tapjob/contracts";

const client = createApiClient({ baseUrl: publicConfig.apiBaseUrl });
const { data, error } = await endpoints.getHealth({ client });
if (error) {
  if (isApiError(error)) {
    // branch on error.code, never error.message
  }
}
```

A consumer's own `tsconfig.json` needs `"exactOptionalPropertyTypes": false` (see the comment in this package's `tsconfig.json` and in `apps/mobile/tsconfig.json`) — a known limitation of `@hey-api/openapi-ts@0.99.0`'s generated fetch-client runtime, not of this facade.

## Tests

`npm run test --workspace @tapjob/contracts` runs `test/generator-fixtures.test.mjs`: regression coverage for the **generator itself** (`$ref`, required/optional fields, `allOf`, a discriminated `oneOf` union, error-response schemas) against small hand-built fixtures, independent of TapJob's own schema. Backend-side error/pagination behavior is tested in `services/api/tests/` (`test_errors.py`, `test_pagination.py`, `test_contract_rules.py`, `test_openapi_export.py`).

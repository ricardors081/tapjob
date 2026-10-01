// Public facade over the generated OpenAPI client (docs/adr/0003-api-contract-client-generation.md).
//
// Nothing outside this file may import from "./generated/*": this package's `exports` map in
// package.json only publishes ".", so a deep import is a module-resolution error, not just a lint
// warning. Re-export a generated name here deliberately when a consumer needs it.
import { type Client, type Config, createClient, createConfig } from "./generated/client";
import type { ErrorResponse } from "./generated/types.gen";

export type { Client } from "./generated/client";

// SDK functions (one per operationId, e.g. getHealth/getReadiness) and their request/response
// types. Call a function with `{ client }` from createApiClient below -- see Hey API's own
// per-call `client` option, which this facade relies on instead of a mutable global client.
export * as endpoints from "./generated/sdk.gen";
export type {
  DependencyName,
  DependencyStatus,
  Environment,
  ErrorCode,
  ErrorResponse,
  GetHealthData,
  GetHealthErrors,
  GetHealthResponses,
  GetReadinessData,
  GetReadinessErrors,
  GetReadinessResponses,
  HealthResponse,
  HealthStatus,
  ReadinessResponse,
  ReadinessStatus,
  ValidationErrorItem,
} from "./generated/types.gen";

export interface ApiClientConfig {
  /** e.g. publicConfig.apiBaseUrl (apps/mobile/src/config/public-config.ts) -- never a literal. */
  baseUrl: string;
  /**
   * Called before every request; returning a token attaches it as `Authorization: Bearer <token>`.
   * No endpoint requires one yet (docs/IMPLEMENTATION_PLAN.md WP0.2), so this is a no-op until a
   * future `/api/v1` route declares auth -- wired as a request interceptor rather than through
   * Hey API's per-operation `security`/`auth` config, so it does not depend on which operations
   * eventually declare a security requirement.
   */
  getAccessToken?: () => string | null | undefined | Promise<string | null | undefined>;
  /** Override for tests or an environment without a global `fetch` (default: globalThis.fetch). */
  fetch?: typeof fetch;
}

export function createApiClient(config: ApiClientConfig): Client {
  const clientConfig: Config = {
    baseUrl: config.baseUrl,
    // Conditional spread, not `fetch: config.fetch`: Config["fetch"] rejects an explicit
    // `undefined` under exactOptionalPropertyTypes, so the key must be absent rather than set to
    // undefined when no override was given.
    ...(config.fetch ? { fetch: config.fetch } : {}),
  };
  const client = createClient(createConfig(clientConfig));

  if (config.getAccessToken) {
    client.interceptors.request.use(async (request) => {
      const token = await config.getAccessToken?.();
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }
      return request;
    });
  }

  return client;
}

/** Narrows an unknown error value (e.g. from a `{ data, error }` SDK call result) to the canonical
 * ErrorResponse envelope (docs/ENGINEERING_STANDARDS.md §8). UI/business logic branches on
 * `.code`, never on `.message`. */
export function isApiError(value: unknown): value is ErrorResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Partial<ErrorResponse>).code === "string" &&
    typeof (value as Partial<ErrorResponse>).message === "string" &&
    typeof (value as Partial<ErrorResponse>).requestId === "string"
  );
}

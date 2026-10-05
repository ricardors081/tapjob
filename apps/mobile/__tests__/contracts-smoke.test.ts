// Technical smoke test only (docs/adr/0003-api-contract-client-generation.md): proves the
// generated @tapjob/contracts client resolves, bundles and runs inside the Expo/Jest environment
// with no real backend involved (fetch is mocked). No product screen consumes this package yet --
// see also `npm run export --workspace @tapjob/mobile`, which proves Metro can bundle it too.
import { createApiClient, endpoints, isApiError } from "@tapjob/contracts";

describe("@tapjob/contracts smoke", () => {
  test("a generated SDK call round-trips through a mocked fetch", async () => {
    const mockHealthResponse = {
      status: "ok",
      service: "api",
      version: "0.1.0",
      environment: "local",
    };
    const mockFetch = jest.fn(async (request: Request) => {
      expect(request.url).toBe("https://api.example.com/health");
      return new Response(JSON.stringify(mockHealthResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const client = createApiClient({ baseUrl: "https://api.example.com", fetch: mockFetch });
    const { data, error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(error).toBeUndefined();
    expect(data).toEqual(mockHealthResponse);
  });

  test("a mocked 500 ErrorResponse is both delivered and narrowed by isApiError", async () => {
    const mockErrorResponse = {
      code: "INTERNAL_ERROR",
      message: "Ocorreu um erro inesperado.",
      requestId: "trace-mobile-smoke-1",
    };
    const mockFetch = jest.fn(
      async () =>
        new Response(JSON.stringify(mockErrorResponse), {
          status: 500,
          headers: { "content-type": "application/json" },
        }),
    ) as unknown as typeof fetch;

    const client = createApiClient({ baseUrl: "https://api.example.com", fetch: mockFetch });
    const { data, error } = await endpoints.getHealth({ client });

    expect(data).toBeUndefined();
    expect(isApiError(error)).toBe(true);
    if (isApiError(error)) {
      expect(error.code).toBe("INTERNAL_ERROR");
      expect(error.requestId).toBe("trace-mobile-smoke-1");
    }
  });

  test("isApiError rejects values that are not a well-formed ErrorResponse", () => {
    expect(isApiError({ code: "RESOURCE_NOT_FOUND", message: "x", requestId: "y" })).toBe(true);
    expect(isApiError({})).toBe(false);
    expect(isApiError(null)).toBe(false);
    expect(isApiError("RESOURCE_NOT_FOUND")).toBe(false);
  });

  test("getAccessToken attaches a bearer token via a request interceptor", async () => {
    let seenAuthorization: string | null = null;
    const mockFetch = jest.fn(async (request: Request) => {
      seenAuthorization = request.headers.get("Authorization");
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => "secret-token-value",
    });
    await endpoints.getHealth({ client });

    expect(seenAuthorization).toBe("Bearer secret-token-value");
  });

  // -- invalid/revoked session handling (2026 independent-review round 2 finding 4) -------------

  function errorResponse(code: string, status = 401): Response {
    return new Response(JSON.stringify({ code, message: "x", requestId: "trace-1" }), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  test("AUTH_TOKEN_REVOKED calls onSessionInvalid and never retries", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_REVOKED"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    await endpoints.getHealth({ client });

    expect(onSessionInvalid).toHaveBeenCalledWith("AUTH_TOKEN_REVOKED");
    expect(mockFetch).toHaveBeenCalledTimes(1); // no retry for a revoked token -- it is terminal.
  });

  test("AUTH_TOKEN_INVALID triggers exactly one forced-refresh retry, which can then succeed", async () => {
    const seenAuthorizations: (string | null)[] = [];
    const mockFetch = jest.fn(async (request: Request) => {
      seenAuthorizations.push(request.headers.get("Authorization"));
      if (seenAuthorizations.length === 1) {
        return errorResponse("AUTH_TOKEN_INVALID");
      }
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as unknown as typeof fetch;
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: (options) => (options?.forceRefresh ? "refreshed-token" : "stale-token"),
      onSessionInvalid,
    });
    const { data, error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2); // the original attempt plus exactly one retry.
    expect(seenAuthorizations).toEqual(["Bearer stale-token", "Bearer refreshed-token"]);
    expect(error).toBeUndefined();
    expect(data).toEqual({ status: "ok" }); // the retry's response is what the caller sees.
    expect(onSessionInvalid).not.toHaveBeenCalled(); // the retry succeeded -- never terminal.
  });

  test("AUTH_TOKEN_INVALID with the retry still failing calls onSessionInvalid, never loops again", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_INVALID"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2); // original + exactly one retry, never a third call.
    expect(onSessionInvalid).toHaveBeenCalledTimes(1);
    expect(onSessionInvalid).toHaveBeenCalledWith("AUTH_TOKEN_INVALID");
  });

  test("AUTH_TOKEN_INVALID never retries when a refreshed token cannot be obtained", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_INVALID"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: (options) => (options?.forceRefresh ? null : "token-1"),
      onSessionInvalid,
    });
    await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(1); // no refreshed token -- no retry attempted at all.
    expect(onSessionInvalid).toHaveBeenCalledWith("AUTH_TOKEN_INVALID");
  });

  // -- round 3: a throwing forced refresh, and the retry's OWN error code (not assumed) ----------

  test("a forced refresh that THROWS is treated as terminal -- never an unhandled rejection, never a second fetch", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_INVALID"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: (options) => {
        if (options?.forceRefresh) {
          throw new Error("provider-network-error");
        }
        return "token-1";
      },
      onSessionInvalid,
    });
    const { error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(1); // the refresh threw before any retry fetch happened.
    expect(onSessionInvalid).toHaveBeenCalledTimes(1);
    expect(onSessionInvalid).toHaveBeenCalledWith("AUTH_TOKEN_INVALID");
    // The caller still sees the ORIGINAL canonical ErrorResponse, never the provider's own error.
    expect(isApiError(error)).toBe(true);
    if (isApiError(error)) {
      expect(error.code).toBe("AUTH_TOKEN_INVALID");
    }
  });

  test("a retry that comes back AUTH_TOKEN_REVOKED is reported as revoked, not invalid", async () => {
    const mockFetch = jest
      .fn()
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_INVALID"))
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_REVOKED"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch as unknown as typeof fetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    const { error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2); // original + exactly one retry, never a third call.
    expect(onSessionInvalid).toHaveBeenCalledTimes(1);
    expect(onSessionInvalid).toHaveBeenCalledWith("AUTH_TOKEN_REVOKED"); // the retry's OWN code.
    if (isApiError(error)) {
      expect(error.code).toBe("AUTH_TOKEN_REVOKED");
    }
  });

  test("a retry that comes back REAUTHENTICATION_REQUIRED never ends the global session", async () => {
    const mockFetch = jest
      .fn()
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_INVALID"))
      .mockResolvedValueOnce(errorResponse("REAUTHENTICATION_REQUIRED"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch as unknown as typeof fetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    const { error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(onSessionInvalid).not.toHaveBeenCalled(); // a security-sensitive endpoint behind a now-
    // valid refreshed token legitimately asks for recent auth -- never a dead session.
    expect(isApiError(error)).toBe(true);
    if (isApiError(error)) {
      expect(error.code).toBe("REAUTHENTICATION_REQUIRED"); // delivered to the caller as-is.
    }
  });

  test("a retry that comes back AUTH_TOKEN_MISSING never triggers a global logout", async () => {
    const mockFetch = jest
      .fn()
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_INVALID"))
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_MISSING"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch as unknown as typeof fetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(onSessionInvalid).not.toHaveBeenCalled();
  });

  test("a retry that comes back an unknown/non-canonical 401 code is never silently reclassified", async () => {
    const mockFetch = jest
      .fn()
      .mockResolvedValueOnce(errorResponse("AUTH_TOKEN_INVALID"))
      .mockResolvedValueOnce(errorResponse("SOME_FUTURE_CODE"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch as unknown as typeof fetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    const { error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(onSessionInvalid).not.toHaveBeenCalled(); // never guessed to be AUTH_TOKEN_INVALID/REVOKED.
    if (isApiError(error)) {
      expect(error.code).toBe("SOME_FUTURE_CODE"); // delivered to the caller unchanged.
    }
  });

  test("REAUTHENTICATION_REQUIRED is a per-operation 401 that never triggers onSessionInvalid", async () => {
    const mockFetch = jest.fn(async () => errorResponse("REAUTHENTICATION_REQUIRED"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => "token-1",
      onSessionInvalid,
    });
    const { error } = await endpoints.getHealth({ client });

    expect(mockFetch).toHaveBeenCalledTimes(1); // never retried either -- not AUTH_TOKEN_INVALID.
    expect(onSessionInvalid).not.toHaveBeenCalled();
    expect(isApiError(error)).toBe(true);
    if (isApiError(error)) {
      expect(error.code).toBe("REAUTHENTICATION_REQUIRED"); // still delivered to the caller.
    }
  });

  test("AUTH_TOKEN_MISSING is delivered to the caller without triggering onSessionInvalid", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_MISSING"));
    const onSessionInvalid = jest.fn();

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => null,
      onSessionInvalid,
    });
    await endpoints.getHealth({ client });

    expect(onSessionInvalid).not.toHaveBeenCalled();
  });

  test("the bearer token is never visible in a thrown/rejected error value", async () => {
    const mockFetch = jest.fn(async () => errorResponse("AUTH_TOKEN_INVALID"));

    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetch: mockFetch,
      getAccessToken: () => "super-secret-token-value",
    });
    const { error } = await endpoints.getHealth({ client });

    expect(JSON.stringify(error)).not.toMatch(/super-secret-token-value/);
  });
});

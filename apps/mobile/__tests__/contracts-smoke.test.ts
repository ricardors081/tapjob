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
});

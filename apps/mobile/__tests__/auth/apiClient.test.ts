/**
 * Proves `apiClient.ts`'s wiring choices directly (2026 independent-review round 2 finding 4):
 * `getAccessToken` forwards `forceRefresh` to `AuthService.getIdToken`, and `onSessionInvalid`
 * reuses `AuthService.signOut()` rather than a parallel mechanism. `createApiClient` itself is
 * mocked -- its own retry/session-invalid logic is covered by `contracts-smoke.test.ts`.
 */
const mockGetIdToken = jest.fn(() => Promise.resolve("fake-id-token"));
const mockSignOut = jest.fn(() => Promise.resolve());

jest.mock("../../src/auth/firebaseAuthService", () => ({
  getAuthService: () => ({ getIdToken: mockGetIdToken, signOut: mockSignOut }),
}));

jest.mock("@tapjob/contracts", () => ({
  createApiClient: jest.fn((config: unknown) => config),
}));

// The mocked `createApiClient` above is an identity function, so this IS the config object
// `apiClient.ts` built.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { apiClient } = require("../../src/auth/apiClient") as {
  apiClient: {
    getAccessToken: (options?: { forceRefresh?: boolean }) => Promise<string>;
    onSessionInvalid: (code: string) => void;
  };
};

describe("apiClient", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("getAccessToken forwards options (e.g. forceRefresh) to AuthService.getIdToken", async () => {
    await apiClient.getAccessToken({ forceRefresh: true });

    expect(mockGetIdToken).toHaveBeenCalledWith({ forceRefresh: true });
  });

  test("onSessionInvalid reuses AuthService.signOut(), not a separate mechanism", () => {
    apiClient.onSessionInvalid("AUTH_TOKEN_REVOKED");

    expect(mockSignOut).toHaveBeenCalledTimes(1);
  });
});

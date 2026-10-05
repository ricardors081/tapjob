import { act, render, screen, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";

import { endpoints } from "@tapjob/contracts";

import { AuthProvider, useAuthState, useRetryBootstrap } from "../../src/auth/AuthContext";
import type { AuthState } from "../../src/auth/types";

jest.mock("@tapjob/contracts", () => ({
  createApiClient: jest.fn(() => ({})),
  endpoints: { bootstrapAccount: jest.fn(() => Promise.resolve({})) },
}));

jest.mock("../../src/auth/firebaseAuthService", () => {
  let currentState: { status: string; user?: { uid: string; email: string | null } } = {
    status: "loading",
  };
  let listener: ((state: unknown) => void) | null = null;

  return {
    getAuthService: () => ({
      getState: () => currentState,
      subscribe: (callback: (state: unknown) => void) => {
        listener = callback;
        callback(currentState);
        return () => {
          listener = null;
        };
      },
      getIdToken: jest.fn(() => Promise.resolve(null)),
    }),
    __setState: (next: typeof currentState) => {
      currentState = next;
      listener?.(currentState);
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const firebaseAuthServiceMock = require("../../src/auth/firebaseAuthService") as {
  __setState: (state: AuthState) => void;
};

const bootstrapAccountMock = endpoints.bootstrapAccount as jest.Mock;

function StatusProbe() {
  const state = useAuthState();
  const retryBootstrap = useRetryBootstrap();
  return (
    <>
      <Text testID="status">{state.status}</Text>
      {state.status === "signedInBootstrapError" ? (
        <Text testID="retry" onPress={retryBootstrap}>
          retry
        </Text>
      ) : null}
    </>
  );
}

/** Sets the fake auth state and flushes React's resulting passive-effect pass. A bare sync
 * `act(() => __setState(...))` does not reliably flush the subscription-driven `setState` call in
 * this RN/React version combo -- an awaited microtask inside the act callback does. */
async function setAuthState(next: AuthState): Promise<void> {
  await act(async () => {
    firebaseAuthServiceMock.__setState(next);
    await Promise.resolve();
  });
}

describe("<AuthProvider />", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    bootstrapAccountMock.mockResolvedValue({});
    firebaseAuthServiceMock.__setState({ status: "loading" });
  });

  test("does not bootstrap while loading or signed out", async () => {
    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    expect(endpoints.bootstrapAccount).not.toHaveBeenCalled();
  });

  test("bootstraps exactly once when transitioning to signed in, and becomes ready on success", async () => {
    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInReady");
    });
    expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);
  });

  test("reports signedInBootstrapping while the bootstrap call is still pending", async () => {
    let resolveBootstrap: (() => void) | null = null;
    bootstrapAccountMock.mockReturnValueOnce(
      new Promise<object>((resolve) => {
        resolveBootstrap = () => resolve({});
      }),
    );

    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });

    expect(screen.getByTestId("status")).toHaveTextContent("signedInBootstrapping");

    await act(async () => {
      resolveBootstrap?.();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInReady");
    });
  });

  test("does not re-bootstrap for the same uid on a redundant state update", async () => {
    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);
    });

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });

    expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);
  });

  test("bootstraps again for a DIFFERENT uid (e.g. sign-out then a different sign-in)", async () => {
    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);
    });

    await setAuthState({ status: "signedOut" });
    expect(screen.getByTestId("status")).toHaveTextContent("signedOut");

    await setAuthState({ status: "signedIn", user: { uid: "uid-2", email: null } });

    await waitFor(() => {
      expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(2);
    });
  });

  test("a first bootstrap failure surfaces as a real, visible error state -- not a silent reset", async () => {
    bootstrapAccountMock.mockRejectedValueOnce(new Error("network error"));

    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInBootstrapError");
    });
  });

  test("retryBootstrap after a failure succeeds without a second automatic attempt in between", async () => {
    bootstrapAccountMock.mockRejectedValueOnce(new Error("network error"));

    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInBootstrapError");
    });
    expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);

    await act(async () => {
      screen.getByTestId("retry").props.onPress();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInReady");
    });
    expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(2);
  });

  test("a persistent failure does not retry automatically or loop", async () => {
    bootstrapAccountMock.mockRejectedValue(new Error("network error"));

    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInBootstrapError");
    });

    // No further automatic attempts, however long we wait, without an explicit retry.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(endpoints.bootstrapAccount).toHaveBeenCalledTimes(1);
  });

  test("signing out resets bootstrap status, not just the raw auth state", async () => {
    bootstrapAccountMock.mockRejectedValueOnce(new Error("network error"));

    await render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await setAuthState({ status: "signedIn", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signedInBootstrapError");
    });

    await setAuthState({ status: "signedOut" });

    expect(screen.getByTestId("status")).toHaveTextContent("signedOut");
  });
});

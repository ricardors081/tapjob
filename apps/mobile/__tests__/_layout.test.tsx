import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import * as ExpoFont from "expo-font";
import * as SplashScreen from "expo-splash-screen";

import RootLayout from "../src/app/_layout";

// jest.mock factories may only reference out-of-scope values lazily via require(), not imports.
// Stack renders a visible marker so tests can assert the real app rendered, not just "something".
jest.mock("expo-router", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { createElement } = require("react") as typeof import("react");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { View } = require("react-native") as typeof import("react-native");
  return {
    Stack: () => createElement(View, { testID: "stack" }),
    useRouter: () => ({ replace: jest.fn() }),
    useSegments: () => [],
  };
});

jest.mock("expo-splash-screen", () => ({
  preventAutoHideAsync: jest.fn(),
  hideAsync: jest.fn(),
}));

jest.mock("expo-font", () => ({
  useFonts: jest.fn(),
}));

// The real SDK schedules an internal cleanup timer on import regardless of init(), which leaves a
// Jest worker handle open; _layout.tsx is the only place in this app that imports it transitively.
jest.mock("@sentry/react-native", () => ({
  init: jest.fn(),
}));

// `_layout.tsx`'s `AuthGate` only needs `useAuthState`/`useRetryBootstrap`/`AuthProvider` -- the
// bootstrap state machine itself is AuthContext's own concern and is exercised exhaustively by
// AuthContext.test.tsx. This mock isolates AuthGate's branching (does it render the protected
// shell, the bootstrapping message, or the error+retry message for a given account state) from
// that machinery, with a tiny pub-sub so `useAuthState()` re-renders on `__setAccountState`.
jest.mock("../src/auth/AuthContext", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require("react") as typeof import("react");
  let currentState: { status: string; user?: { uid: string; email: string | null } } = {
    status: "loading",
  };
  const listeners = new Set<() => void>();
  const retryBootstrap = jest.fn();

  return {
    AuthProvider: ({ children }: { children: React.ReactNode }) => children,
    useAuthState: () => {
      const [state, setState] = React.useState(currentState);
      React.useEffect(() => {
        const listener = () => setState(currentState);
        listeners.add(listener);
        listener();
        return () => {
          listeners.delete(listener);
        };
      }, []);
      return state;
    },
    useRetryBootstrap: () => retryBootstrap,
    __setAccountState: (next: typeof currentState) => {
      currentState = next;
      for (const listener of listeners) {
        listener();
      }
    },
    __retryBootstrapMock: retryBootstrap,
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const authContextMock = require("../src/auth/AuthContext") as {
  __setAccountState: (next: {
    status: string;
    user?: { uid: string; email: string | null };
  }) => void;
  __retryBootstrapMock: jest.Mock;
};

const useFontsMock = ExpoFont.useFonts as jest.Mock;
const hideAsyncMock = SplashScreen.hideAsync as jest.Mock;

async function setAccountState(next: {
  status: string;
  user?: { uid: string; email: string | null };
}): Promise<void> {
  await act(async () => {
    authContextMock.__setAccountState(next);
    await Promise.resolve();
  });
}

describe("<RootLayout />", () => {
  beforeEach(() => {
    useFontsMock.mockClear();
    hideAsyncMock.mockClear();
    authContextMock.__retryBootstrapMock.mockClear();
  });

  test("renders nothing while fonts are still loading, and never hides the splash screen early", async () => {
    useFontsMock.mockReturnValue([false, null]);

    await render(<RootLayout />);

    expect(screen.queryByTestId("stack")).toBeNull();
    expect(hideAsyncMock).not.toHaveBeenCalled();
  });

  test("renders the app once fonts finish loading", async () => {
    useFontsMock.mockReturnValue([true, null]);
    await setAccountState({ status: "signedInReady", user: { uid: "uid-1", email: null } });

    await render(<RootLayout />);

    expect(screen.getByTestId("stack")).toBeTruthy();
    expect(hideAsyncMock).toHaveBeenCalled();
  });

  test("still renders the app if font loading errors, instead of hanging", async () => {
    useFontsMock.mockReturnValue([false, new Error("network error")]);
    await setAccountState({ status: "signedInReady", user: { uid: "uid-1", email: null } });

    await render(<RootLayout />);

    expect(screen.getByTestId("stack")).toBeTruthy();
    expect(hideAsyncMock).toHaveBeenCalled();
  });

  test("does not expose the protected shell while the account is still bootstrapping (2026 review finding 4)", async () => {
    useFontsMock.mockReturnValue([true, null]);
    await setAccountState({
      status: "signedInBootstrapping",
      user: { uid: "uid-1", email: null },
    });

    const view = await render(<RootLayout />);

    expect(screen.queryByTestId("stack")).toBeNull();
    expect(screen.getByText("Preparando sua conta...")).toBeTruthy();

    await setAccountState({ status: "signedInReady", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(screen.getByTestId("stack")).toBeTruthy();
    });

    await act(async () => {
      view.unmount();
    });
  });

  test("does not expose the protected shell when bootstrap fails, and offers a retry instead", async () => {
    useFontsMock.mockReturnValue([true, null]);
    await setAccountState({
      status: "signedInBootstrapError",
      user: { uid: "uid-1", email: null },
    });

    const view = await render(<RootLayout />);

    expect(screen.queryByTestId("stack")).toBeNull();
    expect(screen.getByText("Não foi possível preparar sua conta.")).toBeTruthy();

    fireEvent.press(screen.getByText("Tentar novamente"));
    expect(authContextMock.__retryBootstrapMock).toHaveBeenCalledTimes(1);

    await setAccountState({ status: "signedInReady", user: { uid: "uid-1", email: null } });
    await waitFor(() => {
      expect(screen.getByTestId("stack")).toBeTruthy();
    });

    await act(async () => {
      view.unmount();
    });
  });
});

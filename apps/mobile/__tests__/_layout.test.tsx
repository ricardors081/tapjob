import { render, screen } from "@testing-library/react-native";
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

const useFontsMock = ExpoFont.useFonts as jest.Mock;
const hideAsyncMock = SplashScreen.hideAsync as jest.Mock;

describe("<RootLayout />", () => {
  beforeEach(() => {
    useFontsMock.mockClear();
    hideAsyncMock.mockClear();
  });

  test("renders nothing while fonts are still loading, and never hides the splash screen early", async () => {
    useFontsMock.mockReturnValue([false, null]);

    await render(<RootLayout />);

    expect(screen.queryByTestId("stack")).toBeNull();
    expect(hideAsyncMock).not.toHaveBeenCalled();
  });

  test("renders the app once fonts finish loading", async () => {
    useFontsMock.mockReturnValue([true, null]);

    await render(<RootLayout />);

    expect(screen.getByTestId("stack")).toBeTruthy();
    expect(hideAsyncMock).toHaveBeenCalled();
  });

  test("still renders the app if font loading errors, instead of hanging", async () => {
    useFontsMock.mockReturnValue([false, new Error("network error")]);

    await render(<RootLayout />);

    expect(screen.getByTestId("stack")).toBeTruthy();
    expect(hideAsyncMock).toHaveBeenCalled();
  });
});

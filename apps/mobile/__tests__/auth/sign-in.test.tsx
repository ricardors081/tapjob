import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { Platform } from "react-native";

import "../../src/i18n"; // SignInScreen uses react-i18next's useTranslation(), which needs an initialized instance.
import SignInScreen from "../../src/app/sign-in";
import { getAuthService } from "../../src/auth/firebaseAuthService";

jest.mock("../../src/auth/firebaseAuthService", () => ({
  getAuthService: jest.fn(),
}));

function mockService(overrides: Record<string, jest.Mock> = {}) {
  const service = {
    signInWithEmail: jest.fn(() => Promise.resolve()),
    signUpWithEmail: jest.fn(() => Promise.resolve()),
    signInWithGoogle: jest.fn(() => Promise.resolve()),
    signInWithApple: jest.fn(() => Promise.resolve()),
    signOut: jest.fn(() => Promise.resolve()),
    getIdToken: jest.fn(() => Promise.resolve(null)),
    getState: jest.fn(() => ({ status: "signedOut" })),
    subscribe: jest.fn(() => () => {}),
    ...overrides,
  };
  (getAuthService as jest.Mock).mockReturnValue(service);
  return service;
}

beforeEach(() => {
  jest.clearAllMocks();
  Platform.OS = "ios";
});

describe("<SignInScreen />", () => {
  test("renders email/password fields and both federated provider buttons on iOS", async () => {
    mockService();

    await render(<SignInScreen />);

    expect(screen.getByText("Entrar com Google")).toBeTruthy();
    expect(screen.getByText("Entrar com Apple")).toBeTruthy();
  });

  test("does not render the Apple button on Android -- no supported flow there", async () => {
    Platform.OS = "android";
    mockService();

    await render(<SignInScreen />);

    expect(screen.queryByText("Entrar com Apple")).toBeNull();
  });

  test("pressing sign in calls signInWithEmail with the typed credentials", async () => {
    const service = mockService();

    await render(<SignInScreen />);
    fireEvent.changeText(screen.getByPlaceholderText("E-mail"), "person@example.com");
    await screen.findByDisplayValue("person@example.com");
    fireEvent.changeText(screen.getByPlaceholderText("Senha"), "hunter2-password");
    await screen.findByDisplayValue("hunter2-password");
    fireEvent.press(screen.getByText("Entrar"));

    await waitFor(() => {
      expect(service.signInWithEmail).toHaveBeenCalledWith(
        "person@example.com",
        "hunter2-password",
      );
    });
  });

  test("pressing sign up calls signUpWithEmail", async () => {
    const service = mockService();

    await render(<SignInScreen />);
    fireEvent.changeText(screen.getByPlaceholderText("E-mail"), "person@example.com");
    await screen.findByDisplayValue("person@example.com");
    fireEvent.changeText(screen.getByPlaceholderText("Senha"), "hunter2-password");
    await screen.findByDisplayValue("hunter2-password");
    fireEvent.press(screen.getByText("Criar conta"));

    await waitFor(() => {
      expect(service.signUpWithEmail).toHaveBeenCalledWith(
        "person@example.com",
        "hunter2-password",
      );
    });
  });

  test("pressing the Google button calls signInWithGoogle", async () => {
    const service = mockService();

    await render(<SignInScreen />);
    fireEvent.press(screen.getByText("Entrar com Google"));

    await waitFor(() => {
      expect(service.signInWithGoogle).toHaveBeenCalled();
    });
  });

  test("pressing the Apple button calls signInWithApple", async () => {
    const service = mockService();

    await render(<SignInScreen />);
    fireEvent.press(screen.getByText("Entrar com Apple"));

    await waitFor(() => {
      expect(service.signInWithApple).toHaveBeenCalled();
    });
  });

  test("a provider cancellation/failure shows a generic error, never raw provider details", async () => {
    mockService({
      signInWithGoogle: jest.fn(() => Promise.reject(new Error("auth/popup-closed-by-user"))),
    });

    await render(<SignInScreen />);
    fireEvent.press(screen.getByText("Entrar com Google"));

    await waitFor(() => {
      expect(screen.getByText("Não foi possível autenticar. Tente novamente.")).toBeTruthy();
    });
    expect(screen.queryByText(/auth\/popup-closed-by-user/)).toBeNull();
  });
});

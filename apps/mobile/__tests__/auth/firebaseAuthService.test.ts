import { Platform } from "react-native";

import * as AppleAuthentication from "expo-apple-authentication";
import { GoogleSignin } from "@react-native-google-signin/google-signin";
import * as RNFirebaseAuth from "@react-native-firebase/auth";

import {
  __resetGoogleSignInConfigurationForTests,
  getAuthService,
} from "../../src/auth/firebaseAuthService";

// A plain mutable object (not the real, frozen `publicConfig`) so individual tests can flip
// `googleWebClientId` between a fake value and `undefined`. The factory must not close over an
// outer-scope `const` (Babel hoists `jest.mock`/the `import`s it protects above any later `const`
// in this file, so the factory would see it as still-unassigned) -- `require()`-ing the mocked
// module's own export after registration, same pattern as `AuthContext.test.tsx`, sidesteps that.
jest.mock("../../src/config/public-config", () => ({
  publicConfig: { googleWebClientId: "fake-client-id.apps.googleusercontent.com" },
}));
type MockedPublicConfigModule = {
  publicConfig: { googleWebClientId: string | undefined };
};
// eslint-disable-next-line @typescript-eslint/no-require-imports
const mockPublicConfig = (require("../../src/config/public-config") as MockedPublicConfigModule)
  .publicConfig;

jest.mock("expo-crypto", () => ({
  randomUUID: jest.fn(() => "raw-nonce-1"),
  digestStringAsync: jest.fn(() => Promise.resolve("hashed-nonce-1")),
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
}));

/** The mock Auth's `currentUser` is mutable at runtime (see `__mocks__/@react-native-firebase/
 * auth.js`), but the real SDK's type declares it read-only -- this cast is test-only, confined to
 * this one helper. */
function setMockCurrentUser(user: { uid: string; email: string | null } | null): void {
  (RNFirebaseAuth.getAuth() as { currentUser: unknown }).currentUser = user;
}

beforeEach(() => {
  jest.clearAllMocks();
  Platform.OS = "ios";
  setMockCurrentUser(null);
  mockPublicConfig.googleWebClientId = "fake-client-id.apps.googleusercontent.com";
  __resetGoogleSignInConfigurationForTests();
});

describe("firebaseAuthService", () => {
  test("initial state is signed out once the (mocked) auth replay fires", () => {
    expect(getAuthService().getState()).toEqual({ status: "signedOut" });
  });

  test("subscribe replays the current state immediately", () => {
    const seen: unknown[] = [];

    const unsubscribe = getAuthService().subscribe((state) => seen.push(state));

    expect(seen).toEqual([{ status: "signedOut" }]);
    unsubscribe();
  });

  test("signInWithEmail delegates to signInWithEmailAndPassword with the auth instance", async () => {
    await getAuthService().signInWithEmail("person@example.com", "hunter2-password");

    expect(RNFirebaseAuth.signInWithEmailAndPassword).toHaveBeenCalledWith(
      expect.anything(),
      "person@example.com",
      "hunter2-password",
    );
  });

  test("signUpWithEmail delegates to createUserWithEmailAndPassword", async () => {
    await getAuthService().signUpWithEmail("person@example.com", "hunter2-password");

    expect(RNFirebaseAuth.createUserWithEmailAndPassword).toHaveBeenCalledWith(
      expect.anything(),
      "person@example.com",
      "hunter2-password",
    );
  });

  test("a sign-in failure propagates to the caller (no silent swallow)", async () => {
    (RNFirebaseAuth.signInWithEmailAndPassword as jest.Mock).mockRejectedValueOnce(
      new Error("auth/wrong-password"),
    );

    await expect(getAuthService().signInWithEmail("person@example.com", "wrong")).rejects.toThrow(
      "auth/wrong-password",
    );
  });

  test("signOut delegates to the SDK's signOut", async () => {
    await getAuthService().signOut();

    expect(RNFirebaseAuth.signOut).toHaveBeenCalledWith(expect.anything());
  });

  test("getIdToken returns null when signed out, without calling the SDK", async () => {
    // `getAuth()` returns the same shared mock Auth object every call (as the real SDK does for
    // one app); mutate its `currentUser` rather than re-mocking the return value, so the already-
    // constructed service (which captured that same object reference) observes the change.
    setMockCurrentUser(null);

    const token = await getAuthService().getIdToken();

    expect(token).toBeNull();
    expect(RNFirebaseAuth.getIdToken).not.toHaveBeenCalled();
  });

  test("getIdToken reads the current user's token when signed in", async () => {
    const fakeUser = { uid: "uid-1", email: "person@example.com" };
    setMockCurrentUser(fakeUser);

    const token = await getAuthService().getIdToken();

    expect(token).toBe("fake-id-token");
    expect(RNFirebaseAuth.getIdToken).toHaveBeenCalledWith(fakeUser, undefined);
  });

  test("getIdToken({ forceRefresh: true }) forces a fresh token from the provider, not the cached one", async () => {
    const fakeUser = { uid: "uid-1", email: "person@example.com" };
    setMockCurrentUser(fakeUser);

    await getAuthService().getIdToken({ forceRefresh: true });

    expect(RNFirebaseAuth.getIdToken).toHaveBeenCalledWith(fakeUser, true);
  });

  test("signInWithGoogle exchanges the Google ID token for a Firebase credential", async () => {
    await getAuthService().signInWithGoogle();

    expect(GoogleSignin.hasPlayServices).toHaveBeenCalled();
    expect(GoogleSignin.signIn).toHaveBeenCalled();
    expect(RNFirebaseAuth.GoogleAuthProvider.credential).toHaveBeenCalledWith(
      "fake-google-id-token",
    );
    expect(RNFirebaseAuth.signInWithCredential).toHaveBeenCalled();
  });

  test("signInWithGoogle rejects (cancellation-safe) when no ID token comes back", async () => {
    (GoogleSignin.signIn as jest.Mock).mockResolvedValueOnce({ data: null });

    await expect(getAuthService().signInWithGoogle()).rejects.toThrow(
      "Google sign-in did not return an ID token.",
    );
    expect(RNFirebaseAuth.signInWithCredential).not.toHaveBeenCalled();
  });

  test("signInWithGoogle configures GoogleSignin before calling signIn, with the typed web client ID", async () => {
    const callOrder: string[] = [];
    (GoogleSignin.configure as jest.Mock).mockImplementation(() => callOrder.push("configure"));
    (GoogleSignin.signIn as jest.Mock).mockImplementation(() => {
      callOrder.push("signIn");
      return Promise.resolve({ data: { idToken: "fake-google-id-token" } });
    });

    await getAuthService().signInWithGoogle();

    expect(GoogleSignin.configure).toHaveBeenCalledWith({
      webClientId: "fake-client-id.apps.googleusercontent.com",
    });
    expect(callOrder).toEqual(["configure", "signIn"]);
  });

  test("signInWithGoogle configures GoogleSignin exactly once across repeated sign-ins", async () => {
    await getAuthService().signInWithGoogle();
    await getAuthService().signInWithGoogle();

    expect(GoogleSignin.configure).toHaveBeenCalledTimes(1);
  });

  test("signInWithGoogle fails closed with a clear error when no web client ID is configured", async () => {
    mockPublicConfig.googleWebClientId = undefined;

    await expect(getAuthService().signInWithGoogle()).rejects.toThrow(
      "Google sign-in is not configured for this environment: set EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID.",
    );
    expect(GoogleSignin.configure).not.toHaveBeenCalled();
    expect(GoogleSignin.signIn).not.toHaveBeenCalled();
  });

  test("a cancellation after configure leaves no partial state -- a later retry still signs in", async () => {
    (GoogleSignin.signIn as jest.Mock).mockRejectedValueOnce(
      new Error("auth/popup-closed-by-user"),
    );

    await expect(getAuthService().signInWithGoogle()).rejects.toThrow("auth/popup-closed-by-user");
    expect(RNFirebaseAuth.signInWithCredential).not.toHaveBeenCalled();

    await getAuthService().signInWithGoogle();

    expect(RNFirebaseAuth.signInWithCredential).toHaveBeenCalledTimes(1);
  });

  test("signInWithApple exchanges the Apple identity token for a Firebase credential on iOS", async () => {
    Platform.OS = "ios";

    await getAuthService().signInWithApple();

    expect(AppleAuthentication.signInAsync).toHaveBeenCalled();
    expect(RNFirebaseAuth.signInWithCredential).toHaveBeenCalled();
  });

  test("signInWithApple sends a hashed anti-replay nonce to Apple and the raw nonce to Firebase", async () => {
    Platform.OS = "ios";

    await getAuthService().signInWithApple();

    expect(AppleAuthentication.signInAsync).toHaveBeenCalledWith(
      expect.objectContaining({ nonce: "hashed-nonce-1" }),
    );
    const providerMock = RNFirebaseAuth.OAuthProvider as unknown as jest.Mock;
    const providerInstance = providerMock.mock.results[0]?.value as { credential: jest.Mock };
    expect(providerInstance.credential).toHaveBeenCalledWith(
      expect.objectContaining({ rawNonce: "raw-nonce-1" }),
    );
  });

  test("signInWithApple refuses on Android -- no supported flow there", async () => {
    Platform.OS = "android";

    await expect(getAuthService().signInWithApple()).rejects.toThrow(
      "Sign in with Apple is only supported on iOS.",
    );
    expect(AppleAuthentication.signInAsync).not.toHaveBeenCalled();
  });

  test("signInWithApple rejects (cancellation-safe) when no identity token comes back", async () => {
    Platform.OS = "ios";
    (AppleAuthentication.signInAsync as jest.Mock).mockResolvedValueOnce({ identityToken: null });

    await expect(getAuthService().signInWithApple()).rejects.toThrow(
      "Apple sign-in did not return an identity token.",
    );
    expect(RNFirebaseAuth.signInWithCredential).not.toHaveBeenCalled();
  });
});

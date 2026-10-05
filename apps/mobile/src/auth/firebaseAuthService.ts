/**
 * The real `AuthService` (docs/adr/0009-identity-auth-user-account.md §I), backed by React Native
 * Firebase's modular Auth API -- never the Firebase JS SDK (§H: its only RN persistence stores raw
 * tokens in unencrypted AsyncStorage). Session persistence is entirely provider-owned; this module
 * never reads or writes a Firebase ID/refresh token to `AsyncStorage` itself.
 *
 * Google/Apple credentials are exchanged for a Firebase credential via
 * `signInWithCredential` -- no Google/Apple provider access token is ever sent to or stored by
 * TapJob; only the resulting Firebase ID token leaves this module (via `getIdToken`).
 */
import { Platform } from "react-native";

import * as AppleAuthentication from "expo-apple-authentication";
import * as Crypto from "expo-crypto";
import { GoogleSignin } from "@react-native-google-signin/google-signin";
import { getApp } from "@react-native-firebase/app";
import {
  getAuth,
  GoogleAuthProvider,
  OAuthProvider,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithCredential,
  signInWithEmailAndPassword,
  signOut as firebaseSignOut,
  getIdToken as firebaseGetIdToken,
  type Auth,
  type User,
} from "@react-native-firebase/auth";

import { publicConfig } from "../config/public-config";
import type { AuthService, AuthState, AuthenticatedUser } from "./types";

// Module-level, not per-instance: `GoogleSignin.configure()` is itself a process-wide call into
// native state, so "configured once" means once per process, matching the singleton `AuthService`
// below (2026 review finding 2: `GoogleSignin.configure()` must run before the first `signIn()`).
let googleSignInConfigured = false;

function configureGoogleSignInOnce(): void {
  if (googleSignInConfigured) {
    return;
  }
  const webClientId = publicConfig.googleWebClientId;
  if (!webClientId) {
    // Fails closed and explicitly, rather than calling an unconfigured native SDK and surfacing an
    // opaque provider error later (docs/ENGINEERING_STANDARDS.md §2: no magic/implicit config).
    throw new Error(
      "Google sign-in is not configured for this environment: set EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID.",
    );
  }
  GoogleSignin.configure({ webClientId });
  googleSignInConfigured = true;
}

function toAuthenticatedUser(user: User): AuthenticatedUser {
  return { uid: user.uid, email: user.email };
}

function toAuthState(user: User | null): AuthState {
  return user ? { status: "signedIn", user: toAuthenticatedUser(user) } : { status: "signedOut" };
}

class FirebaseAuthService implements AuthService {
  private readonly auth: Auth;
  private state: AuthState = { status: "loading" };
  private readonly listeners = new Set<(state: AuthState) => void>();

  constructor() {
    this.auth = getAuth(getApp());
    onAuthStateChanged(this.auth, (user) => {
      this.state = toAuthState(user);
      for (const listener of this.listeners) {
        listener(this.state);
      }
    });
  }

  getState(): AuthState {
    return this.state;
  }

  subscribe(listener: (state: AuthState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async signInWithEmail(email: string, password: string): Promise<void> {
    await signInWithEmailAndPassword(this.auth, email, password);
  }

  async signUpWithEmail(email: string, password: string): Promise<void> {
    await createUserWithEmailAndPassword(this.auth, email, password);
  }

  async signInWithGoogle(): Promise<void> {
    configureGoogleSignInOnce();
    await GoogleSignin.hasPlayServices();
    const response = await GoogleSignin.signIn();
    const idToken = response.data?.idToken;
    if (!idToken) {
      throw new Error("Google sign-in did not return an ID token.");
    }
    const credential = GoogleAuthProvider.credential(idToken);
    await signInWithCredential(this.auth, credential);
  }

  async signInWithApple(): Promise<void> {
    if (Platform.OS !== "ios") {
      // No supported Apple flow on Android (docs/adr/0009-...md §I) -- never attempted.
      throw new Error("Sign in with Apple is only supported on iOS.");
    }
    // Anti-replay nonce (Apple's and Firebase's documented native Sign in with Apple pattern,
    // 2026 WP1.1 review): Apple signs the HASHED nonce into the identity token it returns, and
    // Firebase independently re-hashes the RAW nonce it is given to confirm the two match -- so a
    // captured identity token alone can never be replayed against a different sign-in attempt.
    const rawNonce = Crypto.randomUUID();
    const hashedNonce = await Crypto.digestStringAsync(
      Crypto.CryptoDigestAlgorithm.SHA256,
      rawNonce,
    );
    const appleCredential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
      nonce: hashedNonce,
    });
    if (!appleCredential.identityToken) {
      throw new Error("Apple sign-in did not return an identity token.");
    }
    const provider = new OAuthProvider("apple.com");
    const firebaseCredential = provider.credential({
      idToken: appleCredential.identityToken,
      rawNonce,
    });
    await signInWithCredential(this.auth, firebaseCredential);
  }

  async signOut(): Promise<void> {
    await firebaseSignOut(this.auth);
  }

  async getIdToken(options?: { forceRefresh?: boolean }): Promise<string | null> {
    const user = this.auth.currentUser;
    if (!user) {
      return null;
    }
    return firebaseGetIdToken(user, options?.forceRefresh);
  }
}

let instance: AuthService | null = null;

/** Process-wide singleton, created lazily on first use (never at module import time, so importing
 * this file in a test that mocks every native module never touches real Firebase). */
export function getAuthService(): AuthService {
  instance ??= new FirebaseAuthService();
  return instance;
}

/** Test-only: resets the module-level "has `GoogleSignin.configure()` run yet" flag. Never called
 * from production code -- `configureGoogleSignInOnce()` is a real once-per-process guard there. */
export function __resetGoogleSignInConfigurationForTests(): void {
  googleSignInConfigured = false;
}

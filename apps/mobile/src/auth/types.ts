/**
 * The one shape every screen/consumer sees for the auth boundary (docs/adr/0009-identity-auth-
 * user-account.md §I). `AuthService` is implemented by `firebaseAuthService.ts` in production and
 * by a hand-written fake in tests -- no test in this app ever exercises a real provider network.
 */

export interface AuthenticatedUser {
  readonly uid: string;
  readonly email: string | null;
}

export type AuthState =
  | { readonly status: "loading" }
  | { readonly status: "signedOut" }
  | { readonly status: "signedIn"; readonly user: AuthenticatedUser };

export interface AuthService {
  getState(): AuthState;
  /** Returns an unsubscribe function. The listener also fires once immediately with the current
   * state, mirroring `onAuthStateChanged`'s own replay behavior. */
  subscribe(listener: (state: AuthState) => void): () => void;
  signInWithEmail(email: string, password: string): Promise<void>;
  signUpWithEmail(email: string, password: string): Promise<void>;
  signInWithGoogle(): Promise<void>;
  /** Throws on Android -- there is no supported Apple flow there (docs/adr/0009-...md §I). */
  signInWithApple(): Promise<void>;
  signOut(): Promise<void>;
  /** `null` when signed out; never throws for "no user" -- only for a genuine provider error.
   * `forceRefresh: true` forces a fresh token from the provider instead of the SDK's locally
   * cached one -- used for the single bounded retry after the backend rejects a token as
   * `AUTH_TOKEN_INVALID` (docs/adr/0009-identity-auth-user-account.md "invalid/revoked session
   * lifecycle", 2026 independent-review round 2 finding 4). */
  getIdToken(options?: { forceRefresh?: boolean }): Promise<string | null>;
}

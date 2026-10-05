/**
 * React context over `AuthService` (docs/adr/0009-identity-auth-user-account.md §I). Also triggers
 * `POST /api/v1/auth/bootstrap` once per newly-signed-in identity -- the one place this app calls
 * bootstrap, never implicitly from any other screen/request.
 *
 * Firebase being "signed in" is deliberately NOT treated as the TapJob account being ready (2026
 * independent-review finding 4): `AccountState` tracks bootstrap readiness as its own explicit
 * state, separate from the raw Firebase `AuthState`, so a transient bootstrap failure is a real,
 * visible, retryable state rather than a silently-reset ref that never triggers another attempt.
 */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { endpoints } from "@tapjob/contracts";

import { apiClient } from "./apiClient";
import { getAuthService } from "./firebaseAuthService";
import type { AuthenticatedUser, AuthState } from "./types";

type BootstrapStatus = "idle" | "bootstrapping" | "ready" | "error";

interface BootstrapTracking {
  readonly uid: string | null;
  readonly status: BootstrapStatus;
}

export type AccountState =
  | { readonly status: "loading" }
  | { readonly status: "signedOut" }
  | { readonly status: "signedInBootstrapping"; readonly user: AuthenticatedUser }
  | { readonly status: "signedInReady"; readonly user: AuthenticatedUser }
  | { readonly status: "signedInBootstrapError"; readonly user: AuthenticatedUser };

interface AuthContextValue {
  readonly state: AccountState;
  /** Re-attempts bootstrap after a failure. A no-op outside `signedInBootstrapError` -- there is
   * no automatic/looping retry; only this explicit, user-triggered call starts another attempt. */
  readonly retryBootstrap: () => void;
}

const INITIAL_TRACKING: BootstrapTracking = { uid: null, status: "idle" };

const AuthContext = createContext<AuthContextValue>({
  state: { status: "loading" },
  retryBootstrap: () => {},
});

function toAccountState(authState: AuthState, tracking: BootstrapTracking): AccountState {
  if (authState.status !== "signedIn") {
    return authState;
  }
  if (tracking.uid !== authState.user.uid) {
    // A render in between the auth-state change and the tracking effect catching up -- never
    // reported as ready/error for a uid that tracking hasn't caught up to yet.
    return { status: "signedInBootstrapping", user: authState.user };
  }
  switch (tracking.status) {
    case "ready":
      return { status: "signedInReady", user: authState.user };
    case "error":
      return { status: "signedInBootstrapError", user: authState.user };
    default:
      return { status: "signedInBootstrapping", user: authState.user };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>(() => getAuthService().getState());
  const [tracking, setTracking] = useState<BootstrapTracking>(INITIAL_TRACKING);

  useEffect(() => {
    return getAuthService().subscribe(setAuthState);
  }, []);

  // Resets/starts tracking for the current signed-in uid -- a derived-state-from-changed-input
  // adjustment made DURING RENDER (react.dev/learn/you-might-not-need-an-effect#adjusting-some-
  // state-when-a-prop-changes), not inside a `useEffect`: a stale "ready"/"error" from a previous
  // identity must never be shown for a new one, and a synchronous `setState` in an effect body for
  // this is exactly the anti-pattern `react-hooks/set-state-in-effect` flags. React discards this
  // render and immediately re-runs the component with the new `tracking` before anything paints,
  // so no stale frame is ever shown.
  const signedInUid = authState.status === "signedIn" ? authState.user.uid : null;
  if (tracking.uid !== signedInUid) {
    setTracking(
      signedInUid === null ? INITIAL_TRACKING : { uid: signedInUid, status: "bootstrapping" },
    );
  }

  // Performs exactly the bootstrap attempts explicitly started above or via `retryBootstrap` --
  // never a repeated/automatic retry loop. `cancelled` plus the `prev.uid === uid` guard together
  // discard a stale in-flight call's result if a newer attempt (retry, or a different uid) has
  // since superseded it.
  useEffect(() => {
    if (tracking.status !== "bootstrapping" || tracking.uid === null) {
      return;
    }
    const uid = tracking.uid;
    let cancelled = false;
    void endpoints
      .bootstrapAccount({ client: apiClient })
      .then(() => {
        if (!cancelled) {
          setTracking((previous) => (previous.uid === uid ? { uid, status: "ready" } : previous));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTracking((previous) => (previous.uid === uid ? { uid, status: "error" } : previous));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tracking.status, tracking.uid]);

  const retryBootstrap = useCallback(() => {
    setTracking((previous) =>
      previous.status === "error" ? { uid: previous.uid, status: "bootstrapping" } : previous,
    );
  }, []);

  const value: AuthContextValue = {
    state: toAccountState(authState, tracking),
    retryBootstrap,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuthState(): AccountState {
  return useContext(AuthContext).state;
}

export function useRetryBootstrap(): () => void {
  return useContext(AuthContext).retryBootstrap;
}

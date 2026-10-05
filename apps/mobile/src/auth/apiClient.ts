/**
 * The one `@tapjob/contracts` client this app uses (docs/adr/0003-api-contract-client-
 * generation.md, docs/adr/0009-identity-auth-user-account.md §I). `getAccessToken` reads the
 * current Firebase ID token lazily, per request -- never cached/stored here; `forceRefresh` is
 * forwarded through to `getIdToken` for the client's single bounded retry after an
 * `AUTH_TOKEN_INVALID` response.
 *
 * `onSessionInvalid` reuses the SDK's own `signOut()` rather than inventing a parallel "force
 * signed-out" mechanism (2026 independent-review round 2 finding 4): signing out updates
 * `auth.currentUser`/fires `onAuthStateChanged`, which `AuthContext` already observes --
 * `AuthGate` then redirects to `/sign-in` through its existing, already-tested logic, with no new
 * wiring needed. Never called for `REAUTHENTICATION_REQUIRED` (a per-operation condition a future
 * screen handles itself via `isApiError`/`.code`) or `AUTH_TOKEN_MISSING` (there was never a
 * session to invalidate).
 */
import { createApiClient } from "@tapjob/contracts";

import { publicConfig } from "../config/public-config";
import { getAuthService } from "./firebaseAuthService";

export const apiClient = createApiClient({
  baseUrl: publicConfig.apiBaseUrl,
  getAccessToken: (options) => getAuthService().getIdToken(options),
  onSessionInvalid: () => void getAuthService().signOut(),
});

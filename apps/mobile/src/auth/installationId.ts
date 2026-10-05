/**
 * The one TapJob-owned secret this app stores on-device (docs/adr/0009-identity-auth-user-
 * account.md §I): a random, privacy-preserving installation identifier -- never an IMEI, hardware
 * serial, advertising ID or other invasive fingerprint, and never the Firebase ID/refresh token
 * itself (that remains entirely provider-owned, see firebaseAuthService.ts). Persisted via
 * `expo-secure-store` (Keychain/Keystore-backed), not `AsyncStorage`.
 */
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { productConfig } from "@tapjob/product-config";

// Keyed off the canonical product identity (docs/ENGINEERING_STANDARDS.md §3.1), not a hardcoded
// literal -- `deepLinkScheme` is the one already-lowercase, URI-safe identifier product-config
// exposes for exactly this kind of namespacing.
const INSTALLATION_ID_KEY = `${productConfig.deepLinkScheme}.installationId`;

export async function getOrCreateInstallationId(): Promise<string> {
  const existing = await SecureStore.getItemAsync(INSTALLATION_ID_KEY);
  if (existing) {
    return existing;
  }
  const generated = Crypto.randomUUID();
  await SecureStore.setItemAsync(INSTALLATION_ID_KEY, generated);
  return generated;
}

import * as Sentry from "@sentry/react-native";

import { publicConfig } from "../config/public-config";

/**
 * Initializes Sentry when `EXPO_PUBLIC_SENTRY_DSN` is configured; otherwise this is a no-op
 * (docs/FOUNDATION_DECISIONS.md "Technical observability"; `EXPO_PUBLIC_*` DSNs are public by
 * design). CI and local development never set the DSN, so no event ever leaves those runs.
 *
 * Deferred to a later checkpoint: the `@sentry/react-native/expo` config plugin, Metro source-map
 * upload and EAS wiring (see apps/mobile/README.md). `SENTRY_AUTH_TOKEN` is a build-time-only
 * secret for that later work and must never reach the app bundle or this repository.
 */
if (publicConfig.sentryDsn) {
  Sentry.init({
    dsn: publicConfig.sentryDsn,
    environment: publicConfig.appEnv,
    sendDefaultPii: false,
    tracesSampleRate: 0,
  });
}

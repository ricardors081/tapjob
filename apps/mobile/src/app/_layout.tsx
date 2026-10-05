import "../i18n";
import "../observability/sentry";

import { useEffect } from "react";
import type { ReactNode } from "react";
import { useFonts } from "expo-font";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useTranslation } from "react-i18next";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { tokens, themes } from "@tapjob/design-tokens";

import { AuthProvider, useAuthState, useRetryBootstrap } from "../auth/AuthContext";
import { getAuthService } from "../auth/firebaseAuthService";
import { FONT_ASSETS_BY_FAMILY } from "../theme/fonts";

void SplashScreen.preventAutoHideAsync();

const palette = themes.light;

/** Minimal, non-interactive (or with one/two action buttons) placeholder shown instead of the
 * protected shell while the TapJob account isn't ready yet -- never the full shell, and never a
 * silent blank screen (2026 independent-review round 1 finding 4). */
function AccountGateMessage({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <View style={styles.gateScreen}>
      <Text style={styles.gateText}>{text}</Text>
      {children}
    </View>
  );
}

function GateActionButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={styles.retryButton}>
      <Text style={styles.retryButtonText}>{label}</Text>
    </Pressable>
  );
}

/** Redirects between the signed-out `/sign-in` route and the rest of the (protected) shell as
 * account state changes (docs/adr/0009-identity-auth-user-account.md §I) -- the only screen
 * reachable while signed out is `/sign-in` itself. Firebase being "signed in" is never, by itself,
 * enough to expose the protected shell: it is exposed only once the TapJob account bootstrap has
 * actually succeeded (`signedInReady`), never while merely `signedInBootstrapping` or in
 * `signedInBootstrapError` (2026 independent-review round 1 finding 4).
 *
 * The protected shell is also never rendered during the one-frame window between a redirect being
 * scheduled (the effect below) and the router actually navigating away -- `shouldRedirect` gates
 * both (2026 independent-review round 2 finding 5): without it, `children` (the `Stack`, which
 * renders whatever route is CURRENTLY active) could still momentarily render a protected screen
 * while signed out, or the sign-in screen for an instant after becoming ready. */
function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const state = useAuthState();
  const retryBootstrap = useRetryBootstrap();
  const segments = useSegments();
  const router = useRouter();
  const onSignInScreen = segments[0] === "sign-in";

  const redirectingToSignIn = state.status === "signedOut" && !onSignInScreen;
  const redirectingHome = state.status === "signedInReady" && onSignInScreen;

  useEffect(() => {
    if (redirectingToSignIn) {
      router.replace("/sign-in");
    } else if (redirectingHome) {
      router.replace("/");
    }
  }, [redirectingToSignIn, redirectingHome, router]);

  if (state.status === "loading" || redirectingToSignIn || redirectingHome) {
    return null;
  }

  if (state.status === "signedInBootstrapping") {
    return <AccountGateMessage text={t("auth.bootstrapping")} />;
  }

  if (state.status === "signedInBootstrapError") {
    return (
      <AccountGateMessage text={t("auth.bootstrapError")}>
        <GateActionButton label={t("auth.retry")} onPress={retryBootstrap} />
        {/* A user must never be stuck permanently in a Firebase session that keeps failing to
         * bootstrap -- signing out is always an available escape hatch, not just retry. */}
        <GateActionButton
          label={t("auth.signOut")}
          onPress={() => void getAuthService().signOut()}
        />
      </AccountGateMessage>
    );
  }

  return <>{children}</>;
}

const styles = StyleSheet.create({
  gateScreen: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: palette.color.background.canvas,
    padding: tokens.spacing.xl,
    gap: tokens.spacing.md,
  },
  gateText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    textAlign: "center",
  },
  retryButton: {
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    padding: tokens.spacing.sm,
    alignItems: "center",
  },
  retryButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
});

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts(FONT_ASSETS_BY_FAMILY);

  useEffect(() => {
    if (fontsLoaded || fontError) {
      void SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError]);

  // Safe loading/error behavior (runtime useFonts loading, Expo-Go-compatible per
  // docs/adr/0002-mobile-foundation.md): render nothing while the fonts are still loading, but
  // never block forever — a load error still lets the app render, falling back to the system font
  // rather than crashing or hanging on the splash screen.
  if (!fontsLoaded && !fontError) {
    return null;
  }

  return (
    <AuthProvider>
      <StatusBar style="auto" />
      <AuthGate>
        <Stack screenOptions={{ headerShown: false }} />
      </AuthGate>
    </AuthProvider>
  );
}

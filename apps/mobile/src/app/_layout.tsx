import "../i18n";
import "../observability/sentry";

import { useEffect } from "react";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";

import { FONT_ASSETS_BY_FAMILY } from "../theme/fonts";

void SplashScreen.preventAutoHideAsync();

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
    <>
      <StatusBar style="auto" />
      <Stack screenOptions={{ headerShown: false }} />
    </>
  );
}

import { tokens, themes } from "@tapjob/design-tokens";
import { productConfig } from "@tapjob/product-config";
import { Link } from "expo-router";
import { useTranslation } from "react-i18next";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAuthState } from "../auth/AuthContext";
import { getAuthService } from "../auth/firebaseAuthService";

const palette = themes.light;

/** Second route: a minimal proof that Expo Router navigates between screens, not just that a
 * single static screen renders (WP0.1-B). Also the minimal account technical surface for WP1.1:
 * who is signed in, and sign-out (docs/adr/0009-identity-auth-user-account.md §I). */
export default function AboutScreen() {
  const { t } = useTranslation();
  const authState = useAuthState();
  // Any variant with a `user` (bootstrapping/ready/error) still identifies who is signed in to
  // Firebase, independent of whether the TapJob account bootstrap has finished
  // (docs/adr/0009-identity-auth-user-account.md §I).
  const signedInUser = "user" in authState ? authState.user : null;

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>{t("about.title")}</Text>
      <Text style={styles.description}>
        {t("about.description", { shortName: productConfig.shortName })}
      </Text>

      {signedInUser ? (
        <>
          <Text style={styles.description}>
            {t("about.signedInAs", { email: signedInUser.email ?? signedInUser.uid })}
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => void getAuthService().signOut()}
            style={styles.signOutButton}
          >
            <Text style={styles.signOutButtonText}>{t("about.signOut")}</Text>
          </Pressable>
        </>
      ) : null}

      <Link href="/" style={styles.link}>
        {t("about.backLink")}
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
    padding: tokens.spacing.xl,
    justifyContent: "center",
    gap: tokens.spacing.lg,
  },
  title: {
    fontSize: tokens.typography.fontSize.title,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  description: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  link: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.action.primary.background,
  },
  signOutButton: {
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    padding: tokens.spacing.sm,
    alignItems: "center",
  },
  signOutButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
});

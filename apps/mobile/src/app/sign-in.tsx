import { useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useTranslation } from "react-i18next";

import { tokens, themes } from "@tapjob/design-tokens";

import { getAuthService } from "../auth/firebaseAuthService";

const palette = themes.light;

/** Minimal email/Google/Apple sign-in surface (docs/adr/0009-identity-auth-user-account.md §I).
 * Not the full product onboarding -- just enough to prove the auth boundary end to end. */
export default function SignInScreen() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runAuthAction(action: () => Promise<void>): Promise<void> {
    setIsSubmitting(true);
    setError(null);
    try {
      await action();
    } catch {
      setError(t("auth.genericError"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>{t("auth.title")}</Text>

      <TextInput
        accessibilityLabel={t("auth.emailLabel")}
        placeholder={t("auth.emailLabel")}
        autoCapitalize="none"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
        style={styles.input}
        editable={!isSubmitting}
      />
      <TextInput
        accessibilityLabel={t("auth.passwordLabel")}
        placeholder={t("auth.passwordLabel")}
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        style={styles.input}
        editable={!isSubmitting}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable
        accessibilityRole="button"
        disabled={isSubmitting}
        onPress={() => runAuthAction(() => getAuthService().signInWithEmail(email, password))}
        style={styles.primaryButton}
      >
        <Text style={styles.primaryButtonText}>{t("auth.signIn")}</Text>
      </Pressable>

      <Pressable
        accessibilityRole="button"
        disabled={isSubmitting}
        onPress={() => runAuthAction(() => getAuthService().signUpWithEmail(email, password))}
        style={styles.secondaryButton}
      >
        <Text style={styles.secondaryButtonText}>{t("auth.signUp")}</Text>
      </Pressable>

      <Pressable
        accessibilityRole="button"
        disabled={isSubmitting}
        onPress={() => runAuthAction(() => getAuthService().signInWithGoogle())}
        style={styles.secondaryButton}
      >
        <Text style={styles.secondaryButtonText}>{t("auth.signInWithGoogle")}</Text>
      </Pressable>

      {Platform.OS === "ios" ? (
        <Pressable
          accessibilityRole="button"
          disabled={isSubmitting}
          onPress={() => runAuthAction(() => getAuthService().signInWithApple())}
          style={styles.secondaryButton}
        >
          <Text style={styles.secondaryButtonText}>{t("auth.signInWithApple")}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
    padding: tokens.spacing.xl,
    justifyContent: "center",
    gap: tokens.spacing.md,
  },
  title: {
    fontSize: tokens.typography.fontSize.title,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
    marginBottom: tokens.spacing.lg,
  },
  input: {
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    padding: tokens.spacing.sm,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.primary,
  },
  error: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  primaryButton: {
    backgroundColor: palette.color.action.primary.background,
    borderRadius: tokens.radius.md,
    padding: tokens.spacing.sm,
    alignItems: "center",
  },
  primaryButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.background.canvas,
  },
  secondaryButton: {
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    padding: tokens.spacing.sm,
    alignItems: "center",
  },
  secondaryButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
});

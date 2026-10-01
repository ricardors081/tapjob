import { tokens, themes } from "@tapjob/design-tokens";
import { productConfig } from "@tapjob/product-config";
import { Link } from "expo-router";
import { useTranslation } from "react-i18next";
import { StyleSheet, Text, View } from "react-native";

import { publicConfig } from "../config/public-config";
import i18n from "../i18n";

const palette = themes.light;

/** Minimal shell screen proving boot, Expo Router, design tokens, product config, i18n and
 * typed public config all work together (WP0.1-B). Product features start after WP0.2. */
export default function HomeScreen() {
  const { t } = useTranslation();

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>
        {t("home.title", { productName: productConfig.productName })}
      </Text>
      <Text style={styles.subtitle}>{t("home.subtitle")}</Text>

      <View style={styles.card}>
        <InfoRow label={t("home.environmentLabel")} value={publicConfig.appEnv} />
        <InfoRow label={t("home.localeLabel")} value={i18n.language} />
        <InfoRow label={t("home.apiLabel")} value={publicConfig.apiBaseUrl} />
      </View>

      <Link href="/about" style={styles.link}>
        {t("home.aboutLink")}
      </Link>
    </View>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <Text style={styles.value}>{value}</Text>
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
    fontSize: tokens.typography.fontSize.heading,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  subtitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  card: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    gap: tokens.spacing.sm,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: tokens.spacing.md,
  },
  label: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  value: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
    flexShrink: 1,
    textAlign: "right",
  },
  link: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.action.primary.background,
  },
});

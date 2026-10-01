import { tokens, themes } from "@tapjob/design-tokens";
import { productConfig } from "@tapjob/product-config";
import { Link } from "expo-router";
import { useTranslation } from "react-i18next";
import { StyleSheet, Text, View } from "react-native";

const palette = themes.light;

/** Second route: a minimal proof that Expo Router navigates between screens, not just that a
 * single static screen renders (WP0.1-B). */
export default function AboutScreen() {
  const { t } = useTranslation();

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>{t("about.title")}</Text>
      <Text style={styles.description}>
        {t("about.description", { shortName: productConfig.shortName })}
      </Text>
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
});

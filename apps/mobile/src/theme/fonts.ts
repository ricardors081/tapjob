import { Inter_400Regular } from "@expo-google-fonts/inter/400Regular";
import { Inter_600SemiBold } from "@expo-google-fonts/inter/600SemiBold";
import { Inter_700Bold } from "@expo-google-fonts/inter/700Bold";
import { tokens } from "@tapjob/design-tokens";
import type { FontSource } from "expo-font";

/**
 * Maps each loadable font-family token (packages/design-tokens tokens.json
 * `typography.fontFamily.primary*`) to the font file `useFonts` must load for it. This is the one
 * place that needs updating if the shell ever needs another Inter weight: add the token, import
 * its asset here and add the entry below.
 */
export const FONT_ASSETS_BY_FAMILY: Record<string, FontSource> = {
  [tokens.typography.fontFamily.primaryRegular]: Inter_400Regular,
  [tokens.typography.fontFamily.primarySemibold]: Inter_600SemiBold,
  [tokens.typography.fontFamily.primaryBold]: Inter_700Bold,
};

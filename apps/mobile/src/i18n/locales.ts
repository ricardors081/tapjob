import { productConfig } from "@tapjob/product-config";

/**
 * Locale architecture (docs/ENGINEERING_STANDARDS.md §9, docs/DELIVERY_ORDER.md §7/D-037).
 *
 * `@tapjob/product-config` is the single source of truth for the product's default locale
 * (docs/ENGINEERING_STANDARDS.md §3.1): this module must not hardcode its own default. It only
 * declares which locales have a registered i18n resource, and fails fast at import time if the
 * configured default isn't one of them.
 *
 * `es`/`en` are not translated yet, but adding a resource file and a tag to SUPPORTED_LOCALES is
 * the only change needed later — nothing else in the app hardcodes the locale list.
 */

export const SUPPORTED_LOCALES = ["pt-BR"] as const;

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

export function isSupportedLocale(locale: string): locale is SupportedLocale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(locale);
}

/**
 * Validates that product-config's configured default locale has a registered i18n resource.
 * Exported (rather than only applied inline) so it stays testable against values other than the
 * real, currently-valid product.json.
 */
export function resolveDefaultLocale(configuredDefaultLocale: string): SupportedLocale {
  if (!isSupportedLocale(configuredDefaultLocale)) {
    throw new Error(
      `@tapjob/product-config defaultLocale "${configuredDefaultLocale}" has no registered i18n ` +
        "resource. Add a resource file and list it in SUPPORTED_LOCALES " +
        "(apps/mobile/src/i18n/locales.ts).",
    );
  }
  return configuredDefaultLocale;
}

/** Fails fast at import time if product.json's defaultLocale has no matching i18n resource. */
export const DEFAULT_LOCALE: SupportedLocale = resolveDefaultLocale(productConfig.defaultLocale);

/** Resolves a BCP-47 device locale tag to a supported locale, falling back to `defaultLocale`. */
export function resolveSupportedLocale(
  deviceLocaleTag: string | null | undefined,
  defaultLocale: SupportedLocale,
): SupportedLocale {
  if (!deviceLocaleTag) {
    return defaultLocale;
  }

  const normalized = deviceLocaleTag.toLowerCase();
  const exactMatch = SUPPORTED_LOCALES.find((locale) => locale.toLowerCase() === normalized);
  if (exactMatch) {
    return exactMatch;
  }

  const [deviceLanguage] = normalized.split("-");
  const sameLanguageMatch = SUPPORTED_LOCALES.find(
    (locale) => locale.toLowerCase().split("-")[0] === deviceLanguage,
  );
  return sameLanguageMatch ?? defaultLocale;
}

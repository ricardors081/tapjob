import * as Localization from "expo-localization";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { DEFAULT_LOCALE, resolveSupportedLocale } from "./locales";
import ptBR from "./resources/pt-BR.json";

// Resource keys per locale are shared across the app's `t()` calls via this type.
const resources = {
  "pt-BR": { translation: ptBR },
} as const;

const [deviceLocale] = Localization.getLocales();
const initialLocale = resolveSupportedLocale(deviceLocale?.languageTag, DEFAULT_LOCALE);

// eslint-disable-next-line import/no-named-as-default-member -- `i18n.use()` is i18next's documented API.
void i18n.use(initReactI18next).init({
  resources,
  lng: initialLocale,
  fallbackLng: DEFAULT_LOCALE,
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;

import { productConfig } from "@tapjob/product-config";

import { DEFAULT_LOCALE, resolveDefaultLocale, resolveSupportedLocale } from "./locales";

describe("DEFAULT_LOCALE", () => {
  test("comes from @tapjob/product-config, not a hardcoded value", () => {
    expect(DEFAULT_LOCALE).toBe(productConfig.defaultLocale);
  });
});

describe("resolveDefaultLocale", () => {
  test("accepts a configured default that has a registered i18n resource", () => {
    expect(resolveDefaultLocale("pt-BR")).toBe("pt-BR");
  });

  test("throws for a configured default with no registered i18n resource", () => {
    expect(() => resolveDefaultLocale("fr-FR")).toThrow(/defaultLocale "fr-FR"/);
  });
});

describe("resolveSupportedLocale", () => {
  test("returns the given default locale when the device locale is missing", () => {
    expect(resolveSupportedLocale(null, "pt-BR")).toBe("pt-BR");
    expect(resolveSupportedLocale(undefined, "pt-BR")).toBe("pt-BR");
  });

  test("matches an exact supported tag regardless of case", () => {
    expect(resolveSupportedLocale("pt-BR", "pt-BR")).toBe("pt-BR");
    expect(resolveSupportedLocale("pt-br", "pt-BR")).toBe("pt-BR");
  });

  test("falls back to the given default locale for an unsupported language", () => {
    expect(resolveSupportedLocale("en-US", "pt-BR")).toBe("pt-BR");
    expect(resolveSupportedLocale("es-ES", "pt-BR")).toBe("pt-BR");
  });
});

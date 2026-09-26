export const i18nLanguages = [
  { code: "en", label: "English" },
  { code: "zh-CN", label: "Chinese (Simplified)" },
  { code: "zh-TW", label: "Chinese (Traditional)" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
] as const;

export type I18nLocale = typeof i18nLanguages[number]["code"];

export const translationLanguages = i18nLanguages.filter((language) => language.code !== "en");

export function normalizeI18nLocale(locale: string): I18nLocale | null {
  const aliases: Record<string, I18nLocale> = {
    "zh-Hans": "zh-CN",
    "zh-Hant": "zh-TW",
    zh: "zh-CN",
  };
  const normalized = aliases[locale] ?? locale;
  return i18nLanguages.some((language) => language.code === normalized)
    ? normalized as I18nLocale
    : null;
}

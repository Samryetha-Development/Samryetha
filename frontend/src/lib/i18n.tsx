// i18n 基础设施：6 语言（en / zh-CN / zh-TW / ja / ko / es），cookie 持久化（SSR 可读），
// t(key, vars) 支持 {var} 插值，日期/相对时间走 Intl（免 time.* key）。
// 缺 key 策略：回退英文 → 回退 key 本身（永不 crash，typecheck 保证 key 存在）。

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, type I18nKey } from "./locales/en";
import { zhCN } from "./locales/zh-CN";
import { zhTW } from "./locales/zh-TW";
import { ja } from "./locales/ja";
import { ko } from "./locales/ko";
import { es } from "./locales/es";
import { fr } from "./locales/fr";
import { de } from "./locales/de";

export type { I18nKey };

export const LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "es", "fr", "de"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  "zh-CN": "简体中文",
  "zh-TW": "繁體中文",
  ja: "日本語",
  ko: "한국어",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
};

const INTL_LOCALES: Record<Locale, string> = {
  en: "en-US",
  "zh-CN": "zh-CN",
  "zh-TW": "zh-TW",
  ja: "ja-JP",
  ko: "ko-KR",
  es: "es-ES",
  fr: "fr-FR",
  de: "de-DE",
};

const DICTS: Record<Locale, Record<I18nKey, string>> = {
  en,
  "zh-CN": zhCN,
  "zh-TW": zhTW,
  ja,
  ko,
  es,
  fr,
  de,
};

export const LOCALE_COOKIE = "samryetha_lang";

export function parseLocale(value: unknown): Locale | null {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value) ? (value as Locale) : null;
}

export function readLocaleCookie(): Locale {
  if (typeof document === "undefined") return "en";
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === LOCALE_COOKIE) return parseLocale(decodeURIComponent(rest.join("="))) ?? "en";
  }
  return "en";
}

export function writeLocaleCookie(locale: Locale): void {
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
}

export function translate(locale: Locale, key: I18nKey, vars?: Record<string, string | number>): string {
  let text: string = DICTS[locale][key] ?? en[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

type I18n = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
};

const I18nContext = createContext<I18n | null>(null);

export function LanguageProvider({ initialLocale, children }: { initialLocale: Locale; children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    writeLocaleCookie(next);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback((key: I18nKey, vars?: Record<string, string | number>) => translate(locale, key, vars), [locale]);

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within <LanguageProvider>");
  return ctx;
}

// ---- locale 感知的日期/相对时间（替代 lib/format 的英文硬编码） ----

export function formatDateL(ms: number, locale: Locale): string {
  if (!ms) return "";
  const date = new Date(ms);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return new Intl.DateTimeFormat(INTL_LOCALES[locale], sameYear ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" }).format(date);
}

export function timeAgo(ms: number, locale: Locale): string {
  if (!ms) return "";
  const diff = Date.now() - ms;
  const rtf = new Intl.RelativeTimeFormat(INTL_LOCALES[locale], { numeric: "auto" });
  if (diff < 60_000) return rtf.format(0, "second");
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const date = new Date(ms);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return new Intl.DateTimeFormat(INTL_LOCALES[locale], { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }
  const yesterday = new Date(now.getTime() - 86_400_000);
  if (date.toDateString() === yesterday.toDateString()) return rtf.format(-1, "day");
  const days = Math.floor(diff / 86_400_000);
  if (days < 7) return rtf.format(-days, "day");
  return formatDateL(ms, locale);
}

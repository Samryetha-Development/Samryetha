// i18n 基础设施：英语和简体中文。
// 语言来源优先级：账号偏好(user.settings.language) > 浏览器 cookie（SSR 直出） > 浏览器/系统语言。
// t(key, vars) 支持 {var} 插值，日期/相对时间走 Intl（免 time.* key）。
// 缺 key 策略：回退英文 → 回退 key 本身（永不 crash，typecheck 保证 key 存在）。
//
// 翻译词条随前端打包，SSR 与客户端使用同一组本地字典。

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, type I18nKey } from "./locales/en";
import { zhCN } from "./locales/zh-CN";

export type { I18nKey };

export const LOCALES = ["en", "zh-CN"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  "zh-CN": "简体中文",
};

const INTL_LOCALES: Record<Locale, string> = {
  en: "en-US",
  "zh-CN": "zh-CN",
};

export const LOCALE_COOKIE = "samryetha_lang";

export function parseLocale(value: unknown): Locale | null {
  if (typeof value !== "string") return null;
  if (value === "en") return "en";
  if (value.toLowerCase().replaceAll("_", "-").split("-")[0] === "zh") return "zh-CN";
  return null;
}

export function hasLocaleCookie(): boolean {
  if (typeof document === "undefined") return false;
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name !== LOCALE_COOKIE) continue;
    try {
      return parseLocale(decodeURIComponent(rest.join("="))) !== null;
    } catch {
      return false;
    }
  }
  return false;
}

export function clearLocaleCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${LOCALE_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export function readLocaleCookie(): Locale {
  if (typeof document === "undefined") return "en";
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === LOCALE_COOKIE) {
      try {
        return parseLocale(decodeURIComponent(rest.join("="))) ?? "en";
      } catch {
        return "en";
      }
    }
  }
  return "en";
}

export function writeLocaleCookie(locale: Locale): void {
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
}

// 把浏览器语言标签序列（navigator.languages / Accept-Language）映射到支持的语言。
// 所有中文区域标签归简体中文；其他不支持的语言跳过，最终回退英文。
export function resolveLocale(tags: Iterable<string>): Locale {
  for (const raw of tags) {
    const tag = (raw || "").trim().split(";")[0].trim().toLowerCase().replace("_", "-");
    if (!tag) continue;
    if (tag === "zh" || tag.startsWith("zh-")) return "zh-CN";
    if (tag === "en" || tag.startsWith("en-")) return "en";
  }
  return "en";
}

// 浏览器首选语言（客户端专用；SSR 无 navigator → en）。
export function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "en";
  return resolveLocale(navigator.languages ?? [navigator.language ?? "en"]);
}

// ---- 本地 catalog ----

export type Catalog = Partial<Record<I18nKey, string>>;
const catalogs: Record<Locale, Catalog> = { en, "zh-CN": zhCN };
export function catalogFor(locale: Locale): Catalog {
  return catalogs[locale];
}

export function translate(dict: Catalog, key: I18nKey, vars?: Record<string, string | number>): string {
  let text: string = dict[key] ?? en[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

type I18n = {
  locale: Locale;
  /** persist=false 时不写 cookie（用于"跟随系统"：立即切换但不留本地偏好） */
  setLocale: (locale: Locale, opts?: { persist?: boolean }) => void;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
};

const I18nContext = createContext<I18n | null>(null);

/**
 * LanguageProvider
 *
 * @param initialLocale  SSR 决定的初始语言
 */
export function LanguageProvider({
  initialLocale,
  children,
}: {
  initialLocale: Locale;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale, opts?: { persist?: boolean }) => {
    setLocaleState(next);
    if (opts?.persist !== false) writeLocaleCookie(next);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback((key: I18nKey, vars?: Record<string, string | number>) => translate(catalogFor(locale), key, vars), [locale]);

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

// i18n 基础设施：8 语言（en / zh-CN / zh-TW / ja / ko / es / fr / de）。
// 语言来源优先级：账号偏好(user.settings.language) > 浏览器 cookie（SSR 直出） > 浏览器/系统语言。
// t(key, vars) 支持 {var} 插值，日期/相对时间走 Intl（免 time.* key）。
// 缺 key 策略：回退英文 → 回退 key 本身（永不 crash，typecheck 保证 key 存在）。
//
// 运行时 i18n server：
//   - SSR：server.mjs 从 I18N_API_ORIGIN 预取 catalog 并注入 window.__I18N_CATALOG__
//   - CSR hydration：entry-client.tsx 读取注入的 catalog
//   - CSR 后续切换语言：按需从 I18N_API_ORIGIN 加载，不可用时回退内嵌英文字典

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { en, type I18nKey } from "./locales/en";

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

export const LOCALE_COOKIE = "samryetha_lang";

export function parseLocale(value: unknown): Locale | null {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value) ? (value as Locale) : null;
}

export function hasLocaleCookie(): boolean {
  if (typeof document === "undefined") return false;
  for (const part of document.cookie.split(";")) {
    const name = part.trim().split("=")[0];
    if (name === LOCALE_COOKIE) return true;
  }
  return false;
}

export function clearLocaleCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${LOCALE_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

// 读取 cookie 原始值并解析为受支持语言；无 cookie / 非法或不支持的值 → null。
// 校验规则与服务端 requestLocale 一致（仅接受 LOCALES 中的值），供调用方在非法时回退 SSR lang。
export function parseLocaleCookie(): Locale | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === LOCALE_COOKIE) {
      try {
        return parseLocale(decodeURIComponent(rest.join("=")));
      } catch {
        return null;
      }
    }
  }
  return null;
}

export function readLocaleCookie(): Locale {
  return parseLocaleCookie() ?? "en";
}

export function writeLocaleCookie(locale: Locale): void {
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
}

// 把浏览器语言标签序列（navigator.languages / Accept-Language）映射到支持的语言。
// zh-Hant*/zh-HK/zh-MO 等归 zh-TW，其余 zh 归 zh-CN；英文/未知名一律 en 兜底。
export function resolveLocale(tags: Iterable<string>): Locale {
  for (const raw of tags) {
    const tag = (raw || "").trim().split(";")[0].trim().toLowerCase().replaceAll("_", "-");
    if (!tag) continue;
    if (tag === "zh-tw" || tag === "zh-hk" || tag === "zh-mo" || tag === "zh-hant" || tag.startsWith("zh-hant") || tag.startsWith("zh-hk") || tag.startsWith("zh-mo")) return "zh-TW";
    if (tag.startsWith("zh")) return "zh-CN";
    if (tag.startsWith("ja")) return "ja";
    if (tag.startsWith("ko")) return "ko";
    if (tag.startsWith("es")) return "es";
    if (tag.startsWith("fr")) return "fr";
    if (tag.startsWith("de")) return "de";
    if (tag.startsWith("en")) return "en";
  }
  return "en";
}

// 浏览器首选语言（客户端专用；SSR 无 navigator → en）。
export function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "en";
  return resolveLocale(navigator.languages ?? [navigator.language ?? "en"]);
}

// ---- 运行时 catalog ----

/** 运行时获取的翻译字典（可部分覆盖，缺失 key 回退英文内嵌字典） */
export type Catalog = Partial<Record<I18nKey, string>>;

/** SSR 注入的 catalog 数据结构（挂载在 window.__I18N_CATALOG__） */
export type InjectedCatalog = {
  /** 当前 locale 的字典（可能不完整） */
  locale: Locale;
  translations: Catalog;
  /** en 字典（保证完整，用于 fallback） */
  en: Catalog;
};

// 客户端 catalog 缓存：locale → 翻译字典（永不过期，页面生命周期内有效）
const catalogCache = new Map<Locale, Catalog>();

// 英文字典始终写入缓存（内嵌保证完整）
catalogCache.set("en", en as Catalog);

/**
 * 从 i18n server 获取指定 locale 的 catalog。
 * 若 i18n server 不可用，返回 null（调用方 fallback 英文）。
 * 开发环境默认 http://localhost:3002，生产同源（可通过 I18N_API_ORIGIN 覆盖）。
 */
async function fetchCatalog(locale: Locale): Promise<Catalog | null> {
  // 服务端渲染时不调用（由 server.mjs 注入）
  if (typeof window === "undefined") return null;

  // 读取运行时配置（由 server.mjs 注入）
  const win = window as Window & { __I18N_ORIGIN__?: string };
  const origin = (win.__I18N_ORIGIN__ ?? (import.meta.env?.VITE_I18N_API_ORIGIN as string | undefined) ?? "").replace(/\/$/, "");

  const url = `${origin}/api/catalog/${locale}/translations`;
  try {
    const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return null;
    const data = await resp.json() as { translations?: Catalog };
    return data.translations ?? null;
  } catch {
    // 网络错误 / 超时 / 服务不可用 → fallback 英文
    return null;
  }
}

/**
 * 获取指定 locale 的 catalog（优先缓存，缓存未命中时从 i18n server 加载）。
 * en 始终返回内嵌字典。
 */
async function loadCatalog(locale: Locale): Promise<Catalog> {
  if (catalogCache.has(locale)) return catalogCache.get(locale)!;
  const remote = await fetchCatalog(locale);
  const catalog = remote ?? (en as Catalog);
  catalogCache.set(locale, catalog);
  return catalog;
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
 * @param catalog        SSR 注入的翻译字典（若提供则直接使用，跳过客户端首次 fetch）
 */
export function LanguageProvider({
  initialLocale,
  catalog: initialCatalog,
  children,
}: {
  initialLocale: Locale;
  catalog?: Catalog;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  // 当前生效的字典：优先注入 catalog，否则内嵌英文（客户端加载完成后更新）
  const [dict, setDict] = useState<Catalog>(() => {
    if (initialCatalog && Object.keys(initialCatalog).length > 0) {
      // 预填缓存，避免后续重复 fetch
      if (!catalogCache.has(initialLocale)) {
        catalogCache.set(initialLocale, initialCatalog);
      }
      return initialCatalog;
    }
    // 无注入 catalog → 使用英文内嵌（SSR 静默不切换）
    return en as Catalog;
  });

  // 追踪是否已触发过异步加载，避免 StrictMode 双重 effect 重复 fetch
  const loadedRef = useRef<Locale | null>(null);
  // setLocale 竞态守卫：只有最新一次切换的异步 catalog 能生效
  const localeSeqRef = useRef(0);

  // 客户端初次 mount：若 catalog 来自注入则已填缓存，否则异步加载当前 locale
  useEffect(() => {
    if (loadedRef.current === locale) return;
    // 若初始 catalog 已注入且已入缓存，无需 fetch
    if (catalogCache.has(locale)) {
      const cached = catalogCache.get(locale)!;
      setDict(cached);
      loadedRef.current = locale;
      return;
    }
    loadedRef.current = locale;
    // 与 setLocale 共用竞态守卫：若加载期间用户切换语言，丢弃这次 mount 结果
    const seq = localeSeqRef.current;
    void loadCatalog(locale).then((cat) => {
      if (seq === localeSeqRef.current) {
        setDict(cat);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setLocale = useCallback((next: Locale, opts?: { persist?: boolean }) => {
    const seq = ++localeSeqRef.current;
    setLocaleState(next);
    if (opts?.persist !== false) writeLocaleCookie(next);

    // 若缓存命中直接切换，否则异步加载后切换
    if (catalogCache.has(next)) {
      setDict(catalogCache.get(next)!);
    } else {
      void loadCatalog(next).then((cat) => {
        // 只有最新一次切换仍是 next 时才生效（避免快速切换竞态）
        if (seq === localeSeqRef.current) {
          setDict(cat);
          setLocaleState(next);
        }
      });
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback((key: I18nKey, vars?: Record<string, string | number>) => translate(dict, key, vars), [dict]);

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

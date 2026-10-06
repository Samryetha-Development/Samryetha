import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { RootApp } from "./root-app";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";
import { browserLocale, parseLocale, parseLocaleCookie, type Locale, type Catalog, type InjectedCatalog } from "./lib/i18n";

installSafariViewportFix();

// cookie 合法（受支持语言）则采用；否则以服务端已决语言（<html lang>，服务端按 Accept-Language
// 映射系统语言）为准，最后才回退 navigator。避免非法/不支持 cookie 导致 SSR 与客户端选择不一致，
// 造成 hydration 不匹配或语言闪变。
const initialLocale: Locale =
  parseLocaleCookie() ?? parseLocale(document.documentElement.lang) ?? browserLocale();

// 读取 SSR 注入的 catalog（server.mjs 从 i18n server 预取后嵌入 HTML）
const win = window as Window & { __I18N_CATALOG__?: InjectedCatalog; __I18N_ORIGIN__?: string };
const injected = win.__I18N_CATALOG__;
const initialCatalog: Catalog | undefined = injected?.locale === initialLocale
  ? injected.translations
  : injected?.en;

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <RootApp pathname={window.location.pathname} initialLocale={initialLocale} catalog={initialCatalog} />
  </StrictMode>,
);

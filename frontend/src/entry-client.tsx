import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { RootApp } from "./root-app";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";
import { hasLocaleCookie, parseLocale, readLocaleCookie, type Locale } from "./lib/i18n";

installSafariViewportFix();

// 无偏好 cookie 时以服务端已决语言直出（服务端按 Accept-Language 映射系统语言），
// 避免无 cookie 首帧回落 en 造成 hydration 不匹配。
const initialLocale: Locale = hasLocaleCookie() ? readLocaleCookie() : (parseLocale(document.documentElement.lang) ?? "en");

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <RootApp pathname={window.location.pathname} initialLocale={initialLocale} />
  </StrictMode>,
);

import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { TasksStandalone } from "./tasks-standalone";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";
import { browserLocale, parseLocale, parseLocaleCookie, type Locale } from "./lib/i18n";

installSafariViewportFix();

// 与 entry-client 同口径：cookie 合法则采用；否则以服务端已决语言（<html lang>）为准，
// 最后才回退 navigator。避免非法/不支持 cookie 造成 SSR 与客户端语言不一致。
const initialLocale: Locale =
  parseLocaleCookie() ?? parseLocale(document.documentElement.lang) ?? browserLocale();

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <TasksStandalone initialLocale={initialLocale} />
  </StrictMode>,
);

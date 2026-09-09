import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { TasksStandalone } from "./tasks-standalone";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";
import { hasLocaleCookie, parseLocale, readLocaleCookie, type Locale } from "./lib/i18n";

installSafariViewportFix();

const initialLocale: Locale = hasLocaleCookie() ? readLocaleCookie() : (parseLocale(document.documentElement.lang) ?? "en");

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <TasksStandalone initialLocale={initialLocale} />
  </StrictMode>,
);

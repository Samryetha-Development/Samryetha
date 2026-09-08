import { renderToString } from "react-dom/server";
import { RootApp } from "./root-app";
import { TasksStandalone } from "./tasks-standalone";
import { parseLocale, type Locale } from "./lib/i18n";

export function render(url: string, locale?: unknown) {
  const initialLocale: Locale = parseLocale(locale) ?? "en";
  const requestUrl = new URL(url, "http://localhost");
  return renderToString(<RootApp pathname={requestUrl.pathname} initialLocale={initialLocale} />);
}

export function renderTasks(locale?: unknown) {
  const initialLocale: Locale = parseLocale(locale) ?? "en";
  return renderToString(<TasksStandalone initialLocale={initialLocale} />);
}

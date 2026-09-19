import { renderToString } from "react-dom/server";
import { RootApp } from "./root-app";
import { TasksStandalone } from "./tasks-standalone";
import { parseLocale, type Locale, type Catalog } from "./lib/i18n";

export function render(url: string, locale?: unknown, catalog?: Catalog) {
  const initialLocale: Locale = parseLocale(locale) ?? "en";
  const requestUrl = new URL(url, "http://localhost");
  return renderToString(<RootApp pathname={requestUrl.pathname} initialLocale={initialLocale} catalog={catalog} />);
}

export function renderTasks(locale?: unknown, catalog?: Catalog) {
  const initialLocale: Locale = parseLocale(locale) ?? "en";
  return renderToString(<TasksStandalone initialLocale={initialLocale} catalog={catalog} />);
}

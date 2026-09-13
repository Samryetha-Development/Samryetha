import { AuthProvider } from "./lib/auth";
import { LanguageProvider, type Catalog, type Locale } from "./lib/i18n";
import { TasksPage } from "./tasks-page";

export function TasksStandalone({ initialLocale = "en", catalog }: { initialLocale?: Locale; catalog?: Catalog }) {
  return (
    <LanguageProvider initialLocale={initialLocale} catalog={catalog}>
      <AuthProvider>
        <TasksPage />
      </AuthProvider>
    </LanguageProvider>
  );
}

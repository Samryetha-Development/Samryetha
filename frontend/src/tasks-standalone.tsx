import { AuthProvider } from "./lib/auth";
import { LanguageProvider, type Locale } from "./lib/i18n";
import { TasksPage } from "./tasks-page";

export function TasksStandalone({ initialLocale = "en" }: { initialLocale?: Locale }) {
  return (
    <LanguageProvider initialLocale={initialLocale}>
      <AuthProvider>
        <TasksPage />
      </AuthProvider>
    </LanguageProvider>
  );
}

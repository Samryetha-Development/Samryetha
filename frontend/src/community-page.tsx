import { AppShell } from "./app-shell";
import { useI18n } from "./lib/i18n";

export function CommunityPage() {
  const { t } = useI18n();
  return <AppShell><main className="shell community-information">
    <h1>{t("community.title")}</h1>
    <p>{t("community.purpose")}</p>
    <section aria-labelledby="guidelines"><h2 id="guidelines">{t("community.guidelines")}</h2><p>{t("community.conduct")}</p><p>{t("community.reporting")}</p></section>
    <section aria-labelledby="privacy"><h2 id="privacy">{t("community.privacy")}</h2><p>{t("community.collection")}</p><p>{t("community.visibility")}</p><p>{t("community.retention")}</p></section>
    <section aria-labelledby="help"><h2 id="help">{t("community.help")}</h2><p>{t("community.contact")}</p>
      <p><a className="sender" href="https://github.com/Samryetha-Development/Samryetha/issues" target="_blank" rel="noopener noreferrer">{t("community.bugReport")}</a></p>
      <p>{t("auth.recoveryLost")}</p>
    </section>
  </main></AppShell>;
}

import { LOCALES, LOCALE_LABELS, useI18n, type Locale } from "./lib/i18n";

// 顶栏语言切换：原生 select（无障碍 + 零依赖），cookie 持久化 + SSR 同语言直出。
export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className="lang-switch">
      <span className="sr-only">{t("a11y.language")}</span>
      <span className="lang-globe" aria-hidden="true">🌐</span>
      <select
        className="lang-select"
        value={locale}
        aria-label={t("a11y.language")}
        onChange={(event) => {
          const next = event.target.value as Locale;
          if ((LOCALES as readonly string[]).includes(next) && next !== locale) setLocale(next);
        }}
      >
        {LOCALES.map((item) => (
          <option key={item} value={item}>
            {LOCALE_LABELS[item]}
          </option>
        ))}
      </select>
    </label>
  );
}

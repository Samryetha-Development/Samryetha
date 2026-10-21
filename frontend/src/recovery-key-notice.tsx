import { useI18n } from "./lib/i18n";

/** Secrets stay in component memory; never in URLs, localStorage or analytics. */
export function RecoveryKeyNotice({ value }: { value: string | null }) {
  const { t } = useI18n();
  if (!value) return null;
  return <div className="recovery-key-notice" role="status">
    <p>{t("auth.recoverySave")}</p>
    <label className="login-field"><span>{t("auth.recoveryKey")}</span>
      <span className="login-input-frame"><input readOnly value={value} autoComplete="off" spellCheck={false} onFocus={(event) => event.target.select()} /></span>
    </label>
    <p className="settings-field-hint">{t("auth.recoveryLost")}</p>
  </div>;
}

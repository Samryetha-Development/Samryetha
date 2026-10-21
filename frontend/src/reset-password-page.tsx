import { useState, type FormEvent } from "react";
import { api, ApiError } from "./lib/api";
import { useI18n } from "./lib/i18n";
import { RecoveryKeyNotice } from "./recovery-key-notice";

export function ResetPasswordPage() {
  const { t } = useI18n();
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [replacementKey, setReplacementKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (newPassword.length < 8) { setError(t("auth.passwordMin")); return; }
    if (newPassword !== confirm) { setError(t("auth.passwordMismatch")); return; }
    const token = new URLSearchParams(window.location.search).get("token") ?? "";
    if (!token) { setError(t("auth.missingToken")); return; }
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await api.auth.resetPassword({ token, newPassword });
      setReplacementKey(result.recoveryKey);
      window.history.replaceState(window.history.state, "", window.location.pathname);
      setNewPassword(""); setConfirm("");
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auth.resetFail"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <a className="login-wordmark" href="/" aria-label={t("nav.home")}>Samryetha</a>
        <section className="login-card">
          <div className="login-card-content">
            <header className="login-heading"><h1>{t("auth.newPasswordTitle")}</h1><p>{t("auth.newPasswordSubtitle")}</p></header>
            {done ? (
              <div className="empty-state"><p>{t("auth.resetDone")}</p><RecoveryKeyNotice value={replacementKey} /><p><a className="sender" href="/login">{t("auth.signIn")}</a></p></div>
            ) : (
              <form className="login-form" onSubmit={submit} noValidate>
                <label className="login-field"><span>{t("auth.newPassword")}</span><span className="login-input-frame"><input type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder={t("auth.passwordMinPlaceholder")} autoFocus /></span></label>
                <label className="login-field"><span>{t("auth.confirmPassword")}</span><span className="login-input-frame"><input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={t("auth.reenterPassword")} /></span></label>
                {error && <small className="login-error form-error" role="alert">{error}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.resetting") : t("auth.resetPassword")}</button>
              </form>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

import { useState, type FormEvent } from "react";
import { api, ApiError } from "./lib/api";
import { useI18n } from "./lib/i18n";
import { RecoveryKeyNotice } from "./recovery-key-notice";

export function ForgotPasswordPage() {
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [recoveryEmail, setRecoveryEmail] = useState("");
  const [method, setMethod] = useState<"key" | "email">("key");
  const [recoveryKey, setRecoveryKey] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [replacementKey, setReplacementKey] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || (method === "email" ? !recoveryEmail.trim() : !recoveryKey.trim())) {
      setError(t("auth.recoveryRequired"));
      return;
    }
    if (method === "key" && newPassword.length < 8) { setError(t("auth.passwordMin")); return; }
    if (method === "key" && newPassword !== confirm) { setError(t("auth.passwordMismatch")); return; }
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      if (method === "email") {
        await api.auth.forgotPassword({ username: username.trim(), recoveryEmail: recoveryEmail.trim() });
      } else {
        const result = await api.auth.recoverWithKey({ username: username.trim(), recoveryKey: recoveryKey.trim(), newPassword });
        setReplacementKey(result.recoveryKey);
        setRecoveryKey(""); setNewPassword(""); setConfirm("");
      }
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auth.submitFail"));
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
            <header className="login-heading"><h1>{t("auth.resetTitle")}</h1><p>{t("auth.resetSubtitle")}</p></header>
            {submitted ? (
              <div className="empty-state"><p>{t(method === "email" ? "auth.resetSent" : "auth.resetDone")}</p><RecoveryKeyNotice value={replacementKey} /><p><a className="sender" href="/login">{t("auth.backToSignIn")}</a></p></div>
            ) : (
              <form className="login-form" onSubmit={submit} noValidate>
                <div className="recovery-methods theme-picker" role="group" aria-label={t("auth.resetTitle")}>
                  <button type="button" className={`theme-option ${method === "key" ? "active" : ""}`} aria-pressed={method === "key"} onClick={() => { setMethod("key"); setError(null); }}>{t("auth.recoveryKey")}</button>
                  <button type="button" className={`theme-option ${method === "email" ? "active" : ""}`} aria-pressed={method === "email"} onClick={() => { setMethod("email"); setError(null); }}>{t("auth.recoveryEmail")}</button>
                </div>
                <label className="login-field"><span>{t("auth.username")}</span><span className="login-input-frame"><input type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></span></label>
                {method === "email" ? <label className="login-field"><span>{t("auth.recoveryEmail")}</span><span className="login-input-frame"><input type="email" autoComplete="email" value={recoveryEmail} onChange={(e) => setRecoveryEmail(e.target.value)} placeholder={t("auth.recoveryEmailPlaceholder")} /></span></label> : <>
                  <label className="login-field"><span>{t("auth.recoveryKey")}</span><span className="login-input-frame"><input type="password" autoComplete="off" maxLength={200} value={recoveryKey} onChange={(e) => setRecoveryKey(e.target.value)} /></span></label>
                  <label className="login-field"><span>{t("settings.newPassword")}</span><span className="login-input-frame"><input type="password" autoComplete="new-password" maxLength={200} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></span></label>
                  <label className="login-field"><span>{t("auth.confirmPassword")}</span><span className="login-input-frame"><input type="password" autoComplete="new-password" maxLength={200} value={confirm} onChange={(e) => setConfirm(e.target.value)} /></span></label>
                </>}
                <p className="settings-field-hint">{t("auth.recoveryLost")}</p>
                {error && <small className="login-error form-error" role="alert">{error}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.sending") : t(method === "email" ? "auth.sendResetLink" : "auth.resetTitle")}</button>
              </form>
            )}
            <p className="login-register">{t("auth.rememberPassword")} <a href="/login">{t("auth.signIn")}</a></p>
          </div>
        </section>
      </div>
    </main>
  );
}

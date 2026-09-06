import { useState, type FormEvent } from "react";
import { api, ApiError } from "./lib/api";
import { useI18n } from "./lib/i18n";

export function ForgotPasswordPage() {
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [recoveryEmail, setRecoveryEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !recoveryEmail.trim()) {
      setError(t("auth.enterBoth"));
      return;
    }
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await api.auth.forgotPassword({ username: username.trim(), recoveryEmail: recoveryEmail.trim() });
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
              <div className="empty-state"><p>{t("auth.resetSent")}</p><p><a className="sender" href="/login">{t("auth.backToSignIn")}</a></p></div>
            ) : (
              <form className="login-form" onSubmit={submit} noValidate>
                <label className="login-field"><span>{t("auth.username")}</span><input type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></label>
                <label className="login-field"><span>{t("auth.recoveryEmail")}</span><input type="email" autoComplete="email" value={recoveryEmail} onChange={(e) => setRecoveryEmail(e.target.value)} placeholder="you@example.com" /></label>
                {error && <small className="login-error form-error" role="alert">{error}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.sending") : t("auth.sendResetLink")}</button>
              </form>
            )}
            <p className="login-register">{t("auth.rememberPassword")} <a href="/login">{t("auth.signIn")}</a></p>
          </div>
        </section>
      </div>
    </main>
  );
}

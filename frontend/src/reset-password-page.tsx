import { useState, type FormEvent } from "react";
import { api, ApiError } from "./lib/api";

export function ResetPasswordPage() {
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (newPassword.length < 8) { setError("Use at least 8 characters."); return; }
    if (newPassword !== confirm) { setError("Passwords do not match."); return; }
    const token = new URLSearchParams(window.location.search).get("token") ?? "";
    if (!token) { setError("Missing reset token. Use the link from your email."); return; }
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await api.auth.resetPassword({ token, newPassword });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset password.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <a className="login-wordmark" href="/" aria-label="Samryetha home">Samryetha</a>
        <section className="login-card">
          <div className="login-card-content">
            <header className="login-heading"><h1>Choose a new password</h1><p>Enter a new password for your account.</p></header>
            {done ? (
              <div className="empty-state"><p>Your password has been reset. You can now sign in.</p><p><a className="sender" href="/login">Sign in</a></p></div>
            ) : (
              <form className="login-form" onSubmit={submit} noValidate>
                <label className="login-field"><span>New password</span><input type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="At least 8 characters" autoFocus /></label>
                <label className="login-field"><span>Confirm password</span><input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Re-enter password" /></label>
                {error && <small className="login-error form-error" role="alert">{error}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? "Resetting…" : "Reset password"}</button>
              </form>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

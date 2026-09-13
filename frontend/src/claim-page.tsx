import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "./lib/api";
import { useI18n } from "./lib/i18n";

// OIDC 认领页：首次 OAuth 登录无映射时落地于此。凭老用户名+密码把
// 本次登录身份绑定到已有账号；没有老账号则走“创建新账号”出口。
export function ClaimPage() {
  const { t } = useI18n();
  const [ticket, setTicket] = useState<string | null>(null);
  const [identity, setIdentity] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("ticket") ?? "";
    if (!value) {
      setInvalid(true);
      return;
    }
    setTicket(value);
    api.auth
      .claimInfo(value)
      .then((info) => setIdentity(info.email || info.displayName || ""))
      .catch(() => setInvalid(true));
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!ticket || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.auth.claim({ ticket, username: username.trim(), password });
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("claim.failed"));
    } finally {
      setBusy(false);
    }
  };

  const createNew = async () => {
    if (!ticket || creating) return;
    setCreating(true);
    setError(null);
    try {
      await api.auth.claimNew({ ticket });
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("claim.failed"));
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <a className="login-wordmark" href="/" aria-label={t("nav.home")}>Samryetha</a>
        <section className="login-card">
          <div className="login-card-content">
            {invalid ? (
              <div className="empty-state content-fade">
                <p>{t("claim.invalid")}</p>
                <p><a className="sender" href="/login">{t("thread.signIn")}</a></p>
              </div>
            ) : (
              <>
                <header className="login-heading">
                  <h1>{t("claim.title")}</h1>
                  <p>{t("claim.subtitle")}</p>
                  {identity ? <p className="login-sub">{t("claim.identity", { identity })}</p> : null}
                </header>
                <form className="login-form" onSubmit={submit} noValidate>
                  <label className="login-field"><span>{t("auth.username")}</span><span className="login-input-frame"><input type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></span></label>
                  <label className="login-field"><span>{t("auth.password")}</span><span className="login-input-frame"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></span></label>
                  {error && <small className="login-error form-error" role="alert">{error}</small>}
                  <button className="login-primary" type="submit" disabled={busy || !username.trim() || !password}>{busy ? t("claim.linking") : t("claim.submit")}</button>
                </form>
                <p className="login-register">
                  <button type="button" className="sender" style={{ background: "none", border: 0, cursor: "pointer", padding: 0 }} disabled={creating} onClick={() => void createNew()}>
                    {creating ? t("claim.linking") : t("claim.createNew")}
                  </button>
                </p>
              </>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

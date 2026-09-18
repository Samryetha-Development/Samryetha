import { useState, type FormEvent } from "react";
import { LakoAuthError } from "./auth-error.js";
import { useLako } from "./config.js";

export type LakoLoginProps = {
  /**
   * 登录（含 MFA）完成。**宿主负责继续授权流程**——通常是重跑一次 authorize。
   * 早期版本在这里直接 `location.href` 跳走，那正是 iframe 要解决的问题；
   * 现在由宿主决定怎么推进，组件不碰导航。
   */
  onSuccess: () => void;
  /** 面板顶部的产品名。 */
  product?: string;
  /** 副标题，例如「继续前往 Samryetha」。 */
  subtitle?: string;
  /** 不给就不渲染对应的链接。 */
  registerHref?: string;
  resetHref?: string;
};

type Stage = "login" | "mfa";

export function LakoLogin({
  onSuccess,
  product = "Lako",
  subtitle,
  registerHref,
  resetHref,
}: LakoLoginProps) {
  const { fetcher } = useLako();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<Stage>("login");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetcher("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ login: data.get("login"), password: data.get("password") }),
      });
      const body = await response.json().catch(() => null);
      if (body?.mfa_required) {
        setStage("mfa");
        setBusy(false);
        return;
      }
      if (response.ok) {
        onSuccess();
        return;
      }
      setError(body?.error?.message ?? `Sign in failed (HTTP ${response.status})`);
    } catch (cause) {
      // 网络层失败（跨源被拦、URL 非法、断网）。把原始信息带出来——
      // 只说 "Sign in failed" 会让人完全无从下手。
      setError(`Sign in failed: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
    setBusy(false);
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetcher("/api/auth/login/mfa", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: data.get("code") }),
      });
      if (response.ok) {
        onSuccess();
        return;
      }
      const body = await response.json().catch(() => null);
      setError(body?.error?.message ?? `Verification failed (HTTP ${response.status})`);
    } catch (cause) {
      setError(`Verification failed: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
    setBusy(false);
  }

  const mfa = stage === "mfa";
  const authState = busy ? "busy" : mfa ? "mfa" : "idle";

  return (
    <section className="lako-auth-login">
      <div className="lako-auth-panel" data-auth-state={authState} aria-busy={busy}>
        <div className="lako-auth-product">
          <span className="lako-identity-glyph" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          {product}
        </div>

        <div className="lako-auth-stage" key={stage}>
          <h1>{mfa ? "Verification" : "Sign in"}</h1>
          <p className="lako-subtle">
            {mfa ? "Enter your authentication or recovery code." : subtitle}
          </p>

          {mfa ? (
            <form onSubmit={verify}>
              <label>
                Verification code
                <input name="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus />
              </label>
              <LakoAuthError message={error} />
              <button disabled={busy}>{busy ? "Verifying…" : "Continue"}</button>
            </form>
          ) : (
            <form onSubmit={submit}>
              <label>
                Username or email
                <input name="login" autoComplete="username" required autoFocus />
              </label>
              <label>
                Password
                <input name="password" type="password" autoComplete="current-password" required />
              </label>
              <LakoAuthError message={error} />
              <button disabled={busy}>{busy ? "Signing in…" : "Continue"}</button>
            </form>
          )}

          {!mfa && (registerHref || resetHref) && (
            <div className="lako-auth-links">
              {registerHref && <a href={registerHref}>Create account</a>}
              {resetHref && <a href={resetHref}>Forgot password?</a>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

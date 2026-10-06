"use client";

import { FormEvent, useEffect, useState } from "react";

/**
 * 授权前拦截页：会话的邮箱还没验证时，`/oauth/authorize` 把浏览器送到这里。
 *
 * 不验证完就继续的话，RP 拿到的 `email_verified` 永远是 false——论坛侧只能把
 * 用户当成陌生身份去走「认领」，或给他建个小号。所以这里先证明邮箱可用，再用
 * 同一个 `return_to` 回到授权请求（`return_to` 由 Lako 自己生成，只认站内
 * `/oauth/authorize` 续跳，和 /login 一条尺子）。
 *
 * 验证码用 POST /api/account/email/verify/code* 而不是邮件里的链接：这里手里有一个
 * 正在进行的授权请求，跟着链接跳到 /verify 会丢掉它。
 */
function safeReturnTo(value: string | null) {
  return value?.startsWith("/oauth/authorize?") ? value : "/account";
}

export default function VerifyEmail() {
  const [address, setAddress] = useState("");
  const [returnTo, setReturnTo] = useState("/account");
  const [sent, setSent] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    setAddress(params.get("email") ?? "");
    setReturnTo(safeReturnTo(params.get("return_to")));
  }, []);

  function csrf() {
    return decodeURIComponent(document.cookie.split("; ").find((v) => v.startsWith("lako_csrf="))?.split("=")[1] ?? "");
  }

  async function sendCode() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/account/email/verify/code", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({}),
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) {
        location.href = `/login?return_to=${encodeURIComponent(returnTo)}`;
        return;
      }
      if (!response.ok) {
        setError(body.error?.message ?? "Could not send a code.");
        return;
      }
      setSent(true);
      if (body.already_verified) {
        location.href = returnTo;
      }
    } catch {
      setError("Could not send a code.");
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/account/email/verify/code/confirm", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({ code: data.get("code") }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(body.error?.message ?? "That code is invalid or has expired.");
        return;
      }
      setDone(true);
      // 验证已落库，回授权请求继续：同一个 return_to，不换栈。
      location.href = returnTo;
    } catch {
      setError("Verification failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <section className="auth-wrap">
        <div className="panel">
          <div className="eyebrow">Lako</div>
          <h1>Verify your email</h1>
          <p className="subtle">
            {address ? <>Confirm <strong>{address}</strong> to continue signing in.</> : "Confirm your email address to continue signing in."}
          </p>
          {error && <div className="form-error">{error}</div>}
          {done ? (
            <p className="subtle" role="status">Verified — continuing…</p>
          ) : (
            <>
              <form onSubmit={confirm}>
                <label>
                  Six-digit code
                  <input name="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus={sent} />
                </label>
                <button disabled={busy}>{busy ? "Verifying…" : "Verify and continue"}</button>
              </form>
              <p className="subtle">
                {sent ? <span role="status">Code sent. Check your inbox.</span> : "We'll email you a six-digit code."}{" "}
                <button type="button" className="row-action" disabled={busy} onClick={() => void sendCode()}>
                  {sent ? "Send a new code" : "Send code"}
                </button>
              </p>
              <p>
                <a className="auth-link" href="/account/email">Use a different address</a>
              </p>
            </>
          )}
        </div>
      </section>
    </main>
  );
}

"use client";

import { FormEvent, useEffect, useState } from "react";

function safeReturnTo(value: string | null) {
  return value?.startsWith("/oauth/authorize?") ? value : "/account";
}

function AuthError({ message }: { message: string }) {
  const [renderedMessage, setRenderedMessage] = useState(message);
  useEffect(() => {
    if (message) {
      setRenderedMessage(message);
      return;
    }
    const timer = window.setTimeout(() => setRenderedMessage(""), 260);
    return () => window.clearTimeout(timer);
  }, [message]);
  return <div className="form-error auth-error" data-visible={Boolean(message)} aria-live="polite"><div>{renderedMessage}</div></div>;
}

export default function Login() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mfa, setMfa] = useState(false);
  function finish() {
    location.href = safeReturnTo(new URLSearchParams(location.search).get("return_to"));
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ login: data.get("login"), password: data.get("password") }) });
    const body = await response.json();
    if (body.mfa_required) { setMfa(true); setBusy(false); return; }
    if (response.ok) { finish(); return; }
    setError(body.error?.message ?? "Sign in failed"); setBusy(false);
  }
  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login/mfa", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code: data.get("code") }) });
    if (response.ok) { finish(); return; }
    const body = await response.json(); setError(body.error?.message ?? "Verification failed"); setBusy(false);
  }
  const authState = busy ? "busy" : mfa ? "mfa" : "idle";
  return <main className="auth-main"><section className="auth-wrap auth-login"><div className="panel auth-panel" data-auth-state={authState} aria-busy={busy}><div className="auth-product"><span className="identity-glyph" aria-hidden="true"><i /><i /><i /></span>Lako</div><div className="auth-stage" key={mfa ? "mfa" : "login"}><h1>{mfa ? "Verification" : "Sign in"}</h1><p className="subtle">{mfa ? "Enter your authentication or recovery code." : "Continue to Samryetha."}</p>{mfa ? <form onSubmit={verify}><label>Verification code<input name="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus /></label><AuthError message={error} /><button disabled={busy}>{busy ? "Verifying…" : "Continue"}</button></form> : <form onSubmit={submit}><label>Username or email<input name="login" autoComplete="username" required autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label><AuthError message={error} /><button disabled={busy}>{busy ? "Signing in…" : "Continue"}</button></form>}{!mfa && <div className="auth-links"><a href="/register">Create account</a><a href="/reset">Forgot password?</a></div>}</div></div></section></main>;
}

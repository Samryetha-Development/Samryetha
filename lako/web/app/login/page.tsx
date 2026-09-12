"use client";

import { FormEvent, useState } from "react";

function safeReturnTo(value: string | null) {
  return value?.startsWith("/oauth/authorize?") ? value : "/account";
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
  return <main><section className="auth-wrap"><div className="panel"><div className="eyebrow">Lako</div><h1>{mfa ? "Verify it’s you" : "Sign in"}</h1><p className="subtle">{mfa ? "Enter an authenticator code or one recovery code." : "Use your username or email."}</p>{mfa ? <form onSubmit={verify}><label>Verification code<input name="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus /></label>{error && <div className="form-error">{error}</div>}<button disabled={busy}>{busy ? "Verifying…" : "Verify and continue"}</button></form> : <form onSubmit={submit}><label>Username or email<input name="login" autoComplete="username" required autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label>{error && <div className="form-error">{error}</div>}<button disabled={busy}>{busy ? "Signing in…" : "Continue"}</button></form>}{!mfa && <a className="auth-link" href="/register">Create account</a>}</div></section></main>;
}

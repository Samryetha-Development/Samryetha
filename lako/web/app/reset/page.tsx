"use client";

import { FormEvent, useEffect, useState } from "react";

export default function Reset() {
  const [token, setToken] = useState<string | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setToken(new URLSearchParams(location.search).get("token"));
  }, []);
  async function request(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/password/reset/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ login: data.get("login") }),
    });
    setBusy(false);
    if (response.ok) setDone("If an account with a verified address exists, a reset link has been sent.");
    else setError("Request failed. Try again.");
  }
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    const first = String(data.get("password") ?? "");
    if (first !== String(data.get("confirm") ?? "")) { setError("Passwords do not match."); setBusy(false); return; }
    const response = await fetch("/api/auth/password/reset/confirm", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token, new_password: first }),
    });
    const body = await response.json().catch(() => ({}));
    setBusy(false);
    if (response.ok) setDone("Password updated. You can now sign in.");
    else setError(body.error?.message ?? "Reset failed. The link may have expired.");
  }
  return <main><section className="auth-wrap"><div className="panel"><div className="eyebrow">Lako</div><h1>{token ? "Choose a new password" : "Reset password"}</h1>
    {done ? <p className="subtle">{done} <a className="auth-link" href="/login">Back to sign in</a></p>
    : token ? <form onSubmit={confirm}><label>New password<input name="password" type="password" autoComplete="new-password" required autoFocus /></label><label>Confirm password<input name="confirm" type="password" autoComplete="new-password" required /></label>{error && <div className="form-error">{error}</div>}<button disabled={busy}>{busy ? "Saving…" : "Set new password"}</button></form>
    : <form onSubmit={request}><label>Username or email<input name="login" autoComplete="username" required autoFocus /></label>{error && <div className="form-error">{error}</div>}<button disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button></form>}
  </div></section></main>;
}

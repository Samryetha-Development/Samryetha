"use client";

import { FormEvent, useState } from "react";

function csrf() {
  return decodeURIComponent(document.cookie.split("; ").find((value) => value.startsWith("lako_csrf="))?.split("=")[1] ?? "");
}

export default function ChangePassword() {
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setDone("");
    const data = new FormData(event.currentTarget);
    const next = String(data.get("new_password") ?? "");
    if (next !== String(data.get("confirm") ?? "")) { setError("Passwords do not match."); setBusy(false); return; }
    try {
      const response = await fetch("/api/account/password/change", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({ current_password: data.get("current_password"), new_password: next }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error?.message ?? "Request failed");
      setDone("Password updated. Other sessions were signed out.");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return <main><section className="auth-wrap"><div className="panel"><div className="eyebrow">Lako</div><h1>Change password</h1>
    <form onSubmit={submit}><label>Current password<input name="current_password" type="password" autoComplete="current-password" required autoFocus /></label><label>New password<input name="new_password" type="password" autoComplete="new-password" required /></label><label>Confirm new password<input name="confirm" type="password" autoComplete="new-password" required /></label>{error && <div className="form-error">{error}</div>}{done && <p className="subtle">{done}</p>}<button disabled={busy}>{busy ? "Saving…" : "Update password"}</button></form>
    <p><a className="auth-link" href="/account/security">Back to security</a></p>
  </div></section></main>;
}

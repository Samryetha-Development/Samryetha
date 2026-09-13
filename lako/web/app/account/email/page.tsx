"use client";

import { FormEvent, useState } from "react";

function csrf() {
  return decodeURIComponent(document.cookie.split("; ").find((value) => value.startsWith("lako_csrf="))?.split("=")[1] ?? "");
}

export default function ChangeEmail() {
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setDone("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/account/email/change", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({ email: data.get("email") }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error?.message ?? "Request failed");
      setDone("Verification link sent — check your inbox, then open it to finish.");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return <main><section className="auth-wrap"><div className="panel"><div className="eyebrow">Lako</div><h1>Change email</h1><p className="subtle">The new address takes effect after you open the verification link.</p>
    <form onSubmit={submit}><label>New email<input name="email" type="email" autoComplete="email" required autoFocus /></label>{error && <div className="form-error">{error}</div>}{done && <p className="subtle">{done}</p>}<button disabled={busy}>{busy ? "Saving…" : "Save and verify"}</button></form>
    <p><a className="auth-link" href="/account/security">Back to security</a></p>
  </div></section></main>;
}

"use client";

import { FormEvent, useEffect, useState } from "react";

type EmailState = { email: string | null; verified: boolean; placeholder: boolean };

function csrf() {
  return decodeURIComponent(document.cookie.split("; ").find((value) => value.startsWith("lako_csrf="))?.split("=")[1] ?? "");
}

export default function ChangeEmail() {
  const [state, setState] = useState<EmailState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const response = await fetch("/api/account/email");
      if (response.status === 401) { location.href = "/login"; return; }
      if (!response.ok) throw new Error("load failed");
      setState(await response.json());
    } catch {
      setError("Could not load your email settings.");
    }
  }
  useEffect(() => { void load(); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true); setError(""); setNotice("");
    const data = new FormData(form);
    try {
      const response = await fetch("/api/account/email/change", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({ email: data.get("email") }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error?.message ?? "Request failed");
      form.reset();
      setNotice("Verification link sent — open it from your inbox to finish.");
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/account/email/verify/request", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf() },
        body: JSON.stringify({}),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error?.message ?? "Request failed");
      setNotice(body.already_verified ? "This address is already verified." : "Verification link sent — check your inbox.");
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <section className="account">
        <h1>Email address</h1>
        {error && <p className="form-error">{error}</p>}
        {notice && <p className="subtle" role="status">{notice}</p>}
        <div className="security-list">
          <div className="security-row">
            <div className="row-main">
              <strong>Current email</strong>
              <div className="subtle">{state ? (state.email ?? "None on file") : "Loading…"}</div>
            </div>
            <div className="security-controls">
              {state && (
                state.verified
                  ? <span className="security-status security-status-active">Verified</span>
                  : <span className="security-status">Unverified</span>
              )}
              {state && !state.verified && !state.placeholder && (
                <div className="security-action">
                  <button type="button" className="row-action" disabled={busy} onClick={() => void resend()}>Send verification</button>
                </div>
              )}
            </div>
          </div>
        </div>

        {state?.placeholder && (
          <p className="subtle">This account has no deliverable address yet. Set a real email below to finish setup.</p>
        )}

        <form onSubmit={submit}>
          <label>Change email
            <input name="email" type="email" autoComplete="email" required placeholder="you@example.com" />
          </label>
          <p className="subtle">The new address takes effect after you open the verification link.</p>
          <button disabled={busy}>{busy ? "Saving…" : "Save and verify"}</button>
        </form>

        <p><a className="auth-link" href="/account/security">Back to security</a></p>
      </section>
    </main>
  );
}

"use client";

import { FormEvent, useEffect, useState } from "react";

type Status = { totp_enabled: boolean; recovery_codes_remaining: number; assurance_level: string };
type Setup = { secret: string; provisioning_uri: string; qr_code: string };

function csrf() {
  return decodeURIComponent(document.cookie.split("; ").find((value) => value.startsWith("lako_csrf="))?.split("=")[1] ?? "");
}
async function api(path: string, body?: object) {
  const response = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "content-type": "application/json", "x-csrf-token": csrf() } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message ?? "Request failed");
  return data;
}

export default function Security() {
  const [status, setStatus] = useState<Status | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [error, setError] = useState("");
  async function load() { try { setStatus(await api("/api/account/mfa")); } catch { location.href = "/login"; } }
  useEffect(() => { void load(); }, []);
  async function begin() { setError(""); try { setSetup(await api("/api/account/mfa/totp/setup", {})); } catch (reason) { setError((reason as Error).message); } }
  async function confirm(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); setError(""); try { const result = await api("/api/account/mfa/totp/confirm", { code: data.get("code") }); setCodes(result.recovery_codes); setSetup(null); await load(); } catch (reason) { setError((reason as Error).message); } }
  async function stepUp(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); setError(""); try { await api("/api/account/mfa/step-up", { code: data.get("code") }); await load(); } catch (reason) { setError((reason as Error).message); } }
  async function regenerate() { setError(""); try { const result = await api("/api/account/mfa/recovery-codes/regenerate", {}); setCodes(result.recovery_codes); await load(); } catch (reason) { setError((reason as Error).message); } }
  async function disable() { setError(""); try { await api("/api/account/mfa/totp/disable", {}); setCodes([]); await load(); } catch (reason) { setError((reason as Error).message); } }
  return <main><section className="account"><div className="eyebrow">Account security</div><h1>Security</h1>{error && <p className="form-error">{error}</p>}{codes.length > 0 && <article className="card recovery"><h2>Save your recovery codes</h2><p className="subtle">Each code works once. Store them somewhere safe; they will not be shown again.</p><div className="code-grid">{codes.map((code) => <code key={code}>{code}</code>)}</div></article>}<div className="card"><div className="row"><div><strong>Password</strong><div className="subtle">Used for sign in</div></div><span className="badge">Active</span></div><div className="row"><div><strong>Two-factor authentication</strong><div className="subtle">{status?.totp_enabled ? `Enabled · ${status.recovery_codes_remaining} recovery codes remaining` : "Protect sign-in with an authenticator app"}</div></div>{status?.totp_enabled ? <span className="badge">Enabled</span> : <button onClick={() => void begin()}>Set up</button>}</div>{setup && <div className="mfa-setup"><img src={setup.qr_code} width="210" height="210" alt="Authenticator setup QR code" /><div><h2>Scan the code</h2><p className="subtle">Scan with your authenticator app, then enter the six-digit code.</p><code className="secret">{setup.secret}</code><form onSubmit={confirm}><label>Verification code<input name="code" inputMode="numeric" autoComplete="one-time-code" required /></label><button>Enable two-factor authentication</button></form></div></div>}{status?.totp_enabled && status.assurance_level !== "AAL2" && <div className="mfa-inline"><p>Verify a TOTP or recovery code before changing security settings.</p><form onSubmit={stepUp}><input name="code" aria-label="Verification code" placeholder="Verification code" required /><button>Step up</button></form></div>}{status?.totp_enabled && status.assurance_level === "AAL2" && <div className="mfa-actions"><button onClick={() => void regenerate()}>New recovery codes</button><button className="danger" onClick={() => void disable()}>Disable two-factor authentication</button></div>}<div className="row"><div><strong>Passkeys</strong><div className="subtle">No passkeys</div></div><span className="badge">Coming later</span></div><div className="row"><div><strong>Devices</strong><div className="subtle">Review browsers signed into your account</div></div><a href="/account/devices">View devices →</a></div></div></section></main>;
}

"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type Status = { totp_enabled: boolean; recovery_codes_remaining: number; assurance_level: string };
type Setup = { secret: string; provisioning_uri: string; qr_code: string };
type DialogKind = "setup" | "disable" | "recovery" | null;
type Notice = { id: number; message: string };

class ApiFailure extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

function csrf() {
  return decodeURIComponent(document.cookie.split("; ").find((value) => value.startsWith("lako_csrf="))?.split("=")[1] ?? "");
}
async function api(path: string, body?: object) {
  const response = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "content-type": "application/json", "x-csrf-token": csrf() } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json();
  if (!response.ok) throw new ApiFailure(data.error?.code ?? "REQUEST_FAILED", data.error?.message ?? "Request failed");
  return data;
}

export default function Security() {
  const [status, setStatus] = useState<Status | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);   // 驱动 showModal()/close()
  const [view, setView] = useState<DialogKind>(null);       // 决定框里渲染什么，退场动画播完才清
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const exitTimer = useRef<number | null>(null);
  const noticeTimer = useRef<number | null>(null);

  async function load() { try { setStatus(await api("/api/account/mfa")); } catch { location.href = "/login"; } }
  useEffect(() => { void load(); }, []);
  useEffect(() => () => {
    if (exitTimer.current) window.clearTimeout(exitTimer.current);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
  }, []);

  // 原生 <dialog>：打开走 showModal()（焦点陷阱 / Escape / ::backdrop 由浏览器给），Escape 关闭时同步状态。
  useEffect(() => {
    const element = dialogRef.current;
    if (!element) return;
    if (dialog && !element.open) element.showModal();
    if (!dialog && element.open) element.close();
  }, [dialog]);

  // 关闭分两步：先收起 dialog（触发退场动画），内容再延后卸载 —— 否则退场时只剩一个空框。
  // 200ms 与 .modal 的 transition 时长对齐。
  function dismiss() {
    setDialog(null);
    clearNotice();
    if (exitTimer.current) window.clearTimeout(exitTimer.current);
    exitTimer.current = window.setTimeout(() => { setView(null); setSetup(null); setCodes([]); setError(""); exitTimer.current = null; }, 200);
  }
  function openWith(kind: Exclude<DialogKind, null>) {
    if (exitTimer.current) { window.clearTimeout(exitTimer.current); exitTimer.current = null; }
    clearNotice(); setError(""); setView(kind); setDialog(kind);
  }
  function clearNotice() {
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = null;
    setNotice(null);
  }
  function notify(message: string) {
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    setNotice({ id: Date.now(), message });
    noticeTimer.current = window.setTimeout(() => { setNotice(null); noticeTimer.current = null; }, 4200);
  }
  function reportError(reason: unknown) {
    const failure = reason instanceof ApiFailure ? reason : new ApiFailure("REQUEST_FAILED", (reason as Error).message);
    if (failure.code === "STEP_UP_REQUIRED" || failure.code === "INVALID_MFA_CODE" || failure.code === "MFA_CHALLENGE_INVALID") {
      setError("");
      notify(failure.message);
      return;
    }
    setError(failure.message);
  }

  // 先拿到 secret 再开框：开框时内容已完整，避免"加载态 → 内容"那一下跳变。
  async function begin() { clearNotice(); setError(""); setBusy(true); try { const next = await api("/api/account/mfa/totp/setup", {}); setSetup(next); openWith("setup"); } catch (reason) { reportError(reason); } finally { setBusy(false); } }
  async function confirm(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); clearNotice(); setError(""); try { const result = await api("/api/account/mfa/totp/confirm", { code: data.get("code") }); setCodes(result.recovery_codes); setView("recovery"); setDialog("recovery"); await load(); } catch (reason) { reportError(reason); } }
  async function stepUp(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); clearNotice(); setError(""); try { await api("/api/account/mfa/step-up", { code: data.get("code") }); await load(); } catch (reason) { reportError(reason); } }
  async function regenerate() { clearNotice(); setError(""); try { const result = await api("/api/account/mfa/recovery-codes/regenerate", {}); setCodes(result.recovery_codes); openWith("recovery"); await load(); } catch (reason) { reportError(reason); } }
  async function turnOff() { clearNotice(); setError(""); try { await api("/api/account/mfa/totp/disable", {}); setCodes([]); dismiss(); await load(); } catch (reason) { reportError(reason); } }

  // 二态项（2FA）用开关；开启不是瞬时生效（要走扫码确认），关闭是破坏性操作（对话框里二次确认）。
  const enabled = status?.totp_enabled ?? false;
  const needsStepUp = enabled && status?.assurance_level !== "AAL2";

  return (
    <main>
      <section className="account security-account">
        <h1>Security</h1>
        {error && !view && <p className="form-error">{error}</p>}
        {notice && !view && <div key={notice.id} className="security-notification" role="alert">{notice.message}</div>}
        <div className="security-list">
          <div className="security-row">
            <div className="row-main">
              <strong>Password</strong>
              <div className="subtle">Used for sign in; changing it signs out other sessions</div>
            </div>
            <div className="security-controls">
              <span className="security-status security-status-active">Active</span>
              <div className="security-action"><a className="row-action" href="/account/password">Change</a></div>
            </div>
          </div>

          <div className="security-row">
            <div className="row-main">
              <strong>Email address</strong>
              <div className="subtle">Change and verify your email</div>
            </div>
            <div className="security-controls">
              <div className="security-action"><a className="row-action" href="/account/email">Change</a></div>
            </div>
          </div>

          <div className="security-row">
            <div className="row-main">
              <strong>Two-factor authentication</strong>
              <div className="subtle">{enabled ? "Enabled with an authenticator app" : "Protect sign-in with an authenticator app"}</div>
            </div>
            <div className="security-controls">
              <div className="security-action">
                <button type="button" role="switch" aria-checked={enabled} aria-label="Two-factor authentication" className="switch" disabled={!status || busy} onClick={() => (enabled ? openWith("disable") : void begin())}>
                  <span className="switch-thumb" />
                </button>
              </div>
            </div>
          </div>

          {enabled && status?.assurance_level === "AAL2" && (
            <div className="security-row">
              <div className="row-main">
                <strong>Recovery codes</strong>
                <div className="subtle">{status.recovery_codes_remaining} codes unused</div>
              </div>
              <div className="security-controls">
                <div className="security-action"><button type="button" className="row-action" onClick={() => void regenerate()}>Generate new codes</button></div>
              </div>
            </div>
          )}

          <div className="security-row">
            <div className="row-main">
              <strong>Passkeys</strong>
              <div className="subtle">Sign in without a password</div>
            </div>
            <div className="security-controls">
              <span className="security-status security-status-end">Coming soon</span>
            </div>
          </div>

          <div className="security-row">
            <div className="row-main">
              <strong>Devices</strong>
              <div className="subtle">Review browsers signed into your account</div>
            </div>
            <div className="security-controls">
              <div className="security-action"><a className="row-action" href="/account/devices">View devices</a></div>
            </div>
          </div>
        </div>
      </section>

      <dialog ref={dialogRef} className={`modal${notice && view ? " modal-with-notification" : ""}`} onClose={dismiss} onClick={(event) => { if (event.target === dialogRef.current) dismiss(); }}>
        {notice && view && <div key={notice.id} className="security-notification security-notification-dialog" role="alert">{notice.message}</div>}
        {view === "setup" && setup && (
          <div className="modal-body">
            <h2>Set up two-factor authentication</h2>
            <p className="subtle">Scan the code with your authenticator app, then enter the six digits it shows.</p>
            {error && <p className="form-error">{error}</p>}
            <div className="mfa-setup">
              <img src={setup.qr_code} width="200" height="200" alt="Authenticator setup QR code" />
              <div>
                <code className="secret">{setup.secret}</code>
                <form onSubmit={confirm}>
                  <label>Verification code<input name="code" autoFocus inputMode="numeric" autoComplete="one-time-code" required /></label>
                  <div className="modal-actions">
                    <button>Enable</button>
                    <button type="button" onClick={dismiss}>Cancel</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        )}

        {view === "disable" && (
          <div className="modal-body">
            {needsStepUp ? (
              <>
                <h2>Confirm it&rsquo;s you</h2>
                <p className="subtle">Enter a code from your authenticator app, or a recovery code, to change security settings.</p>
                {error && <p className="form-error">{error}</p>}
                <form onSubmit={stepUp}>
                  <label>Verification code<input name="code" autoFocus inputMode="numeric" autoComplete="one-time-code" required /></label>
                  <div className="modal-actions">
                    <button>Verify</button>
                    <button type="button" onClick={dismiss}>Cancel</button>
                  </div>
                </form>
              </>
            ) : (
              <>
                <h2>Turn off two-factor authentication?</h2>
                <p className="subtle">Your account goes back to being protected by your password alone, and the recovery codes you saved will stop working.</p>
                {error && <p className="form-error">{error}</p>}
                <div className="modal-actions">
                  <button type="button" className="danger" onClick={() => void turnOff()}>Turn off</button>
                  <button type="button" onClick={dismiss}>Cancel</button>
                </div>
              </>
            )}
          </div>
        )}

        {view === "recovery" && codes.length > 0 && (
          <div className="modal-body recovery-dialog">
            <h2>Save your recovery codes</h2>
            <p className="subtle">Each code works once. Store them somewhere safe; they will not be shown again.</p>
            <div className="code-grid">{codes.map((code) => <code key={code}>{code}</code>)}</div>
            <div className="modal-actions">
              <button type="button" onClick={dismiss}>Done</button>
            </div>
          </div>
        )}
      </dialog>
    </main>
  );
}

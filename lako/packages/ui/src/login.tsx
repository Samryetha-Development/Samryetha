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

type Stage = "login" | "mfa" | "email-code";

function b64urlToBytes(value: string): Uint8Array {
  const pad = value.length % 4 === 0 ? "" : "=".repeat(4 - (value.length % 4));
  const binary = atob((value + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}
function bytesToB64url(buffer: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
/** Usernameless passkey sign-in: options carry no allowCredentials by design. */
async function getPasskey(options: Record<string, unknown>) {
  const publicKey = {
    ...options,
    challenge: b64urlToBytes(options.challenge as string),
    allowCredentials: ((options.allowCredentials as { id: string }[] | undefined) ?? []).map((item) => ({ ...item, id: b64urlToBytes(item.id) })),
  } as unknown as PublicKeyCredentialRequestOptions;
  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey sign-in was cancelled");
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: bytesToB64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToB64url(response.clientDataJSON),
      authenticatorData: bytesToB64url(response.authenticatorData),
      signature: bytesToB64url(response.signature),
      userHandle: response.userHandle ? bytesToB64url(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

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
  // Login identifier kept across stages: the email-code flows need it again
  // after the password step (mfa) and on the passwordless path.
  const [identifier, setIdentifier] = useState("");
  const [codeHint, setCodeHint] = useState("");
  // Which flow the code entry belongs to: the second step of a password sign-in
  // (redeemed against the pending MFA challenge cookie) or passwordless sign-in
  // (redeemed against the identifier). They are different endpoints.
  const [codeOrigin, setCodeOrigin] = useState<"mfa" | "passwordless">("passwordless");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const login = String(data.get("login") ?? "");
    setIdentifier(login);
    try {
      const response = await fetcher("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ login, password: data.get("password") }),
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

  /** Passwordless): mail a code to the address on the identifier the user typed. */
  async function requestEmailCode() {
    setBusy(true);
    setError("");
    try {
      const response = await fetcher("/api/auth/email-code/request", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ login: identifier }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(body?.error?.message ?? `Could not send a code (HTTP ${response.status})`);
        setBusy(false);
        return;
      }
      // The endpoint answers identically for unknown accounts (no enumeration),
      // so the copy must not promise that a message was actually sent.
      setCodeHint("If that account can receive mail, a code is on its way.");
      setCodeOrigin("passwordless");
      setStage("email-code");
    } catch (cause) {
      setError(`Could not send a code: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
    setBusy(false);
  }

  /** Second step of a password sign-in: mail the code /api/auth/login/mfa accepts. */
  async function requestMfaEmailCode() {
    setBusy(true);
    setError("");
    try {
      const response = await fetcher("/api/auth/login/mfa/email-code", { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(body?.error?.message ?? `Could not send a code (HTTP ${response.status})`);
        setBusy(false);
        return;
      }
      setCodeHint(body?.email ? `Code sent to ${body.email}.` : "Code sent.");
      setCodeOrigin("mfa");
      setStage("email-code");
    } catch (cause) {
      setError(`Could not send a code: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
    setBusy(false);
  }

  async function verifyEmailCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const code = String(data.get("code") ?? "");
    // Reached from the MFA stage, the pending challenge cookie is what proves the
    // password step; from the first stage there is no challenge and the login
    // identifier is the first factor instead.
    const passwordless = codeOrigin === "passwordless";
    try {
      const response = await fetcher(passwordless ? "/api/auth/login/email-code" : "/api/auth/login/mfa", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(passwordless ? { login: identifier, code } : { code }),
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

  async function passkey() {
    setBusy(true);
    setError("");
    try {
      const optionsResponse = await fetcher("/api/auth/webauthn/options", { method: "POST" });
      if (!optionsResponse.ok) throw new Error(`Passkey sign-in unavailable (HTTP ${optionsResponse.status})`);
      const credential = await getPasskey(await optionsResponse.json());
      const response = await fetcher("/api/auth/webauthn/verify", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ credential }),
      });
      if (response.ok) {
        onSuccess();
        return;
      }
      const body = await response.json().catch(() => null);
      setError(body?.error?.message ?? `Passkey sign-in failed (HTTP ${response.status})`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Passkey sign-in failed");
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
  const emailCode = stage === "email-code";
  const authState = busy ? "busy" : mfa || emailCode ? "mfa" : "idle";

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
          <h1>{emailCode ? "Check your email" : mfa ? "Verification" : "Sign in"}</h1>
          <p className="lako-subtle">
            {emailCode
              ? codeHint || "Enter the six-digit code we emailed you."
              : mfa
                ? "Enter your authentication or recovery code."
                : subtitle}
          </p>

          {emailCode ? (
            <form onSubmit={verifyEmailCode}>
              <label>
                Email code
                <input name="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus />
              </label>
              <LakoAuthError message={error} />
              <button disabled={busy}>{busy ? "Verifying…" : "Continue"}</button>
            </form>
          ) : mfa ? (
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

          {mfa && (
            <div className="lako-auth-links">
              <button type="button" className="lako-auth-linklike" disabled={busy} onClick={() => void requestMfaEmailCode()}>
                Email me a code instead
              </button>
            </div>
          )}

          {!mfa && !emailCode && (
            <div className="lako-auth-passkey">
              <button type="button" disabled={busy} onClick={() => void passkey()}>Sign in with a passkey</button>
            </div>
          )}

          {!mfa && !emailCode && (
            <div className="lako-auth-links">
              <button type="button" className="lako-auth-linklike" disabled={busy} onClick={() => void requestEmailCode()}>
                Email me a sign-in code
              </button>
            </div>
          )}

          {emailCode && (
            <div className="lako-auth-links">
              <button
                type="button"
                className="lako-auth-linklike"
                disabled={busy}
                onClick={() => {
                  setStage(codeOrigin === "mfa" ? "mfa" : "login");
                  setError("");
                }}
              >
                Back
              </button>
            </div>
          )}

          {!mfa && !emailCode && (registerHref || resetHref) && (
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

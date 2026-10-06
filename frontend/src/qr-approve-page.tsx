import { useEffect, useState } from "react";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n } from "./lib/i18n";

// 手机端扫码确认页：展示 PC 请求上下文，批准/拒绝本次扫码登录。
// 未登录先去登录（ticket 暂存，登录后由 RootApp.signIn 带回本页继续）。
// 账号有真实已验证邮箱时，批准前多一步：先发 6 位确认码邮件，再输码批准。
export function QrApprovePage() {
  const { user, loading } = useAuth();
  const { locale, t } = useI18n();
  const [ticket] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("t") ?? "";
  });
  const [info, setInfo] = useState<{
    createdAt: number;
    expiresAt: number;
    ip: string | null;
    userAgent: string | null;
    emailConfirmationRequired: boolean;
    emailHint: string | null;
  } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [done, setDone] = useState<"approved" | "denied" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 邮箱确认码步骤：服务端说需要，或一键批准时被 EMAIL_CODE_REQUIRED 顶回来。
  const [codeStep, setCodeStep] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [emailHint, setEmailHint] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!ticket) {
      setInvalid(true);
      return;
    }
    if (loading) return;
    if (!user) {
      try {
        // 带时间戳暂存（5 分钟有效，过期由 signIn 丢弃）
        sessionStorage.setItem("pending_qr_ticket", JSON.stringify({ t: ticket, at: Date.now() }));
      } catch {
        // 无痕模式等存储不可用时退化为登录后手动重扫
      }
      window.location.href = "/login";
      return;
    }
    api.auth
      .qrInfo(ticket)
      .then((data) => {
        setInfo(data);
        setEmailHint(data.emailHint);
        setCodeStep(data.emailConfirmationRequired);
      })
      .catch(() => setInvalid(true));
  }, [ticket, user, loading]);

  // 发码：成功后进入输码步骤；服务端说不需要（占位邮箱）则回到一键批准。
  const sendCode = async () => {
    if (!ticket) return;
    setSending(true);
    setError(null);
    try {
      const res = await api.auth.qrRequestCode({ ticket_id: ticket });
      if (!res.required) {
        setCodeStep(false);
        return;
      }
      setCodeStep(true);
      setCodeSent(true);
      if (res.emailHint) setEmailHint(res.emailHint);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("qr.error"));
    } finally {
      setSending(false);
    }
  };

  const decide = async (approve: boolean) => {
    if (!ticket || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (approve) {
        await api.auth.qrApprove(codeStep ? { ticket_id: ticket, code: code.trim() } : { ticket_id: ticket });
      } else {
        await api.auth.qrDeny({ ticket_id: ticket });
      }
      // 成功后擦掉 query 中的 ticket，避免刷新重放/历史残留
      try {
        window.history.replaceState({}, "", window.location.pathname);
      } catch {
        // 忽略（旧浏览器）
      }
      setDone(approve ? "approved" : "denied");
    } catch (err) {
      // 服务端要求确认码（FORBIDDEN + 这句 message 是流程信号）：切到输码步骤并
      // 自动发码，而不是给个死路错误。
      if (approve && err instanceof ApiError && err.code === "FORBIDDEN" && err.message === "EMAIL_CODE_REQUIRED") {
        setCodeStep(true);
        await sendCode();
        return;
      }
      if (codeStep && err instanceof ApiError && err.code === "BAD_REQUEST") {
        setError(t("qr.codeInvalid"));
        return;
      }
      setError(err instanceof ApiError ? err.message : t("qr.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <a className="login-wordmark" href="/" aria-label={t("nav.home")}>Samryetha</a>
        <section className="login-card">
          <div className="login-card-content">
            {invalid ? (
              <div className="empty-state content-fade">
                <p>{t("qr.expired")}</p>
                <p><a className="sender" href="/login">{t("qr.signIn")}</a></p>
              </div>
            ) : done ? (
              <div className="empty-state content-fade">
                <p>{done === "approved" ? t("qr.approvedDone") : t("qr.deniedDone")}</p>
              </div>
            ) : !user ? (
              <div className="empty-state content-fade">
                <p>{t("qr.signInFirst")}</p>
              </div>
            ) : info ? (
              <>
                <header className="login-heading">
                  <h1>{codeStep ? t("qr.codeTitle") : t("qr.approveTitle")}</h1>
                  <p>{codeStep ? t("qr.codeDesc") : t("qr.approveDesc")}</p>
                </header>
                <p className="login-sub">
                  {t("qr.requestedAt", { time: timeAgo(info.createdAt, locale) })}
                  {info.ip ? ` · ${t("qr.requestFrom", { ip: info.ip })}` : ""}
                </p>
                {info.userAgent ? <p className="login-sub">{info.userAgent}</p> : null}
                {codeStep && codeSent ? (
                  <>
                    {emailHint ? <p className="login-sub">{t("qr.codeSent", { email: emailHint })}</p> : null}
                    <label className="login-field">
                      <span>{t("qr.codeLabel")}</span>
                      <span className="login-input-frame">
                        <input
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={6}
                          value={code}
                          onChange={(event) => setCode(event.target.value)}
                          autoFocus
                        />
                      </span>
                    </label>
                  </>
                ) : null}
                {error && <p className="login-error form-error" role="alert">{error}</p>}
                <div className="dialog-actions">
                  <button type="button" className="action-btn" disabled={busy || sending} onClick={() => void decide(false)}>{t("qr.deny")}</button>
                  {codeStep ? (
                    codeSent ? (
                      <button type="button" className="primary-action" disabled={busy || sending || !code.trim()} onClick={() => void decide(true)}>{t("qr.codeApprove")}</button>
                    ) : (
                      <button type="button" className="primary-action" disabled={busy || sending} onClick={() => void sendCode()}>{t("qr.sendCode")}</button>
                    )
                  ) : (
                    <button type="button" className="primary-action" disabled={busy || sending} onClick={() => void decide(true)}>{t("qr.approve")}</button>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-state content-fade">
                <p>{t("common.loading")}</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

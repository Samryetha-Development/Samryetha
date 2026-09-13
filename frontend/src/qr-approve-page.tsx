import { useEffect, useState } from "react";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n } from "./lib/i18n";

// 手机端扫码确认页：展示 PC 请求上下文，批准/拒绝本次扫码登录。
// 未登录先去登录（ticket 暂存，登录后由 RootApp.signIn 带回本页继续）。
export function QrApprovePage() {
  const { user, loading } = useAuth();
  const { locale, t } = useI18n();
  const [ticket] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("t") ?? "";
  });
  const [info, setInfo] = useState<{ createdAt: number; expiresAt: number; ip: string | null; userAgent: string | null } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [done, setDone] = useState<"approved" | "denied" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticket) {
      setInvalid(true);
      return;
    }
    if (loading) return;
    if (!user) {
      try {
        sessionStorage.setItem("pending_qr_ticket", ticket);
      } catch {
        // 无痕模式等存储不可用时退化为登录后手动重扫
      }
      window.location.href = "/login";
      return;
    }
    api.auth
      .qrInfo(ticket)
      .then((data) => setInfo(data))
      .catch(() => setInvalid(true));
  }, [ticket, user, loading]);

  const decide = async (approve: boolean) => {
    if (!ticket || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (approve) await api.auth.qrApprove({ ticket_id: ticket });
      else await api.auth.qrDeny({ ticket_id: ticket });
      setDone(approve ? "approved" : "denied");
    } catch (err) {
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
                  <h1>{t("qr.approveTitle")}</h1>
                  <p>{t("qr.approveDesc")}</p>
                </header>
                <p className="login-sub">
                  {t("qr.requestedAt", { time: timeAgo(info.createdAt, locale) })}
                  {info.ip ? ` · ${t("qr.requestFrom", { ip: info.ip })}` : ""}
                </p>
                {info.userAgent ? <p className="login-sub">{info.userAgent}</p> : null}
                {error && <p className="login-error form-error" role="alert">{error}</p>}
                <div className="dialog-actions">
                  <button type="button" className="action-btn" disabled={busy} onClick={() => void decide(false)}>{t("qr.deny")}</button>
                  <button type="button" className="primary-action" disabled={busy} onClick={() => void decide(true)}>{t("qr.approve")}</button>
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

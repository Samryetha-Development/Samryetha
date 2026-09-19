import { type AnimationEvent, type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Dialog } from "samryetha-ui-commons";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { reducedMotion } from "./lib/prefs";
import { useI18n } from "./lib/i18n";
import { EyeIcon } from "./icons";

export type AuthMode = "login" | "register";
type FieldErrors = Record<string, string | undefined>;
type QrStatus = "waiting" | "approved" | "denied" | "expired" | "error";

// 统一登录入口：Lako 的 OIDC 起始地址（论坛后端会 302 到 IdP）。
const OIDC_ENTRY = "/api/auth/login";
const OIDC_ENTRY_FALLBACK = `${OIDC_ENTRY}?returnTo=%2F`;

// 扫码登录弹窗：二维码展示 + SSE 等待手机批准，批准后换会话进站。
function QrLoginModal({ onSignedIn, onClose }: { onSignedIn: () => void; onClose: () => void }) {
  const { t } = useI18n();
  const [qr, setQr] = useState<{ ticket_id: string; secret: string; qr_data_uri: string } | null>(null);
  const [status, setStatus] = useState<QrStatus>("waiting");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let alive = true;
    api.auth
      .qrStart()
      .then((data) => {
        if (!alive) return;
        setQr(data);
        const source = new EventSource(`/api/auth/qr/wait?ticket_id=${encodeURIComponent(data.ticket_id)}`);
        sourceRef.current = source;
        source.addEventListener("approved", () => {
          source.close();
          if (!alive) return;
          setStatus("approved");
          api.auth
            .qrExchange({ ticket_id: data.ticket_id, secret: data.secret })
            .then(() => {
              if (alive) onSignedIn();
            })
            .catch(() => {
              if (alive) setStatus("error");
            });
        });
        const terminal = (next: QrStatus) => {
          source.close();
          if (alive) setStatus(next);
        };
        source.addEventListener("denied", () => terminal("denied"));
        source.addEventListener("expired", () => terminal("expired"));
        source.addEventListener("closed", () => terminal("expired"));
        source.onerror = () => {
          if (source.readyState === EventSource.CLOSED && alive) {
            setStatus((current) => (current === "waiting" ? "error" : current));
          }
        };
      })
      .catch(() => {
        if (alive) setStatus("error");
      });
    return () => {
      alive = false;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const statusText =
    status === "waiting"
      ? t("qr.waiting")
      : status === "approved"
        ? t("qr.approved")
        : status === "denied"
          ? t("qr.denied")
          : status === "expired"
            ? t("qr.expired")
            : t("qr.error");

  // 由调用方在扫码流程激活时条件挂载，所以这里 open 恒为 true（不会进 SSR 输出）。
  // 迁移前这个框没有接 Esc，换成 Dialog 后由 Radix 提供（同时补上焦点陷阱与滚动锁）。
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("qr.title")}
      contentClassName="feedback-modal"
      contentProps={{ "aria-label": t("qr.title") }}
      actions={<button type="button" className="action-btn" onClick={onClose}>{t("qr.cancel")}</button>}
    >
      <p className="admin-muted">{t("qr.hint")}</p>
      {qr ? (
        <p style={{ textAlign: "center", margin: "14px 0" }}>
          <img src={qr.qr_data_uri} alt={t("qr.title")} width={210} height={210} />
        </p>
      ) : (
        <p className="admin-muted">{t("common.loading")}</p>
      )}
      <p role="status">{statusText}</p>
    </Dialog>
  );
}

export function LoginPage({ mode, onSignedIn }: { mode: AuthMode; onSignedIn: () => void }) {
  const { refresh } = useAuth();
  const { t } = useI18n();
  const [displayedMode, setDisplayedMode] = useState<AuthMode>(mode);
  const [phase, setPhase] = useState<"" | "is-leaving" | "is-entering">("");
  const [cardHeight, setCardHeight] = useState<number>();
  const [loginUsername, setLoginUsername] = useState("");
  const [password, setPassword] = useState("");
  const [registerUsername, setRegisterUsername] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [registered, setRegistered] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [autofilled, setAutofilled] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [passwordAuthEnabled, setPasswordAuthEnabled] = useState(true);
  // 登录入口的 href：带上当前页的 returnTo，签完回到用户本来想去的地方。
  // 在 effect 里算而不是渲染时读 window，避免 SSR 阶段访问不到 location。
  const [oidcHref, setOidcHref] = useState(OIDC_ENTRY_FALLBACK);
  const contentRef = useRef<HTMLDivElement>(null);
  const transitionToken = useRef(0);

  useEffect(() => {
    void api.auth
      .config()
      .then(({ oidcEnabled: oidc, passwordAuthEnabled: password }) => {
        setOidcEnabled(oidc);
        setPasswordAuthEnabled(password);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("returnTo");
    setOidcHref(target ? `${OIDC_ENTRY}?returnTo=${encodeURIComponent(target)}` : OIDC_ENTRY);
  }, []);

  useEffect(() => {
    if (mode === displayedMode) return;
    setErrors({});
    setRegistered(false);
    if (reducedMotion()) {
      setDisplayedMode(mode);
      return;
    }
    const token = ++transitionToken.current;
    setPhase("is-leaving");
    const timer = window.setTimeout(() => {
      if (token !== transitionToken.current) return;
      setDisplayedMode(mode);
      setPhase("is-entering");
      requestAnimationFrame(() => setPhase(""));
    }, 125);
    return () => window.clearTimeout(timer);
  }, [displayedMode, mode]);

  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const measure = () => setCardHeight(content.scrollHeight + 2);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [displayedMode, errors, registered, oidcEnabled, passwordAuthEnabled]);

  const detectAutofill = (field: string, setValue: (value: string) => void) => (event: AnimationEvent<HTMLInputElement>) => {
    if (event.animationName !== "on-autofill-start") return;
    setAutofilled((current) => ({ ...current, [field]: true }));
    setValue(event.currentTarget.value);
  };
  const clearError = (field: string) => { if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined })); };
  const inputClass = (field: string) => autofilled[field] ? "is-autofilled" : "";

  // 后端统一错误 → 字段级/表单级错误
  const applyApiError = (err: unknown, fieldMap: Record<string, string>) => {
    if (err instanceof ApiError) {
      if (err.code === "VALIDATION_ERROR" && Array.isArray(err.details)) {
        const next: FieldErrors = {};
        for (const d of err.details as unknown[]) {
          if (typeof d !== "object" || d === null) continue;
          const detail = d as { field?: unknown; message?: unknown };
          if (typeof detail.field !== "string" || typeof detail.message !== "string") continue;
          next[fieldMap[detail.field] ?? detail.field] = detail.message;
        }
        setErrors(Object.keys(next).length > 0 ? next : { form: err.message });
      } else if (err.code === "INVALID_CREDENTIALS" || err.code === "AUTH_REQUIRED") {
        setErrors({ password: err.message });
      } else {
        setErrors({ form: err.message });
      }
    } else {
      setErrors({ form: t("auth.somethingWrong") });
    }
  };

  const submitLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next: FieldErrors = {};
    if (!loginUsername.trim()) next.username = t("auth.enterUsername");
    if (!password) next.password = t("auth.enterPassword");
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    setSubmitting(true);
    try {
      await api.auth.login({ username: loginUsername.trim(), password });
      await refresh();
      onSignedIn();
    } catch (err) {
      applyApiError(err, {});
    } finally {
      setSubmitting(false);
    }
  };

  const submitRegister = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next: FieldErrors = {};
    if (!registerUsername.trim()) next.username = t("auth.chooseUsername");
    else if (!/^[a-z0-9_]{3,30}$/i.test(registerUsername.trim())) next.username = t("auth.usernameRule");
    if (registerPassword.length < 8) next.password = t("auth.passwordMin");
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    setSubmitting(true);
    try {
      await api.auth.register({ username: registerUsername.trim(), password: registerPassword });
      setRegistered(true);
    } catch (err) {
      applyApiError(err, { username: "username" });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <a className="login-wordmark" href="/" aria-label={t("nav.home")}>Samryetha</a>
        <section className="login-card" style={{ height: cardHeight }}>
          <div className={`login-card-content ${phase}`} ref={contentRef}>
            {displayedMode === "login" && <>
              <header className="login-heading"><h1>{t("auth.welcomeBack")}</h1><p>{oidcEnabled ? t("auth.signInWithAccount") : t("auth.signInWithUsername")}</p></header>
              {oidcEnabled && <a className="login-primary login-oidc" href={oidcHref}>{t("auth.oidcButton")}</a>}
              <p className="login-register">
                <button
                  type="button"
                  className="sender"
                  style={{ background: "none", border: 0, cursor: "pointer", padding: 0, font: "inherit" }}
                  onClick={() => setShowQr(true)}
                >
                  {t("auth.qrScan")}
                </button>
              </p>
              {showQr && <QrLoginModal onSignedIn={onSignedIn} onClose={() => setShowQr(false)} />}
              {/* 入口统一到 Lako 之后，论坛自己的密码表单从这里消失——用户不该再看到
                  论坛在管密码。扫码留着：它是无密码的 PC 授权流程，不体现"论坛管密码"。
                  OIDC 没启用时必须原样保留，否则整页登录直接不可用（线上目前就是这种状态）。 */}
              {!oidcEnabled && !passwordAuthEnabled && <p className="login-sub">{t("auth.passwordRetired")}</p>}
              {!oidcEnabled && passwordAuthEnabled && <>
              <form className="login-form" onSubmit={submitLogin} noValidate>
                <label className="login-field"><span>{t("auth.username")}</span><span className={`login-input-frame ${errors.username ? "invalid" : ""}`}><input type="text" autoComplete="username" placeholder={t("auth.usernamePlaceholder")} value={loginUsername} aria-invalid={Boolean(errors.username)} onChange={(event) => { setLoginUsername(event.target.value); clearError("username"); }} autoFocus /></span>{errors.username && <small className="login-error">{errors.username}</small>}</label>
                <label className="login-field"><span>{t("auth.password")}</span><span className={`login-input-frame ${errors.password ? "invalid" : ""}`}><input className={inputClass("password")} type="password" autoComplete="current-password" value={password} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("password", setPassword)} onChange={(event) => { setPassword(event.target.value); clearError("password"); }} /></span>{errors.password && <small className="login-error">{errors.password}</small>}</label>
                {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.signingIn") : t("auth.signIn")}</button>
              </form>
              <p className="login-register"><a href="/forgot-password">{t("auth.forgotPassword")}</a></p>
              <p className="login-register">{t("auth.newHere")} <a href="/register">{t("auth.submitApplication")}</a></p>
              </>}
            </>}

            {/* 注册也归 Lako：论坛不再收新密码。签完若 Lako 那边没账号，Lako 登录页自己有注册入口。 */}
            {displayedMode === "register" && !registered && oidcEnabled && <>
              <header className="login-heading"><h1>{t("auth.createAccount")}</h1><p>{t("auth.signInWithAccount")}</p></header>
              <a className="login-primary login-oidc" href={oidcHref}>{t("auth.oidcButton")}</a>
              <p className="login-register"><a href="/login">{t("auth.haveAccount")}</a></p>
            </>}

            {displayedMode === "register" && !registered && !oidcEnabled && !passwordAuthEnabled && <>
              <header className="login-heading"><h1>{t("auth.registerClosed")}</h1><p>{t("auth.registerClosedDesc")}</p></header>
              <p className="login-register"><a href="/login">{t("auth.signIn")}</a></p>
            </>}

            {displayedMode === "register" && !registered && !oidcEnabled && passwordAuthEnabled && <>
              <header className="login-heading"><h1>{t("auth.createAccount")}</h1><p>{t("auth.betaNote")}</p></header>
              <form className="login-form" onSubmit={submitRegister} noValidate>
                <label className="login-field"><span>{t("auth.username")}</span><span className={`login-input-frame ${errors.username ? "invalid" : ""}`}><input type="text" autoComplete="username" placeholder={t("auth.usernamePlaceholder")} value={registerUsername} aria-invalid={Boolean(errors.username)} onChange={(event) => { setRegisterUsername(event.target.value); clearError("username"); }} autoFocus /></span>{errors.username && <small className="login-error">{errors.username}</small>}</label>
                <label className="login-field"><span>{t("auth.password")}</span><span className={`login-input-frame has-action ${errors.password ? "invalid" : ""}`}><input className={inputClass("registerPassword")} type={passwordVisible ? "text" : "password"} autoComplete="new-password" placeholder={t("auth.passwordMinPlaceholder")} value={registerPassword} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("registerPassword", setRegisterPassword)} onChange={(event) => { setRegisterPassword(event.target.value); clearError("password"); }} /><button className="password-visibility" type="button" aria-label={passwordVisible ? t("auth.hidePassword") : t("auth.showPassword")} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((value) => !value)}><EyeIcon visible={passwordVisible} /></button></span>{errors.password && <small className="login-error">{errors.password}</small>}</label>
                {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.submitting") : t("auth.submitApplication")}</button>
              </form>
              <p className="login-register">{t("auth.haveAccount")} <a href="/login">{t("auth.signIn")}</a></p>
            </>}

            {displayedMode === "register" && registered && <>
              <a className="auth-back" href="/register" onClick={(event) => { event.preventDefault(); setRegistered(false); }}>{t("common.back")}</a>
              <header className="login-heading"><h1>{t("auth.appSubmitted")}</h1></header>
              <p className="login-sub">{t("auth.appPending", { username: registerUsername })} {t("auth.appReview")}</p>
              <p className="reset-confirmation" role="status">{t("auth.approvedNote")}</p>
            </>}
          </div>
        </section>
        <p className="login-note">{oidcEnabled ? t("auth.betaFooterOidc") : t("auth.betaFooter")}</p>
      </div>
    </main>
  );
}

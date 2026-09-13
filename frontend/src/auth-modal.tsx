// 登录弹层：密码登录 / 注册 + OIDC(Lako) iframe。
// 关键点：全程在当前页弹窗内完成，不整页跳转 —— 登录成功后父窗口 SPA 状态原样保留，
// 自然"回到"发起登录的页面，不再被甩到首页。
//
// OIDC 走 iframe：src = /api/auth/login?returnTo=%2Flogin%2Fdone
//   主站 login → 302 Lako authorize →（未登录）302 Lako /login → 用户填表 → 回 authorize
//   → 302 主站 callback（写 samryetha_session，top-level 与 iframe 同 site，first-party）
//   → 302 /login/done。
// 父窗口在 iframe onLoad 里读 contentWindow.location.pathname === "/login/done"
// （同源可读；中间跨源 Lako 阶段 try/catch 跳过），判定完成 → refresh() → 关弹层。

import { createContext, useCallback, useContext, useEffect, useRef, useState, type AnimationEvent, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { EyeIcon } from "./icons";
import { useEscapeKey, useModalScrollLock } from "./lib/use-modal-scroll-lock";

type AuthModalMode = "login" | "register";

type AuthModalState = {
  /** 弹层当前是否打开 */
  open: boolean;
  openModal: (mode?: AuthModalMode) => void;
  closeModal: () => void;
};

const AuthModalContext = createContext<AuthModalState | null>(null);

const OIDC_DONE_PATH = "/login/done";
const OIDC_ENTRY = `/api/auth/login?returnTo=${encodeURIComponent(OIDC_DONE_PATH)}`;

export function AuthModalProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AuthModalMode>("login");
  const openModal = useCallback((nextMode: AuthModalMode = "login") => {
    setMode(nextMode);
    setOpen(true);
  }, []);
  const closeModal = useCallback(() => setOpen(false), []);

  return (
    <AuthModalContext.Provider value={{ open, openModal, closeModal }}>
      {children}
      {open && <AuthModal mode={mode} onSwitchMode={setMode} onClose={closeModal} />}
    </AuthModalContext.Provider>
  );
}

// ---------------------------------------------------------------- modal

function AuthModal({
  mode,
  onSwitchMode,
  onClose,
}: {
  mode: AuthModalMode;
  onSwitchMode: (mode: AuthModalMode) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  // 弹层打开时锁定背景滚动 + Esc 关闭
  useModalScrollLock(true);
  useEscapeKey(true, onClose);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [oidcActive, setOidcActive] = useState(false);

  useEffect(() => {
    void api.auth.config().then(({ oidcEnabled: enabled }) => setOidcEnabled(enabled)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!mode) return;
    // 切换模式时重置 OIDC 子视图
    setOidcActive(false);
  }, [mode]);

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div
        className="dialog-content login-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t("auth.welcomeBack")}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="login-modal-close" type="button" aria-label={t("common.close")} onClick={onClose}>×</button>
        {oidcEnabled && !oidcActive ? (
          <OidcEntry onStart={() => setOidcActive(true)} />
        ) : oidcEnabled && oidcActive ? (
          <OidcFrame onClose={onClose} />
        ) : null}

        {(!oidcEnabled || !oidcActive) && (
          <AuthForms mode={mode} onSwitchMode={onSwitchMode} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- password / register

function AuthForms({
  mode,
  onSwitchMode,
  onClose,
}: {
  mode: AuthModalMode;
  onSwitchMode: (mode: AuthModalMode) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [submitting, setSubmitting] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [autofilled, setAutofilled] = useState<Record<string, boolean>>({});

  const clearError = (field: string) => {
    if (errors[field]) setErrors((cur) => ({ ...cur, [field]: undefined }));
  };

  const applyError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.code === "INVALID_CREDENTIALS" || err.code === "AUTH_REQUIRED") {
        setErrors({ password: err.message });
      } else if (err.code === "VALIDATION_ERROR" && Array.isArray(err.details)) {
        const next: Record<string, string> = {};
        for (const d of err.details as unknown[]) {
          if (typeof d !== "object" || d === null) continue;
          const detail = d as { field?: unknown; message?: unknown };
          if (typeof detail.field !== "string" || typeof detail.message !== "string") continue;
          next[detail.field === "username" ? "username" : detail.field] = detail.message;
        }
        setErrors(Object.keys(next).length > 0 ? next : { form: err.message });
      } else {
        setErrors({ form: err.message });
      }
    } else {
      setErrors({ form: t("auth.somethingWrong") });
    }
  };

  const submitLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next: Record<string, string | undefined> = {};
    if (!username.trim()) next.username = t("auth.enterUsername");
    if (!password) next.password = t("auth.enterPassword");
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      onClose(); // 原地登录完成（父窗口 SPA 未动）
    } catch (err) {
      applyError(err);
    } finally {
      setSubmitting(false);
    }
  };

  const submitRegister = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next: Record<string, string | undefined> = {};
    if (!username.trim()) next.username = t("auth.chooseUsername");
    else if (!/^[a-z0-9_]{3,30}$/i.test(username.trim())) next.username = t("auth.usernameRule");
    if (password.length < 8) next.password = t("auth.passwordMin");
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    setSubmitting(true);
    try {
      await api.auth.register({ username: username.trim(), password });
      setRegistered(true);
    } catch (err) {
      applyError(err);
    } finally {
      setSubmitting(false);
    }
  };

  const detectAutofill = (field: string) => (event: AnimationEvent<HTMLInputElement>) => {
    if (event.animationName !== "on-autofill-start") return;
    setAutofilled((cur) => ({ ...cur, [field]: true }));
    setUsername(event.currentTarget.value);
  };
  const inputClass = (field: string) => (autofilled[field] ? "is-autofilled" : "");

  if (mode === "register" && registered) {
    return (
      <div className="login-modal-body">
        <header className="login-heading"><h1>{t("auth.appSubmitted")}</h1></header>
        <a className="auth-back" href="#register" onClick={(e) => { e.preventDefault(); setRegistered(false); }}>{t("common.back")}</a>
        <p className="login-sub">{t("auth.appPending", { username })} {t("auth.appReview")}</p>
        <p className="reset-confirmation" role="status">{t("auth.approvedNote")}</p>
      </div>
    );
  }

  return (
    <div className="login-modal-body">
      <header className="login-heading">
        <h1>{mode === "login" ? t("auth.welcomeBack") : t("auth.createAccount")}</h1>
        <p>{mode === "login" ? t("auth.signInWithUsername") : t("auth.betaNote")}</p>
      </header>

      {mode === "login" ? (
        <form className="login-form" onSubmit={submitLogin} noValidate>
          <label className="login-field"><span>{t("auth.username")}</span>
            <span className={`login-input-frame ${errors.username ? "invalid" : ""}`}>
              <input type="text" autoComplete="username" value={username} aria-invalid={Boolean(errors.username)} onChange={(e) => { setUsername(e.target.value); clearError("username"); }} autoFocus />
            </span>
            {errors.username && <small className="login-error">{errors.username}</small>}
          </label>
          <label className="login-field"><span>{t("auth.password")}</span>
            <span className={`login-input-frame has-action ${errors.password ? "invalid" : ""}`}>
              <input className={inputClass("password")} type={passwordVisible ? "text" : "password"} autoComplete="current-password" value={password} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("password")} onChange={(e) => { setPassword(e.target.value); clearError("password"); }} />
              <button className="password-visibility" type="button" aria-label={passwordVisible ? t("auth.hidePassword") : t("auth.showPassword")} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((v) => !v)}><EyeIcon visible={passwordVisible} /></button>
            </span>
            {errors.password && <small className="login-error">{errors.password}</small>}
          </label>
          {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
          <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.signingIn") : t("auth.signIn")}</button>
        </form>
      ) : (
        <form className="login-form" onSubmit={submitRegister} noValidate>
          <label className="login-field"><span>{t("auth.username")}</span>
            <span className={`login-input-frame ${errors.username ? "invalid" : ""}`}>
              <input type="text" autoComplete="username" placeholder={t("auth.usernamePlaceholder")} value={username} aria-invalid={Boolean(errors.username)} onChange={(e) => { setUsername(e.target.value); clearError("username"); }} autoFocus />
            </span>
            {errors.username && <small className="login-error">{errors.username}</small>}
          </label>
          <label className="login-field"><span>{t("auth.password")}</span>
            <span className={`login-input-frame has-action ${errors.password ? "invalid" : ""}`}>
              <input className={inputClass("registerPassword")} type={passwordVisible ? "text" : "password"} autoComplete="new-password" placeholder={t("auth.passwordMinPlaceholder")} value={password} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("registerPassword")} onChange={(e) => { setPassword(e.target.value); clearError("password"); }} />
              <button className="password-visibility" type="button" aria-label={passwordVisible ? t("auth.hidePassword") : t("auth.showPassword")} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((v) => !v)}><EyeIcon visible={passwordVisible} /></button>
            </span>
            {errors.password && <small className="login-error">{errors.password}</small>}
          </label>
          {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
          <button className="login-primary" type="submit" disabled={submitting}>{submitting ? t("auth.submitting") : t("auth.submitApplication")}</button>
        </form>
      )}

      <p className="login-register">
        {mode === "login" ? (
          <a href="#register" onClick={(e) => { e.preventDefault(); onSwitchMode("register"); }}>{t("auth.newHere")}</a>
        ) : (
          <a href="#login" onClick={(e) => { e.preventDefault(); onSwitchMode("login"); }}>{t("auth.haveAccount")}</a>
        )}
      </p>
      <p className="login-register"><a href="/forgot-password" onClick={(e) => { e.preventDefault(); onClose(); /* 目标整页跳转 */ }}>{t("auth.forgotPassword")}</a></p>
    </div>
  );
}

// ---------------------------------------------------------------- OIDC

function OidcEntry({ onStart }: { onStart: () => void }) {
  const { t } = useI18n();
  return (
    <div className="login-modal-body">
      <header className="login-heading"><h1>{t("auth.welcomeBack")}</h1><p>{t("auth.signInWithAccount")}</p></header>
      <button className="login-primary login-oidc" type="button" onClick={onStart}>{t("auth.oidcButton")}</button>
      <div className="login-divider"><span>{t("auth.backupLogin")}</span></div>
    </div>
  );
}

function OidcFrame({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { refresh } = useAuth();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [hint, setHint] = useState<"" | "done" | "timeout">("");
  const [frameKey, setFrameKey] = useState(0);
  const timerRef = useRef<number | null>(null);

  const finish = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    setHint("done");
    // iframe 已写入 samryetha_session，刷新会话后父窗口 SPA 原地完成登录
    void refresh().then(() => onClose());
  }, [refresh, onClose]);

  const onLoad = () => {
    const win = frameRef.current?.contentWindow;
    if (!win) return;
    try {
      // 同源 /login/done 可读；中间跨源 Lako 阶段抛 SecurityError，安全忽略
      if (win.location.pathname === OIDC_DONE_PATH) finish();
    } catch {
      // Lako 跨源页面尚未到达主站信号页
    }
  };

  useEffect(() => {
    timerRef.current = window.setTimeout(() => setHint("timeout"), 5 * 60_000);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const retry = () => { setHint(""); setFrameKey((k) => k + 1); };

  return (
    <div className="login-modal-body">
      <header className="login-heading"><h1>{t("auth.oidcFrameTitle")}</h1><p>{t("auth.oidcFrameHint")}</p></header>
      <div className="login-oidc-frame">
        <iframe key={frameKey} ref={frameRef} src={OIDC_ENTRY} title={t("auth.oidcFrameTitle")} onLoad={onLoad} />
      </div>
      {(hint === "done") && <p className="login-error form-error" role="status">{t("auth.signingIn")}</p>}
      {(hint === "timeout") && (
        <p className="login-error form-error" role="alert">
          {t("auth.oidcTimeout")}{" "}
          <button className="login-link-btn" type="button" onClick={retry}>{t("common.retry")}</button>
          <button className="login-link-btn" type="button" onClick={onClose}>{t("common.cancel")}</button>
        </p>
      )}
    </div>
  );
}

export function useAuthModal(): AuthModalState {
  const ctx = useContext(AuthModalContext);
  if (!ctx) throw new Error("useAuthModal must be used within <AuthModalProvider>");
  return ctx;
}
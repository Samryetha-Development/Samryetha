// 登录弹层：直接承载 Lako 账户选择器；切换账户时由 Lako 提升到顶层页面。

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
const OIDC_ENTRY = `/api/auth/login?returnTo=${encodeURIComponent(OIDC_DONE_PATH)}&embedded=true`;

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
  const [closing, setClosing] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const finishClose = useCallback(() => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    onClose();
  }, [onClose]);
  const requestClose = useCallback(() => {
    if (closing) return;
    setClosing(true);
    // Accessibility modes can disable CSS animation, so retain a fallback only.
    closeTimerRef.current = window.setTimeout(finishClose, 260);
  }, [closing, finishClose]);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
  }, []);

  // 弹层打开时锁定背景滚动 + Esc 关闭
  useModalScrollLock(true);
  useEscapeKey(true, requestClose);
  const [authConfig, setAuthConfig] = useState<{ oidcEnabled: boolean; passwordAuthEnabled: boolean } | null>(null);

  useEffect(() => {
    void api.auth.config().then(setAuthConfig).catch(() => setAuthConfig({ oidcEnabled: false, passwordAuthEnabled: false }));
  }, []);

  return (
    <div className="dialog-overlay" data-state={closing ? "closed" : "open"} onClick={requestClose}>
      <div
        className={`dialog-content login-modal ${authConfig?.oidcEnabled ? "login-modal-oidc" : ""}`}
        data-state={closing ? "closed" : "open"}
        role="dialog"
        aria-modal="true"
        aria-label={t("auth.welcomeBack")}
        onClick={(e) => e.stopPropagation()}
        onAnimationEnd={(event) => {
          if (closing && event.target === event.currentTarget && event.animationName === "dialog-pop-out") finishClose();
        }}
      >
        <button className="login-modal-close" type="button" aria-label={t("common.close")} onClick={requestClose}>×</button>
        {authConfig?.oidcEnabled ? (
          <OidcFrame onClose={requestClose} />
        ) : authConfig?.passwordAuthEnabled ? (
          <AuthForms mode={mode} onSwitchMode={onSwitchMode} onClose={requestClose} oidcEnabled={false} passwordAuthEnabled onStartOidc={() => undefined} />
        ) : null}
      </div>
    </div>
  );
}

function OidcFrame({ onClose }: { onClose: () => void }) {
  const { refresh } = useAuth();
  const { t } = useI18n();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const finishedRef = useRef(false);
  // Match the embedded chooser's first-paint height so its async size report
  // does not move the centered dialog immediately after opening.
  const [frameHeight, setFrameHeight] = useState(363);

  useEffect(() => {
    const receiveSize = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: unknown; height?: unknown } | null;
      if (data?.type !== "lako:embedded-size" || typeof data.height !== "number") return;
      setFrameHeight(Math.max(320, Math.min(700, Math.ceil(data.height))));
    };
    window.addEventListener("message", receiveSize);
    return () => window.removeEventListener("message", receiveSize);
  }, []);

  const onLoad = () => {
    if (finishedRef.current) return;
    const win = frameRef.current?.contentWindow;
    if (!win) return;
    try {
      if (win.location.pathname !== OIDC_DONE_PATH) return;
      finishedRef.current = true;
      void refresh().then(onClose);
    } catch {
      // Lako is cross-origin until the OAuth callback returns to Samryetha.
    }
  };

  return (
    <div className="login-oidc-frame">
      <iframe ref={frameRef} src={OIDC_ENTRY} title={t("auth.oidcFrameTitle")} style={{ height: frameHeight }} onLoad={onLoad} />
    </div>
  );
}

// ---------------------------------------------------------------- password / register

function AuthForms({
  mode,
  onSwitchMode,
  onClose,
  oidcEnabled,
  passwordAuthEnabled,
  onStartOidc,
}: {
  mode: AuthModalMode;
  onSwitchMode: (mode: AuthModalMode) => void;
  onClose: () => void;
  oidcEnabled: boolean;
  passwordAuthEnabled: boolean;
  onStartOidc: () => void;
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

  if (passwordAuthEnabled && mode === "register" && registered) {
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
        <h1>{!passwordAuthEnabled || mode === "login" ? t("auth.welcomeBack") : t("auth.createAccount")}</h1>
        <p>{oidcEnabled ? t("auth.signInWithAccount") : mode === "login" ? t("auth.signInWithUsername") : t("auth.betaNote")}</p>
      </header>

      {oidcEnabled && (
        <>
          <button className="login-primary login-oidc" type="button" onClick={onStartOidc}>{t("auth.oidcButton")}</button>
          {passwordAuthEnabled && <div className="login-divider"><span>{t("auth.backupLogin")}</span></div>}
        </>
      )}

      {passwordAuthEnabled && (mode === "login" ? (
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
      ))}

      {passwordAuthEnabled && <p className="login-register">
        {mode === "login" ? (
          <a href="#register" onClick={(e) => { e.preventDefault(); onSwitchMode("register"); }}>{t("auth.newHere")}</a>
        ) : (
          <a href="#login" onClick={(e) => { e.preventDefault(); onSwitchMode("login"); }}>{t("auth.haveAccount")}</a>
        )}
      </p>}
      {passwordAuthEnabled && <p className="login-register"><a href="/forgot-password" onClick={(e) => { e.preventDefault(); onClose(); /* 目标整页跳转 */ }}>{t("auth.forgotPassword")}</a></p>}
    </div>
  );
}

export function useAuthModal(): AuthModalState {
  const ctx = useContext(AuthModalContext);
  if (!ctx) throw new Error("useAuthModal must be used within <AuthModalProvider>");
  return ctx;
}

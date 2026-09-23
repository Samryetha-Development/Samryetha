// 登录弹层：直接承载 Lako 账户选择器；切换账户时由 Lako 提升到顶层页面。

import { createContext, useCallback, useContext, useEffect, useRef, useState, type AnimationEvent, type FormEvent, type ReactNode } from "react";
import { Dialog } from "samryetha-ui-commons";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { EyeIcon } from "./icons";
import { OidcDirect, OidcSkeleton } from "./oidc-direct";
import { useIsomorphicLayoutEffect } from "./lib/use-isomorphic-layout-effect";

type AuthModalMode = "login" | "register";
type AuthConfig = {
  oidcEnabled: boolean;
  passwordAuthEnabled: boolean;
  oidcMode: "redirect" | "json";
  lakoOrigin: string | null;
};

type AuthModalState = {
  /** 弹层当前是否打开 */
  open: boolean;
  openModal: (mode?: AuthModalMode) => void;
  closeModal: () => void;
};

function AuthSizeTransition({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ height: 0, ready: false, animate: false });

  useIsomorphicLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const measure = () => {
      // Dialog 入场时会 scale；getBoundingClientRect() 会返回缩放后的高度，
      // 进而把容器锁短并裁掉卡片底部。scrollHeight 只看布局尺寸，不受 transform 影响。
      const height = content.scrollHeight;
      setFrame((current) => {
        if (current.ready && Math.abs(current.height - height) < 1) return current;
        return { height, ready: true, animate: current.ready && enabled };
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [enabled]);

  return (
    <div
      className={`auth-size-transition${frame.ready ? " ready" : ""}${frame.animate ? " animate" : ""}`}
      style={frame.ready ? { height: frame.height } : undefined}
      onTransitionEnd={(event) => {
        if (event.propertyName === "height") {
          setFrame((current) => ({ ...current, animate: false }));
        }
      }}
    >
      <div className="auth-size-transition-content" ref={contentRef}>{children}</div>
    </div>
  );
}

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
  // 背景滚动锁与 Esc 关闭现在由 Radix Dialog 提供，不再手写。
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [configError, setConfigError] = useState(false);
  const [presented, setPresented] = useState(false);
  const revealFramesRef = useRef<number[]>([]);

  const loadConfig = useCallback(() => {
    setAuthConfig(null);
    setConfigError(false);
    void api.auth.config().then(setAuthConfig).catch(() => setConfigError(true));
  }, []);

  useEffect(() => loadConfig(), [loadConfig]);

  const reveal = useCallback(() => {
    if (presented || revealFramesRef.current.length > 0) return;
    const first = window.requestAnimationFrame(() => {
      const second = window.requestAnimationFrame(() => {
        revealFramesRef.current = [];
        setPresented(true);
      });
      revealFramesRef.current.push(second);
    });
    revealFramesRef.current.push(first);
  }, [presented]);

  useEffect(() => {
    // 快请求先在不可见状态完成初始阶段，避免高骨架立刻缩成账户卡片时
    // 垂直居中的 Dialog 整体向下移动；真正慢时仍会按时展示骨架。
    const timer = window.setTimeout(() => setPresented(true), 160);
    return () => {
      window.clearTimeout(timer);
      for (const frame of revealFramesRef.current) window.cancelAnimationFrame(frame);
      revealFramesRef.current = [];
    };
  }, []);

  // 开关缺一不可：模式是 json 且拿得到 Lako 的源，才走弹层内原生渲染；
  // 否则一律退回 iframe（包括配置写错、后端还没升级这类情况）。
  const embedded =
    authConfig?.oidcEnabled && authConfig.oidcMode === "json" && authConfig.lakoOrigin ? authConfig.lakoOrigin : null;

  useEffect(() => {
    if (configError || (authConfig !== null && !embedded)) reveal();
  }, [authConfig, configError, embedded, reveal]);

  // 退出动画、Esc、遮罩点击、滚动锁、焦点陷阱全部交给 Radix。
  // 此前这里用 closing 状态 + setTimeout(260) + onAnimationEnd 手工模仿 Presence，
  // 而 globals.css 的 .dialog-content[data-state="closed"] 本就是照 Radix 写的。
  const pendingConfig = authConfig === null && !configError;
  const childClassName = `login-modal${pendingConfig || authConfig?.oidcEnabled ? " login-modal-oidc" : ""}${presented ? "" : " auth-modal-concealed"}`;
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      contentClassName={childClassName}
      contentProps={{ "aria-label": t("auth.welcomeBack") }}
    >
      <button className="login-modal-close" type="button" aria-label={t("common.close")} onClick={onClose}>×</button>
      <AuthSizeTransition enabled={presented}>
        {configError ? (
          <div className="auth-config-error" role="alert">
            <p>{t("auth.oidcTimeout")}</p>
            <button className="action-btn" type="button" onClick={loadConfig}>{t("common.retry")}</button>
          </div>
        ) : authConfig === null ? (
          <div className="lako-auth lako-auth-main" data-lako-embedded="">
            <OidcSkeleton label={t("common.loading")} />
          </div>
        ) : authConfig?.oidcEnabled ? (
          embedded ? (
            <OidcDirect origin={embedded} onClose={onClose} onInitialReady={reveal} />
          ) : (
            <OidcFrame onClose={onClose} />
          )
        ) : authConfig?.passwordAuthEnabled ? (
          <AuthForms mode={mode} onSwitchMode={onSwitchMode} onClose={onClose} oidcEnabled={false} passwordAuthEnabled onStartOidc={() => undefined} />
        ) : (
          <p className="login-sub">{t("auth.passwordRetired")}</p>
        )}
      </AuthSizeTransition>
    </Dialog>
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

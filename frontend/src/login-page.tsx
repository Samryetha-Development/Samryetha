import { type AnimationEvent, type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { EyeIcon } from "./icons";

export type AuthMode = "login" | "register";
type FieldErrors = Record<string, string | undefined>;

export function LoginPage({ mode, onSignedIn }: { mode: AuthMode; onSignedIn: () => void }) {
  const { refresh } = useAuth();
  const [displayedMode, setDisplayedMode] = useState<AuthMode>(mode);
  const [phase, setPhase] = useState<"" | "is-leaving" | "is-entering">("");
  const [cardHeight, setCardHeight] = useState<number>();
  const [loginUsername, setLoginUsername] = useState("");
  const [password, setPassword] = useState("");
  const [registerUsername, setRegisterUsername] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [registered, setRegistered] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [autofilled, setAutofilled] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const transitionToken = useRef(0);

  useEffect(() => {
    if (mode === displayedMode) return;
    setErrors({});
    setRegistered(false);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
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
  }, [displayedMode, errors, registered]);

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
      setErrors({ form: "Something went wrong. Try again." });
    }
  };

  const submitLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next: FieldErrors = {};
    if (!loginUsername.trim()) next.username = "Enter your username.";
    if (!password) next.password = "Enter your password.";
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
    if (!registerUsername.trim()) next.username = "Choose a username.";
    else if (!/^[a-z0-9_]{3,30}$/i.test(registerUsername.trim())) next.username = "3-30 letters, numbers, or underscores.";
    if (registerPassword.length < 8) next.password = "Use at least 8 characters.";
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
        <a className="login-wordmark" href="/" aria-label="Samryetha home">Samryetha</a>
        <section className="login-card" style={{ height: cardHeight }}>
          <div className={`login-card-content ${phase}`} ref={contentRef}>
            {displayedMode === "login" && <>
              <header className="login-heading"><h1>Welcome back</h1><p>Sign in with your username.</p></header>
              <form className="login-form" onSubmit={submitLogin} noValidate>
                <label className="login-field"><span>Username</span><span className={`login-input-frame ${errors.username ? "invalid" : ""}`}><input type="text" autoComplete="username" placeholder="sora" value={loginUsername} aria-invalid={Boolean(errors.username)} onChange={(event) => { setLoginUsername(event.target.value); clearError("username"); }} autoFocus /></span>{errors.username && <small className="login-error">{errors.username}</small>}</label>
                <label className="login-field"><span>Password</span><span className={`login-input-frame ${errors.password ? "invalid" : ""}`}><input className={inputClass("password")} type="password" autoComplete="current-password" value={password} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("password", setPassword)} onChange={(event) => { setPassword(event.target.value); clearError("password"); }} /></span>{errors.password && <small className="login-error">{errors.password}</small>}</label>
                {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? "Signing in…" : "Sign in"}</button>
              </form>
              <p className="login-register"><a href="/forgot-password">Forgot password?</a></p>
              <p className="login-register">New to Samryetha? <a href="/register">Submit an application</a></p>
            </>}

            {displayedMode === "register" && !registered && <>
              <header className="login-heading"><h1>Create your account</h1><p>Internal beta — no email needed. Your application will be reviewed by an admin.</p></header>
              <form className="login-form" onSubmit={submitRegister} noValidate>
                <label className="login-field"><span>Username</span><span className={`login-input-frame ${errors.username ? "invalid" : ""}`}><input type="text" autoComplete="username" placeholder="sora" value={registerUsername} aria-invalid={Boolean(errors.username)} onChange={(event) => { setRegisterUsername(event.target.value); clearError("username"); }} autoFocus /></span>{errors.username && <small className="login-error">{errors.username}</small>}</label>
                <label className="login-field"><span>Password</span><span className={`login-input-frame has-action ${errors.password ? "invalid" : ""}`}><input className={inputClass("registerPassword")} type={passwordVisible ? "text" : "password"} autoComplete="new-password" placeholder="At least 8 characters" value={registerPassword} aria-invalid={Boolean(errors.password)} onAnimationStart={detectAutofill("registerPassword", setRegisterPassword)} onChange={(event) => { setRegisterPassword(event.target.value); clearError("password"); }} /><button className="password-visibility" type="button" aria-label={passwordVisible ? "Hide password" : "Show password"} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((value) => !value)}><EyeIcon visible={passwordVisible} /></button></span>{errors.password && <small className="login-error">{errors.password}</small>}</label>
                {errors.form && <small className="login-error form-error" role="alert">{errors.form}</small>}
                <button className="login-primary" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Submit application"}</button>
              </form>
              <p className="login-register">Already have an account? <a href="/login">Sign in</a></p>
            </>}

            {displayedMode === "register" && registered && <>
              <a className="auth-back" href="/register" onClick={(event) => { event.preventDefault(); setRegistered(false); }}>← Back</a>
              <header className="login-heading"><h1>Application submitted</h1></header>
              <p className="login-sub">Your account <strong>@{registerUsername}</strong> is now <strong>pending approval</strong>. An admin will review it before you can sign in.</p>
              <p className="reset-confirmation" role="status">You’ll be able to log in once your application is approved.</p>
            </>}
          </div>
        </section>
        <p className="login-note">Internal beta · username + password only.</p>
      </div>
    </main>
  );
}

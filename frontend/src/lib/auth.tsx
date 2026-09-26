import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, SESSION_EXPIRED_EVENT, type UserDTO } from "./api";

type AuthState = {
  user: UserDTO | null;
  loading: boolean;
  /** 会话过期（后端已登出 / 会话失效），RootApp 收到后弹 Toast 提示 */
  authExpired: boolean;
  dismissExpired: () => void;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<UserDTO>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [authExpired, setAuthExpired] = useState(false);
  const userRef = useRef<UserDTO | null>(null);
  userRef.current = user;
  // 每次 refresh 的代号：过期请求（旧代号）不回写状态；login/logout 递增以作废在途 refresh。
  const refreshGeneration = useRef(0);

  const markExpired = useCallback(() => {
    if (!userRef.current) return;
    userRef.current = null;
    setUser(null);
    setAuthExpired(true);
  }, []);

  const dismissExpired = useCallback(() => setAuthExpired(false), []);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    try {
      const { user } = await api.auth.me();
      if (generation !== refreshGeneration.current) return;
      userRef.current = user;
      setUser(user);
      setAuthExpired(false);
    } catch (error) {
      if (generation !== refreshGeneration.current) return;
      // 只有 401 才算登出；网络抖动 / 5xx 保留旧状态
      if (error instanceof ApiError && error.status === 401) markExpired();
      else if (userRef.current === null) setUser(null);
    } finally {
      setLoading(false);
    }
  }, [markExpired]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 任意 API 返回 401（apiFetch 广播）→ 视为会话过期；未登录时忽略（登录页输错密码也是 401）
  useEffect(() => {
    const onExpired = () => markExpired();
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [markExpired]);

  // 失活页签切回/可见 + 每 5 分钟静默复核：无条件 refresh（401 守卫已在 refresh/auth 内部，不会误弹）
  useEffect(() => {
    const recheck = () => {
      void refresh();
    };
    const onFocus = () => recheck();
    const onVisible = () => {
      if (document.visibilityState === "visible") recheck();
    };
    const timer = window.setInterval(recheck, 5 * 60_000);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const { user } = await api.auth.login({ username, password });
    // 登录成功：作废在途 refresh（其 401 不能把新会话登出）
    refreshGeneration.current += 1;
    userRef.current = user;
    setUser(user);
    setAuthExpired(false);
    return user;
  }, []);

  const logout = useCallback(async () => {
    // 登出：作废在途 refresh（其响应不能把用户重新写回）
    refreshGeneration.current += 1;
    try {
      await api.auth.logout();
    } catch {
      // 会话可能已失效，忽略
    } finally {
      userRef.current = null;
      setUser(null);
      setAuthExpired(false);
    }
  }, []);

  return <AuthContext.Provider value={{ user, loading, authExpired, dismissExpired, refresh, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

/** OIDC(Lako) 是否启用：读 GET /api/auth/config。 */
export function useOidcEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    void api.auth.config().then(({ oidcEnabled }) => setEnabled(oidcEnabled)).catch(() => undefined);
  }, []);
  return enabled;
}

/**
 * 静默结束 IdP(SSO) 会话：隐藏 iframe 走 /api/auth/oidc/logout（后端 302 到 Lako
 * end-session 再回跳），父窗口不导航 —— 登出和登录一样留在当前页，不再被甩回首页。
 * 仅在启用 OIDC 时调用（未启用时 /api/auth/oidc/logout 会 302 到首页，iframe 白跑一趟）。
 */
export function endOidcSessionSilently(): void {
  if (typeof document === "undefined") return;
  const frame = document.createElement("iframe");
  frame.style.display = "none";
  frame.setAttribute("aria-hidden", "true");
  frame.src = "/api/auth/oidc/logout";
  document.body.appendChild(frame);
  window.setTimeout(() => frame.remove(), 10_000);
}

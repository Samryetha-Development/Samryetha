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

  const markExpired = useCallback(() => {
    if (!userRef.current) return;
    userRef.current = null;
    setUser(null);
    setAuthExpired(true);
  }, []);

  const dismissExpired = useCallback(() => setAuthExpired(false), []);

  const refresh = useCallback(async () => {
    try {
      const { user } = await api.auth.me();
      userRef.current = user;
      setUser(user);
      setAuthExpired(false);
    } catch (error) {
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

  // 失活页签切回 + 每 5 分钟静默复核（已登录才查）：无操作时过期也能被发现
  useEffect(() => {
    const recheck = () => {
      if (userRef.current) void refresh();
    };
    const onFocus = () => recheck();
    const timer = window.setInterval(recheck, 5 * 60_000);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const { user } = await api.auth.login({ username, password });
    userRef.current = user;
    setUser(user);
    setAuthExpired(false);
    return user;
  }, []);

  const logout = useCallback(async () => {
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

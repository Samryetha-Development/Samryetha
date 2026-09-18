import { createContext, useContext, useMemo, type ReactNode } from "react";

/**
 * 组件要跟 Lako 的后端说话，但「后端在哪」取决于宿主：
 *
 *   · lako/web 自己就是 OIDC_ISSUER 的宿主 → origin 留空，走相对路径（同源）
 *   · 论坛跑在另一个源上 → origin 给绝对地址，跨源带 credentials
 *
 * 两种情况都用 `credentials: "include"`。同源时它无害，跨源时它是唯一能
 * 让 `lako_session` 发出去的办法——那个 cookie 是 host-only 且 SameSite=Lax，
 * 靠的是宿主和 Lako **同站**（生产上 auth.samryetha.com 是 samryetha.com 的子域）。
 */
export type LakoConfig = {
  origin: string;
  /**
   * 发请求。参数是**路径**（形如 `/api/auth/login`），不是拼好的绝对地址——
   * origin 由这里统一加。传绝对地址会被拼成
   * `http://hosthttp://host/api/...` 这种非法 URL，fetch 直接抛。
   */
  fetcher: (path: string, init?: RequestInit) => Promise<Response>;
};

const LakoContext = createContext<LakoConfig>({
  origin: "",
  fetcher: (path, init) => fetch(path, { ...init, credentials: "include" }),
});

export function LakoProvider({ origin = "", children }: { origin?: string; children: ReactNode }) {
  const value = useMemo<LakoConfig>(() => {
    const normalized = origin.replace(/\/+$/, "");
    return {
      origin: normalized,
      fetcher: (path, init) => fetch(`${normalized}${path}`, { ...init, credentials: "include" }),
    };
  }, [origin]);

  return <LakoContext.Provider value={value}>{children}</LakoContext.Provider>;
}

export function useLako(): LakoConfig {
  return useContext(LakoContext);
}

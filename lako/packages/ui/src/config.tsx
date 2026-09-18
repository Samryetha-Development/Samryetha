import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

/**
 * 被宿主用 iframe 嵌入时，把面板高度报给宿主（`lako:embedded-size`）。
 *
 * 宿主侧（论坛的登录弹层）iframe 初始高度是个写死的估值，靠这条消息才会调到真实高度；
 * 没有它，面板会被截断、iframe 里出现滚动条。这个上报原先长在 lako/web 的账户选择器里，
 * 抽成 @lako/ui 时丢了，于是嵌入模式一直是坏的。
 *
 * 只在宿主把根元素标了 `data-lako-embedded` 时才工作。
 */
function useEmbeddedSizeReport() {
  useEffect(() => {
    if (typeof window === "undefined" || window.parent === window) return;
    const root = document.querySelector(".lako-auth[data-lako-embedded]");
    const panel = root?.querySelector(".lako-auth-panel");
    if (!(panel instanceof HTMLElement)) return;
    const report = () =>
      window.parent.postMessage({ type: "lako:embedded-size", height: Math.ceil(panel.getBoundingClientRect().height) }, "*");
    report();
    const observer = new ResizeObserver(report);
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);
}

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

  useEmbeddedSizeReport();

  return <LakoContext.Provider value={value}>{children}</LakoContext.Provider>;
}

export function useLako(): LakoConfig {
  return useContext(LakoContext);
}

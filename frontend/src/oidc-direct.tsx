/**
 * 嵌入流：在论坛弹层里**原生渲染** Lako 的授权 UI（@lako/ui），不再套 iframe。
 *
 * 与 iframe 版本的区别：
 *   · 没有跨源 iframe、没有 postMessage 高度协商、没有 CSP frame-ancestors
 *   · 组件直接带着凭据跨源调 Lako 的 JSON authorize，全程不离开本页
 *   · 因此父窗口的 SPA 状态（滚动、打开的帖子、草稿）原样保留
 *
 * 流程（与重定向流一一对应）：
 *   start 建事务 → authorize(prompt) → 没会话？登录 → authorize(prompt)
 *   → 有会话？选号 → authorize(无 prompt) → 拿到 code → complete 换本机会话
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { LakoLogin, LakoProvider, LakoSelectAccount, type LakoAccount } from "@lako/ui";
import "@lako/ui/auth.css";
import { api } from "./lib/api";
import { useAuth } from "./lib/auth";

/**
 * Lako 的 UI 目前没有本地化——现在线上 iframe 里显示的也是这句英文原文。
 * 这里保持与之一致，而不是单给副标题加 8 份翻译。真正的做法是让 @lako/ui
 * 接受一个 strings/locale prop，那是独立的一件事。
 */
const CONTINUE_TO_SAMRYETHA = "Continue to Samryetha.";

type Stage =
  | { kind: "starting" }
  | { kind: "login" }
  | { kind: "select"; account: LakoAccount }
  | { kind: "error"; message: string };

type AuthorizeResult =
  | { status: "login_required" }
  | { status: "select_account"; account: LakoAccount }
  | { status: "code"; redirect: string };

export function OidcSkeleton({ label = "Loading sign-in" }: { label?: string }) {
  return (
    <section className="lako-auth-login" role="status" aria-label={label} aria-busy="true">
      <div className="lako-auth-panel lako-auth-skeleton" aria-hidden="true">
        <div className="lako-auth-product">
          <span className="lako-identity-glyph"><i /><i /><i /></span>
          <span className="auth-skeleton-line auth-skeleton-brand" />
        </div>
        <div className="auth-skeleton-title auth-skeleton-line" />
        <div className="auth-skeleton-subtitle auth-skeleton-line" />
        <div className="auth-skeleton-form">
          <div className="auth-skeleton-field"><span className="auth-skeleton-line" /><i /></div>
          <div className="auth-skeleton-field"><span className="auth-skeleton-line" /><i /></div>
          <div className="auth-skeleton-button" />
        </div>
      </div>
    </section>
  );
}

export function OidcDirect({
  origin,
  onClose,
  onInitialReady,
}: {
  origin: string;
  onClose: () => void;
  onInitialReady?: () => void;
}) {
  const { refresh } = useAuth();
  const [stage, setStage] = useState<Stage>({ kind: "starting" });
  const paramsRef = useRef<Record<string, string> | null>(null);
  // 组件卸载后不要再 setState / 不要再导航（用户可能在请求飞行中关了弹层）。
  // 注意 setup 里必须**重新置 true**：StrictMode 会 mount→cleanup→mount，
  // 只在 cleanup 里置 false 的话它一辈子都是 false，流程直接卡死在起始态。
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);
  // 起始流程只跑一次。这个 effect 依赖为空，StrictMode 的第二次 mount 靠它去重；
  // 跑两次会铸出两个事务、后一个把 cookie 覆盖掉，complete 就会 state mismatch。
  const startedRef = useRef(false);

  /**
   * 调 Lako 的 JSON authorize。参数与重定向流完全同构——只有承载方式不同。
   * `withPrompt` 为 false 时去掉 `prompt=select_account`，表示「账号已选定，直接铸码」。
   */
  const authorize = useCallback(
    async (params: Record<string, string>, withPrompt: boolean): Promise<AuthorizeResult> => {
      const search = new URLSearchParams(params);
      if (!withPrompt) search.delete("prompt");
      const response = await fetch(`${origin}/api/oauth/authorize?${search.toString()}`, {
        method: "POST",
        credentials: "include",
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error_description || body.error || "authorization failed");
      }
      return body as AuthorizeResult;
    },
    [origin],
  );

  const complete = useCallback(
    async (redirect: string) => {
      const url = new URL(redirect);
      const result = await api.auth.oidcComplete({
        code: url.searchParams.get("code") ?? "",
        state: url.searchParams.get("state") ?? "",
      });
      if (result.status === "claim_required") {
        // 先导航、再让调用方关弹层（这里直接跳走，弹层随页面卸载）。
        // ticket 是一次性的、只有 5 次尝试，丢了用户就卡死了，所以不能先关。
        // 用相对路径而不是后端给的 claimUrl：永远落在当前源上，不会被配置带偏。
        window.location.href = `/claim?ticket=${encodeURIComponent(result.ticket)}`;
        return;
      }
      await refresh();
      onClose();
    },
    [onClose, refresh],
  );

  /** 推进一次授权：能铸码就收尾，否则切换到对应阶段。 */
  const advance = useCallback(
    async (withPrompt: boolean) => {
      const params = paramsRef.current;
      if (!params) return;
      try {
        const result = await authorize(params, withPrompt);
        if (!aliveRef.current) return;
        if (result.status === "login_required") setStage({ kind: "login" });
        else if (result.status === "select_account") setStage({ kind: "select", account: result.account });
        else await complete(result.redirect);
      } catch (error) {
        if (aliveRef.current) {
          setStage({ kind: "error", message: error instanceof Error ? error.message : String(error) });
        }
      }
    },
    [authorize, complete],
  );

  // 用 ref 取最新的 advance，这样起始 effect 的依赖可以是空的——
  // 直接依赖 advance 的话，onClose 一变（闭包捕获了 closing state）effect 就重跑，
  // 每次重跑都新铸一个事务并把 cookie 覆盖掉。
  const advanceRef = useRef(advance);
  useEffect(() => { advanceRef.current = advance; }, [advance]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void (async () => {
      try {
        const { params } = await api.auth.oidcStart({ returnTo: "/" });
        if (!aliveRef.current) return;
        paramsRef.current = params;
        await advanceRef.current(true);
      } catch (error) {
        if (aliveRef.current) {
          setStage({ kind: "error", message: error instanceof Error ? error.message : String(error) });
        }
      }
    })();
  }, []);

  useEffect(() => {
    if (stage.kind !== "starting") onInitialReady?.();
  }, [onInitialReady, stage.kind]);

  return (
    <LakoProvider origin={origin}>
      <div className="lako-auth lako-auth-main" data-lako-embedded="">
        {stage.kind === "starting" && <OidcSkeleton />}

        {stage.kind === "login" && (
          <LakoLogin
            onSuccess={() => void advance(true)}
            subtitle={CONTINUE_TO_SAMRYETHA}
          />
        )}

        {stage.kind === "select" && (
          <LakoSelectAccount
            account={stage.account}
            subtitle={CONTINUE_TO_SAMRYETHA}
            onSuccess={() => void advance(false)}
            // 换账号 = 重新走登录。不需要先登出 Lako：用新凭据登录会直接覆盖那个会话。
            // （真去调 Lako 的登出做不到——跨源读不到 lako_csrf。）
            onUseAnotherAccount={() => setStage({ kind: "login" })}
          />
        )}

        {stage.kind === "error" && (
          <div className="lako-auth-login">
            <div className="lako-auth-panel">
              <p className="login-error form-error" role="alert">{stage.message}</p>
            </div>
          </div>
        )}
      </div>
    </LakoProvider>
  );
}

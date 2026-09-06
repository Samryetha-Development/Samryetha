import { useEffect, useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { DiscussionApp, type View } from "./discussion-app";
import { PostPage } from "./post-page";
import { ProfilePage } from "./profile-page";
import { SettingsPage } from "./settings-page";
import { LoginPage, type AuthMode } from "./login-page";
import { ThreadPage } from "./thread-page";
import { AdminPage } from "./admin-page";
import { FeedbackPage } from "./feedback-page";
import { ForgotPasswordPage } from "./forgot-password-page";
import { ResetPasswordPage } from "./reset-password-page";
import { AuthProvider } from "./lib/auth";
import { InboxPage } from "./inbox-page";

type TransitionDocument = Document & {
  startViewTransition?: (update: () => void) => { finished: Promise<void> };
};

type TransitionStyle = "thread-enter" | "thread-return";
type NotificationTone = "success" | "error" | "info";
type NotificationItem = { id: number; message: string; tone: NotificationTone };

function notificationTone(message: string): NotificationTone {
  return /failed|could not|cannot|error|already|permission|managed/i.test(message) ? "error" : /saved|created|deleted|published|updated|restored|changed/i.test(message) ? "success" : "info";
}

function NotificationIcon({ tone }: { tone: NotificationTone }) {
  if (tone === "success") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7" /></svg>;
  if (tone === "error") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5m0 3.5v.1M4.7 19h14.6a1.7 1.7 0 0 0 1.5-2.5L13.5 4a1.7 1.7 0 0 0-3 0l-7.3 12.5A1.7 1.7 0 0 0 4.7 19Z" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 10v6m0-10v.1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" /></svg>;
}

function Notifications({ items }: { items: NotificationItem[] }) {
  if (items.length === 0) return null;
  return <div className="notifications" role="region" aria-label="Notifications" aria-live="polite">
    {items.map((item) => <div className={`notification notification-${item.tone}`} role="status" key={item.id}>
      <span className="notification-icon"><NotificationIcon tone={item.tone} /></span>
      <span>{item.message}</span>
    </div>)}
  </div>;
}

function runTransition(update: () => void, style?: TransitionStyle): Promise<void> | undefined {
  const transitionDocument = document as TransitionDocument;
  if (!transitionDocument.startViewTransition) {
    update();
    return undefined;
  }

  if (style) document.documentElement.dataset.transition = style;
  const transition = transitionDocument.startViewTransition(update);
  if (style) {
    void transition.finished
      .catch(() => undefined)
      .finally(() => {
        delete document.documentElement.dataset.transition;
      });
  }
  return transition.finished;
}

const DETAIL_PATTERN = /^\/d\/(\d+)$/;

function RootAppInner({ pathname }: { pathname: string }) {
  const [activePath, setActivePath] = useState(pathname);
  const [discussionView, setDiscussionView] = useState<View>("latest");
  const discussionViewRef = useRef(discussionView);
  discussionViewRef.current = discussionView;
  const [transitionTitle, setTransitionTitle] = useState<{ id: number; title: string } | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const notificationId = useRef(0);
  const notificationTimers = useRef<number[]>([]);

  useEffect(() => {
    const changePage = (
      nextPath: string,
      nextUrl?: string,
      nextView?: View,
      style?: TransitionStyle,
      sharedTitle?: { id: number; title: string } | null,
    ) => {
      const update = () => {
        // 先更新 URL 再切状态：否则新页面组件在 flushSync 同步渲染时读到的仍是旧的 window.location.search
        // Push the URL first, then switch state: otherwise the newly-mounted page reads the stale location.search during the synchronous flushSync render
        // history.state 带上视图，popstate 时恢复（?board= 在 URL 里，由 DiscussionApp 自己读）
        if (nextUrl) window.history.pushState({ view: nextView ?? (nextPath === "/" ? discussionViewRef.current : null) }, "", nextUrl);
        flushSync(() => {
          if (nextView) setDiscussionView(nextView);
          setTransitionTitle(sharedTitle ?? null);
          setActivePath(nextPath);
        });
        window.scrollTo({ top: 0 });
      };

      const authPaths = ["/login", "/register", "/forgot-password", "/reset-password"];
      if (authPaths.includes(activePath) && authPaths.includes(nextPath)) {
        update();
        return undefined;
      }
      return runTransition(update, style);
    };

    const navigate = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!(event.target instanceof Element)) return;
      const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target || anchor.hasAttribute("download")) return;

      const destination = new URL(anchor.href, window.location.href);
      const requestedView = anchor.dataset.view;
      const nextView: View | undefined = requestedView === "latest" || requestedView === "followed" || requestedView === "boards" ? requestedView : undefined;
      if (destination.origin !== window.location.origin) return;
      // 同路径无视图意图（如点 wordmark 回首页）直接跳过；带 data-view 放行
      // （移动端汉堡菜单在首页切 Latest/Followed/Boards 就是这个场景）。
      if (destination.pathname === activePath && !nextView) return;
      const isDetail = DETAIL_PATTERN.test(destination.pathname);
      const isApp = destination.pathname === "/" || destination.pathname === "/post" || destination.pathname === "/profile" || destination.pathname === "/settings" || destination.pathname === "/admin" || destination.pathname === "/feedback" || destination.pathname === "/inbox";
      if (!isDetail && !isApp && !["/login", "/register", "/forgot-password", "/reset-password"].includes(destination.pathname)) return;
      event.preventDefault();
      // 保留 search（如 /?board=study），供 DiscussionApp 挂载时读板块初始化筛选。
      const currentIsDetail = DETAIL_PATTERN.test(activePath);
      const style = isDetail && !currentIsDetail
        ? "thread-enter"
        : currentIsDetail && destination.pathname === "/"
          ? "thread-return"
          : undefined;
      const detailId = isDetail ? Number(destination.pathname.match(DETAIL_PATTERN)?.[1]) : null;
      const sourceTitle = isDetail ? anchor.querySelector<HTMLElement>(".thread-title") : null;
      const sharedTitle = detailId && sourceTitle?.textContent
        ? { id: detailId, title: sourceTitle.textContent }
        : null;
      if (sharedTitle) sourceTitle!.style.viewTransitionName = "thread-title";
      const finished = changePage(destination.pathname, destination.pathname + destination.search, nextView, style, sharedTitle);
      if (sharedTitle && sourceTitle) {
        const clearName = () => {
          sourceTitle.style.viewTransitionName = "";
        };
        if (finished) void finished.then(clearName, clearName);
        else clearName();
      }
    };

    const restoreHistory = (event: PopStateEvent) => {
      const state = event.state as { view?: unknown } | null;
      const view = state?.view;
      changePage(
        window.location.pathname,
        undefined,
        view === "latest" || view === "followed" || view === "boards" ? view : undefined,
      );
    };

    document.addEventListener("click", navigate);
    window.addEventListener("popstate", restoreHistory);
    return () => {
      document.removeEventListener("click", navigate);
      window.removeEventListener("popstate", restoreHistory);
    };
  }, [activePath]);

  useEffect(() => () => {
    notificationTimers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const goToThread = (id: number) => {
    const path = `/d/${id}`;
    runTransition(() => {
      flushSync(() => setActivePath(path));
      window.history.pushState({}, "", path);
      window.scrollTo({ top: 0 });
    });
  };

  const signIn = () => {
    runTransition(() => {
      flushSync(() => setActivePath("/"));
      window.history.pushState({ view: discussionViewRef.current }, "", "/");
      window.scrollTo({ top: 0 });
    });
  };

  const showToast = (message: string) => {
    const id = ++notificationId.current;
    setNotifications((current) => [...current, { id, message, tone: notificationTone(message) }].slice(-4));
    const timer = window.setTimeout(() => {
      notificationTimers.current = notificationTimers.current.filter((item) => item !== timer);
      setNotifications((current) => current.filter((item) => item.id !== id));
    }, 3000);
    notificationTimers.current.push(timer);
  };

  const authModes: Partial<Record<string, AuthMode>> = { "/login": "login", "/register": "register" };
  const authMode = authModes[activePath];
  const detailMatch = activePath.match(DETAIL_PATTERN);
  let page: ReactNode;
  if (authMode) page = <LoginPage mode={authMode} onSignedIn={signIn} />;
  else if (activePath === "/forgot-password") page = <ForgotPasswordPage />;
  else if (activePath === "/reset-password") page = <ResetPasswordPage />;
  else if (detailMatch) {
    const id = Number(detailMatch[1]);
    // key={id}：跨帖切换强制重建，避免 replyText/replyingTo 等草稿状态残留下一个帖子
    page = <ThreadPage key={id} id={id} initialTitle={transitionTitle?.id === id ? transitionTitle.title : undefined} />;
  } else if (activePath === "/post") page = <PostPage onPublished={(id) => { goToThread(id); showToast("Published"); }} />;
  else if (activePath === "/profile") page = <ProfilePage />;
  else if (activePath === "/settings") page = <SettingsPage />;
  else if (activePath === "/admin") page = <AdminPage onNotify={showToast} />;
  else if (activePath === "/feedback") page = <FeedbackPage />;
  else if (activePath === "/inbox") page = <InboxPage />;
  else page = <DiscussionApp initialView={discussionView} onViewChange={setDiscussionView} />;
  return (
    <>
      {page}
      <Notifications items={notifications} />
    </>
  );
}

export function RootApp({ pathname }: { pathname: string }) {
  return (
    <AuthProvider>
      <RootAppInner pathname={pathname} />
    </AuthProvider>
  );
}

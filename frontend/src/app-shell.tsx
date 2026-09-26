import type { ReactNode } from "react";
import { UserMenu } from "./user-menu";
import { MobileMenu } from "./mobile-menu";
import { InboxIcon } from "./inbox-icon";
import { useI18n } from "./lib/i18n";
import { APP_VERSION, COPYRIGHT_NOTICE } from "./lib/version";
import { TopNav } from "./top-nav";

export type ShellLocation = "post" | "profile" | "settings" | "feedback" | "inbox";
export type ShellView = "latest" | "followed" | "boards";

// 统一外壳：topbar（wordmark + 导航 + 搜索 + 汉堡菜单 + UserMenu + Post）。
// discussion-app 通过 nav / search 插槽传入自己交互版的导航和搜索框。
// 注意：默认导航里的 data-view 属性被 root-app 的 SPA 路由读取，不要删。
// activeView 只在首页有意义（当前视图），供移动端汉堡菜单高亮导航项。
export function AppShell({
  children,
  current,
  wordmarkHref = "/",
  nav,
  search,
  activeView,
}: {
  children: ReactNode;
  current?: ShellLocation;
  wordmarkHref?: string;
  nav?: ReactNode;
  search?: ReactNode;
  activeView?: ShellView;
}) {
  const { t } = useI18n();
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href={wordmarkHref} className="wordmark" aria-label={t("nav.home")}>Samryetha</a>
          {nav ?? <TopNav current={current === "feedback" ? current : undefined} />}
          <div className="actions">
            {/* 非首页无 search prop 时不渲染死搜索框：保留同 class 占位，避免顶栏布局跳动 */}
            {search ?? <span className="search-field search-field-placeholder" aria-hidden="true" />}
            <MobileMenu activeView={activeView} />
            <UserMenu current={current === "profile" || current === "settings" ? current : undefined} />
            <InboxIcon />
            <a className="compose" href="/post" aria-current={current === "post" ? "page" : undefined}>{t("nav.post")}</a>
          </div>
        </div>
      </header>
      <div className="app-shell-content">{children}</div>
      <footer className="app-footer">
        <div className="shell app-footer-inner">
          <span>{COPYRIGHT_NOTICE}</span>
          <span className="app-footer-dot">·</span>
          <span>v{APP_VERSION}</span>
        </div>
      </footer>
    </div>
  );
}

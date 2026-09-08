import type { ReactNode } from "react";
import { UserMenu } from "./user-menu";
import { MobileMenu } from "./mobile-menu";
import { SearchIcon } from "./icons";
import { InboxIcon } from "./inbox-icon";
import { LanguageSwitcher } from "./language-switcher";
import { useI18n } from "./lib/i18n";

export type ShellLocation = "post" | "profile" | "settings" | "feedback" | "tasks" | "inbox";
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
    <>
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href={wordmarkHref} className="wordmark" aria-label={t("nav.home")}>Samryetha</a>
          {nav ?? (
            <nav className="primary-nav" aria-label={t("nav.primary")}>
              <a className="nav-link" href="/" data-view="latest">{t("nav.latest")}</a>
              <a className="nav-link" href="/" data-view="followed">{t("nav.followed")}</a>
              <a className="nav-link" href="/" data-view="boards">{t("nav.boards")}</a>
              <a className="nav-link" href="/feedback">{t("nav.feedback")}</a>
              <a className="nav-link" href="/tasks">{t("nav.tasks")}</a>
            </nav>
          )}
          <div className="actions">
            {search ?? (
              <label className="search-field">
                <SearchIcon />
                <span className="sr-only">{t("nav.searchDiscussions")}</span>
                <input type="search" placeholder={t("nav.searchDiscussions")} autoComplete="off" />
              </label>
            )}
            <MobileMenu activeView={activeView} />
            <LanguageSwitcher />
            <UserMenu current={current === "profile" || current === "settings" ? current : undefined} />
            <InboxIcon />
            <a className="compose" href="/post" aria-current={current === "post" ? "page" : undefined}>{t("nav.post")}</a>
          </div>
        </div>
      </header>
      {children}
    </>
  );
}

import type { ReactNode } from "react";
import { UserMenu } from "./user-menu";
import { MobileMenu } from "./mobile-menu";
import { SearchIcon } from "./icons";
import { InboxIcon } from "./inbox-icon";

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
  return (
    <>
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href={wordmarkHref} className="wordmark" aria-label="Samryetha home">Samryetha</a>
          {nav ?? (
            <nav className="primary-nav" aria-label="Primary navigation">
              <a className="nav-link" href="/" data-view="latest">Latest</a>
              <a className="nav-link" href="/" data-view="followed">Followed</a>
              <a className="nav-link" href="/" data-view="boards">Boards</a>
              <a className="nav-link" href="/feedback">Feedback</a>
              <a className="nav-link" href="/tasks">Tasks</a>
            </nav>
          )}
          <div className="actions">
            {search ?? (
              <label className="search-field">
                <SearchIcon />
                <span className="sr-only">Search discussions</span>
                <input type="search" placeholder="Search discussions" autoComplete="off" />
              </label>
            )}
            <MobileMenu activeView={activeView} />
            <UserMenu current={current === "profile" || current === "settings" ? current : undefined} />
            <InboxIcon />
            <a className="compose" href="/post" aria-current={current === "post" ? "page" : undefined}>Post</a>
          </div>
        </div>
      </header>
      {children}
    </>
  );
}

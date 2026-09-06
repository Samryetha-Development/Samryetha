import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useAuth } from "./lib/auth";
import { initials } from "./lib/format";
import { AdminIcon, CloseIcon, HamburgerIcon, LogOutIcon, SettingsIcon } from "./icons";

type MenuView = "latest" | "followed" | "boards";
type MenuLink = { href: string; view?: MenuView; label: string };

// 主导航项。view 项统一走 `<a href="/" data-view>` SPA 路由（root-app 处理），
// 这样首页/非首页都能切视图；其它页面只负责收菜单。
const NAV_LINKS: MenuLink[] = [
  { href: "/", view: "latest", label: "Latest" },
  { href: "/", view: "followed", label: "Followed" },
  { href: "/", view: "boards", label: "Boards" },
  { href: "/feedback", label: "Feedback" },
  { href: "/tasks", label: "Tasks" },
  { href: "/inbox", label: "Inbox" },
  { href: "/post", label: "Post" },
];

// 离场动画时长，和 CSS 的 yFadeSlideOut 一致，到点才卸载。
const CLOSE_MS = 300;

// 全屏遮罩菜单（仅移动端显示触发按钮）。
// 打开时 fresh mount → CSS 自动播放 menu-link 的错列入场；关闭先加 .closing
// 播离场动画，CLOSE_MS 后再卸载，避免离场动画被直接打断。
export function MobileMenu({ activeView }: { activeView?: MenuView }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const close = useCallback(() => {
    if (closing) return;
    setClosing(true);
    timer.current = window.setTimeout(() => {
      setOpen(false);
      setClosing(false);
    }, CLOSE_MS);
  }, [closing]);

  const openMenu = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    setClosing(false);
    setOpen(true);
  }, []);

  // 打开时：Esc 关闭 + 锁定 body 滚动
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, close]);

  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  // SPA pushState 会同步 location.pathname，这里直接读即为当前页。
  // SSR 无 window：菜单体只在 open 时渲染（open 必是客户端交互触发），SSR 走不到这里。
  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;
  const footerBase = NAV_LINKS.length;

  return (
    <>
      <button className="icon-btn mobile-menu-trigger" type="button" aria-label="Open menu" aria-expanded={open} aria-haspopup="dialog" onClick={openMenu}>
        <HamburgerIcon />
      </button>
      {open &&
        // createPortal 到 body：菜单是 fixed 全屏，而 .topbar 有 backdrop-filter，
        // 会创建 containing block 把 fixed 子元素相对 topbar 定位（菜单会缩成 topbar 高度）。
        // 挂到 body 后彻底脱离，inset:0 才相对视口。
        createPortal(
        <div className={`mobile-menu ${closing ? "closing" : ""}`} role="dialog" aria-modal="true" aria-label="Menu">
          <div className="mobile-menu-head">
            <a href="/" className="wordmark" onClick={close}>Samryetha</a>
            <button className="icon-btn mobile-menu-close" type="button" aria-label="Close menu" onClick={close}><CloseIcon /></button>
          </div>

          <nav className="mobile-menu-nav" aria-label="Primary navigation">
            {NAV_LINKS.map((link, index) => {
              const isActive = link.view
                ? activeView === link.view && pathname === "/"
                : pathname === link.href;
              return (
                <a
                  key={link.label}
                  href={link.href}
                  data-view={link.view}
                  className={`menu-link ${isActive ? "active" : ""}`}
                  style={{ "--d": `${index * 45}ms` } as CSSProperties}
                  onClick={close}
                >
                  {link.label}
                </a>
              );
            })}
          </nav>

          <div className="mobile-menu-footer">
            {user ? (
              <>
                <div className="mobile-menu-user">
                  <span className="mobile-menu-user-avatar" aria-hidden="true">{initials(user.displayName)}</span>
                  <span className="mobile-menu-user-id"><strong>{user.displayName}</strong><small>@{user.handle}</small></span>
                </div>
                <a className="menu-link menu-link-small" href="/settings" style={{ "--d": `${footerBase * 45}ms` } as CSSProperties} onClick={close}><SettingsIcon />Settings</a>
                {user.role === "admin" && (
                  <a className="menu-link menu-link-small" href="/admin" style={{ "--d": `${(footerBase + 1) * 45}ms` } as CSSProperties} onClick={close}><AdminIcon />Admin</a>
                )}
                <button
                  className="menu-link menu-link-small menu-link-logout"
                  type="button"
                  style={{ "--d": `${(footerBase + (user.role === "admin" ? 2 : 1)) * 45}ms` } as CSSProperties}
                  onClick={handleLogout}
                >
                  <LogOutIcon />Log out
                </button>
              </>
            ) : (
              <a className="menu-link menu-link-signin" href="/login" style={{ "--d": `${footerBase * 45}ms` } as CSSProperties} onClick={close}>Sign in</a>
            )}
          </div>
        </div>,
        document.body,
        )}
    </>
  );
}

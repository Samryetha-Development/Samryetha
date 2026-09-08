import { useEffect, useRef, useState } from "react";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { initials } from "./lib/format";
import { AdminIcon, LogOutIcon, ProfileIcon, SettingsIcon } from "./icons";

type UserMenuLocation = "profile" | "settings" | "admin";

// 关闭延迟：popover 与 trigger 之间有 8px 间隙，鼠标横向跨越间隙时若立即关闭
// 会来不及点进菜单。移出后留 ~150ms 窗口，期间移入菜单(或点击)取消关闭。
const HOVER_CLOSE_MS = 150;

export function UserMenu({ current }: { current?: UserMenuLocation }) {
  const { user, loading, logout } = useAuth();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  const cancelClose = () => {
    if (closeTimer.current !== undefined) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = undefined;
    }
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), HOVER_CLOSE_MS);
  };

  if (loading) {
    return <span className="user-menu-loading" aria-hidden="true" />;
  }

  if (!user) {
    return (
      <a className="compose" href="/login" style={{ margin: 0 }}>{t("menu.signIn")}</a>
    );
  }

  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  return (
    <div
      className="user-menu"
      ref={menuRef}
      onMouseEnter={() => {
        cancelClose();
        setOpen(true);
      }}
      onMouseLeave={scheduleClose}
    >
      <button className={`icon-btn user-menu-trigger ${current ? "profile-current" : ""}`} type="button" aria-label={t("menu.accountMenu")} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <ProfileIcon />
        <span className="user-menu-name">{user.displayName}</span>
      </button>
      <div
        className={`user-menu-popover ${open ? "open" : ""}`}
        role="menu"
        aria-label={t("menu.account")}
        aria-hidden={!open}
        onMouseEnter={cancelClose}
        onMouseLeave={scheduleClose}
      >
        <a className="user-menu-profile" href="/profile" role="menuitem" tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}>
          <span className="user-menu-avatar" aria-hidden="true">{initials(user.displayName)}</span>
          <span><strong>{user.displayName}</strong><small>@{user.handle}</small></span>
        </a>
        <div className="user-menu-divider" role="separator" />
        <a className="user-menu-item" href="/settings" role="menuitem" tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}><SettingsIcon /><span>{t("menu.settings")}</span></a>
        {user.role === "admin" && <a className="user-menu-item" href="/admin" role="menuitem" tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}><AdminIcon /><span>{t("menu.admin")}</span></a>}
        <button className="user-menu-item user-menu-logout" type="button" role="menuitem" tabIndex={open ? 0 : -1} onClick={handleLogout}><LogOutIcon /><span>{t("menu.logout")}</span></button>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export type LakoAccount = {
  display_name: string;
  username: string;
  email: string;
};

export type LakoSelectAccountProps = {
  account: LakoAccount;
  /** 「用这个账号继续」——宿主重跑 authorize。 */
  onSuccess: () => void;
  onUseAnotherAccount?: () => void;
  onCreateAccount?: () => void;
  /**
   * 「把这个账号从选择器里移除」（等价于登出该 Lako 会话）。
   *
   * 组件自己**做不到**这件事：那需要 `lako_csrf`（double-submit），而该 cookie
   * 是 host-only 在 Lako 的源上的——宿主跨源读不到 `document.cookie`。
   * 所以这里交给宿主处理，不传就整个不渲染这个菜单。
   */
  onForgetAccount?: () => void | Promise<void>;
  /** 副标题，例如「继续前往 Samryetha」。 */
  subtitle?: string;
  /** 面板顶部的产品名。 */
  product?: string;
};

/**
 * 用 <a> 而不是 <button> 承载这几个动作行：`.lako-account-option-main` /
 * `.lako-account-action` 是按 <a> 的盒子写的，换成 <button> 会被 `.lako-auth button`
 * 的胶囊样式盖掉。保留 <a> 的盒子，只把行为改成回调。
 */
function ActionRow({
  className,
  onClick,
  children,
}: {
  className: string;
  onClick: () => void;
  children: ReactNode;
}) {
  const activate = (event: KeyboardEvent<HTMLAnchorElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onClick();
  };
  return (
    <a
      className={className}
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.preventDefault();
        onClick();
      }}
      onKeyDown={activate}
    >
      {children}
    </a>
  );
}

export function LakoSelectAccount({
  account,
  onSuccess,
  onUseAnotherAccount,
  onCreateAccount,
  onForgetAccount,
  subtitle,
  product = "Lako",
}: LakoSelectAccountProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  const forget = async () => {
    if (busy || !onForgetAccount) return;
    setBusy(true);
    try {
      await onForgetAccount();
    } finally {
      setBusy(false);
    }
  };

  const initial = (account.display_name || account.username).slice(0, 1).toUpperCase();

  return (
    <section className="lako-auth-login">
      <div className="lako-auth-panel lako-account-chooser">
        <div className="lako-auth-product">
          <span className="lako-identity-glyph" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          {product}
        </div>

        <h1>Choose an account</h1>
        {subtitle && <p className="lako-subtle">{subtitle}</p>}

        <div className="lako-account-options">
        <div className="lako-account-option">
          <ActionRow className="lako-account-option-main" onClick={onSuccess}>
            <span className="lako-account-avatar" aria-hidden="true">
              {initial}
            </span>
            <span className="lako-account-copy">
              <strong>{account.display_name}</strong>
              <small>
                @{account.username}
                {account.email ? ` · ${account.email}` : ""}
              </small>
            </span>
          </ActionRow>

          {onForgetAccount && (
            <div className="lako-account-menu-wrap" ref={menuRef}>
              <button
                className="lako-account-menu-trigger"
                type="button"
                aria-label={`More options for ${account.display_name}`}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <span />
                <span />
                <span />
              </button>
              {menuOpen && (
                <div className="lako-account-menu" role="menu">
                  <button type="button" role="menuitem" disabled={busy} onClick={() => void forget()}>
                    {busy ? "Removing…" : "Remove"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {onUseAnotherAccount && (
          <ActionRow className="lako-account-action" onClick={onUseAnotherAccount}>
            Use another account
          </ActionRow>
        )}
        {onCreateAccount && (
          <ActionRow className="lako-account-action" onClick={onCreateAccount}>
            Create account
          </ActionRow>
        )}
        </div>
      </div>
    </section>
  );
}

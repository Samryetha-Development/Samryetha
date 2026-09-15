"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
  displayName: string;
  username: string;
  email: string;
  initial: string;
  returnTo: string;
  embedded?: boolean;
};

function csrfToken(): string {
  const value = document.cookie.split("; ").find((item) => item.startsWith("lako_csrf="))?.split("=")[1] ?? "";
  return decodeURIComponent(value);
}

export function AccountChoice({ displayName, username, email, initial, returnTo, embedded = false }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  useEffect(() => {
    if (!embedded) return;
    const panel = menuRef.current?.closest(".auth-panel");
    if (!(panel instanceof HTMLElement)) return;
    const report = () => window.parent.postMessage({
      type: "lako:embedded-size",
      height: Math.ceil(panel.getBoundingClientRect().height),
    }, "*");
    report();
    const observer = new ResizeObserver(report);
    observer.observe(panel);
    return () => observer.disconnect();
  }, [embedded]);

  const remove = async () => {
    if (busy) return;
    setBusy(true);
    const response = await fetch("/api/sessions/logout", {
      method: "POST",
      headers: { "x-csrf-token": csrfToken() },
    });
    if (response.ok) {
      location.href = `/login?return_to=${encodeURIComponent(returnTo)}`;
      return;
    }
    setBusy(false);
  };

  return <div className="account-option"><a className="account-option-main" href={returnTo}><span className="account-avatar" aria-hidden="true">{initial}</span><span className="account-copy"><strong>{displayName}</strong><small>@{username}{email ? ` · ${email}` : ""}</small></span></a><div className="account-menu-wrap" ref={menuRef}><button className="account-menu-trigger" type="button" aria-label={`More options for ${displayName}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}><span /><span /><span /></button>{open && <div className="account-menu" role="menu"><button type="button" role="menuitem" disabled={busy} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete"}</button></div>}</div></div>;
}

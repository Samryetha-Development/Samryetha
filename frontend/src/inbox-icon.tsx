import { useCallback, useEffect, useState } from "react";
import { api } from "./lib/api";
import { MailIcon } from "./icons";
import { useAuth } from "./lib/auth";
import { useSse } from "./lib/realtime";

// 顶栏信封图标：显示通知 + 私信的未读总数，SSE 实时刷新
// Top-bar envelope icon: shows combined unread count of notifications + direct messages, refreshed via SSE
export function InboxIcon() {
  const { user } = useAuth();
  const [unread, setUnread] = useState(0);

  const refresh = useCallback(() => {
    if (!user) return;
    Promise.all([api.notifications.unreadCount(), api.messages.unreadCount()])
      .then(([n, m]) => setUnread(n.unreadCount + m.unreadCount))
      .catch(() => undefined);
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useSse(() => refresh(), Boolean(user));

  if (!user) return null;

  return (
    <a className="icon-btn inbox-btn" href="/inbox" aria-label={unread > 0 ? `Inbox, ${unread} unread` : "Inbox"}>
      <MailIcon />
      {unread > 0 && <span className="badge">{unread > 99 ? "99+" : unread}</span>}
    </a>
  );
}

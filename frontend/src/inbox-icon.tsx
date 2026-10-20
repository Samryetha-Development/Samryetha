import { MailIcon } from "./icons";
import { useAuth } from "./lib/auth";
import { useRealtimeState } from "./lib/realtime";
import { useI18n } from "./lib/i18n";

// 顶栏信封图标：未读总数直接来自 SSE 权威快照。
export function InboxIcon() {
  const { user } = useAuth();
  const { t } = useI18n();
  const { unreadCounts } = useRealtimeState(Boolean(user), user?.id);
  const unread = unreadCounts?.total ?? 0;

  if (!user) return null;

  return (
    <a className="icon-btn inbox-btn" href="/inbox" aria-label={unread > 0 ? t("inbox.unread", { count: unread }) : t("inbox.title")}>
      <MailIcon />
      {unread > 0 && <span className="badge">{unread > 99 ? "99+" : unread}</span>}
    </a>
  );
}

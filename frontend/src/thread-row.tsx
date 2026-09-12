import type { ThreadSummary } from "./lib/api";
import { timeAgo, useI18n } from "./lib/i18n";

export function ThreadRow({ thread, showSender = true }: { thread: ThreadSummary; showSender?: boolean }) {
  const { locale, t } = useI18n();
  const repliesLabel = t("thread.repliesCount", { count: thread.replyCount });
  return (
    <a className="thread" href={`/d/${thread.id}`}>
      <div className="thread-main">
        <h3 className="thread-title">{thread.title}</h3>
        {thread.preview && <p className="thread-preview">{thread.preview}</p>}
        <div className="meta">
          {showSender && (
            <>
              <span className="sender">{thread.author.displayName}</span>
              <span className="dot" />
            </>
          )}
          <span className="tag">{thread.board.name}</span>
          <span className="dot" />
          <span>{timeAgo(thread.createdAt, locale)}</span>
          {thread.replyCount > 0 && (
            <>
              <span className="dot" />
              <span>{t("thread.lastReply")} {timeAgo(thread.lastActivityAt, locale)}</span>
            </>
          )}
        </div>
      </div>
      <div className="count" aria-label={repliesLabel} title={repliesLabel}>
        {thread.replyCount}
      </div>
    </a>
  );
}

import type { ModerationStatus } from "./api";
import { useI18n } from "./i18n";

/**
 * 审核状态标记。
 *
 * 用途：让作者一眼看出自己的内容还在审核中、让管理员一眼看出哪些是待审/已封禁，
 * 不必逐个点开。三种状态：
 *
 *   - approved：正常，**不渲染任何东西**（否则满屏标记，等于没有标记）；
 *   - pending ：审核中（作者自己、版主、管理员能看到）；
 *   - rejected：已封禁（只有管理员能看到被拒的内容，所以这个标记实际只出现在
 *               管理员的浏览视图里）。
 *
 * 后端只对"本来就有权看到这一行"的请求下发 `moderationStatus`，因此这里直接按值
 * 渲染即可，不需要再做权限判断。
 */
export function ModerationBadge({ status, compact = false }: { status?: ModerationStatus; compact?: boolean }) {
  const { t } = useI18n();
  if (!status || status === "approved") return null;

  const pending = status === "pending";
  const label = pending ? t("mod.badgePending") : t("mod.badgeRejected");
  const hint = pending ? t("mod.badgePendingHint") : t("mod.badgeRejectedHint");

  return (
    <span className={`mod-badge ${pending ? "is-pending" : "is-rejected"}${compact ? " is-compact" : ""}`} title={hint} role="status">
      <span className="mod-badge-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

/**
 * 内容外框的审核态样式类。挂在帖子/回复的容器上，让"审核中"整体看起来不一样
 * （左侧色条），比只有一个小徽章更容易扫出来。
 */
export function moderationClass(status?: ModerationStatus): string {
  if (!status || status === "approved") return "";
  return status === "pending" ? "mod-frame mod-frame-pending" : "mod-frame mod-frame-rejected";
}

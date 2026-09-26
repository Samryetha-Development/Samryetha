import type { ReactNode } from "react";

export type LakoNotificationTone = "success" | "error" | "info";
export type LakoNotificationItem = {
  id: string | number;
  message: ReactNode;
  tone?: LakoNotificationTone;
};

function NotificationIcon({ tone }: { tone: LakoNotificationTone }) {
  if (tone === "success") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7" /></svg>;
  if (tone === "error") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5m0 3.5v.1M4.7 19h14.6a1.7 1.7 0 0 0 1.5-2.5L13.5 4a1.7 1.7 0 0 0-3 0l-7.3 12.5A1.7 1.7 0 0 0 4.7 19Z" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 10v6m0-10v.1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" /></svg>;
}

export function LakoNotification({ message, tone = "info" }: Omit<LakoNotificationItem, "id">) {
  return (
    <div className={`lako-ui-notification ${tone}`} role="status">
      <span className="lako-ui-notification-icon"><NotificationIcon tone={tone} /></span>
      <span>{message}</span>
    </div>
  );
}

export function LakoNotifications({ items, ariaLabel = "Notifications" }: { items: LakoNotificationItem[]; ariaLabel?: string }) {
  if (items.length === 0) return null;
  return (
    <div className="lako-ui-notifications" role="region" aria-label={ariaLabel} aria-live="polite">
      {items.map((item) => <LakoNotification key={item.id} message={item.message} tone={item.tone} />)}
    </div>
  );
}

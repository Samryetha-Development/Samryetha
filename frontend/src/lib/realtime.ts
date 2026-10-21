import { useEffect, useState } from "react";
import type { Presence } from "./api";

export interface UnreadCounts {
  notificationUnreadCount: number;
  messageUnreadCount: number;
  total: number;
}

export interface RealtimeState {
  unreadCounts: UnreadCounts | null;
  presence: Presence | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseUnreadCounts(value: string): UnreadCounts | null {
  const data: unknown = JSON.parse(value);
  if (!isRecord(data)) return null;
  if (
    typeof data.notificationUnreadCount !== "number"
    || typeof data.messageUnreadCount !== "number"
    || typeof data.total !== "number"
  ) return null;
  return {
    notificationUnreadCount: data.notificationUnreadCount,
    messageUnreadCount: data.messageUnreadCount,
    total: data.total,
  };
}

function parsePresence(value: string): Presence | null {
  const data: unknown = JSON.parse(value);
  return isRecord(data) && typeof data.onlineCount === "number" ? { onlineCount: data.onlineCount } : null;
}

// SSE 首帧和后续事件都携带权威完整快照；EventSource 重连后会自动重新取得当前状态。
export function useRealtimeState(enabled: boolean, identity?: number): RealtimeState {
  const [unreadCounts, setUnreadCounts] = useState<UnreadCounts | null>(null);
  const [presence, setPresence] = useState<Presence | null>(null);
  const identityKey = enabled ? identity ?? "session" : null;
  const [owner, setOwner] = useState<typeof identityKey>(null);

  useEffect(() => {
    setOwner(identityKey);
    setUnreadCounts(null);
    setPresence(null);
    if (!enabled) {
      return;
    }
    let alive = true;
    const source = new EventSource("/api/events");
    const onUnreadCount = (event: MessageEvent) => {
      if (!alive) return;
      try {
        const counts = parseUnreadCounts(event.data);
        if (counts) setUnreadCounts(counts);
      } catch {
        // 忽略坏负载；重连首帧或下一次完整快照会恢复状态。
      }
    };
    const onPresence = (event: MessageEvent) => {
      if (!alive) return;
      try {
        const nextPresence = parsePresence(event.data);
        if (nextPresence) setPresence(nextPresence);
      } catch {
        // 同上。
      }
    };
    source.addEventListener("unread-count", onUnreadCount);
    source.addEventListener("presence", onPresence);
    return () => {
      alive = false;
      source.removeEventListener("unread-count", onUnreadCount);
      source.removeEventListener("presence", onPresence);
      source.close();
    };
  }, [enabled, identityKey]);

  return { unreadCounts: enabled && owner === identityKey ? unreadCounts : null, presence: enabled && owner === identityKey ? presence : null };
}

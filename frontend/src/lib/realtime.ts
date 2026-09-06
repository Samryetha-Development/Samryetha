import { useEffect, useRef, useState } from "react";
import { api, type Presence } from "./api";

// SSE：订阅属于当前用户的实时事件。断线依赖 EventSource 自带重连；
// 重连窗口内遗漏的事件不会补拉，未读数等计数可能与后端短暂不一致。
export function useSse(onNotification: (data: { userId?: number }) => void, enabled: boolean) {
  const handlerRef = useRef(onNotification);
  handlerRef.current = onNotification;

  useEffect(() => {
    if (!enabled) return;
    const source = new EventSource("/api/events");
    const onEvent = (event: MessageEvent) => {
      try {
        handlerRef.current(JSON.parse(event.data) as { userId?: number });
      } catch {
        // 忽略坏负载
      }
    };
    source.addEventListener("notification.created", onEvent);
    return () => {
      source.removeEventListener("notification.created", onEvent);
      source.close();
    };
  }, [enabled]);
}

// Presence：45s 心跳（后端 TTL 60s）+ 30s 拉在线列表。
export function usePresence(enabled: boolean) {
  const [presence, setPresence] = useState<Presence | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const load = async () => {
      try {
        const data = await api.presence.get();
        if (alive) setPresence(data);
      } catch {
        // 静默：心跳失败由下一轮补
      }
    };
    const beat = async () => {
      try {
        await api.presence.heartbeat();
      } catch {
        // 会话可能过期
      }
    };
    void load();
    void beat();
    const hb = window.setInterval(() => void beat(), 45_000);
    const pl = window.setInterval(() => void load(), 30_000);
    return () => {
      alive = false;
      window.clearInterval(hb);
      window.clearInterval(pl);
    };
  }, [enabled]);

  return presence;
}

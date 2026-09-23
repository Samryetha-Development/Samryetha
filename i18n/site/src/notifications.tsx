import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { LakoNotifications, type LakoNotificationItem, type LakoNotificationTone } from "@lako/ui";

type Notify = (message: string, tone?: LakoNotificationTone) => void;
const NotificationContext = createContext<Notify>(() => undefined);

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<LakoNotificationItem[]>([]);
  const notify = useCallback<Notify>((message, tone = "info") => {
    const id = `${Date.now()}-${Math.random()}`;
    setItems((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 3600);
  }, []);
  const value = useMemo(() => notify, [notify]);
  return (
    <NotificationContext.Provider value={value}>
      {children}
      <LakoNotifications items={items} />
    </NotificationContext.Provider>
  );
}

export function useNotify() {
  return useContext(NotificationContext);
}

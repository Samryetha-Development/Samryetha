import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { FeedPage } from "./api";

const sizeKey = "samryetha_posts_per_page";
const sizeEvent = "samryetha-page-size";
const defaultSize = 20;
let fallbackSize = defaultSize;

function readSize(): number {
  try {
    const value = Number(window.localStorage.getItem(sizeKey));
    fallbackSize = Number.isInteger(value) && value >= 1 && value <= 50 ? value : defaultSize;
    return fallbackSize;
  } catch {
    return fallbackSize;
  }
}

function subscribeSize(notify: () => void) {
  window.addEventListener("storage", notify);
  window.addEventListener(sizeEvent, notify);
  return () => {
    window.removeEventListener("storage", notify);
    window.removeEventListener(sizeEvent, notify);
  };
}

export function usePageSizePreference() {
  const stored = useSyncExternalStore(subscribeSize, readSize, () => defaultSize);
  return {
    pageSize: stored,
    localSize: null,
    setPageSize: (value: number) => {
      if (!Number.isInteger(value) || value < 1 || value > 50) return;
      fallbackSize = value;
      try { window.localStorage.setItem(sizeKey, String(value)); } catch { /* Keep the session preference when storage is blocked. */ }
      window.dispatchEvent(new Event(sizeEvent));
    },
  };
}

type Navigation = { scope: string; limit: number; cursors: (string | undefined)[] };
type PageResult<T> = FeedPage<T> & { key: string; error: unknown };

function restoreNavigation(scope: string, limit: number): Navigation | null {
  const saved: unknown = window.history.state?.postPagination;
  if (!saved || typeof saved !== "object") return null;
  const nav = saved as Partial<Navigation>;
  if (nav.scope !== scope || nav.limit !== limit || !Array.isArray(nav.cursors) || !nav.cursors.length) return null;
  if (nav.cursors[0] != null || !nav.cursors.slice(1).every((cursor) => typeof cursor === "string")) return null;
  return { scope, limit, cursors: [undefined, ...nav.cursors.slice(1)] };
}

// The scope must include every filter/account used by load. Cursor history provides
// previous-page navigation without changing the API's stable keyset pagination.
export function usePagedList<T>(
  scope: string,
  load: (cursor: string | undefined, limit: number) => Promise<FeedPage<T>>,
  { enabled = true, remember = false }: { enabled?: boolean; remember?: boolean } = {},
) {
  const { pageSize: limit, localSize, setPageSize } = usePageSizePreference();
  const [navigation, setNavigation] = useState<Navigation>({ scope, limit, cursors: [undefined] });
  const current = navigation.scope === scope && navigation.limit === limit
    ? navigation : { scope, limit, cursors: [undefined] };
  const cursor = current.cursors.at(-1);
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([scope, limit, cursor, revision]);
  const [result, setResult] = useState<PageResult<T> | null>(null);
  const loader = useRef(load);
  loader.current = load;
  const restored = useRef(!remember);
  const [ready, setReady] = useState(!remember);

  useEffect(() => {
    setNavigation((value) => value.scope === scope && value.limit === limit ? value : { scope, limit, cursors: [undefined] });
  }, [scope, limit]);

  useEffect(() => {
    if (!enabled || restored.current) return;
    if (localSize === null && limit !== readSize()) return;
    restored.current = true;
    const saved = restoreNavigation(scope, limit);
    if (saved) setNavigation(saved);
    setReady(true);
  }, [enabled, scope, limit]);

  useEffect(() => {
    if (!enabled || !ready) return;
    let alive = true;
    void loader.current(cursor, limit).then((data) => {
      if (alive) setResult({ ...data, key, error: null });
    }).catch((error: unknown) => {
      if (alive) setResult({ key, items: [], nextCursor: null, error });
    });
    return () => { alive = false; };
  }, [key, cursor, limit, enabled, ready]);

  useEffect(() => {
    if (remember && enabled && ready && window.location.pathname === "/") {
      window.history.replaceState({ ...window.history.state, postPagination: current }, "");
    }
  }, [remember, enabled, ready, scope, limit, cursor]);

  const loaded = enabled && ready && result?.key === key;
  const loading = enabled && !loaded;
  const items = loaded ? result.items : [];
  const nextCursor = loaded ? result.nextCursor : null;
  const pagination = {
    page: current.cursors.length,
    limit,
    loading,
    hasPrevious: current.cursors.length > 1,
    hasNext: nextCursor !== null,
    goTo: (page: number) => {
      if (!loading && Number.isInteger(page) && page >= 1 && page <= current.cursors.length) setNavigation({ ...current, cursors: current.cursors.slice(0, page) });
    },
    previous: () => {
      if (!loading && current.cursors.length > 1) setNavigation({ ...current, cursors: current.cursors.slice(0, -1) });
    },
    next: () => {
      if (!loading && nextCursor !== null) setNavigation({ ...current, cursors: [...current.cursors, nextCursor] });
    },
    changeLimit: (value: number) => {
      if (!Number.isInteger(value) || value < 1 || value > 50) return;
      setPageSize(value);
      setNavigation({ scope, limit: value, cursors: [undefined] });
    },
  };

  return {
    items,
    loading,
    error: loaded ? result.error : null,
    pagination,
    retry: () => setRevision((value) => value + 1),
    setItems: (update: (items: T[]) => T[]) => setResult((value) => value?.key === key ? { ...value, items: update(value.items) } : value),
  };
}

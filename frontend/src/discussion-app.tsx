import { useEffect, useMemo, useRef, useState } from "react";
import { ThreadRow } from "./thread-row";
import { Loading } from "./loading";
import { AppShell } from "./app-shell";
import { SearchIcon } from "./icons";
import { useAnimatedTabs } from "./lib/use-animated-tabs";
import { useTabIndicator } from "./lib/use-tab-indicator";
import { api, type BoardSummary, type TaskItem, type ThreadSummary } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n, formatDateL } from "./lib/i18n";
import { usePresence, useSse } from "./lib/realtime";

export type View = "latest" | "followed" | "boards";
type Filter = "all" | string;

const viewLabelKeys = { latest: "nav.latest", followed: "nav.followed", boards: "nav.boards" } as const;

export function DiscussionApp({ initialView = "latest", onViewChange, restoreScroll = null, onScrollRestored }: { initialView?: View; onViewChange?: (view: View) => void; restoreScroll?: number | null; onScrollRestored?: () => void }) {
  const { user } = useAuth();
  const { locale, t } = useI18n();
  const [query, setQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const filterTabs = useAnimatedTabs<Filter>({ initial: "all", duration: 95 });

  // 从 URL 带 board 参数进入（详情页板块链接 / 分享链接）：初始化板块筛选。
  // 客户端过滤最近 30 条，板块内容不完整是既有限制，不在这次范围。
  // popstate 回退到带 ?board= 的历史条目时组件不一定重挂载，需重新读 URL。
  useEffect(() => {
    const readBoardParam = () => {
      const board = new URLSearchParams(window.location.search).get("board");
      filterTabs.jumpTo(board ?? "all");
    };
    readBoardParam();
    window.addEventListener("popstate", readBoardParam);
    return () => window.removeEventListener("popstate", readBoardParam);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const skipFilterReset = useRef(false);
  const viewTabs = useAnimatedTabs<View>({
    initial: initialView,
    duration: 125,
    onSelect: (v) => onViewChange?.(v),
    onCommit: () => {
      // openBoard 从 boards 视图点板块：是「板块跳转」而非「视图切换」，保留刚设置的板块筛选
      if (skipFilterReset.current) {
        skipFilterReset.current = false;
        return;
      }
      filterTabs.jumpTo("all");
      setQuery("");
      setSearchQuery("");
    },
  });

  // 跟随外部视图（SPA 路由 / 移动端汉堡菜单跳视图）：initialView 变化时同步本地 tabs。
  // jumpTo 不触发 onSelect，不会回写父级，无循环。
  useEffect(() => {
    if (initialView !== viewTabs.active) viewTabs.jumpTo(initialView);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialView]);

  // 筛选变化回写 URL，刷新后仍定位到当前板块（replaceState 不污染历史）。
  // 同时把当前视图写进 history.state，与 root-app 的 popstate 恢复协调。
  useEffect(() => {
    if (window.location.pathname !== "/") return;
    const url = filterTabs.active === "all" ? "/" : `/?board=${encodeURIComponent(filterTabs.active)}`;
    window.history.replaceState({ view: viewTabs.active }, "", url);
  }, [filterTabs.active, viewTabs.active]);

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [sidebarError, setSidebarError] = useState(false);
  const [sidebarReloadToken, setSidebarReloadToken] = useState(0);
  const [unread, setUnread] = useState(0);
  const [today, setToday] = useState<number | null>(null);
  // 管理员首页小任务列表：进行中（status=open）的任务
  const [openTasks, setOpenTasks] = useState<TaskItem[]>([]);
  const searchRef = useRef<HTMLInputElement>(null);
  const primaryNavRef = useRef<HTMLElement>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const navIndicator = useTabIndicator(primaryNavRef, (v) => `[data-view="${v}"]`, viewTabs.active);
  const filterIndicator = useTabIndicator(tabsRef, (f) => `[data-filter="${f}"]`, filterTabs.active, { enabled: viewTabs.committed !== "boards", measureDeps: [boards.length] });

  // 搜索防抖
  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  // 拉 feed / 搜索 / 板块
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError(false);
    const load = async () => {
      try {
        if (viewTabs.committed === "boards") {
          const data = await api.boards.list();
          if (alive) setBoards(data.items);
        } else if (searchQuery) {
          const data = await api.search(searchQuery);
          if (alive) setThreads(data.items);
        } else {
          const data = await api.discussions.feed({ feed: viewTabs.committed, limit: 30 });
          if (alive) setThreads(data.items);
        }
      } catch {
        if (alive) setLoadError(true);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [viewTabs.committed, searchQuery, reloadToken]);

  // 右侧栏：板块列表 + 在线
  useEffect(() => {
    if (boards.length !== 0 || viewTabs.committed === "boards") return;
    let alive = true;
    api.boards
      .list()
      .then((data) => {
        if (!alive) return;
        setBoards(data.items);
        setSidebarError(false);
      })
      .catch(() => {
        if (alive) setSidebarError(true);
      });
    return () => {
      alive = false;
    };
  }, [boards.length, viewTabs.committed, sidebarReloadToken]);

  // 挂载后再填日期，避免 SSR 与客户端各自 Date.now() 造成 hydration 文本不匹配
  useEffect(() => {
    setToday(Date.now());
  }, []);

  // 从帖子返回：首屏数据落定后回到记忆位置（内容高度此时才稳定，提前滚会被截断）
  useEffect(() => {
    if (loading || restoreScroll == null) return;
    const raf = requestAnimationFrame(() => {
      window.scrollTo(0, restoreScroll);
      onScrollRestored?.();
    });
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, restoreScroll]);

  // 通知未读数
  useEffect(() => {
    if (!user) {
      setUnread(0);
      return;
    }
    api.notifications
      .unreadCount()
      .then((data) => setUnread(data.unreadCount))
      .catch(() => undefined);
  }, [user]);

  // 管理员首页：拉取进行中（status=open）任务，侧栏显示小任务列表
  useEffect(() => {
    if (user?.role !== "admin") {
      setOpenTasks([]);
      return;
    }
    let alive = true;
    api.tasks
      .list()
      .then((data) => alive && setOpenTasks(data.items.filter((item) => item.status === "open")))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [user]);

  useSse(
    () => setUnread((n) => n + 1),
    Boolean(user),
  );

  const presence = usePresence(Boolean(user));

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const openBoard = (slug: string) => {
    // 已经在 latest 时 setActive 不会触发 onCommit，无需跳过重置
    if (viewTabs.active !== "latest") skipFilterReset.current = true;
    filterTabs.setActive(slug);
    viewTabs.setActive("latest");
  };

  const filterOptions = useMemo(
    () => [{ key: "all", label: t("feed.all") }, ...boards.map((b) => ({ key: b.slug, label: b.name }))],
    [boards, t],
  );

  const visibleThreads = useMemo(() => {
    const normalized = searchQuery.toLocaleLowerCase();
    return threads.filter((thread) => {
      if (filterTabs.committed !== "all" && thread.board.slug !== filterTabs.committed) return false;
      if (normalized && !`${thread.title} ${thread.preview} ${thread.author.displayName} ${thread.board.name}`.toLocaleLowerCase().includes(normalized)) return false;
      return true;
    });
  }, [filterTabs.committed, searchQuery, threads]);

  const visibleBoards = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return boards.filter((board) => !normalized || `${board.name} ${board.description}`.toLocaleLowerCase().includes(normalized));
  }, [boards, query]);

  const activeBoards = useMemo(() => [...boards].sort((a, b) => b.todayActivity - a.todayActivity).slice(0, 4), [boards]);
  const empty = viewTabs.committed === "boards" ? visibleBoards.length === 0 : visibleThreads.length === 0;

  return (
    <AppShell
      wordmarkHref="#main-content"
      activeView={viewTabs.active}
      nav={
        <nav className="primary-nav" aria-label={t("nav.primary")} ref={primaryNavRef}>
          {(["latest", "followed", "boards"] as View[]).map((item) => (
            <button key={item} data-view={item} className={`nav-link ${viewTabs.active === item ? "active" : ""}`} type="button" aria-current={viewTabs.active === item ? "page" : undefined} onClick={() => viewTabs.setActive(item)}>{t(viewLabelKeys[item])}</button>
          ))}
          <span className={`nav-indicator ${navIndicator.ready ? "ready" : ""}`} style={{ width: navIndicator.width, transform: `translateX(${navIndicator.x}px)` }} aria-hidden="true" />
          <a className="nav-link" href="/feedback">{t("nav.feedback")}</a>
          {user?.role === "admin" && <a className="nav-link" href="/tasks">{t("nav.tasks")}</a>}
        </nav>
      }
      search={
        <label className="search-field">
          <SearchIcon />
          <span className="sr-only">{viewTabs.committed === "boards" ? t("feed.searchBoards") : t("nav.searchDiscussions")}</span>
          <input ref={searchRef} type="search" placeholder={viewTabs.committed === "boards" ? t("feed.searchBoards") : t("nav.searchDiscussions")} autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      }
    >
      <main className="shell page" id="main-content">
        <section className={`feed ${viewTabs.phase}`} aria-labelledby="feed-title">
          <div className="feed-head">
            <h1 className="feed-title" id="feed-title">{searchQuery ? t("feed.searchResults") : t(viewLabelKeys[viewTabs.committed])}</h1>
            <div className="feed-date">{today === null ? "" : formatDateL(today, locale)}</div>
          </div>

          {viewTabs.committed !== "boards" && (
            <div className="tabs" role="tablist" aria-label={t("feed.filters")} ref={tabsRef}>
              {filterOptions.map((item) => (
                <button key={item.key} data-filter={item.key} className={`tab ${filterTabs.active === item.key ? "active" : ""}`} type="button" role="tab" aria-selected={filterTabs.active === item.key} onClick={() => filterTabs.setActive(item.key)}>{item.label}</button>
              ))}
              <span className={`filter-indicator ${filterIndicator.ready ? "ready" : ""}`} style={{ width: filterIndicator.width, transform: `translateX(${filterIndicator.x}px)` }} aria-hidden="true" />
            </div>
          )}

          <div className={`feed-body ${filterTabs.phase}`} aria-live="polite">
            {loading ? (
              <Loading />
            ) : loadError ? (
              <div className="empty-state content-fade">
                <p>{viewTabs.committed === "boards" ? t("feed.loadBoardsFail") : searchQuery ? t("feed.loadSearchFail") : t("feed.loadThreadsFail")}</p>
                <button type="button" className="action-btn" onClick={() => setReloadToken((n) => n + 1)}>{t("common.retry")}</button>
              </div>
            ) : viewTabs.committed === "boards" ? (
              <div className="board-list content-fade">
                {visibleBoards.map((board) => (
                  <button className="board-row" type="button" key={board.slug} onClick={() => openBoard(board.slug)}>
                    <div><h3 className="board-name">{board.name}</h3><p className="board-description">{board.description}</p><div className="board-meta">{t("feed.members", { count: board.memberCount })}</div></div>
                    <div className="board-activity"><strong>{board.todayActivity}</strong>{t("feed.todaySuffix")}</div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="thread-list content-fade">
                {visibleThreads.map((thread) => (
                  <ThreadRow thread={thread} key={thread.id} />
                ))}
              </div>
            )}
            {!loading && !loadError && empty && <div className="empty-state content-fade">{viewTabs.committed === "boards" ? t("feed.noBoards") : searchQuery ? t("feed.noResults") : viewTabs.committed === "followed" ? t("feed.noFollowed") : t("feed.noThreads")}</div>}
          </div>
        </section>

        <aside className="now" aria-label={t("feed.currentActivity")}>
          <h2>{t("feed.rightNow")}</h2>
          <div className="online"><span className="pulse" aria-hidden="true" /><span><strong>{presence?.onlineCount ?? 0}</strong> {t("feed.onlineUnit")}</span></div>
          <div className="now-section"><p className="now-label">{t("feed.activeBoards")}</p><div className="now-links">
            {sidebarError && boards.length === 0 ? (
              <button type="button" className="now-link" onClick={() => setSidebarReloadToken((n) => n + 1)}><span>{t("feed.loadBoardsFail")}</span><span>{t("common.retry")}</span></button>
            ) : (
              activeBoards.map((board) => <a href={`/?board=${board.slug}`} className="now-link" key={board.slug} onClick={(e) => { e.preventDefault(); openBoard(board.slug); }}><span>{board.name}</span><span>{board.todayActivity}</span></a>)
            )}
          </div></div>
          <div className="now-section"><p className="now-label">{t("feed.today")}</p><div className="now-links">
            <a href="#main-content" className="now-link"><span>{t("feed.newDiscussions")}</span><span>{boards.reduce((sum, b) => sum + b.todayActivity, 0)}</span></a>
            {user && <a href="/settings" className="now-link"><span>{t("feed.unreadForYou")}</span><span>{unread}</span></a>}
          </div></div>
          {user?.role === "admin" && (
            <div className="now-section"><p className="now-label">{t("task.tasks")}</p><div className="now-links">
              {openTasks.length === 0 ? (
                <span className="now-link"><span>{t("task.noTasks")}</span></span>
              ) : (
                openTasks.slice(0, 6).map((task) => (
                  <a href="/tasks" className="now-link" key={task.id}>
                    <span>{task.title}</span>
                    <span>{task.priority === "urgent" ? t("task.urgent") : ""}</span>
                  </a>
                ))
              )}
            </div></div>
          )}
        </aside>
      </main>
    </AppShell>
  );
}

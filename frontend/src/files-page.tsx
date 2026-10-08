import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { api, ApiError, type FileConfig, type FileResourceSummary, type FileSort } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatBytes } from "./lib/format";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { FileUploadDialog } from "./file-upload-dialog";

// 文件服务列表页（面向新生的资料库）。
// File-service list page, the resource library aimed at newcomers.
//
// 布局取舍（详见 docs/file-service/00-plan.md §3）：列表用表格而不是卡片流。
// 学习资料的决策依据是"科目 + 格式 + 时间 + 口碑"，全是文本字段；一屏能扫 15-20 行
// 比卡片流的一屏 4-6 条更符合"快速找到我要的那份"这个真实任务。
// Layout decision (see docs/file-service/00-plan.md section 3): a table rather than a card
// grid. The basis for choosing study material is subject, format, recency and reputation,
// all of them text fields, and scanning 15-20 rows at once serves the real task ("find the
// one I need, fast") far better than 4-6 cards.

type Tab = "all" | "favorites" | "mine";

const SORT_KEYS: Record<FileSort, I18nKey> = {
  latest: "file.sortLatest",
  downloads: "file.sortDownloads",
  favorites: "file.sortFavorites",
  rating: "file.sortRating",
  name: "file.sortName",
};

const PAGE_SIZE = 20;
// 新生专区条数：刻意克制，调研显示专题聚合的容量应当有限（贴吧精品分类每吧上限 8 个）。
// Size of the newcomer strip, deliberately small: the research shows topical aggregation
// should stay bounded (a Tieba board caps its featured categories at eight).
const NEWCOMER_LIMIT = 4;

function RatingText({ value, count }: { value: number | null; count: number }) {
  const { t } = useI18n();
  if (value === null || count === 0) return <span className="files-muted">{t("file.ratingNone")}</span>;
  return (
    <span className="files-rating-text">
      {t("file.ratingValue", { value: value.toFixed(1) })}
      <span className="files-muted"> · {t("file.ratingCount", { count })}</span>
    </span>
  );
}

export function FilesPage() {
  const { t, locale } = useI18n();
  const { user, loading: authLoading } = useAuth();

  const [config, setConfig] = useState<FileConfig | null>(null);
  const [items, setItems] = useState<FileResourceSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [newcomer, setNewcomer] = useState<FileResourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  const [tab, setTab] = useState<Tab>("all");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [sort, setSort] = useState<FileSort>("latest");
  const [page, setPage] = useState(1);
  const [uploadOpen, setUploadOpen] = useState(false);
  // 发布成功后的刷新令牌：上传者很可能本来就停在"我的上传"且页码为 1，
  // 那种情况下 tab/page 的赋值全是同值写入，React 不会重新渲染、加载 effect 也不会重跑，
  // 于是刚发布的资料不出现在列表里。用一个单调递增的令牌显式驱动刷新。
  // A refresh token for successful publishes: an uploader is very likely already sitting on
  // "my uploads" at page 1, where assigning the same tab and page values triggers no re-render
  // and no reload, so the freshly published resource never shows up. A monotonically increasing
  // token drives the refresh explicitly instead.
  const [refreshToken, setRefreshToken] = useState(0);

  const mountedRef = useRef(true);
  const listRequestRef = useRef(0);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 从 URL 查询串初始化筛选条件：详情页面包屑与标签链接都靠它把"点进某个分类/标签"
  // 变成可直接分享、可后退的地址，而不是只活在组件状态里。
  // Seed the filters from the URL query string: the detail page's breadcrumb and tag links
  // rely on it to turn "open this category or tag" into a shareable, back-navigable address
  // instead of state that lives only inside the component.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const initialCategory = params.get("category") ?? "";
    const initialTag = params.get("tag") ?? "";
    const initialQuery = params.get("q") ?? "";
    if (initialCategory) setCategory(initialCategory);
    if (initialTag) setTag(initialTag);
    if (initialQuery) {
      setQuery(initialQuery);
      setQueryInput(initialQuery);
    }
  }, []);

  // 配置与列表分开加载：配置失败时列表仍可尝试，避免一个接口挂掉整页空白。
  // Config and the list load separately so a failing config endpoint does not blank the
  // whole page while the list could still have worked.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.files.config();
        if (mountedRef.current && !cancelled) setConfig(data);
      } catch {
        if (mountedRef.current && !cancelled) setConfig(null);
      }
    })();
    // 发布后重跑：分类计数与标签云都会因新资料而变化。
    // 另外必须跟随 user?.id：分类计数是按可见性算的，登录/登出或换人之后同一分类的计数会变，
    // 只依赖刷新令牌会让访客看到上一个身份的计数。
    // Re-run after a publish: category counts and the tag cloud both change with a new resource.
    // It must also follow user?.id, because the counts are visibility-dependent and therefore change
    // when a visitor signs in, signs out or switches accounts; keying only off the refresh token
    // would leave a guest looking at the previous identity's counts.
    return () => { cancelled = true; };
  }, [refreshToken, user?.id]);

  const loadList = useCallback(async () => {
    const requestId = ++listRequestRef.current;
    const current = () => mountedRef.current && requestId === listRequestRef.current;
    setLoading(true);
    setLoadError("");
    try {
      if (tab === "favorites") {
        if (!user) {
          setItems([]);
          setTotal(0);
          return;
        }
        const data = await api.files.favorites();
        if (!current()) return;
        setItems(data.items);
        setTotal(data.total);
        return;
      }
      if (tab === "mine") {
        if (!user) {
          setItems([]);
          setTotal(0);
          return;
        }
        const data = await api.files.mine();
        if (!current()) return;
        setItems(data.items);
        setTotal(data.total);
        return;
      }
      const data = await api.files.list({ category, tag, q: query, sort, page, pageSize: PAGE_SIZE });
      if (!current()) return;
      setItems(data.items);
      setTotal(data.total);
    } catch {
      if (current()) setLoadError(t("file.loadFail"));
    } finally {
      if (current()) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, category, tag, query, sort, page, user?.id, refreshToken]);

  useEffect(() => {
    if (authLoading) return;
    void loadList();
    return () => { listRequestRef.current += 1; };
  }, [authLoading, loadList]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        // 新生专区：直接取"新生攻略"类里下载最多的几份，无需管理员先手动精选就能立即有用。
        // The newcomer strip takes the most downloaded guides directly, so it is useful
        // immediately without an admin having to curate anything first.
        const data = await api.files.list({ kind: "guide", sort: "downloads", pageSize: NEWCOMER_LIMIT });
        if (mountedRef.current && !cancelled) setNewcomer(data.items);
      } catch {
        if (mountedRef.current && !cancelled) setNewcomer([]);
      }
    })();
    // 新生专区也会受新资料影响，同样随刷新令牌重跑；它同样按可见性取数，因此也跟随 user?.id。
    // The newcomer strip is affected by new resources too, so it follows the same token; it is also
    // fetched per visibility, so it follows user?.id as well.
    return () => { cancelled = true; };
  }, [refreshToken, user?.id]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtersActive = Boolean(category || tag || query);

  const clearFilters = () => {
    setCategory("");
    setTag("");
    setQuery("");
    setQueryInput("");
    setPage(1);
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setQuery(queryInput.trim());
    setPage(1);
  };

  const onDownload = async (item: FileResourceSummary) => {
    setActionError("");
    try {
      const ticket = await api.files.download(item.id);
      const anchor = document.createElement("a");
      anchor.href = ticket.downloadUrl;
      anchor.download = ticket.originalFilename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setActionError(t("file.downloadSignIn"));
      else setActionError(t("file.downloadFail"));
    }
  };

  const onToggleFavorite = async (item: FileResourceSummary) => {
    if (!user) {
      setActionError(t("file.signInToSave"));
      return;
    }
    setActionError("");
    try {
      const next = await api.files.setFavorite(item.id, !item.isFavorited);
      setItems((current) =>
        current.map((row) =>
          row.id === item.id
            ? { ...row, isFavorited: next.isFavorited, favoriteCount: next.favoriteCount }
            : row,
        ),
      );
    } catch {
      setActionError(t("file.saveFail"));
    }
  };

  // 个人标签页只对已登录用户展示：收藏与上传都是按 viewer 取数的个人视图，
  // 匿名访客点进去只会拿到空列表。会话尚未落定（authLoading）时同样不展示，
  // 免得匿名访客先看到"我的收藏/我的上传"再被下面的整页门槛换掉，白闪一下。
  // Personal tabs are for signed-in users only: both are viewer-scoped personal views, so an
  // anonymous visitor could only ever get an empty list out of them. They stay hidden while the
  // session lookup is still in flight (authLoading) too, so an anonymous visitor never sees
  // "my saved / my uploads" flash before the page-level gate below replaces them.
  const personalTabs: Array<{ key: Tab; labelKey: I18nKey }> = [
    { key: "favorites", labelKey: "file.tabFavorites" },
    { key: "mine", labelKey: "file.tabMine" },
  ];
  const tabs: Array<{ key: Tab; labelKey: I18nKey }> = [
    { key: "all", labelKey: "file.tabAll" },
    ...(user ? personalTabs : []),
  ];

  // 上传能力：与后端 authz 的 FILE_CREATE（仅管理员）对齐。
  // The upload ability, aligned with the backend's authz FILE_CREATE, which is admin-only.
  const canUpload = user?.role === "admin";

  // 空态口径：三个标签页各有各的"暂无"，登录提示绝不能冒充空态。
  // 修复前这里对非 "all" 标签页一律回退到 file.signInToSave（"登录后可使用收藏。"），
  // 于是已登录的普通用户在两处都会看到登录提示：①"我的上传"——普通用户没有上传权限
  // （后端 FILE_CREATE 仅管理员），列表恒为空；②"我的收藏"——收藏为空时。
  // 两种情况都谎报了登录状态，正是本次要修的严重 bug。
  // Empty-state wording: each tab gets its own "nothing here yet" copy, and the sign-in hint must
  // never stand in for one. Before the fix every non-"all" tab fell back to file.signInToSave
  // ("Sign in to use saved resources."), so a signed-in plain user saw a sign-in prompt in two
  // places: (1) "my uploads", because a plain user holds no upload permission (the backend's
  // FILE_CREATE is admin-only) and that list is therefore always empty, and (2) "my saved"
  // whenever their saved list was empty. Both lied about their login state, which is the bug.
  const emptyMessage = tab === "favorites"
    ? t("file.emptyFavorites")
    : tab === "mine"
      ? t("file.emptyMine")
      : t("file.empty");

  // 未登录（匿名）访问：整个文件服务页面给一个统一的登录门槛，
  // 而不是"列表能看、再由各标签页各自提示登录"。判定必须连带 authLoading：
  // 只看 !user 会让已登录用户在会话尚未落定的首屏被误判成匿名、闪一下登录提示。
  // 提示样式与文案复用仓库既有约定（与反馈页 fb.signInToView 同款 empty-state + /login 链接）。
  // Anonymous access gets one page-level sign-in gate for the whole file service, instead of a
  // browsable list whose tabs each prompt for sign-in separately. The check has to include
  // authLoading: testing !user alone would misjudge a signed-in visitor as anonymous during the
  // first session lookup and flash a sign-in prompt at them. The prompt reuses the repository's
  // existing convention (the same empty-state plus /login link as the feedback page's
  // fb.signInToView) rather than inventing a new one.
  if (!authLoading && !user) {
    return (
      <AppShell current="files">
        <main className="shell files-layout">
          <div className="empty-state">
            {t("file.signInToView")} <a className="sender" href="/login">{t("file.signIn")}</a>
          </div>
        </main>
      </AppShell>
    );
  }

  const errorBanner = actionError
    ? <div className="files-banner files-banner-error" role="status">{actionError}</div>
    : null;

  return (
    <AppShell current="files">
      <main className="shell files-layout">
        <header className="files-header">
          <div>
            <h1 className="files-title">{t("file.title")}</h1>
            <p className="files-subtitle">{t("file.subtitle")}</p>
          </div>
          <div className="files-header-actions">
            {config ? <span className="files-stat">{t("file.total", { count: config.stats.resourceCount })}</span> : null}
            {/* 上传入口只对管理员显示：2026-10-08 起后端把上传/新建资料收口为管理员能力
                （authz 的 FILE_CREATE），这里同步隐藏按钮与弹窗。
                但隐藏只是界面整洁，安全边界在后端：普通用户即使手工调用
                /api/files/resources/presign 与 /api/files/resources 也会被 403 拒绝。
                The upload entry point is shown to admins only: since 2026-10-08 the backend
                restricts uploading and creating resources to the admin (authz's FILE_CREATE), and
                this hides the button and the dialog to match. Hiding is only cosmetic though; the
                security boundary is the backend, where a plain user calling
                /api/files/resources/presign or /api/files/resources by hand is refused with 403. */}
            {canUpload ? (
              <button type="button" className="files-primary" onClick={() => setUploadOpen(true)}>
                {t("file.upload")}
              </button>
            ) : null}
          </div>
        </header>

        {newcomer.length > 0 ? (
          <section className="files-newcomer" aria-label={t("file.newcomerZone")}>
            <h2 className="files-newcomer-title">{t("file.newcomerZone")}</h2>
            <ul className="files-newcomer-list">
              {newcomer.map((item) => (
                <li key={item.id}>
                  <a className="files-newcomer-item" href={`/files/${item.id}`}>
                    <span className="files-newcomer-name">{item.title}</span>
                    <span className="files-muted">{t("file.tableDownloads")} {item.downloadCount}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {errorBanner}

        <nav className="files-tabs" aria-label={t("file.title")}>
          {tabs.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className={`files-tab ${tab === entry.key ? "active" : ""}`}
              aria-current={tab === entry.key ? "page" : undefined}
              onClick={() => {
                setTab(entry.key);
                setPage(1);
              }}
            >
              {t(entry.labelKey)}
            </button>
          ))}
        </nav>

        {tab === "all" ? (
          <>
            <div className="files-toolbar">
              <div className="files-categories">
                <button
                  type="button"
                  className={`files-chip ${category === "" ? "active" : ""}`}
                  onClick={() => {
                    setCategory("");
                    setPage(1);
                  }}
                >
                  {t("file.allCategories")}
                </button>
                {(config?.categories ?? []).map((entry) => (
                  <button
                    key={entry.slug}
                    type="button"
                    className={`files-chip ${category === entry.slug ? "active" : ""}`}
                    onClick={() => {
                      setCategory(entry.slug);
                      setPage(1);
                    }}
                  >
                    {entry.name}
                    {typeof entry.resourceCount === "number" ? <span className="files-chip-count">{entry.resourceCount}</span> : null}
                  </button>
                ))}
              </div>
              <form className="files-search" onSubmit={submitSearch} role="search">
                <input
                  type="search"
                  value={queryInput}
                  onChange={(event) => setQueryInput(event.target.value)}
                  placeholder={t("file.searchPlaceholder")}
                  aria-label={t("file.searchPlaceholder")}
                />
                <button type="submit" className="files-search-submit">{t("file.search")}</button>
              </form>
              <label className="files-sort">
                <span>{t("file.sortBy")}</span>
                <select
                  value={sort}
                  onChange={(event) => {
                    setSort(event.target.value as FileSort);
                    setPage(1);
                  }}
                >
                  {(config?.sorts ?? (Object.keys(SORT_KEYS) as FileSort[])).map((key) => (
                    <option key={key} value={key}>{t(SORT_KEYS[key])}</option>
                  ))}
                </select>
              </label>
            </div>

            {(config?.tagCloud?.length ?? 0) > 0 ? (
              <div className="files-tagcloud" aria-label={t("file.tagCloud")}>
                <span className="files-tagcloud-label">{t("file.tagCloud")}</span>
                {config!.tagCloud.map((entry) => (
                  <button
                    key={entry.tag}
                    type="button"
                    className={`files-tag ${tag === entry.tag ? "active" : ""}`}
                    onClick={() => {
                      setTag(tag === entry.tag ? "" : entry.tag);
                      setPage(1);
                    }}
                  >
                    {entry.tag}
                    <span className="files-chip-count">{entry.count}</span>
                  </button>
                ))}
                {filtersActive ? (
                  <button type="button" className="files-clear" onClick={clearFilters}>{t("file.clearFilters")}</button>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}

        {loading ? (
          <Loading label={t("common.loading")} />
        ) : loadError ? (
          <div className="files-banner files-banner-error">
            {loadError}
            <button type="button" className="files-link" onClick={() => void loadList()}>{t("common.retry")}</button>
          </div>
        ) : items.length === 0 ? (
          <div className="empty-state">
            <p>{emptyMessage}</p>
            {tab === "all" ? <p className="files-muted">{t("file.emptyHint")}</p> : null}
          </div>
        ) : (
          <>
            <div className="files-table-wrap">
              <table className="files-table">
                <thead>
                  <tr>
                    <th scope="col">{t("file.tableTitle")}</th>
                    <th scope="col">{t("file.tableCategory")}</th>
                    <th scope="col">{t("file.tableFormat")}</th>
                    <th scope="col">{t("file.tableSize")}</th>
                    <th scope="col">{t("file.tableUploader")}</th>
                    <th scope="col">{t("file.tableTime")}</th>
                    <th scope="col">{t("file.tableDownloads")}</th>
                    <th scope="col">{t("file.tableRating")}</th>
                    <th scope="col" aria-label={t("file.download")} />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="files-cell-title">
                        <a className="files-row-title" href={`/files/${item.id}`}>{item.title}</a>
                        <div className="files-row-tags">
                          {item.tags.map((entry) => (
                            <button
                              key={entry}
                              type="button"
                              className="files-tag files-tag-small"
                              onClick={() => {
                                setTab("all");
                                setTag(entry);
                                setPage(1);
                              }}
                            >
                              {entry}
                            </button>
                          ))}
                        </div>
                      </td>
                      <td>{item.category.name}</td>
                      <td className="files-cell-format">{(item.extension || "?").replace(".", "").toUpperCase()}</td>
                      <td className="files-cell-size">{formatBytes(item.sizeBytes)}</td>
                      <td>{item.uploader.displayName}</td>
                      <td className="files-cell-time">
                        {timeAgo(item.updatedAt || item.createdAt, locale)}
                      </td>
                      <td className="files-cell-number">{item.downloadCount}</td>
                      <td><RatingText value={item.ratingAvg} count={item.ratingCount} /></td>
                      <td className="files-cell-actions">
                        <button type="button" className="files-link" onClick={() => void onDownload(item)}>
                          {t("file.download")}
                        </button>
                        <button
                          type="button"
                          className={`files-link files-fav ${item.isFavorited ? "active" : ""}`}
                          aria-pressed={item.isFavorited}
                          onClick={() => void onToggleFavorite(item)}
                        >
                          {item.isFavorited ? t("file.favorited") : t("file.favorite")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {tab === "all" && totalPages > 1 ? (
              <nav className="files-pagination" aria-label={t("file.pagination")}>
                <button type="button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                  {t("file.prevPage")}
                </button>
                <span>{t("file.pageOf", { page, total: totalPages })}</span>
                <button type="button" disabled={page >= totalPages} onClick={() => setPage((current) => current + 1)}>
                  {t("file.nextPage")}
                </button>
              </nav>
            ) : null}
          </>
        )}
      </main>

      {uploadOpen && canUpload && config ? (
        <FileUploadDialog
          config={config}
          onClose={() => setUploadOpen(false)}
          onPublished={() => {
            setUploadOpen(false);
            setTab("mine");
            setPage(1);
            // 上传者往往本来就停在"我的上传"的页码 1，上面两次赋值都是同值写入，
            // React 不会重渲染、加载 effect 也不会重跑，新资料就不会出现。显式递增令牌强制刷新，
            // 分类计数、标签云与新生专区也一并重取。
            // An uploader is typically already on "my uploads" page 1, so the two assignments
            // above write identical values, React skips the re-render and the loading effect never
            // re-runs, leaving the new resource invisible. Bumping the token forces a refresh and
            // also refetches the category counts, the tag cloud and the newcomer strip.
            setRefreshToken((value) => value + 1);
          }}
        />
      ) : null}
    </AppShell>
  );
}

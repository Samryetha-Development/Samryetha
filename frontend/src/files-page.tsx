import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { api, ApiError, uploadFileBytes, type FileConfig, type FileResourceDetail, type FileResourceSummary, type FileSort, type FileVisibility } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatBytes } from "./lib/format";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { FileUploadDialog } from "./file-upload-dialog";
import { FileFolderBar } from "./file-folder-bar";
import { FileMetadataDialog } from "./file-metadata-dialog";
import { flattenFolders, folderPathLabel, foldersAtLevel, parentFolderView, type FolderView } from "./lib/folder-tree";
import { planDropEntries, runDropUploads, summarizeDrop, titleFromFilename, type DropEntry } from "./lib/drop-upload";

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
  // 文件夹视图："all" 不按文件夹过滤；"unfiled" 只看未分类；数字 = 该文件夹。
  // The folder view: "all" filters nothing, "unfiled" shows only unfiled resources, and a number
  // selects one folder.
  const [folderView, setFolderView] = useState<FolderView>("all");
  const [query, setQuery] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [sort, setSort] = useState<FileSort>("latest");
  const [page, setPage] = useState(1);
  const [uploadOpen, setUploadOpen] = useState(false);
  // 列表页的行内编辑器。列表拿到的只是摘要（没有简介、没有 can 权限位），所以点「编辑」时
  // 必须先按 id 取一次详情，拿不到就明确报错——绝不能打开一个字段不全的弹窗，
  // 否则用户一保存就会把没看见的简介覆盖掉。
  // The in-row editor on the list page. A list row is only a summary (no description, no permission
  // flags), so clicking Edit fetches the detail by id first and reports an explicit error when that
  // fails: opening a dialog with missing fields would let a save overwrite a description the user
  // never saw.
  const [editorDetail, setEditorDetail] = useState<FileResourceDetail | null>(null);
  const [editorLoadingId, setEditorLoadingId] = useState<number | null>(null);
  // 行内删除的两段式确认：先武装某一行，再点确认。删除是不可逆动作，
  // 单次点击即删会让误触直接毁掉一份资料。
  // Two-step confirmation for an in-row delete: arm one row, then confirm. Deletion is
  // irreversible, and a single-click delete would let a stray click destroy a resource.
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  // 发布成功后的刷新令牌：上传者很可能本来就停在"我的上传"且页码为 1，
  // 那种情况下 tab/page 的赋值全是同值写入，React 不会重新渲染、加载 effect 也不会重跑，
  // 于是刚发布的资料不出现在列表里。用一个单调递增的令牌显式驱动刷新。
  // A refresh token for successful publishes: an uploader is very likely already sitting on
  // "my uploads" at page 1, where assigning the same tab and page values triggers no re-render
  // and no reload, so the freshly published resource never shows up. A monotonically increasing
  // token drives the refresh explicitly instead.
  const [refreshToken, setRefreshToken] = useState(0);

  // 拖拽上传：dragActive 只驱动"松手即上传"的提示层，dropEntries 是每份文件的可见进度与结果。
  // 两者分开是因为提示层必须随拖入/拖出立刻消失，而结果清单要一直留到用户下次拖入或关闭。
  // Drag-and-drop: dragActive drives only the "release to upload" hint layer, while dropEntries
  // carries each file's visible progress and outcome. They are separate because the hint must
  // vanish the instant the pointer leaves, whereas the result list stays until the user drops
  // again or dismisses it.
  const [dragActive, setDragActive] = useState(false);
  const [dropEntries, setDropEntries] = useState<DropEntry[]>([]);
  const [dropBusy, setDropBusy] = useState(false);
  // dragenter/dragleave 会为子元素成对触发，用深度计数才不会在划过子节点时误判"离开"。
  // dragenter/dragleave fire in pairs for child elements, so a depth counter is what keeps a slide
  // across a child node from being misread as leaving.
  const dragDepthRef = useRef(0);
  // 条目 id 的单调种子：同一份文件可以被拖两次，id 必须仍然唯一。
  // A monotonically increasing seed for entry ids: the same file may be dropped twice, and the ids
  // still have to stay unique.
  const dropSeedRef = useRef(0);

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
      const data = await api.files.list({
        category,
        tag,
        q: query,
        sort,
        page,
        pageSize: PAGE_SIZE,
        // 只把当前视图真正对应的那一个参数发出去："全部"两个都不带（即不过滤文件夹），
        // "未分类"带 unfiled，具体文件夹带 folderId。带上另一个会让后端拿到互相矛盾的条件。
        // Only the parameter that actually matches the current view is sent: "all" sends neither
        // (no folder filtering), "unfiled" sends unfiled and a real folder sends folderId. Sending
        // both would hand the backend two contradictory conditions.
        ...(folderView === "unfiled"
          ? { unfiled: true }
          : typeof folderView === "number"
            ? { folderId: folderView }
            : {}),
      });
      if (!current()) return;
      setItems(data.items);
      setTotal(data.total);
    } catch {
      if (current()) setLoadError(t("file.loadFail"));
    } finally {
      if (current()) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, category, tag, query, sort, page, user?.id, refreshToken, folderView]);

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

  // ================================================================ 文件夹 / folders

  const folders = config?.folders ?? [];

  // 当前层级要以"文件夹行"列出的文件夹。只在"全部资料"标签下出现——与 FileFolderBar 同一口径：
  // 个人视图（我的收藏/我的上传）是按 viewer 取数的，在那里叠一层文件夹只会让人以为资料少了。
  // The folders that belong on screen as folder rows at this level, shown only under the "All" tab,
  // the same rule the folder bar follows: the personal tabs are viewer-scoped, and stacking a folder
  // level on top of them would only make people think their own resources had gone missing.
  const levelFolders = tab === "all" ? foldersAtLevel(folders, folderView) : [];
  // 向上路径：只要不在根上，就有一条明确的"返回上一级"。列表本身因此自带往返路径，
  // 不必只依赖导航条上的面包屑。
  // The way up: anywhere but the root has an explicit "up one level", so the list carries its own
  // there-and-back path instead of leaning on the breadcrumb alone.
  const upView = tab === "all" ? parentFolderView(folders, folderView) : null;

  const enterFolder = (next: FolderView) => {
    setFolderView(next);
    setPage(1);
  };

  // 文件夹的写操作都只做两件事：发请求、然后刷新（分类计数与标签云同样会随归属变化）。
  // 失败一律向上抛，由 FileFolderBar 接住并显示——这一层不吞异常，否则删除/改名失败会静默。
  // Every folder write does exactly two things: send the request and refresh (the category counts
  // and the tag cloud move with membership too). Failures are rethrown so FileFolderBar can show
  // them; swallowing here is what would make a failed rename or delete silent.
  const createFolder = async (name: string, parentId: number | null) => {
    await api.files.createFolder(parentId === null ? { name } : { name, parentId });
    setRefreshToken((value) => value + 1);
  };

  const renameFolder = async (id: number, name: string) => {
    await api.files.updateFolder(id, { name });
    setRefreshToken((value) => value + 1);
  };

  const deleteFolder = async (id: number) => {
    await api.files.deleteFolder(id);
    // 删除会把资料解绑到未分类，当前若正停在被删的文件夹上就必须退回"全部"，
    // 否则列表会去查一个已经不存在的文件夹、看起来像"资料全没了"。
    // Deleting detaches its resources into unfiled, so a viewer sitting inside the deleted folder
    // has to fall back to "All"; otherwise the list queries a folder that no longer exists and
    // looks as though every resource vanished.
    setFolderView("all");
    setPage(1);
    setRefreshToken((value) => value + 1);
  };

  // 谁能把资料放进文件夹：与后端 authz 的 FILE_UPDATE 完全对齐（管理员 或 该资料的 owner），
  // 所以这里显示不出来的控件，后端也不会允许；后端仍然做权威判定。
  // Who may file a resource: exactly the backend's FILE_UPDATE (an admin or the resource's owner),
  // so a control hidden here is one the backend would refuse anyway. The backend still decides.
  const canFileResource = (item: FileResourceSummary) =>
    Boolean(user) && (user?.role === "admin" || user?.id === item.uploader.id);

  const moveResource = async (item: FileResourceSummary, raw: string) => {
    setActionError("");
    const target = raw === "" ? null : Number(raw);
    try {
      await api.files.setFolder(item.id, target);
      setItems((current) => current.map((row) => (row.id === item.id ? { ...row, folderId: target } : row)));
      setRefreshToken((value) => value + 1);
    } catch (caught) {
      // 保留服务端的具体原因（如"文件夹不存在"），它比通用文案更有助于自助排查。
      // Keep the server's specific reason (a missing folder, say); it helps far more than generic
      // copy.
      if (caught instanceof ApiError && caught.message) setActionError(caught.message);
      else setActionError(t("file.folderMoveFail"));
    }
  };

  // 打开行内编辑器：列表行只有摘要，所以先按 id 取详情再开弹窗。
  // Opening the in-row editor: a list row is only a summary, so the detail is fetched by id first.
  const openEditor = async (item: FileResourceSummary) => {
    setActionError("");
    setEditorLoadingId(item.id);
    try {
      const detail = await api.files.get(item.id);
      if (mountedRef.current) setEditorDetail(detail);
    } catch (caught) {
      // 打不开编辑器必须是可见的失败：否则点「编辑」毫无反应，会被读成页面坏了。
      // Failing to open the editor has to be visible: an Edit button that does nothing reads as a
      // broken page.
      if (mountedRef.current) {
        if (caught instanceof ApiError && caught.message) setActionError(caught.message);
        else setActionError(t("file.metaOpenFail"));
      }
    } finally {
      if (mountedRef.current) setEditorLoadingId(null);
    }
  };

  // 行内删除。权限口径与"移动/编辑"完全一致（后端 authz 的 FILE_DELETE 与 FILE_UPDATE 同一条规则：
  // 活跃账号 且（全局管理员 或 该资料的 owner）），前端不另发明一套判定。
  // In-row deletion. The rule is exactly the one behind moving and editing (the backend's FILE_DELETE
  // is the same predicate as FILE_UPDATE: an active actor who is either a global admin or the
  // uploader), so no separate client-side rule is invented here.
  const deleteResource = async (item: FileResourceSummary) => {
    setActionError("");
    setConfirmDeleteId(null);
    try {
      await api.files.del(item.id);
      // 就地移除该行并让分类计数、标签云与文件夹计数重新取数：被删的资料可能正是当前筛选的支柱，
      // 留着旧计数会让界面说一个已经不成立的数字。
      // The row is removed in place and the category counts, tag cloud and folder counts are refetched:
      // the deleted resource may well have been what the current filter hinged on, and a stale count
      // would have the interface assert something no longer true.
      setItems((current) => current.filter((row) => row.id !== item.id));
      setRefreshToken((value) => value + 1);
    } catch (caught) {
      // 失败绝不静默：服务端给的原因优先，其次才是通用文案。
      // A failure is never silent: the server's own reason wins, the generic copy is the fallback.
      if (caught instanceof ApiError && caught.message) setActionError(caught.message);
      else setActionError(t("file.deleteFail"));
    }
  };

  // ================================================================ 拖拽上传 / drag and drop

  // 拖拽上传复用与上传弹窗完全相同的一段三步链路（presign -> PUT 字节 -> 建资料），
  // 刻意不另造并行通道：既有的授权（后端 FILE_CREATE 仅管理员）与配额校验因此原样生效，
  // 拖拽只是同一能力的第二个入口。
  // Drag-and-drop reuses the very same three-step chain the upload dialog uses (presign, PUT the
  // bytes, create the resource) rather than a parallel pipeline, so the existing authorisation
  // (the backend's admin-only FILE_CREATE) and quota checks apply unchanged; dropping is simply a
  // second entry point onto the same ability.
  const uploadDroppedFile = async (file: File, categoryId: number, visibility: FileVisibility) => {
    const presign = await api.files.presign({
      filename: file.name,
      mimeType: file.type || "application/octet-stream",
      sizeBytes: file.size,
    });
    await uploadFileBytes(presign.uploadUrl, file);
    await api.files.create({
      objectKey: presign.objectKey,
      expires: presign.expires,
      sig: presign.sig,
      sizeBytes: file.size,
      categoryId,
      title: titleFromFilename(file.name) || file.name.slice(0, 160),
      descriptionMarkdown: "",
      tags: [],
      visibility,
      originalFilename: file.name,
      mimeType: file.type || "application/octet-stream",
    });
  };

  const dropCarriesFiles = (event: DragEvent<HTMLElement>) =>
    Array.from(event.dataTransfer?.types ?? []).includes("Files");

  const onDragEnter = (event: DragEvent<HTMLElement>) => {
    if (!dropCarriesFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    if (canUpload && !dropBusy) setDragActive(true);
  };

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    if (!dropCarriesFiles(event)) return;
    // 无条件阻止默认行为：否则浏览器会直接打开被拖入的文件、把用户带离当前页，
    // 连 drop 事件都不会派发——非管理员拖入时也一样，那时我们要给的是明确的无权限提示。
    // The default is prevented unconditionally: otherwise the browser opens the dropped file and
    // navigates away, and no drop event is ever dispatched. That holds for a non-admin drop too,
    // where the point is precisely to show an explicit permission message.
    event.preventDefault();
    event.dataTransfer.dropEffect = canUpload ? "copy" : "none";
    if (canUpload && !dropBusy) setDragActive(true);
  };

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    if (!dropCarriesFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  };

  const onDrop = async (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    const dropped = Array.from(event.dataTransfer?.files ?? []);
    if (dropped.length === 0 || dropBusy) return;
    // 非管理员拖入：明确告知没有权限。上传入口对普通用户是隐藏的（与隐藏上传按钮同一口径），
    // 但"拖了却毫无反应"会被误读成页面坏了，所以这里必须说清楚。
    // A non-admin drop is told plainly that it lacks the permission. The entry point is hidden from
    // plain users (the same choice the upload button already makes), but "nothing happened" reads
    // as a broken page, so silence is not an option here.
    if (!canUpload) {
      setActionError(t("file.dropNoPermission"));
      return;
    }
    if (!config) {
      setActionError(t("file.dropConfigMissing"));
      return;
    }
    setActionError("");
    const entries = planDropEntries(
      dropped.map((file) => ({ name: file.name, size: file.size })),
      config,
      dropSeedRef.current,
    );
    dropSeedRef.current += entries.length;
    setDropEntries(entries);
    setDropBusy(true);
    // 条目与 File 对象一一对应：planDropEntries 只保留名字与体积，字节仍由调用方持有，
    // 免得把 File 对象塞进纯逻辑层的类型里。
    // Entries map onto File objects one to one: planDropEntries keeps only the name and the size,
    // while the bytes stay with the caller, so the pure logic layer never has to carry a File.
    const byId = new Map(entries.map((entry, index) => [entry.id, dropped[index]]));
    const categoryId = config.categories[0]?.id ?? 0;
    let settled: DropEntry[] = entries;
    try {
      settled = await runDropUploads(
        entries,
        (entry) => {
          const file = byId.get(entry.id);
          if (!file) throw new Error(t("file.dropUploadFail"));
          return uploadDroppedFile(file, categoryId, "members");
        },
        (next) => { if (mountedRef.current) setDropEntries(next); },
      );
    } finally {
      if (mountedRef.current) setDropBusy(false);
    }
    // 只有真的有文件落地才刷新列表与计数：全部失败时刷新只会让人以为"成功了但列表没变"。
    // The list and its counts refresh only when something actually landed; refreshing after an
    // all-failed drop would look like "it worked but the list did not change".
    if (mountedRef.current && summarizeDrop(settled).succeeded > 0) {
      setRefreshToken((value) => value + 1);
      setTab("mine");
      setPage(1);
    }
  };

  const dropStatusLabel = (entry: DropEntry): string => {
    if (entry.status === "success") return t("file.dropStatusSuccess");
    if (entry.status === "uploading") return t("file.dropStatusUploading");
    if (entry.status === "pending") return t("file.dropStatusPending");
    if (entry.failure === "badExtension") return t("file.badExtension");
    if (entry.failure === "fileTooLarge") return t("file.fileTooLarge", { size: formatBytes(entry.limitBytes) });
    return entry.detail || t("file.uploadFail");
  };

  const dropSummary = summarizeDrop(dropEntries);

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
      <main
        className={`shell files-layout ${dragActive ? "files-drop-active" : ""}`}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={(event) => void onDrop(event)}
      >
        {/* 松手即上传的提示层：只在管理员拖入时出现；普通用户拖入刻意不给这个视觉承诺。 */}
        {/* The "release to upload" hint layer appears only while an admin is dragging; a plain
            user's drag is deliberately given no such visual promise. */}
        {dragActive ? (
          <div className="files-dropzone" role="status">
            <p className="files-dropzone-title">{t("file.dropActive")}</p>
            <p className="files-muted">{t("file.dropHint")}</p>
          </div>
        ) : null}
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
            {/* 拖拽入口的可见提示：拖放本身没有可点的按钮，不写一句说明就没有人会发现它。
                与上传按钮同条件渲染——普通用户看不到入口，拖入时会得到明确的无权限提示。 */}
            {/* A visible hint for the drag entry point: a drop target has no button to click, so
                without a line of copy nobody discovers it. Rendered under the same condition as
                the upload button, so a plain user sees no entry point and is told about the
                missing permission if they drop anyway. */}
            {canUpload ? <span className="files-drop-hint">{t("file.dropEntryHint")}</span> : null}
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

        {/* 拖拽结果清单：逐份文件列出状态与失败原因，并且只有用户自己关得掉。
            上传失败绝不能只体现在"列表里没多出东西"上——那份文件背后是一次真实的写操作。 */}
        {/* The drop result list: one line per file with its status and, on failure, the reason.
            Only the user can dismiss it. A failed upload must never show up merely as "the list
            did not grow": behind that file there was a real write. */}
        {dropEntries.length > 0 ? (
          <section className="files-drop-panel" aria-label={t("file.dropPanelTitle")} role="status">
            <div className="files-drop-panel-head">
              <span className="files-drop-summary">
                {dropSummary.busy
                  ? t("file.dropInProgress", { done: dropSummary.succeeded + dropSummary.failed, total: dropEntries.length })
                  : t("file.dropSummary", { succeeded: dropSummary.succeeded, failed: dropSummary.failed })}
              </span>
              <button type="button" className="files-link" onClick={() => setDropEntries([])} disabled={dropBusy}>
                {t("file.dropDismiss")}
              </button>
            </div>
            <ul className="files-drop-list">
              {dropEntries.map((entry) => (
                <li key={entry.id} className={`files-drop-item files-drop-${entry.status}`}>
                  <span className="files-drop-name">{entry.name}</span>
                  <span className="files-drop-status">{dropStatusLabel(entry)}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

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
            {/* 文件夹导航：只挂在"全部"标签下——"我的收藏/我的上传"是按 viewer 取数的个人视图，
                在那里再叠一层文件夹过滤只会让人以为"我的资料少了"。 */}
            {/* The folder bar lives under the "All" tab only: "my saved" and "my uploads" are
                viewer-scoped personal views, and stacking a folder filter on top of them would only
                make people think their own resources had gone missing. */}
            <FileFolderBar
              folders={folders}
              view={folderView}
              onSelect={(next) => {
                setFolderView(next);
                setPage(1);
              }}
              canManage={canUpload}
              busy={loading}
              onCreate={createFolder}
              onRename={renameFolder}
              onDelete={deleteFolder}
            />
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
        ) : items.length === 0 && levelFolders.length === 0 && upView === null ? (
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
                {/* 文件夹行独立成一个 tbody，并整体排在资料行之前。
                    之所以要有"行"这一形态：此前文件夹只以筛选胶囊的样子出现，与分类/标签共用同一套
                    外观，列表里一条文件夹记录都没有，于是"文件夹"在界面上根本不成其为一种东西。
                    这里给它图标 + "文件夹"字样 + 条目数 + 明确的进入动作，让第一眼就能与资料行分开。
                    Folder rows get a tbody of their own and sit above every resource row. The reason
                    is that folders used to exist only as filter-looking chips sharing the category
                    and tag styling, with no folder record anywhere in the list, so a folder was not
                    recognisable as a thing at all. Here it carries an icon, the word "folder", an
                    item count and an explicit way in, so a glance is enough to tell it from a file. */}
                <tbody className="files-folder-rows">
                  {upView !== null ? (
                    <tr className="files-row-up">
                      <td colSpan={8} className="files-cell-folder">
                        {/* 弹性布局放在内层 div：直接给这个带 colSpan 的 <td> 设 display:flex 会让它
                            不再是 table-cell，跨列失效（底色只覆盖两列、动作掉出动作列）。
                            The flex layout sits on an inner div: giving this colSpan-bearing <td>
                            display:flex would stop it being a table-cell and lose the span, leaving the
                            fill over two columns and the action outside the action column. */}
                        <div className="files-folder-main">
                          <button type="button" className="files-folder-up" onClick={() => enterFolder(upView)}>
                            <span aria-hidden="true">{"<"}</span> {t("file.folderUp")}
                          </button>
                          <span className="files-muted">{t("file.folderUpHint")}</span>
                        </div>
                      </td>
                      <td className="files-cell-actions" />
                    </tr>
                  ) : null}
                  {levelFolders.map((folder) => (
                    <tr key={`folder-${folder.id}`} className="files-row-folder">
                      <td colSpan={8} className="files-cell-folder">
                        {/* 同"返回上一级"行：flex 下沉到内层 div，保住 colSpan 的跨列。 */}
                        {/* As in the step-up row: flex moves to an inner div so the colSpan survives. */}
                        <div className="files-folder-main">
                          <button
                            type="button"
                            className="files-folder-open"
                            onClick={() => enterFolder(folder.id)}
                            aria-label={t("file.folderEnterNamed", { name: folder.name })}
                          >
                            {/* 文件夹图标用内联 SVG 而不是字符：字符图标在不同字体下可能被渲染成彩色 emoji。
                                The folder icon is an inline SVG rather than a glyph, which some fonts
                                render as a colour emoji. */}
                            <span className="files-folder-icon" aria-hidden="true">
                              <svg viewBox="0 0 24 24">
                                <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2c.4 0 .78.16 1.06.44L11 6.5h8.5A1.5 1.5 0 0 1 21 8v9.5A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z" />
                              </svg>
                            </span>
                            <span className="files-folder-name">{folder.name}</span>
                          </button>
                          <span className="files-folder-kind">{t("file.folderKind")}</span>
                          <span className="files-folder-count">
                            {t("file.folderItemCount", { count: folder.resourceCount })}
                          </span>
                          {folder.childCount > 0 ? (
                            <span className="files-muted">{t("file.folderChildCount", { count: folder.childCount })}</span>
                          ) : null}
                          {folder.description ? (
                            <span className="files-muted files-folder-desc">{folder.description}</span>
                          ) : null}
                        </div>
                      </td>
                      <td className="files-cell-actions">
                        <div className="files-row-actions">
                          <button type="button" className="files-link" onClick={() => enterFolder(folder.id)}>
                            {t("file.folderEnter")}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="files-cell-title">
                        <a className="files-row-title" href={`/files/${item.id}`}>{item.title}</a>
                        {item.isPublic ? (
                          <span className="files-badge files-badge-public">{t("file.openToPublic")}</span>
                        ) : null}
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
                        {/* 行内操作组：下载 / 收藏 / 编辑 / 删除 并列同位（管理员点名要求编辑出现在这一组）。
                            排布取舍：四个动作排成一行按钮组，原有的"所属文件夹"下拉下沉到自己的一行——
                            下拉带标签文字、天然比按钮宽，与本按钮组同排会把这一列撑爆、并在窄屏下换行错乱。
                            The in-row action group: download, save, edit and delete sit side by side,
                            with edit in this very group as the administrator asked. Layout trade-off:
                            the four actions form one button row and the pre-existing "folder" select
                            drops to a line of its own, because a labelled select is inherently wider
                            than a button and sharing the row would stretch this column and wrap badly
                            on narrow screens. */}
                        <div className="files-row-actions">
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
                          {/* 编辑入口：与"移动/删除"同一权限口径（后端 FILE_UPDATE）。
                              列表只有摘要，所以点击时先取详情（见 openEditor），加载中禁用避免重复请求。 */}
                          {/* The edit entry point, under the same rule as moving and deleting (the
                              backend's FILE_UPDATE). A list row is only a summary, so the click
                              fetches the detail first (see openEditor) and stays disabled while that
                              is in flight, which also bars a double request. */}
                          {canFileResource(item) ? (
                            <button
                              type="button"
                              className="files-link"
                              disabled={editorLoadingId === item.id}
                              onClick={() => void openEditor(item)}
                            >
                              {editorLoadingId === item.id ? t("common.loading") : t("file.edit")}
                            </button>
                          ) : null}
                          {canFileResource(item) && confirmDeleteId !== item.id ? (
                            <button
                              type="button"
                              className="files-link files-danger-link"
                              onClick={() => {
                                setActionError("");
                                setConfirmDeleteId(item.id);
                              }}
                            >
                              {t("file.delete")}
                            </button>
                          ) : null}
                        </div>
                        {/* 删除的两段式确认：先武装，再确认。删除不可逆，单次点击即删会让误触毁掉一份资料；
                            确认态必须把后果写在按钮旁边，而不是让用户去猜。 */}
                        {/* Two-step delete: arm, then confirm. Deletion is irreversible and a
                            single-click delete would let a stray click destroy a resource, so the
                            confirming state states the consequence next to the button rather than
                            leaving the user to guess it. */}
                        {canFileResource(item) && confirmDeleteId === item.id ? (
                          <div className="files-row-confirm">
                            <span className="files-folder-warn">{t("file.deleteWarning", { title: item.title })}</span>
                            <button type="button" className="files-danger" onClick={() => void deleteResource(item)}>
                              {t("file.deleteConfirm")}
                            </button>
                            <button type="button" className="files-link" onClick={() => setConfirmDeleteId(null)}>
                              {t("common.cancel")}
                            </button>
                          </div>
                        ) : null}
                        {/* 移动到文件夹：只在有写权限的行上出现（管理员或该资料的 owner），
                            与后端 FILE_UPDATE 同一口径。 */}
                        {/* Move to folder: shown only on rows the viewer may write (an admin or the
                            resource's owner), the very same rule as the backend's FILE_UPDATE. */}
                        {canFileResource(item) ? (
                          <label className="files-move">
                            <span className="files-muted">{t("file.folderMoveTo")}</span>
                            <select
                              value={item.folderId === null || item.folderId === undefined ? "" : String(item.folderId)}
                              aria-label={t("file.folderMoveTo")}
                              disabled={loading}
                              onChange={(event) => void moveResource(item, event.target.value)}
                            >
                              <option value="">{t("file.folderUnfiled")}</option>
                              {flattenFolders(folders).map(({ folder }) => (
                                <option key={folder.id} value={String(folder.id)}>
                                  {folderPathLabel(folders, folder.id)}
                                </option>
                              ))}
                            </select>
                          </label>
                        ) : null}
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

      {/* 行内编辑器。保存成功后既回填弹窗（用服务端的归一化结果，而不是本地输入），
          也刷新列表：标题、分类、标签、状态都直接体现在列表的列上，不刷新就会出现
          "保存成功但列表还是旧值"的假象。 */}
      {/* The in-row editor. A successful save writes the response back into the dialog (the server's
          normalised result rather than the local input) and refreshes the list: the title, category,
          tags and status all show up as list columns, so skipping the refresh would fake a "saved but
          the list still shows the old value" state. */}
      {editorDetail ? (
        <FileMetadataDialog
          detail={editorDetail}
          onClose={() => setEditorDetail(null)}
          onSaved={(saved) => {
            setEditorDetail(saved);
            setRefreshToken((value) => value + 1);
          }}
        />
      ) : null}
    </AppShell>
  );
}

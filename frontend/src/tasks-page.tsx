import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ConfirmDialog, Dialog } from "samryetha-ui-commons";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { api, ApiError, type TaskComment, type TaskItem, type TaskPriority, type TaskStatus } from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { MathText } from "./lib/math-text";
import { SDropdown } from "./s-dropdown";

// 论坛内的任务页（与 Feedback 同级，仅管理员可见）。
// In-forum Tasks page (same level as Feedback, admins only).

type PriorityFilter = "" | TaskPriority;
type SortKey = "latest" | "urgent" | "oldest";

const PRIORITY_KEYS: Record<TaskPriority, I18nKey> = { urgent: "task.urgent", normal: "task.normal" };
// 评论嵌套沿用主站回复的深度 clamp：d4 之后缩进收窄。
const MAX_TASK_COMMENT_DEPTH = 4;

export function TasksPage() {
  const { user, loading } = useAuth();
  const { locale, t } = useI18n();

  const [items, setItems] = useState<TaskItem[]>([]);
  const [canWrite, setCanWrite] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadError, setLoadError] = useState("");

  const [category, setCategory] = useState("All");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<PriorityFilter>("");
  const [sort, setSort] = useState<SortKey>("latest");
  const [scope, setScope] = useState<"all" | "mine">("all");

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TaskItem | null>(null);
  const [form, setForm] = useState<{ category: string; title: string; notes: string; priority: TaskPriority }>({
    category: "General",
    title: "",
    notes: "",
    priority: "normal",
  });
  const [formError, setFormError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<TaskItem | null>(null);
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null);
  const [commentsByItem, setCommentsByItem] = useState<Record<number, TaskComment[]>>({});
  const [commentsFailed, setCommentsFailed] = useState<Record<number, boolean>>({});
  const [commentDraft, setCommentDraft] = useState("");
  const [replyingTo, setReplyingTo] = useState<number | null>(null);
  const [opError, setOpError] = useState("");

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  const isAdmin = user?.role === "admin";

  const load = async () => {
    setLoadingItems(true);
    setLoadError("");
    try {
      const data = await api.tasks.list();
      if (!mountedRef.current) return;
      setItems(data.items);
      setCanWrite(data.canWrite);
    } catch {
      if (mountedRef.current) setLoadError(t("task.loadFail"));
    } finally {
      if (mountedRef.current) setLoadingItems(false);
    }
  };

  useEffect(() => {
    if (!isAdmin) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  const me = user?.id;

  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      if (item.status === "open") counts.set(item.category, (counts.get(item.category) ?? 0) + 1);
    }
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [items]);

  const visible = useMemo(() => {
    const kw = query.trim().toLowerCase();
    let list = items.filter((i) => {
      if (category !== "All" && i.category !== category) return false;
      if (scope === "mine" && i.author.id !== me) return false;
      if (priority && i.priority !== priority) return false;
      if (kw) {
        if (!i.title.toLowerCase().includes(kw) && !i.notes.toLowerCase().includes(kw) && !i.author.handle.toLowerCase().includes(kw)) return false;
      }
      return true;
    });
    if (sort === "urgent") list = [...list].sort((a, b) => (a.priority === b.priority ? b.createdAt - a.createdAt : a.priority === "urgent" ? -1 : 1));
    else if (sort === "oldest") list = [...list].sort((a, b) => a.createdAt - b.createdAt);
    else list = [...list].sort((a, b) => b.createdAt - a.createdAt);
    return list;
  }, [items, category, scope, priority, query, sort, me]);

  const openItems = visible.filter((i) => i.status === "open");
  const closedItems = visible.filter((i) => i.status !== "open");
  const stats = useMemo(
    () => ({
      total: items.length,
      open: items.filter((i) => i.status === "open").length,
      urgent: items.filter((i) => i.status === "open" && i.priority === "urgent").length,
      done: items.filter((i) => i.status === "done").length,
    }),
    [items],
  );

  if (loading) {
    return (
      <AppShell current="tasks">
        <main className="shell feedback-layout">
          <section className="feedback-main"><Loading /></section>
        </main>
      </AppShell>
    );
  }

  if (!user || !isAdmin) {
    return (
      <AppShell current="tasks">
        <main className="shell feedback-layout">
          <section className="feedback-main">
            <div className="empty-state">
              {user ? t("adm.forbidden") : <>{t("adm.signInRequired")} <a className="sender" href="/login">{t("adm.signIn")}</a></>}
            </div>
          </section>
        </main>
      </AppShell>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setForm({ category: category === "All" ? "General" : category, title: "", notes: "", priority: "normal" });
    setFormError("");
    setModalOpen(true);
  };

  const openEdit = (item: TaskItem) => {
    setEditing(item);
    setForm({ category: item.category, title: item.title, notes: item.notes, priority: item.priority });
    setFormError("");
    setModalOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.title.trim()) {
      setFormError(t("task.titleRequired"));
      return;
    }
    try {
      const body = { category: form.category.trim() || "General", title: form.title.trim(), notes: form.notes, priority: form.priority };
      if (editing) await api.tasks.update(editing.id, body);
      else await api.tasks.create(body);
      setModalOpen(false);
      await load();
    } catch (err) {
      if (mountedRef.current) setFormError(err instanceof ApiError ? err.message : t("task.saveFail"));
    }
  };

  const setStatus = async (item: TaskItem, status: TaskStatus) => {
    try {
      const updated = await api.tasks.setStatus(item.id, status);
      if (!mountedRef.current) return;
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("task.statusFail"));
    }
  };

  const confirmDeleteAction = async () => {
    if (!confirmDelete) return;
    try {
      await api.tasks.del(confirmDelete.id);
      if (!mountedRef.current) return;
      setItems((prev) => prev.filter((i) => i.id !== confirmDelete.id));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("task.deleteFail"));
    }
    setConfirmDelete(null);
  };

  const loadComments = async (taskId: number) => {
    try {
      const data = await api.tasks.comments(taskId);
      if (!mountedRef.current) return;
      setCommentsByItem((prev) => ({ ...prev, [taskId]: data.items }));
      setCommentsFailed((prev) => ({ ...prev, [taskId]: false }));
    } catch {
      if (mountedRef.current) setCommentsFailed((prev) => ({ ...prev, [taskId]: true }));
    }
  };

  const toggleComments = (taskId: number) => {
    if (expandedItemId === taskId) {
      setExpandedItemId(null);
      return;
    }
    setExpandedItemId(taskId);
    setReplyingTo(null);
    setCommentDraft("");
    void loadComments(taskId);
  };

  const submitComment = async (taskId: number, parentCommentId: number | null) => {
    if (!commentDraft.trim()) return;
    try {
      await api.tasks.createComment(taskId, { body: commentDraft.trim(), parentCommentId });
      if (!mountedRef.current) return;
      setCommentDraft("");
      setReplyingTo(null);
      setOpError("");
      await loadComments(taskId);
    } catch {
      if (mountedRef.current) setOpError(t("fb.commentFail"));
    }
  };

  const renderCommentsSection = (item: TaskItem): ReactNode => {
    const itemComments = commentsByItem[item.id] ?? [];
    const byParent = new Map<number | null, TaskComment[]>();
    for (const c of itemComments) {
      const group = byParent.get(c.parentCommentId) ?? [];
      group.push(c);
      byParent.set(c.parentCommentId, group);
    }
    const renderNested = (parentId: number | null, depth: number): ReactNode => {
      const children = byParent.get(parentId) ?? [];
      if (children.length === 0) return null;
      return (
        <div className={parentId === null ? "reply-children is-root" : "reply-children"}>
          {children.map((c) => (
            <div className={`rnode fb-comment ${depth === 0 ? "top" : "nested"} d${Math.min(depth, MAX_TASK_COMMENT_DEPTH)}`} key={c.id}>
              <div className="fb-comment-head">
                <b>{c.author.handle}</b> · {timeAgo(c.createdAt, locale)}
                <button type="button" className="reply-action" onClick={() => setReplyingTo(c.id)}>{t("fb.reply")}</button>
              </div>
              <div className="fb-comment-body"><MathText>{c.body}</MathText></div>
              {renderNested(c.id, depth + 1)}
            </div>
          ))}
        </div>
      );
    };
    return (
      <div className="fb-comments">
        {itemComments.length === 0 && (
          commentsFailed[item.id]
            ? <div className="empty-state">{t("fb.commentsLoadFail")} <button type="button" className="reply-action" onClick={() => void loadComments(item.id)}>{t("common.retry")}</button></div>
            : <div className="empty-state">{t("fb.noComments")}</div>
        )}
        {renderNested(null, 0)}
        <div className="fb-comment-form">
          {replyingTo !== null && (
            <span className="replying-banner">
              {t("fb.replyingToComment")} <button type="button" className="reply-cancel" onClick={() => setReplyingTo(null)}>{t("fb.cancel")}</button>
            </span>
          )}
          <textarea value={commentDraft} onChange={(e) => setCommentDraft(e.target.value)} rows={2} maxLength={5000} placeholder={replyingTo !== null ? t("fb.writeReply") : t("fb.writeComment")} />
          <button type="button" className="primary-action" disabled={!commentDraft.trim()} onClick={() => void submitComment(item.id, replyingTo)}>{t("fb.postComment")}</button>
        </div>
      </div>
    );
  };

  const renderRow = (item: TaskItem) => (
    <Fragment key={item.id}>
      <div className="admin-row">
        <div className="admin-row-main">
          <strong>
            <span className="fb-seq">#{item.id}</span> {item.title}
          </strong>
          <div className="admin-row-tags">
            {category === "All" && item.category !== "General" ? <span className="feedback-tag">{item.category}</span> : null}
            {item.priority === "urgent" ? <span className="feedback-tag feedback-tag-urgent">{t("task.urgent")}</span> : null}
            {item.status !== "open" ? <span className="admin-badge done">{t("task.done")}</span> : null}
            <span className="admin-muted">
              {t("task.by")} <b>{item.author.handle}</b> · {timeAgo(item.createdAt, locale)}
              {item.doneAt ? ` · ${t("task.done")} ${timeAgo(item.doneAt, locale)}` : ""}
            </span>
          </div>
          {item.notes ? <span className="admin-muted fb-detail"><MathText>{item.notes}</MathText></span> : null}
        </div>
        <div className="admin-row-actions">
          {item.status === "open" ? (
            <button className="admin-btn" type="button" onClick={() => void setStatus(item, "done")}>{t("task.markDone")}</button>
          ) : (
            <button className="admin-btn" type="button" onClick={() => void setStatus(item, "open")}>{t("task.markOpen")}</button>
          )}
          <button className="admin-btn" type="button" onClick={() => toggleComments(item.id)}>{t("fb.comments")}</button>
          <button className="admin-btn" type="button" onClick={() => openEdit(item)}>{t("task.edit")}</button>
          <ConfirmDialog
            open={confirmDelete?.id === item.id}
            onOpenChange={(o) => !o && setConfirmDelete(null)}
            trigger={<button className="admin-btn danger" type="button" onClick={() => setConfirmDelete(item)}>{t("task.delete")}</button>}
            title={t("task.deleteTitle")}
            description={t("task.deleteDesc", { title: item.title })}
            cancelLabel={t("task.cancel")}
            confirmLabel={t("task.delete")}
            onConfirm={() => void confirmDeleteAction()}
          />
        </div>
      </div>
      {expandedItemId === item.id && renderCommentsSection(item)}
    </Fragment>
  );

  return (
    <AppShell current="tasks">
      <main className="shell feedback-layout">
        <aside className="feedback-sidebar">
          <h1>{t("task.tasks")}</h1>
          <button className="primary-action feedback-submit" type="button" onClick={openCreate} disabled={!canWrite}>{t("task.newTask")}</button>
          <nav className="feedback-projects" aria-label={t("task.groups")}>
            <button
              className={`feedback-project ${category === "All" ? "active" : ""}`}
              type="button"
              aria-current={category === "All" ? "page" : undefined}
              onClick={() => setCategory("All")}
            >
              <span>{t("task.all")}</span>
              <small>{stats.open}</small>
            </button>
            {categories.map(([name, count]) => (
              <button
                key={name}
                className={`feedback-project ${category === name ? "active" : ""}`}
                type="button"
                aria-current={category === name ? "page" : undefined}
                onClick={() => setCategory(name)}
              >
                <span>{name}</span>
                <small>{count}</small>
              </button>
            ))}
          </nav>
        </aside>

        <section className="feedback-main">
          {loadingItems ? (
            <Loading />
          ) : loadError ? (
            <div className="empty-state">{loadError}</div>
          ) : (
            <>
              <div className="admin-stat-grid">
                <div className="admin-stat"><strong>{stats.total}</strong><span>{t("task.total")}</span></div>
                <div className="admin-stat"><strong>{stats.open}</strong><span>{t("task.openStatus")}</span></div>
                <div className="admin-stat"><strong>{stats.urgent}</strong><span>{t("task.urgent")}</span></div>
                <div className="admin-stat"><strong>{stats.done}</strong><span>{t("task.done")}</span></div>
              </div>

              <div className="admin-filters">
                <label className="admin-search">
                  <span className="sr-only">{t("task.searchTasks")}</span>
                  <input type="search" placeholder={t("task.searchPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
                </label>
                <SDropdown
                  items={["", "urgent", "normal"] as PriorityFilter[]}
                  value={priority}
                  onChange={setPriority}
                  getKey={(item) => item || "all-priority"}
                  getLabel={(item) => item ? t(PRIORITY_KEYS[item]) : t("task.allPriority")}
                  ariaLabel={t("task.filterPriority")}
                  className="admin-dropdown"
                />
                <SDropdown
                  items={["latest", "urgent", "oldest"] as SortKey[]}
                  value={sort}
                  onChange={setSort}
                  getKey={(item) => item}
                  getLabel={(item) => t(item === "latest" ? "task.latestFirst" : item === "urgent" ? "task.urgentFirst" : "task.oldestFirst")}
                  ariaLabel={t("task.sort")}
                  className="admin-dropdown"
                />
                <div className="admin-pills">
                  <button className={`admin-pill ${scope === "all" ? "active" : ""}`} type="button" onClick={() => setScope("all")}>{t("task.all")}</button>
                  <button className={`admin-pill ${scope === "mine" ? "active" : ""}`} type="button" onClick={() => setScope("mine")}>{t("task.mine")}</button>
                </div>
              </div>

              <div className="admin-list content-fade">
                {opError && <p className="notice" role="alert">{opError}</p>}
                {openItems.length === 0 ? <div className="empty-state">{items.length ? t("task.allDone") : t("task.noTasks")}</div> : openItems.map(renderRow)}
              </div>

              {closedItems.length > 0 && (
                <details className="feedback-closed">
                  <summary>{t("task.completed", { count: closedItems.length })}</summary>
                  <div className="admin-list">{closedItems.map(renderRow)}</div>
                </details>
              )}
            </>
          )}
        </section>
      </main>

      <Dialog
        open={modalOpen}
        onOpenChange={setModalOpen}
        title={editing ? t("task.editTask") : t("task.newTaskTitle")}
        contentClassName="feedback-modal"
        contentProps={{ "aria-label": editing ? t("task.editTask") : t("task.newTaskTitle") }}
      >
        <form onSubmit={submit}>
          <label className="form-field">
            <span>{t("task.title")}</span>
            <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={120} placeholder={t("task.titlePlaceholder")} />
          </label>
          <div className="feedback-field-row">
            <label className="form-field">
              <span>{t("task.group")}</span>
              <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={40} placeholder={t("task.groupPlaceholder")} />
            </label>
            <SDropdown
              items={["normal", "urgent"] as TaskPriority[]}
              value={form.priority}
              onChange={(value) => setForm({ ...form, priority: value })}
              getKey={(item) => item}
              getLabel={(item) => t(PRIORITY_KEYS[item])}
              label={t("task.priority")}
              ariaLabel={t("task.taskPriority")}
              className="form-dropdown"
            />
          </div>
          <label className="form-field">
            <span>{t("task.notes")}</span>
            <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength={5000} rows={5} placeholder={t("task.notesPlaceholder")} />
          </label>
          {formError && <div className="dialog-error">{formError}</div>}
          <div className="dialog-actions">
            <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>{t("task.cancel")}</button>
            <button type="submit" className="primary-action">{t("task.save")}</button>
          </div>
        </form>
      </Dialog>
    </AppShell>
  );
}

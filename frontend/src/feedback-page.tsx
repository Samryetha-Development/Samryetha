import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import {
  api,
  ApiError,
  type FeedbackComment,
  type FeedbackItem,
  type FeedbackProjectSummary,
  type FeedbackStatus,
  type FeedbackType,
  type FeedbackUrgency,
} from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { SDropdown } from "./s-dropdown";

type TypeFilter = "" | FeedbackType;
type UrgencyFilter = "" | FeedbackUrgency;
type SortKey = "latest" | "urgent" | "oldest";

const TYPE_KEYS: Record<FeedbackType, I18nKey> = { bug: "fb.bug", suggestion: "fb.suggestion" };
const URGENCY_KEYS: Record<FeedbackUrgency, I18nKey> = { urgent: "fb.urgent", normal: "fb.normal" };
const STATUS_KEYS: Record<FeedbackStatus, I18nKey> = { open: "fb.open", done: "fb.done", expired: "fb.expired" };
// 反馈评论嵌套同样压平：4 层后不再缩进（与帖子回复一致），深层不丢、横向不爆
const MAX_FB_COMMENT_DEPTH = 4;

export function FeedbackPage() {
  const { user, loading } = useAuth();
  const { locale, t } = useI18n();
  const [projects, setProjects] = useState<FeedbackProjectSummary[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<number | null>(null);
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadError, setLoadError] = useState("");

  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("");
  const [urgencyFilter, setUrgencyFilter] = useState<UrgencyFilter>("");
  const [sort, setSort] = useState<SortKey>("latest");
  const [scope, setScope] = useState<"all" | "mine">("all");

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<FeedbackItem | null>(null);
  const [form, setForm] = useState({ title: "", detail: "", type: "suggestion" as FeedbackType, urgency: "normal" as FeedbackUrgency });
  const [formError, setFormError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<FeedbackItem | null>(null);
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null);
  const [commentsByItem, setCommentsByItem] = useState<Record<number, FeedbackComment[]>>({});
  const [commentsFailed, setCommentsFailed] = useState<Record<number, boolean>>({});
  const [commentDraft, setCommentDraft] = useState("");
  const [replyingTo, setReplyingTo] = useState<number | null>(null);
  const [opError, setOpError] = useState("");

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  useEffect(() => {
    if (!user) return;
    api.feedback
      .myProjects()
      .then(({ items: list }) => {
        setProjects(list);
        if (list.length) setCurrentProjectId((prev) => (list.some((p) => p.id === prev) ? prev : list[0].id));
      })
      .catch(() => setProjects([]));
  }, [user]);

  useEffect(() => {
    if (currentProjectId == null) {
      setItems([]);
      return;
    }
    let alive = true;
    setLoadingItems(true);
    setLoadError("");
    api.feedback
      .list(currentProjectId)
      .then((data) => {
        if (!alive) return;
        setItems(data.items);
        setCanManage(data.canManage);
      })
      .catch(() => {
        if (alive) setLoadError(t("fb.loadFail"));
      })
      .finally(() => {
        if (alive) setLoadingItems(false);
      });
    return () => {
      alive = false;
    };
  }, [currentProjectId]);

  const me = user?.id;

  const visible = useMemo(() => {
    const kw = query.trim().toLowerCase();
    let list = items.filter((i) => {
      if (scope === "mine" && i.author.id !== me) return false;
      if (typeFilter && i.type !== typeFilter) return false;
      if (urgencyFilter && i.urgency !== urgencyFilter) return false;
      if (kw) {
        if (!i.title.toLowerCase().includes(kw) && !i.detail.toLowerCase().includes(kw) && !i.author.handle.toLowerCase().includes(kw)) return false;
      }
      return true;
    });
    if (sort === "urgent") list = [...list].sort((a, b) => (a.urgency === b.urgency ? b.createdAt - a.createdAt : a.urgency === "urgent" ? -1 : 1));
    else if (sort === "oldest") list = [...list].sort((a, b) => a.createdAt - b.createdAt);
    else list = [...list].sort((a, b) => b.createdAt - a.createdAt);
    return list;
  }, [items, query, typeFilter, urgencyFilter, sort, scope, me]);

  const openItems = visible.filter((i) => i.status === "open");
  const closedItems = visible.filter((i) => i.status !== "open");
  const stats = useMemo(
    () => ({
      bug: items.filter((i) => i.type === "bug").length,
      suggestion: items.filter((i) => i.type === "suggestion").length,
      urgent: items.filter((i) => i.urgency === "urgent").length,
      done: items.filter((i) => i.status === "done").length,
      expired: items.filter((i) => i.status === "expired").length,
    }),
    [items],
  );

  if (!loading && !user) {
    return (
      <AppShell>
        <main className="shell feedback-layout">
          <section className="feedback-main">
            <div className="empty-state">
              {t("fb.signInToView")} <a className="sender" href="/login">{t("fb.signIn")}</a>
            </div>
          </section>
        </main>
      </AppShell>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setForm({ title: "", detail: "", type: "suggestion", urgency: "normal" });
    setFormError("");
    setModalOpen(true);
  };

  const openEdit = (item: FeedbackItem) => {
    setEditing(item);
    setForm({ title: item.title, detail: item.detail, type: item.type, urgency: item.urgency });
    setFormError("");
    setModalOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.title.trim()) {
      setFormError(t("fb.titleRequired"));
      return;
    }
    try {
      if (editing) {
        await api.feedback.update(editing.id, { title: form.title.trim(), detail: form.detail, type: form.type, urgency: form.urgency });
      } else {
        if (currentProjectId == null) {
          setFormError(t("fb.noProject"));
          return;
        }
        await api.feedback.create({ projectId: currentProjectId, title: form.title.trim(), detail: form.detail, type: form.type, urgency: form.urgency });
      }
      setModalOpen(false);
      if (currentProjectId != null) {
        const data = await api.feedback.list(currentProjectId);
        if (!mountedRef.current) return;
        setItems(data.items);
        setCanManage(data.canManage);
      }
    } catch (err) {
      if (mountedRef.current) setFormError(err instanceof ApiError ? err.message : t("fb.saveFail"));
    }
  };

  const setStatus = async (item: FeedbackItem, status: FeedbackStatus) => {
    try {
      const updated = await api.feedback.setStatus(item.id, status);
      if (!mountedRef.current) return;
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("fb.statusFail"));
    }
  };

  const confirmDeleteAction = async () => {
    if (!confirmDelete) return;
    try {
      await api.feedback.del(confirmDelete.id);
      if (!mountedRef.current) return;
      setItems((prev) => prev.filter((i) => i.id !== confirmDelete.id));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("fb.deleteFail"));
    }
    setConfirmDelete(null);
  };

  const loadComments = async (itemId: number) => {
    try {
      const data = await api.feedback.comments(itemId);
      if (!mountedRef.current) return;
      setCommentsByItem((prev) => ({ ...prev, [itemId]: data.items }));
      setCommentsFailed((prev) => ({ ...prev, [itemId]: false }));
    } catch {
      if (mountedRef.current) setCommentsFailed((prev) => ({ ...prev, [itemId]: true }));
    }
  };

  const toggleComments = (itemId: number) => {
    if (expandedItemId === itemId) {
      setExpandedItemId(null);
      return;
    }
    setExpandedItemId(itemId);
    setReplyingTo(null);
    setCommentDraft("");
    void loadComments(itemId);
  };

  const submitComment = async (itemId: number, parentCommentId: number | null) => {
    if (!commentDraft.trim()) return;
    try {
      await api.feedback.createComment(itemId, { body: commentDraft.trim(), parentCommentId });
      if (!mountedRef.current) return;
      setCommentDraft("");
      setReplyingTo(null);
      setOpError("");
      await loadComments(itemId);
    } catch {
      if (mountedRef.current) setOpError(t("fb.commentFail"));
    }
  };

  const renderCommentsSection = (item: FeedbackItem): ReactNode => {
    // 按 parentCommentId 分组，递归渲染嵌套评论（与帖子回复一致）
    // Group by parentCommentId and render nested comments recursively (consistent with post replies)
    const itemComments = commentsByItem[item.id] ?? [];
    const byParent = new Map<number | null, FeedbackComment[]>();
    for (const c of itemComments) {
      const group = byParent.get(c.parentCommentId) ?? [];
      group.push(c);
      byParent.set(c.parentCommentId, group);
    }
    const renderNested = (parentId: number | null, depth: number): ReactNode => (
      <>
        {(byParent.get(parentId) ?? []).map((c) => (
          <div className="fb-comment" key={c.id} style={{ marginLeft: Math.min(depth, MAX_FB_COMMENT_DEPTH) * 18 }}>
            <div className="fb-comment-head">
              <b>{c.author.handle}</b> · {timeAgo(c.createdAt, locale)}
              <button type="button" className="reply-action" onClick={() => setReplyingTo(c.id)}>{t("fb.reply")}</button>
            </div>
            <div className="fb-comment-body">{c.body}</div>
            {renderNested(c.id, depth + 1)}
          </div>
        ))}
      </>
    );
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

  const renderRow = (item: FeedbackItem) => {
    const isOwner = item.author.id === me;
    const canEdit = isOwner || canManage;
    return (
      <Fragment key={item.id}>
      <div className="admin-row">
        <div className="admin-row-main">
          <strong>
            <span className="fb-seq">#{item.seq}</span> {item.title}
          </strong>
          <div className="admin-row-tags">
            <span className={`feedback-tag feedback-tag-${item.type}`}>{t(TYPE_KEYS[item.type])}</span>
            {item.urgency === "urgent" ? <span className="feedback-tag feedback-tag-urgent">{t("fb.urgent")}</span> : null}
            {item.status !== "open" ? <span className={`admin-badge ${item.status}`}>{t(STATUS_KEYS[item.status])}</span> : null}
            <span className="admin-muted">
              {t("fb.by")} <b>{item.author.handle}</b> · {timeAgo(item.createdAt, locale)}
              {item.closedAt ? ` · ${t("fb.closedAt", { time: timeAgo(item.closedAt, locale) })}` : ""}
              {item.editedAt ? ` · ${t("fb.editedAt", { time: timeAgo(item.editedAt, locale) })}` : ""}
            </span>
          </div>
          {item.detail ? <span className="admin-muted fb-detail">{item.detail}</span> : null}
        </div>
        <div className="admin-row-actions">
          {canManage && item.status === "open" && (
            <>
              <button className="admin-btn" type="button" onClick={() => void setStatus(item, "done")}>{t("fb.markDone")}</button>
              <button className="admin-btn" type="button" onClick={() => void setStatus(item, "expired")}>{t("fb.expire")}</button>
            </>
          )}
          {canManage && item.status !== "open" && (
            <button className="admin-btn" type="button" onClick={() => void setStatus(item, "open")}>{t("fb.restore")}</button>
          )}
          <button className="admin-btn" type="button" onClick={() => toggleComments(item.id)}>{t("fb.comments")}</button>
          {canEdit && (
            <>
              <button className="admin-btn" type="button" onClick={() => openEdit(item)}>{t("fb.edit")}</button>
              <AlertDialog.Root open={confirmDelete?.id === item.id} onOpenChange={(o) => !o && setConfirmDelete(null)}>
                <AlertDialog.Trigger asChild>
                  <button className="admin-btn danger" type="button" onClick={() => setConfirmDelete(item)}>{t("fb.delete")}</button>
                </AlertDialog.Trigger>
                <AlertDialog.Portal>
                  <AlertDialog.Overlay className="dialog-overlay" />
                  <AlertDialog.Content className="dialog-content">
                    <AlertDialog.Title className="dialog-title">{t("fb.deleteTitle", { seq: item.seq })}</AlertDialog.Title>
                    <AlertDialog.Description className="dialog-description">
                      {t("fb.deleteDesc")}
                    </AlertDialog.Description>
                    <div className="dialog-actions">
                      <AlertDialog.Cancel asChild>
                        <button type="button" className="action-btn">{t("fb.cancel")}</button>
                      </AlertDialog.Cancel>
                      <AlertDialog.Action asChild>
                        <button type="button" className="dialog-danger" onClick={() => void confirmDeleteAction()}>{t("fb.delete")}</button>
                      </AlertDialog.Action>
                    </div>
                  </AlertDialog.Content>
                </AlertDialog.Portal>
              </AlertDialog.Root>
            </>
          )}
        </div>
      </div>
      {expandedItemId === item.id && renderCommentsSection(item)}
      </Fragment>
    );
  };

  return (
    <AppShell current="feedback">
      <main className="shell feedback-layout">
        <aside className="feedback-sidebar">
          <h1>{t("fb.feedback")}</h1>
          <button className="primary-action feedback-submit" type="button" onClick={openCreate} disabled={!currentProjectId}>{t("fb.submitFeedback")}</button>
          <nav className="feedback-projects" aria-label={t("fb.projects")}>
            {projects.map((p) => (
              <button
                key={p.id}
                className={`feedback-project ${currentProjectId === p.id ? "active" : ""}`}
                type="button"
                aria-current={currentProjectId === p.id ? "page" : undefined}
                onClick={() => setCurrentProjectId(p.id)}
              >
                <span>{p.name}</span>
                <small>{p.isProgrammer ? t("fb.programmer") : t("fb.members", { count: p.memberCount })}</small>
              </button>
            ))}
          </nav>
        </aside>

        <section className="feedback-main">
          {loadingItems ? (
            <Loading />
          ) : loadError ? (
            <div className="empty-state">{loadError}</div>
          ) : !currentProjectId ? (
            <div className="empty-state">
              {projects.length ? t("fb.selectProject") : t("fb.noProjects")}
            </div>
          ) : (
            <>
              <div className="admin-stat-grid">
                <div className="admin-stat"><strong>{items.length}</strong><span>{t("fb.total")}</span></div>
                <div className="admin-stat"><strong>{stats.bug}</strong><span>{t("fb.bugs")}</span></div>
                <div className="admin-stat"><strong>{stats.suggestion}</strong><span>{t("fb.suggestions")}</span></div>
                <div className="admin-stat"><strong>{stats.urgent}</strong><span>{t("fb.urgent")}</span></div>
                <div className="admin-stat"><strong>{stats.done}</strong><span>{t("fb.done")}</span></div>
                <div className="admin-stat"><strong>{stats.expired}</strong><span>{t("fb.expired")}</span></div>
              </div>

              <div className="admin-filters">
                <label className="admin-search">
                  <span className="sr-only">{t("fb.searchFeedback")}</span>
                  <input type="search" placeholder={t("fb.searchPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
                </label>
                <SDropdown
                  items={["", "bug", "suggestion"] as TypeFilter[]}
                  value={typeFilter}
                  onChange={setTypeFilter}
                  getKey={(item) => item || "all-types"}
                  getLabel={(item) => item ? t(TYPE_KEYS[item]) : t("fb.allTypes")}
                  ariaLabel={t("fb.filterType")}
                  className="admin-dropdown"
                />
                <SDropdown
                  items={["", "urgent", "normal"] as UrgencyFilter[]}
                  value={urgencyFilter}
                  onChange={setUrgencyFilter}
                  getKey={(item) => item || "all-urgency"}
                  getLabel={(item) => item ? t(URGENCY_KEYS[item]) : t("fb.allUrgency")}
                  ariaLabel={t("fb.filterUrgency")}
                  className="admin-dropdown"
                />
                <SDropdown
                  items={["latest", "urgent", "oldest"] as SortKey[]}
                  value={sort}
                  onChange={setSort}
                  getKey={(item) => item}
                  getLabel={(item) => t(item === "latest" ? "fb.latestFirst" : item === "urgent" ? "fb.urgentFirst" : "fb.oldestFirst")}
                  ariaLabel={t("fb.sort")}
                  className="admin-dropdown"
                />
                <div className="admin-pills">
                  <button className={`admin-pill ${scope === "all" ? "active" : ""}`} type="button" onClick={() => setScope("all")}>{t("fb.all")}</button>
                  <button className={`admin-pill ${scope === "mine" ? "active" : ""}`} type="button" onClick={() => setScope("mine")}>{t("fb.mine")}</button>
                </div>
              </div>

              <div className="admin-list content-fade">
                {opError && <p className="notice" role="alert">{opError}</p>}
                {openItems.length === 0 ? <div className="empty-state">{t("fb.noOpen")}</div> : openItems.map(renderRow)}
              </div>

              {closedItems.length > 0 && (
                <details className="feedback-closed">
                  <summary>{t("fb.closedSummary", { count: closedItems.length })}</summary>
                  <div className="admin-list">{closedItems.map(renderRow)}</div>
                </details>
              )}
            </>
          )}
        </section>
      </main>

      <Dialog.Root open={modalOpen} onOpenChange={setModalOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content feedback-modal" aria-label={editing ? t("fb.editFeedback", { seq: editing.seq }) : t("fb.submitFeedback")}>
            <Dialog.Title className="dialog-title">{editing ? t("fb.editFeedback", { seq: editing.seq }) : t("fb.submitFeedback")}</Dialog.Title>
            <form onSubmit={submit}>
              <label className="form-field">
                <span>{t("fb.title")}</span>
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={120} placeholder={t("fb.titlePlaceholder")} />
              </label>
              <div className="feedback-field-row">
                <SDropdown
                  items={["bug", "suggestion"] as FeedbackType[]}
                  value={form.type}
                  onChange={(type) => setForm({ ...form, type })}
                  getKey={(item) => item}
                  getLabel={(item) => t(TYPE_KEYS[item])}
                  label={t("fb.type")}
                  ariaLabel={t("fb.feedbackType")}
                  className="form-dropdown"
                />
                <SDropdown
                  items={["normal", "urgent"] as FeedbackUrgency[]}
                  value={form.urgency}
                  onChange={(urgency) => setForm({ ...form, urgency })}
                  getKey={(item) => item}
                  getLabel={(item) => t(URGENCY_KEYS[item])}
                  label={t("fb.urgency")}
                  ariaLabel={t("fb.feedbackUrgency")}
                  className="form-dropdown"
                />
              </div>
              <label className="form-field">
                <span>{t("fb.detail")}</span>
                <textarea value={form.detail} onChange={(e) => setForm({ ...form, detail: e.target.value })} maxLength={5000} rows={5} placeholder={t("fb.detailPlaceholder")} />
              </label>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>{t("fb.cancel")}</button>
                <button type="submit" className="primary-action">{t("fb.save")}</button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </AppShell>
  );
}

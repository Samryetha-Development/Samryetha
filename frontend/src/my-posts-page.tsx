import { useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { ScheduleDialog } from "./schedule-dialog";
import { ConfirmDialog } from "./ui-commons";
import { api, ApiError, type ScheduledPost, type ThreadSummary } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n, formatDateL } from "./lib/i18n";

export function MyPostsPage({ onNotify }: { onNotify: (message: string, tone: "success" | "error") => void }) {
  const { user, loading: authLoading } = useAuth();
  const { t, locale } = useI18n();
  const [tab, setTab] = useState<"scheduled" | "published">("scheduled");
  const [scheduled, setScheduled] = useState<ScheduledPost[]>([]);
  const [published, setPublished] = useState<ThreadSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [editing, setEditing] = useState<ScheduledPost | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const requestId = useRef(0);
  const dateTime = (stamp: number) => new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(stamp);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestId.current += 1; };
  }, []);

  useEffect(() => {
    if (!user) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    setCursor(null);
    setScheduled([]);
    setPublished([]);
    const request = tab === "scheduled" ? api.scheduledPosts.list() : api.ownPosts();
    void request.then((data) => {
      if (!mounted.current || requestId.current !== id) return;
      if (tab === "scheduled") setScheduled(data.items as ScheduledPost[]);
      else setPublished(data.items as ThreadSummary[]);
      setCursor(data.nextCursor);
    }).catch((err) => {
      if (mounted.current && requestId.current === id) setError(err instanceof ApiError ? err.message : t("myPosts.loadFail"));
    }).finally(() => { if (mounted.current && requestId.current === id) setLoading(false); });
    return () => { requestId.current += 1; };
  }, [user?.id, tab, retry]);

  const loadMore = async () => {
    if (!cursor || loading) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const data = tab === "scheduled" ? await api.scheduledPosts.list(cursor) : await api.ownPosts(cursor);
      if (!mounted.current || requestId.current !== id) return;
      if (tab === "scheduled") setScheduled((items) => [...items, ...data.items as ScheduledPost[]]);
      else setPublished((items) => [...items, ...data.items as ThreadSummary[]]);
      setCursor(data.nextCursor);
    } catch (err) {
      if (mounted.current && requestId.current === id) setError(err instanceof ApiError ? err.message : t("myPosts.loadFail"));
    } finally {
      if (mounted.current && requestId.current === id) setLoading(false);
    }
  };

  const changeTime = async (stamp: number) => {
    if (!editing || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const changed = await api.scheduledPosts.reschedule(editing.id, stamp);
      if (!mounted.current) return;
      setScheduled((items) => items.map((item) => item.id === changed.id ? changed : item));
      setEditing(null);
      onNotify(t("schedule.timeSaved"), "success");
    } catch (err) {
      if (mounted.current) setActionError(err instanceof ApiError ? err.message : t("schedule.changeFail"));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const cancel = async (post: ScheduledPost) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await api.scheduledPosts.cancel(post.id);
      if (!mounted.current) return;
      setScheduled((items) => items.filter((item) => item.id !== post.id));
      onNotify(t("schedule.cancelled"), "success");
    } catch (err) {
      if (mounted.current) onNotify(err instanceof ApiError ? err.message : t("schedule.cancelFail"), "error");
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return <AppShell current="my-posts"><main className="shell drafts-layout">
    <div className="drafts-heading"><div><h1 className="feed-title">{t("myPosts.title")}</h1><p>{t("myPosts.description")}</p></div><a className="primary-action" href="/post">{t("post.newDiscussion")}</a></div>
    {!authLoading && !user ? <div className="empty-state">{t("myPosts.signIn")} <a className="sender" href="/login">{t("post.signIn")}</a></div> : <>
      <div className="my-posts-toolbar"><div className="my-posts-tabs" role="group" aria-label={t("myPosts.status")}>
        <button type="button" className={tab === "scheduled" ? "active" : ""} aria-pressed={tab === "scheduled"} onClick={() => setTab("scheduled")}>{t("myPosts.scheduled")}</button>
        <button type="button" className={tab === "published" ? "active" : ""} aria-pressed={tab === "published"} onClick={() => setTab("published")}>{t("myPosts.published")}</button>
      </div><button className="draft-action" type="button" disabled={loading || busy} onClick={() => setRetry((n) => n + 1)}>{t("myPosts.refresh")}</button></div>
      {error && <div role="alert"><p className="form-error">{error}</p><button className="draft-action" type="button" onClick={() => { if (cursor) void loadMore(); else setRetry((n) => n + 1); }}>{t("common.retry")}</button></div>}
      {loading && scheduled.length + published.length === 0 && <p className="empty-state" role="status">{t("common.loading")}</p>}
      {!loading && !error && scheduled.length + published.length === 0 && <p className="empty-state">{t(tab === "scheduled" ? "myPosts.noScheduled" : "myPosts.noPublished")}</p>}
      <ul className="draft-list">{tab === "scheduled" ? scheduled.map((post) => <li className="draft-card" key={post.id}>
        <div className="draft-copy"><strong className="draft-title">{post.title}</strong><p className="draft-preview">{post.preview}</p>
          <div className="draft-meta"><span className={post.status === "failed" ? "schedule-locked" : ""}>{t(post.status === "failed" ? "schedule.failed" : "schedule.waiting")}</span><span>{dateTime(post.scheduledAt)}</span><span>{post.boardSlug}</span></div>
          <p className="schedule-hint">{t(post.canModify ? "schedule.editable" : "schedule.locked")}</p>
          {post.status === "failed" && <p className="form-error">{t("schedule.failureHint")}{post.lastError && ` ${post.lastError}`}</p>}
        </div>
        {post.canModify && (post.status === "pending" || post.status === "failed") && <div className="draft-card-actions"><button className="edit-profile" type="button" disabled={busy} onClick={() => { setActionError(null); setEditing(post); }}>{t("schedule.changeTime")}</button>
          <ConfirmDialog trigger={<button className="draft-action danger" type="button" disabled={busy}>{t("schedule.cancel")}</button>} title={t("schedule.cancelTitle")} description={t("schedule.cancelDescription")} cancelLabel={t("common.cancel")} confirmLabel={t("schedule.cancel")} onConfirm={() => void cancel(post)} pending={busy} />
        </div>}
      </li>) : published.map((post) => <li className="draft-card" key={post.id}><div className="draft-copy"><a className="draft-title" href={`/d/${post.id}`}>{post.title}</a><p className="draft-preview">{post.preview}</p><div className="draft-meta"><span>{formatDateL(post.createdAt, locale)}</span><span>{post.board.name}</span></div></div><a className="edit-profile" href={`/d/${post.id}`}>{t("myPosts.view")}</a></li>)}</ul>
      {cursor && <button className="edit-profile drafts-more" type="button" disabled={loading} onClick={() => void loadMore()}>{t(loading ? "common.loading" : "drafts.loadMore")}</button>}
      <p className="schedule-hint"><a className="sender" href="/drafts">{t("drafts.view")}</a></p>
    </>}
    {editing && <ScheduleDialog key={editing.id} open onOpenChange={(open) => { if (!open) setEditing(null); }} initialAt={editing.scheduledAt > Date.now() ? editing.scheduledAt : Date.now() + 3600_000} pending={busy} error={actionError} onConfirm={(stamp) => void changeTime(stamp)} />}
  </main></AppShell>;
}

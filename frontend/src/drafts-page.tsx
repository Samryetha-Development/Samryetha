import { useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "samryetha-ui-commons";
import { AppShell } from "./app-shell";
import { api, ApiError, type DraftSummary } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n, formatDateL } from "./lib/i18n";

export function DraftsPage({ onNotify }: { onNotify: (message: string, tone: "success" | "error") => void }) {
  const { user, loading: authLoading } = useAuth();
  const { t, locale } = useI18n();
  const [items, setItems] = useState<DraftSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [retry, setRetry] = useState(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!user) return;
    let alive = true;
    setLoading(true);
    setError(null);
    void api.drafts.list().then((data) => {
      if (!alive) return;
      setItems(data.items);
      setCursor(data.nextCursor);
    }).catch((err) => {
      if (alive) setError(err instanceof ApiError ? err.message : t("drafts.loadFail"));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [user?.id, retry]);

  const loadMore = async () => {
    if (!cursor || loading) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.drafts.list(cursor);
      if (!mounted.current) return;
      setItems((current) => [...current, ...data.items]);
      setCursor(data.nextCursor);
    } catch (err) {
      if (mounted.current) setError(err instanceof ApiError ? err.message : t("drafts.loadFail"));
    } finally {
      if (mounted.current) setLoading(false);
    }
  };

  const remove = async (id: number) => {
    if (deleting !== null) return;
    setDeleting(id);
    try {
      await api.drafts.del(id);
      if (!mounted.current) return;
      setItems((current) => current.filter((item) => item.id !== id));
      onNotify(t("drafts.deleted"), "success");
    } catch (err) {
      if (mounted.current) onNotify(err instanceof ApiError ? err.message : t("drafts.deleteFail"), "error");
    } finally {
      if (mounted.current) setDeleting(null);
    }
  };

  return <AppShell current="drafts">
    <main className="shell drafts-layout">
      <div className="drafts-heading"><div><h1 className="feed-title">{t("drafts.title")}</h1><p>{t("drafts.description")}</p></div><a className="primary-action" href="/post">{t("post.newDiscussion")}</a></div>
      {!authLoading && !user ? <div className="empty-state">{t("drafts.signIn")} <a className="sender" href="/login">{t("post.signIn")}</a></div> : <>
        {error && <div className="drafts-error" role="alert"><p className="form-error">{error}</p><button className="draft-action" type="button" onClick={() => { if (items.length && cursor) void loadMore(); else setRetry((n) => n + 1); }}>{t("common.retry")}</button></div>}
        {loading && items.length === 0 && <p className="empty-state" role="status">{t("common.loading")}</p>}
        {!loading && !error && items.length === 0 && <div className="empty-state">{t("drafts.empty")}</div>}
        <ul className="draft-list">{items.map((draft) => <li className="draft-card" key={draft.id}>
          <div className="draft-copy"><a className="draft-title" href={`/drafts/${draft.id}`}>{draft.title.trim() || t("drafts.untitled")}</a>{draft.preview && <p className="draft-preview">{draft.preview}</p>}
            <div className="draft-meta"><span>{t("drafts.updated", { date: formatDateL(draft.updatedAt, locale) })}</span>{draft.boardSlug && <span>{draft.boardSlug}</span>}{draft.attachmentCount > 0 && <span>{t("drafts.attachments", { count: draft.attachmentCount })}</span>}</div>
          </div>
          <div className="draft-card-actions"><a className="edit-profile" href={`/drafts/${draft.id}`}>{t("drafts.continue")}</a><ConfirmDialog
            trigger={<button className="draft-action danger" type="button" disabled={deleting !== null}>{t("common.delete")}</button>}
            title={t("drafts.deleteTitle")} description={t("drafts.deleteDescription")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")}
            pending={deleting === draft.id} onConfirm={() => void remove(draft.id)}
          /></div>
        </li>)}</ul>
        {cursor && <button className="edit-profile drafts-more" type="button" disabled={loading} onClick={() => void loadMore()}>{t(loading ? "common.loading" : "drafts.loadMore")}</button>}
      </>}
    </main>
  </AppShell>;
}

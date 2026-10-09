import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { EditorField } from "./editor-field";
import { PollFields, PollToggle, pollError, type PollComposition } from "./poll-editor";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat, type DraftInput } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { formatBytes } from "./lib/format";

const FALLBACK_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".pdf", ".txt", ".md", ".csv", ".zip", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mov", ".mp3", ".wav"]);
const FALLBACK_MAX_BYTES = 50 * 1024 * 1024;
const extensionOf = (name: string) => name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
const isImage = (name: string) => [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"].includes(extensionOf(name));
type PendingUpload = { id: number; filename: string; previewUrl: string };

export function PostPage({ draftId, onDraftSaved, onPublished }: {
  draftId?: number;
  onDraftSaved: (id: number) => void;
  onPublished: (id: number) => void;
}) {
  const { user, loading } = useAuth();
  const { t } = useI18n();
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [poll, setPoll] = useState<PollComposition | null>(null);
  const [format, setFormat] = useState<BodyFormat>("text");
  const [selectedBoard, setSelectedBoard] = useState<BoardSummary | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [draftLoading, setDraftLoading] = useState(true);
  const [draftLoadError, setDraftLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [boardUnavailable, setBoardUnavailable] = useState(false);
  const [savedInput, setSavedInput] = useState<string | null>(null);
  // 上传约束优先取后端 config，失败回退本地常量（后端仍权威校验）
  const [uploadCfg, setUploadCfg] = useState({ exts: FALLBACK_EXTENSIONS, maxBytes: FALLBACK_MAX_BYTES });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadControllers = useRef(new Map<number, AbortController>());
  const objectUrls = useRef(new Set<string>());
  const actionInFlight = useRef(false);

  useEffect(() => {
    if (loading || !user) return;
    let alive = true;
    setDraftLoadError(null);
    setDraftLoading(true);
    const boardRequest = api.boards.list().catch(() => {
      if (alive) setError(t("post.loadBoardsFail"));
      return { items: [] as BoardSummary[] };
    });
    void Promise.all([boardRequest, draftId === undefined ? Promise.resolve(null) : api.drafts.get(draftId)])
      .then(([data, draft]) => {
        if (!alive) return;
        setBoards(data.items);
        if (draft) {
          const restoredPoll = draft.poll ? { question: draft.poll.question ?? "", allowMultiple: draft.poll.allowMultiple ?? false, options: draft.poll.options } : null;
          setPoll(restoredPoll);
          setTitle(draft.title);
          setBody(draft.bodyMarkdown);
          setFormat(draft.bodyFormat);
          setSelectedBoard(data.items.find((board) => board.slug === draft.boardSlug) ?? null);
          setBoardUnavailable(Boolean(draft.boardSlug && !data.items.some((board) => board.slug === draft.boardSlug)));
          setPending(draft.attachments.map((item) => ({ id: item.id, filename: item.originalFilename, previewUrl: item.isImage ? item.downloadUrl : "" })));
          setSavedInput(JSON.stringify({ title: draft.title, bodyMarkdown: draft.bodyMarkdown, bodyFormat: draft.bodyFormat, boardSlug: draft.boardSlug, attachmentIds: draft.attachments.map((item) => item.id), poll: restoredPoll }));
        } else {
          setSelectedBoard((current) => current ?? data.items[0] ?? null);
        }
      })
      .catch((err) => { if (alive) setDraftLoadError(err instanceof ApiError ? err.message : t("drafts.loadFail")); })
      .finally(() => { if (alive) setDraftLoading(false); });
    return () => {
      alive = false;
    };
  }, [draftId, user?.id, loading, loadAttempt]);

  useEffect(() => {
    let alive = true;
    api.attachments
      .config()
      .then((cfg) => {
        if (!alive) return;
        setUploadCfg({
          exts: new Set(cfg.allowedExtensions.map((e) => e.toLowerCase())),
          maxBytes: cfg.maxUploadBytes,
        });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const controller of uploadControllers.current.values()) controller.abort();
      for (const url of objectUrls.current) URL.revokeObjectURL(url);
    };
  }, []);

  const draftInput: DraftInput = { title, bodyMarkdown: body, bodyFormat: format, boardSlug: selectedBoard?.slug ?? null, attachmentIds: pending.map((item) => item.id), poll };
  const hasContent = Boolean(title.trim() || body.trim() || pending.length || poll);
  const busy = submitting || saving || removing || uploading;
  const editorDisabled = busy || draftLoading || Boolean(draftLoadError) || loading || !user;
  const dirty = savedInput !== JSON.stringify(draftInput);

  const saveDraft = async () => {
    if (editorDisabled || !hasContent || actionInFlight.current) return;
    actionInFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      const saved = draftId === undefined ? await api.drafts.create(draftInput) : await api.drafts.update(draftId, draftInput);
      if (!mountedRef.current) return;
      setSavedInput(JSON.stringify(draftInput));
      onDraftSaved(saved.id);
    } catch (err) {
      if (mountedRef.current) setError(err instanceof ApiError ? err.message : t("drafts.saveFail"));
    } finally {
      actionInFlight.current = false;
      if (mountedRef.current) setSaving(false);
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (editorDisabled) return;
    setUploading(true);
    for (const file of files) {
      if (!mountedRef.current) break;
      if (!uploadCfg.exts.has(extensionOf(file.name))) { setError(t("post.unsupportedType", { name: file.name })); continue; }
      if (file.size <= 0 || file.size > uploadCfg.maxBytes) { setError(t("post.tooLarge", { name: file.name, limit: formatBytes(uploadCfg.maxBytes) })); continue; }
      let attachmentId: number | undefined;
      let controller: AbortController | undefined;
      try {
        const presigned = await api.attachments.presign({ filename: file.name, mimeType: file.type || "application/octet-stream", sizeBytes: file.size });
        attachmentId = presigned.attachmentId;
        if (!mountedRef.current) {
          void api.attachments.del(attachmentId).catch(() => undefined);
          break;
        }
        controller = new AbortController();
        uploadControllers.current.set(attachmentId, controller);
        const res = await fetch(presigned.uploadUrl, { method: presigned.uploadMethod, headers: presigned.uploadHeaders, body: file, signal: controller.signal });
        if (!res.ok) throw new ApiError(res.status, { code: "UPLOAD_FAILED", message: t("post.uploadFail", { name: file.name }) });
        uploadControllers.current.delete(attachmentId);
        if (!mountedRef.current) break;
        const previewUrl = isImage(file.name) ? URL.createObjectURL(file) : "";
        if (previewUrl) objectUrls.current.add(previewUrl);
        setPending((current) => [...current, { id: attachmentId!, filename: file.name, previewUrl }]);
      } catch (err) {
        const aborted = (err as Error).name === "AbortError" || controller?.signal.aborted === true;
        if (attachmentId !== undefined) {
          uploadControllers.current.delete(attachmentId);
          // PUT 失败留下服务端孤儿行：清掉（失败吞掉不阻断）。用户取消（abort）由 removePending 负责删除，跳过以免重复 DELETE。
          if (!aborted) void api.attachments.del(attachmentId).catch(() => undefined);
        }
        if (!aborted && mountedRef.current) setError(err instanceof ApiError ? err.message : t("post.uploadFail", { name: file.name }));
      }
    }
    if (mountedRef.current) setUploading(false);
  };
  const onPickFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    event.target.value = "";
    if (pending.length + files.length > 10) { setError(t("drafts.attachmentLimit")); return; }
    void uploadFiles(files);
  };
  const removePending = async (id: number) => {
    if (editorDisabled) return;
    setRemoving(true);
    setError(null);
    try {
      await api.attachments.del(id);
      if (!mountedRef.current) return;
      const item = pending.find((entry) => entry.id === id);
      if (item?.previewUrl && objectUrls.current.delete(item.previewUrl)) URL.revokeObjectURL(item.previewUrl);
      setPending((current) => current.filter((entry) => entry.id !== id));
    } catch (err) {
      if (mountedRef.current) setError(err instanceof ApiError ? err.message : t("post.removeFail"));
    } finally {
      if (mountedRef.current) setRemoving(false);
    }
  };


  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!body.trim() || !selectedBoard || editorDisabled || actionInFlight.current) return;
    const invalidPoll = pollError(poll);
    if (invalidPoll) { setError(t(invalidPoll)); return; }
    actionInFlight.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.discussions.create({
        boardSlug: selectedBoard.slug,
        title: title.trim(),
        bodyMarkdown: body.trim(),
        bodyFormat: format,
        attachmentIds: pending.map((item) => item.id),
        draftId,
        poll,
      });
      if (mountedRef.current) onPublished(created.id);
    } catch (err) {
      if (mountedRef.current) setError(err instanceof ApiError ? err.message : t("post.postFail"));
    } finally {
      actionInFlight.current = false;
      if (mountedRef.current) setSubmitting(false);
    }
  };

  if (!loading && !user) {
    return (
      <AppShell current="post">
        <main className="shell post-layout">
          <section className="post-editor" aria-labelledby="post-title">
            <div className="post-heading"><h1 className="feed-title" id="post-title">{t("post.newDiscussion")}</h1></div>
            <div className="empty-state">
              {t("post.signInToPost")} <a className="sender" href="/login">{t("post.signIn")}</a>
            </div>
          </section>
        </main>
      </AppShell>
    );
  }

  return (
    <AppShell current="post">
      <main className="shell post-layout">
        <section className="post-editor" aria-labelledby="post-title">
          <div className="post-heading">
            <h1 className="feed-title" id="post-title">{t(draftId === undefined ? "post.newDiscussion" : "drafts.edit")}</h1>
            <a className="draft-action" href="/drafts">{t("drafts.view")}</a>
          </div>

          <form className="post-form" onSubmit={submit} noValidate>
              <div className="post-title-row">
                <label className="form-field">
                  <span className="sr-only">{t("thread.title")}</span>
                  <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} placeholder={t("post.titlePlaceholder")} disabled={editorDisabled} autoFocus />
                  <small>{title.trim() ? `${title.length}/100` : t("post.titleOptional")}</small>
                </label>

                <SDropdown
                  items={boards}
                  value={selectedBoard}
                  onChange={(board) => { setSelectedBoard(board); setBoardUnavailable(false); }}
                  disabled={editorDisabled}
                  getKey={(board) => board.slug}
                  getLabel={(board) => board.name}
                  placeholder={t("post.chooseBoard")}
                  ariaLabel={t("post.board")}
                />
              </div>

              <EditorField value={body} onChange={setBody} format={format} onFormatChange={setFormat} rows={11} disabled={editorDisabled} placeholder={format === "markdown" ? t("post.bodyMdPlaceholder") : t("post.bodyTextPlaceholder")}
                tools={<PollToggle value={poll} onChange={setPoll} disabled={editorDisabled} />}>
                <PollFields value={poll} onChange={setPoll} disabled={editorDisabled} />
              </EditorField>

              {pending.length > 0 && <ul className="attachment-list">{pending.map((item) => <li className={`attachment-item ${isImage(item.filename) ? "" : "attachment-item-file"}`} key={item.id}>{isImage(item.filename) ? <span className="attachment-thumb"><img src={item.previewUrl} alt={item.filename} /></span> : <span className="attachment-file"><span className="attachment-file-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true"><path d="M6 3h7l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M13 3v4h4" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg></span><span className="attachment-file-name">{item.filename}</span></span>}<button type="button" className="attachment-remove" disabled={editorDisabled} onClick={() => void removePending(item.id)} aria-label={t("post.removeFile", { name: item.filename })}>{t("post.remove")}</button></li>)}</ul>}

              {draftLoading && <p className="draft-status" role="status">{t("common.loading")}</p>}
              {draftLoadError && <div role="alert"><p className="form-error">{draftLoadError}</p><button className="draft-action" type="button" onClick={() => setLoadAttempt((n) => n + 1)}>{t("common.retry")}</button></div>}
              {boardUnavailable && <p className="draft-status">{t("drafts.boardUnavailable")}</p>}
              {!draftLoading && !draftLoadError && hasContent && <p className="draft-status" role="status">{t(dirty ? "drafts.unsaved" : "drafts.saved")}</p>}
              {error && <p className="form-error" role="alert">{error}</p>}

              <div className="editor-actions">
                <input ref={fileInputRef} className="sr-only" type="file" multiple accept={Array.from(uploadCfg.exts).join(",")} onChange={onPickFiles} aria-label={t("post.chooseAttachments")} />
                <button className="attachment-action" type="button" onClick={() => fileInputRef.current?.click()} disabled={editorDisabled}>{uploading ? t("post.uploading") : t("post.addAttachment")}</button>
                <div className="submit-actions">
                  <button className="draft-action" type="button" onClick={() => void saveDraft()} disabled={editorDisabled || !hasContent || !dirty}>{t(saving ? "drafts.saving" : "post.saveDraft")}</button>
                  <button className="primary-action" type="submit" disabled={!body.trim() || !selectedBoard || editorDisabled}>{uploading ? t("post.uploading") : submitting ? t("post.posting") : t("post.postDiscussion")}</button>
                </div>
              </div>
          </form>
        </section>

        <aside className="post-aside" aria-label={t("post.guideTitle")}>
          <h2>{t("post.guideTitle")}</h2>
          <ol>
            <li><span>01</span><p><strong>{t("post.guide1Title")}</strong> {t("post.guide1Body")}</p></li>
            <li><span>02</span><p><strong>{t("post.guide2Title")}</strong> {t("post.guide2Body")}</p></li>
            <li><span>03</span><p><strong>{t("post.guide3Title")}</strong> {t("post.guide3Body")}</p></li>
          </ol>
          <p className="community-note">{t("post.guideNote")}</p>
        </aside>
      </main>
    </AppShell>
  );
}

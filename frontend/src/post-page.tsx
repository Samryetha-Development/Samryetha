import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { formatBytes } from "./lib/format";

const FALLBACK_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".pdf", ".txt", ".md", ".csv", ".zip", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mov", ".mp3", ".wav"]);
const FALLBACK_MAX_BYTES = 50 * 1024 * 1024;
const extensionOf = (name: string) => name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
const isImage = (name: string) => [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"].includes(extensionOf(name));
type PendingUpload = { id: number; file: File; previewUrl: string };

export function PostPage({ onPublished }: { onPublished: (id: number) => void }) {
  const { user, loading } = useAuth();
  const { t } = useI18n();
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [format, setFormat] = useState<BodyFormat>("text");
  const [selectedBoard, setSelectedBoard] = useState<BoardSummary | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  // 上传约束优先取后端 config，失败回退本地常量（后端仍权威校验）
  const [uploadCfg, setUploadCfg] = useState({ exts: FALLBACK_EXTENSIONS, maxBytes: FALLBACK_MAX_BYTES });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadControllers = useRef(new Map<number, AbortController>());

  useEffect(() => {
    let alive = true;
    api.boards
      .list()
      .then((data) => {
        if (!alive) return;
        setBoards(data.items);
        setSelectedBoard((current) => current ?? data.items[0] ?? null);
      })
      .catch(() => {
        if (alive) setError(t("post.loadBoardsFail"));
      });
    return () => {
      alive = false;
    };
  }, []);

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
  useEffect(() => () => {
    mountedRef.current = false;
    for (const controller of uploadControllers.current.values()) controller.abort();
  }, []);

  const uploadFiles = async (files: File[]) => {
    setUploading(true);
    for (const file of files) {
      if (!uploadCfg.exts.has(extensionOf(file.name))) { setError(t("post.unsupportedType", { name: file.name })); continue; }
      if (file.size <= 0 || file.size > uploadCfg.maxBytes) { setError(t("post.tooLarge", { name: file.name, limit: formatBytes(uploadCfg.maxBytes) })); continue; }
      let attachmentId: number | undefined;
      try {
        const presigned = await api.attachments.presign({ filename: file.name, mimeType: file.type || "application/octet-stream", sizeBytes: file.size });
        attachmentId = presigned.attachmentId;
        const controller = new AbortController();
        uploadControllers.current.set(attachmentId, controller);
        const res = await fetch(presigned.uploadUrl, { method: presigned.uploadMethod, headers: presigned.uploadHeaders, body: file, signal: controller.signal });
        if (!res.ok) throw new ApiError(res.status, { code: "UPLOAD_FAILED", message: t("post.uploadFail", { name: file.name }) });
        uploadControllers.current.delete(attachmentId);
        setPending((current) => [...current, { id: attachmentId!, file, previewUrl: isImage(file.name) ? URL.createObjectURL(file) : "" }]);
      } catch (err) {
        if (attachmentId !== undefined) uploadControllers.current.delete(attachmentId);
        if ((err as Error).name !== "AbortError") setError(err instanceof ApiError ? err.message : t("post.uploadFail", { name: file.name }));
      }
    }
    if (mountedRef.current) setUploading(false);
  };
  const onPickFiles = (event: ChangeEvent<HTMLInputElement>) => { const files = event.target.files ? Array.from(event.target.files) : []; event.target.value = ""; void uploadFiles(files); };
  const removePending = (id: number) => {
    uploadControllers.current.get(id)?.abort();
    uploadControllers.current.delete(id);
    setPending((current) => { const item = current.find((entry) => entry.id === id); if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl); return current.filter((entry) => entry.id !== id); });
    void api.attachments.del(id).catch(() => undefined);
  };


  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (title.trim().length < 3) {
      setError(t("post.titleShort"));
      return;
    }
    if (!body.trim() || !selectedBoard || submitting || uploading) return;
    setSubmitting(true);
    setError(null);
    setHint(null);
    try {
      const created = await api.discussions.create({
        boardSlug: selectedBoard.slug,
        title: title.trim(),
        bodyMarkdown: body.trim(),
        bodyFormat: format,
        attachmentIds: pending.map((item) => item.id),
      });
      onPublished(created.id);
    } catch (err) {
      if (mountedRef.current) setError(err instanceof ApiError ? err.message : t("post.postFail"));
    } finally {
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
            <h1 className="feed-title" id="post-title">{t("post.newDiscussion")}</h1>
          </div>

          <form className="post-form" onSubmit={submit} noValidate>
              <div className="post-title-row">
                <label className="form-field">
                  <span className="sr-only">{t("thread.title")}</span>
                  <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} placeholder={t("post.titlePlaceholder")} autoFocus />
                  <small>{title.trim().length < 3 ? t("post.titleMin", { count: title.trim().length }) : `${title.length}/100`}</small>
                </label>

                <SDropdown
                  items={boards}
                  value={selectedBoard}
                  onChange={setSelectedBoard}
                  getKey={(board) => board.slug}
                  getLabel={(board) => board.name}
                  placeholder={t("post.chooseBoard")}
                  ariaLabel={t("post.board")}
                />
              </div>

              <label className="form-field body-field">
                <div className="body-field-head">
                  <span>{t("thread.message")}</span>
                  <div className="format-toggle" role="group" aria-label={t("thread.textFormat")}>
                    <button type="button" className={`format-toggle-btn ${format === "markdown" ? "active" : ""}`} aria-pressed={format === "markdown"} onClick={() => setFormat("markdown")}>{t("thread.markdown")}</button>
                    <button type="button" className={`format-toggle-btn ${format === "text" ? "active" : ""}`} aria-pressed={format === "text"} onClick={() => setFormat("text")}>{t("thread.plainText")}</button>
                  </div>
                </div>
                <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={11} maxLength={40000} placeholder={format === "markdown" ? t("post.bodyMdPlaceholder") : t("post.bodyTextPlaceholder")} />
              </label>

              {pending.length > 0 && <ul className="attachment-list">{pending.map((item) => <li className={`attachment-item ${isImage(item.file.name) ? "" : "attachment-item-file"}`} key={item.id}>{isImage(item.file.name) ? <span className="attachment-thumb"><img src={item.previewUrl} alt={item.file.name} /></span> : <span className="attachment-file"><span className="attachment-file-icon">file</span><span className="attachment-file-name">{item.file.name}</span></span>}<button type="button" className="attachment-remove" onClick={() => removePending(item.id)} aria-label={t("post.removeFile", { name: item.file.name })}>{t("post.remove")}</button></li>)}</ul>}

              {error && <p className="form-error" role="alert">{error}</p>}
              {hint && <p className="form-hint" role="status">{hint}</p>}

              <div className="editor-actions">
                <input ref={fileInputRef} className="sr-only" type="file" multiple accept={Array.from(uploadCfg.exts).join(",")} onChange={onPickFiles} aria-label={t("post.chooseAttachments")} />
                <button className="attachment-action" type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? t("post.uploading") : t("post.addAttachment")}</button>
                <div className="submit-actions">
                  <button className="draft-action" type="button" disabled>{t("post.saveDraft")}</button>
                  <button className="primary-action" type="submit" disabled={title.trim().length < 3 || !body.trim() || !selectedBoard || submitting || uploading}>{uploading ? t("post.uploading") : submitting ? t("post.posting") : t("post.postDiscussion")}</button>
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

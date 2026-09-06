import { FormEvent, useEffect, useRef, useState, type ChangeEvent } from "react";
import { AppShell } from "./app-shell";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";
import { formatBytes } from "./lib/format";

// 上传约束：优先用后端下发的 config（GET /api/attachments/config），失败回退本地镜像常量；
// 后端仍会权威校验，这里只做即时 UX 拦截。
// Upload constraints: prefer server-sent config, fall back to mirrored constants; the backend
// re-validates authoritatively.
const FALLBACK_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
  ".pdf", ".txt", ".md", ".csv",
  ".zip", ".rar", ".7z",
  ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
  ".mp4", ".mov", ".mp3", ".wav",
]);
const FALLBACK_MAX_BYTES = 50 * 1024 * 1024;
// 与后端 isImage 口径对齐：按扩展名判断，不用浏览器 file.type（后者在改名文件上前后不一致）
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"]);

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

// 待挂载附件：attachmentId 用于提交时关联到讨论；previewUrl 为图片本地预览（objectURL）
// Pending attachment: attachmentId links it to the discussion on submit; previewUrl is a local objectURL for images
type PendingUpload = { id: number; file: File; previewUrl: string; isImage: boolean };

export function PostPage({ onPublished }: { onPublished: (id: number) => void }) {
  const { user, loading } = useAuth();
  const { t } = useI18n();
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [format, setFormat] = useState<BodyFormat>("markdown");
  const [selectedBoard, setSelectedBoard] = useState<BoardSummary | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadCfg, setUploadCfg] = useState({ exts: FALLBACK_EXTENSIONS, maxBytes: FALLBACK_MAX_BYTES });
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 在途上传的 AbortController：卸载时全部 abort，防 PUT 后完成写出孤儿文件
  const inflightRef = useRef(new Set<AbortController>());

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

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  // 卸载时撤销本地图片预览 objectURL，并 abort 在途上传（后端 pending 附件不随组件卸载自动删除，
  // 仅本地预览需回收；在途 PUT 被 abort 后不再进 pending 列表）
  // Revoke local image preview objectURLs and abort in-flight uploads on unmount
  const pendingRef = useRef<PendingUpload[]>([]);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);
  useEffect(() => () => {
    for (const ctrl of inflightRef.current) ctrl.abort();
    inflightRef.current.clear();
    for (const item of pendingRef.current) {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    }
  }, []);

  // 上传约束取后端 config，失败保持本地回退值
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

  const uploadFiles = async (files: File[]) => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of files) {
        const ext = extOf(file.name);
        if (!uploadCfg.exts.has(ext)) {
          setError(t("post.unsupportedType", { name: file.name }));
          continue;
        }
        if (file.size <= 0 || file.size > uploadCfg.maxBytes) {
          setError(t("post.tooLarge", { name: file.name, limit: formatBytes(uploadCfg.maxBytes) }));
          continue;
        }
        const ctrl = new AbortController();
        inflightRef.current.add(ctrl);
        try {
          const presigned = await api.attachments.presign({
            filename: file.name,
            mimeType: file.type || "application/octet-stream",
            sizeBytes: file.size,
          });
          const res = await fetch(presigned.uploadUrl, {
            method: presigned.uploadMethod,
            headers: presigned.uploadHeaders ?? {},
            body: file,
            signal: ctrl.signal,
          });
          // 直传失败必须抛错：否则失败文件会被当成功挂载，发帖后读者点开 400
          if (!res.ok) {
            throw new ApiError(res.status, { code: "UPLOAD_FAILED", message: t("post.uploadFail", { name: file.name }) });
          }
          // 卸载后才完成的上传：删掉刚建好的后端行，不进 pending（防孤儿）
          if (!mountedRef.current) {
            await api.attachments.del(presigned.attachmentId).catch(() => undefined);
            continue;
          }
          const isImage = IMAGE_EXTENSIONS.has(ext);
          const previewUrl = isImage ? URL.createObjectURL(file) : "";
          setPending((prev) => [...prev, { id: presigned.attachmentId, file, previewUrl, isImage }]);
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") continue;
          setError(err instanceof ApiError ? err.message : t("post.uploadFail", { name: file.name }));
        } finally {
          inflightRef.current.delete(ctrl);
        }
      }
    } finally {
      if (mountedRef.current) setUploading(false);
    }
  };

  const onPickFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    event.target.value = ""; // 重置 input，允许再次选择同一文件
    void uploadFiles(files);
  };

  const removePending = (id: number) => {
    const item = pending.find((p) => p.id === id);
    if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
    setPending((prev) => prev.filter((p) => p.id !== id));
    // 尽力删除后端 pending 行（孤儿附件）；失败不阻断（后端有 orphaned 兜底）
    void api.attachments.del(id).catch(() => undefined);
  };


  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (title.trim().length < 3) {
      setError(t("post.titleShort"));
      return;
    }
    if (!body.trim() || !selectedBoard || submitting) return;
    setSubmitting(true);
    setError(null);
    setHint(null);
    try {
      const created = await api.discussions.create({
        boardSlug: selectedBoard.slug,
        title: title.trim(),
        bodyMarkdown: body.trim(),
        bodyFormat: format,
        attachmentIds: pending.map((p) => p.id),
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

              {pending.length > 0 && (
                <ul className="attachment-list">
                  {pending.map((p) => {
                    return (
                      <li className={`attachment-item ${!p.isImage ? "attachment-item-file" : ""}`} key={p.id}>
                        {p.isImage ? (
                          <span className="attachment-thumb"><img src={p.previewUrl} alt={p.file.name} /></span>
                        ) : (
                          <span className="attachment-file">
                            <span className="attachment-file-icon" aria-hidden="true">file</span>
                            <span className="attachment-file-meta">
                              <span className="attachment-file-name">{p.file.name}</span>
                              <span className="attachment-file-size">{formatBytes(p.file.size)}</span>
                            </span>
                          </span>
                        )}
                        <button type="button" className="attachment-remove" onClick={() => removePending(p.id)} aria-label={t("post.removeFile", { name: p.file.name })}>{t("post.remove")}</button>
                      </li>
                    );
                  })}
                </ul>
              )}

              {error && <p className="form-error" role="alert">{error}</p>}
              {hint && <p className="form-hint" role="status">{hint}</p>}

              <div className="editor-actions">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="sr-only"
                  onChange={onPickFiles}
                  aria-label={t("post.chooseAttachments")}
                />
                <button className="attachment-action" type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? t("post.uploading") : t("post.addAttachment")}</button>
                <div className="submit-actions">
                  <button className="draft-action" type="button" disabled>{t("post.saveDraft")}</button>
                  {/* 上传未完成禁止提交：在途文件进不了 attachmentIds，会变孤儿 */}
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

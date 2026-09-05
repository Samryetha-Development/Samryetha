import { FormEvent, useEffect, useRef, useState, type ChangeEvent } from "react";
import { AppShell } from "./app-shell";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatBytes } from "./lib/format";

// 前端上传前的扩展名白名单与体积上限（镜像后端 storage.py 的 ALLOWED_EXTENSIONS / MAX_UPLOAD_BYTES，
// 仅做即时 UX 校验；后端仍会权威校验，前端仅提前拦截明显非法项）
// Client-side extension whitelist + size cap for instant UX validation (mirrors backend
// ALLOWED_EXTENSIONS / MAX_UPLOAD_BYTES; backend re-validates authoritatively)
const ALLOWED_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
  ".pdf", ".txt", ".md", ".csv",
  ".zip", ".rar", ".7z",
  ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
  ".mp4", ".mov", ".mp3", ".wav",
]);
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

// 待挂载附件：attachmentId 用于提交时关联到讨论；previewUrl 为图片本地预览（objectURL）
// Pending attachment: attachmentId links it to the discussion on submit; previewUrl is a local objectURL for images
type PendingUpload = { id: number; file: File; previewUrl: string };

export function PostPage({ onPublished }: { onPublished: (id: number) => void }) {
  const { user, loading } = useAuth();
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
  const fileInputRef = useRef<HTMLInputElement>(null);

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
        if (alive) setError("Could not load boards. Refresh to try again.");
      });
    return () => {
      alive = false;
    };
  }, []);

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  // 卸载时撤销本地图片预览 objectURL（后端 pending 附件不随组件卸载自动删除，仅本地预览需回收）
  // Revoke local image preview objectURLs on unmount (server-side pending rows aren't auto-deleted; only local previews need cleanup)
  const pendingRef = useRef<PendingUpload[]>([]);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);
  useEffect(() => () => {
    for (const item of pendingRef.current) {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    }
  }, []);

  const uploadFiles = async (files: File[]) => {
    if (files.length === 0) return;
    setUploading(true);
    for (const file of files) {
      const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
      if (!ALLOWED_EXTENSIONS.has(ext)) {
        setError(`Unsupported file type: ${file.name}`);
        continue;
      }
      if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) {
        setError(`${file.name} exceeds the 50MB limit.`);
        continue;
      }
      try {
        const presigned = await api.attachments.presign({
          filename: file.name,
          mimeType: file.type || "application/octet-stream",
          sizeBytes: file.size,
        });
        await fetch(presigned.uploadUrl, {
          method: presigned.uploadMethod,
          headers: presigned.uploadHeaders ?? {},
          body: file,
        });
        const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : "";
        setPending((prev) => [...prev, { id: presigned.attachmentId, file, previewUrl }]);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : `Could not upload ${file.name}.`);
      }
    }
    setUploading(false);
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
      setError("Title must be at least 3 characters");
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
      if (mountedRef.current) setError(err instanceof ApiError ? err.message : "Could not post. Try again.");
    } finally {
      if (mountedRef.current) setSubmitting(false);
    }
  };

  if (!loading && !user) {
    return (
      <AppShell current="post">
        <main className="shell post-layout">
          <section className="post-editor" aria-labelledby="post-title">
            <div className="post-heading"><h1 className="feed-title" id="post-title">New discussion</h1></div>
            <div className="empty-state">
              Sign in to start a discussion. <a className="sender" href="/login">Sign in</a>
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
            <h1 className="feed-title" id="post-title">New discussion</h1>
          </div>

          <form className="post-form" onSubmit={submit} noValidate>
              <div className="post-title-row">
                <label className="form-field">
                  <span className="sr-only">Title</span>
                  <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} placeholder="What do you want to discuss?" autoFocus />
                  <small>{title.trim().length < 3 ? `Min 3 chars (${title.trim().length}/3)` : `${title.length}/100`}</small>
                </label>

                <SDropdown
                  items={boards}
                  value={selectedBoard}
                  onChange={setSelectedBoard}
                  getKey={(board) => board.slug}
                  getLabel={(board) => board.name}
                  placeholder="Choose a board"
                  ariaLabel="Board"
                />
              </div>

              <label className="form-field body-field">
                <div className="body-field-head">
                  <span>Message</span>
                  <div className="format-toggle" role="group" aria-label="Text format">
                    <button type="button" className={`format-toggle-btn ${format === "markdown" ? "active" : ""}`} aria-pressed={format === "markdown"} onClick={() => setFormat("markdown")}>Markdown</button>
                    <button type="button" className={`format-toggle-btn ${format === "text" ? "active" : ""}`} aria-pressed={format === "text"} onClick={() => setFormat("text")}>Plain text</button>
                  </div>
                </div>
                <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={11} maxLength={40000} placeholder={format === "markdown" ? "Add context, details, or a question…" : "Write plain text…"} />
              </label>

              {pending.length > 0 && (
                <ul className="attachment-list">
                  {pending.map((p) => {
                    const isImage = p.file.type.startsWith("image/");
                    return (
                      <li className={`attachment-item ${!isImage ? "attachment-item-file" : ""}`} key={p.id}>
                        {isImage ? (
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
                        <button type="button" className="attachment-remove" onClick={() => removePending(p.id)} aria-label={`Remove ${p.file.name}`}>Remove</button>
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
                  aria-label="Choose attachments"
                />
                <button className="attachment-action" type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? "Uploading…" : "Add attachment"}</button>
                <div className="submit-actions">
                  <button className="draft-action" type="button" disabled>Save draft</button>
                  <button className="primary-action" type="submit" disabled={title.trim().length < 3 || !body.trim() || !selectedBoard || submitting}>{submitting ? "Posting…" : "Post discussion"}</button>
                </div>
              </div>
          </form>
        </section>

        <aside className="post-aside" aria-label="Posting guidance">
          <h2>Before you post</h2>
          <ol>
            <li><span>01</span><p><strong>Choose the closest board.</strong> It helps the right people find your discussion.</p></li>
            <li><span>02</span><p><strong>Make the title specific.</strong> A clear title usually gets a better answer.</p></li>
            <li><span>03</span><p><strong>Keep personal details private.</strong> This space is visible across campus.</p></li>
          </ol>
          <p className="community-note">Be curious, constructive, and kind.</p>
        </aside>
      </main>
    </AppShell>
  );
}

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";

const ALLOWED_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".pdf", ".txt", ".md", ".csv", ".zip", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mov", ".mp3", ".wav"]);
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
// 移动端：无 accept 时 iOS/Android 文件选择器默认偏图片，非图片文件选择受限/不可用。显式列出全部允许扩展名，
// 使移动端与桌面端一致支持图片+文件。Explicit accept so mobile browsers offer full file (not just image) selection
const ACCEPT = Array.from(ALLOWED_EXTENSIONS).join(",");
const extensionOf = (name: string) => name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
const isImage = (name: string) => [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"].includes(extensionOf(name));
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
        if (alive) setError("Could not load boards. Refresh to try again.");
      });
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
      if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name))) { setError(`Unsupported file type: ${file.name}`); continue; }
      if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) { setError(`${file.name} exceeds the 50 MB limit.`); continue; }
      let attachmentId: number | undefined;
      try {
        const presigned = await api.attachments.presign({ filename: file.name, mimeType: file.type || "application/octet-stream", sizeBytes: file.size });
        attachmentId = presigned.attachmentId;
        const controller = new AbortController();
        uploadControllers.current.set(attachmentId, controller);
        const res = await fetch(presigned.uploadUrl, { method: presigned.uploadMethod, headers: presigned.uploadHeaders, body: file, signal: controller.signal });
        if (!res.ok) throw new ApiError(res.status, { code: "UPLOAD_FAILED", message: `Upload failed (${res.status})` });
        uploadControllers.current.delete(attachmentId);
        setPending((current) => [...current, { id: attachmentId!, file, previewUrl: isImage(file.name) ? URL.createObjectURL(file) : "" }]);
      } catch (err) {
        if (attachmentId !== undefined) uploadControllers.current.delete(attachmentId);
        if ((err as Error).name !== "AbortError") setError(err instanceof ApiError ? err.message : `Could not upload ${file.name}.`);
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
      setError("Title must be at least 3 characters");
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

              {pending.length > 0 && <ul className="attachment-list">{pending.map((item) => <li className={`attachment-item ${isImage(item.file.name) ? "" : "attachment-item-file"}`} key={item.id}>{isImage(item.file.name) ? <span className="attachment-thumb"><img src={item.previewUrl} alt={item.file.name} /></span> : <span className="attachment-file"><span className="attachment-file-icon">file</span><span className="attachment-file-name">{item.file.name}</span></span>}<button type="button" className="attachment-remove" onClick={() => removePending(item.id)}>Remove</button></li>)}</ul>}

              {error && <p className="form-error" role="alert">{error}</p>}
              {hint && <p className="form-hint" role="status">{hint}</p>}

              <div className="editor-actions">
                <input ref={fileInputRef} className="sr-only" type="file" multiple accept={ACCEPT} onChange={onPickFiles} aria-label="Choose attachments" />
                <button className="attachment-action" type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? "Uploading…" : "Add attachment"}</button>
                <div className="submit-actions">
                  <button className="draft-action" type="button" disabled>Save draft</button>
                  <button className="primary-action" type="submit" disabled={title.trim().length < 3 || !body.trim() || !selectedBoard || submitting || uploading}>{submitting ? "Posting…" : "Post discussion"}</button>
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

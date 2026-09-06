import { FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type BoardSummary, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";

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

              {error && <p className="form-error" role="alert">{error}</p>}
              {hint && <p className="form-hint" role="status">{hint}</p>}

              <div className="editor-actions">
                <button className="attachment-action" type="button" onClick={() => setHint("Attachments aren’t wired up yet — plain text works fine.")}>Add attachment</button>
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

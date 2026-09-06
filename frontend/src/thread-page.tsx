import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Loading } from "./loading";
import { api, type DiscussionDetail, type ReplyDTO, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatTime } from "./lib/format";
import { useIsomorphicLayoutEffect } from "./lib/use-isomorphic-layout-effect";
import { AppShell } from "./app-shell";

const MAX_REPLY_DEPTH = 8;

export function ThreadPage({ id, initialTitle }: { id: number; initialTitle?: string }) {
  const { user } = useAuth();
  const [detail, setDetail] = useState<DiscussionDetail | null>(null);
  const [replies, setReplies] = useState<ReplyDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editBody, setEditBody] = useState("");
  const [editFormat, setEditFormat] = useState<BodyFormat>("markdown");
  const [replyText, setReplyText] = useState("");
  const [replyFormat, setReplyFormat] = useState<BodyFormat>("markdown");
  const [replyingTo, setReplyingTo] = useState<number | null>(null);
  const replyInputRef = useRef<HTMLTextAreaElement>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const isStaff = user?.role === "admin";

  useIsomorphicLayoutEffect(() => {
    const textarea = replyInputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const maxHeight = 280;
    const height = Math.max(128, Math.min(textarea.scrollHeight, maxHeight));
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [replyText]);

  // ---- Save/Follow 的"单条时间线"动效 ----
  // 统一用 element.animate() 手动驱动（不用 CSS @keyframes / key remount），并存入 Animation
  // 引用：动画播放中被再次点击时，先 getComputedStyle 读当前实际渲染值作为新动画起点，
  // cancel 旧的再接管——"打断即转向"，而不是等旧的播完或从头重炸。
  const actionsRef = useRef<HTMLDivElement>(null);
  const saveBtnRef = useRef<HTMLButtonElement>(null);
  const saveLabelRef = useRef<HTMLSpanElement>(null);
  const followBtnRef = useRef<HTMLButtonElement>(null);
  const followLabelRef = useRef<HTMLSpanElement>(null);
  const runningAnims = useRef(new Map<Element, Animation>());
  const flipStart = useRef<Map<Element, [number, number, number, number]> | null>(null);
  const flipOrigin = useRef<Element | null>(null);
  const toggleToken = useRef(0);

  // 可打断动画：有动画在跑 → 读当前渲染值（transform/opacity/filter）作起点；否则用 freshStart
  const runInterruptible = (
    el: Element,
    props: string[],
    freshStart: Record<string, string>,
    to: Keyframe[],
    opts: KeyframeAnimationOptions,
  ) => {
    const prev = runningAnims.current.get(el);
    let start = freshStart;
    if (prev) {
      if (prev.playState === "running") {
        const cs = getComputedStyle(el);
        start = {};
        for (const p of props) start[p] = cs.getPropertyValue(p);
      }
      prev.cancel();
    }
    const anim = el.animate([start, ...to], opts);
    runningAnims.current.set(el, anim);
  };

  // 新 toggle 前清掉所有在跑的动画（尤其兄弟 FLIP：否则 getBoundingClientRect 会把 transform 算进去）
  const cancelRunning = () => {
    for (const anim of runningAnims.current.values()) anim.cancel();
    runningAnims.current.clear();
  };

  const captureLayout = () => {
    flipStart.current = new Map(
      Array.from(actionsRef.current?.children ?? []).map((el) => {
        const r = el.getBoundingClientRect();
        return [el, [r.left, r.top, r.width, r.height] as const];
      }),
    );
  };

  // 状态翻转后的动效：渲染提交后跑，此刻 getComputedStyle 若读到在播动画就是中间态 → 可接管
  const prevSaved = useRef<boolean | null>(null);
  const prevFollowing = useRef<boolean | null>(null);
  useIsomorphicLayoutEffect(() => {
    if (!detail) return;
    if (prevSaved.current === null) {
      // 首次拿到数据：只记录基线，不播放入场动画
      prevSaved.current = detail.isSaved;
      prevFollowing.current = detail.isFollowing;
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      prevSaved.current = detail.isSaved;
      prevFollowing.current = detail.isFollowing;
      return;
    }
    if (prevSaved.current !== detail.isSaved) {
      prevSaved.current = detail.isSaved;
      if (saveBtnRef.current) {
        runInterruptible(saveBtnRef.current, ["transform"], { transform: "scale(.96)" }, [
          { transform: "scale(1.03)" },
          { transform: "scale(1)" },
        ], { duration: 280, easing: "cubic-bezier(.22, .8, .24, 1)" });
      }
      if (saveLabelRef.current) {
        runInterruptible(saveLabelRef.current, ["opacity", "filter"], { opacity: "0", filter: "blur(6px)" }, [
          { opacity: "1", filter: "blur(0px)" },
        ], { duration: 280, easing: "cubic-bezier(.22, .8, .24, 1)" });
      }
    }
    if (prevFollowing.current !== detail.isFollowing) {
      prevFollowing.current = detail.isFollowing;
      if (followBtnRef.current) {
        runInterruptible(followBtnRef.current, ["transform"], { transform: "scale(.96)" }, [
          { transform: "scale(1.03)" },
          { transform: "scale(1)" },
        ], { duration: 280, easing: "cubic-bezier(.22, .8, .24, 1)" });
      }
      if (followLabelRef.current) {
        runInterruptible(followLabelRef.current, ["opacity", "filter"], { opacity: "0", filter: "blur(6px)" }, [
          { opacity: "1", filter: "blur(0px)" },
        ], { duration: 280, easing: "cubic-bezier(.22, .8, .24, 1)" });
      }
    }
  });

  // FLIP：宽度变化后，兄弟按钮从旧位滑到新位（弹簧过冲 + 距触发越远延迟越长 + 先回缩再弹）
  useIsomorphicLayoutEffect(() => {
    if (!flipStart.current) return;
    const start = flipStart.current;
    flipStart.current = null;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const origin = flipOrigin.current;
    const originCenter = origin
      ? (() => {
          const r = origin.getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()
      : null;
    for (const el of Array.from(actionsRef.current?.children ?? [])) {
      const from = start.get(el);
      if (!from) continue;
      const r = el.getBoundingClientRect();
      const dx = from[0] - r.left;
      const dy = from[1] - r.top;
      if (Math.abs(dx) <= 0.5 && Math.abs(dy) <= 0.5) continue;
      let delay = 0;
      if (originCenter) {
        const dist = Math.hypot(from[0] + from[2] / 2 - originCenter[0], from[1] + from[3] / 2 - originCenter[1]);
        delay = Math.min(dist / 5, 48);
      }
      runInterruptible(
        el,
        ["transform"],
        { transform: `translate(${dx}px, ${dy}px)` },
        [
          { transform: `translate(${dx * 1.08}px, ${dy * 1.08}px)`, offset: 0.3 },
          { transform: "translate(0, 0)", offset: 1 },
        ],
        { duration: 280, delay, fill: "both", easing: "cubic-bezier(0.34, 1.56, 0.64, 1)" },
      );
    }
    flipOrigin.current = null;
  });

  const load = useCallback(async () => {
    try {
      const [d, r] = await Promise.all([api.discussions.get(id), api.discussions.replies(id)]);
      setDetail(d);
      setReplies(r.items);
      setEditTitle(d.title);
      setEditBody(d.bodyMarkdown);
      setEditFormat(d.bodyFormat);
    } catch {
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // 从通知跳转过来时标记该通知已读
  useEffect(() => {
    const notif = new URLSearchParams(window.location.search).get("notif");
    if (!notif) return;
    const notifId = Number(notif);
    if (!Number.isFinite(notifId)) return;
    void api.notifications.markRead(notifId).catch(() => undefined);
  }, []);

  const flashTimer = useRef<number | null>(null);
  const flash = (message: string) => {
    setNotice(message);
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setNotice(null), 2200);
  };
  useEffect(() => () => {
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
  }, []);

  // 乐观更新：点击立即翻转，不等网络；失败只有"最新一次"点击能弹回，旧请求不覆盖新状态。
  // 按钮全程可点——动效可打断（runInterruptible 从当前状态接管），API 用 token 防竞态回滚。
  const toggleSave = () => {
    if (!detail) return;
    const wasSaved = detail.isSaved;
    const wasCount = detail.saveCount;
    const token = ++toggleToken.current;
    flipOrigin.current = saveBtnRef.current;
    cancelRunning();
    captureLayout();
    setDetail((d) => d && { ...d, isSaved: !wasSaved, saveCount: wasCount + (wasSaved ? -1 : 1) });
    void (async () => {
      try {
        await (wasSaved ? api.discussions.unsave(detail.id) : api.discussions.save(detail.id));
      } catch {
        if (token === toggleToken.current) {
          setDetail((d) => d && { ...d, isSaved: wasSaved, saveCount: wasCount });
        }
      }
    })();
  };

  const toggleFollow = () => {
    if (!detail) return;
    const wasFollowing = detail.isFollowing;
    const token = ++toggleToken.current;
    flipOrigin.current = followBtnRef.current;
    cancelRunning();
    captureLayout();
    setDetail((d) => d && { ...d, isFollowing: !wasFollowing });
    void (async () => {
      try {
        await (wasFollowing ? api.discussions.unfollow(detail.id) : api.discussions.follow(detail.id));
      } catch {
        if (token === toggleToken.current) {
          setDetail((d) => d && { ...d, isFollowing: wasFollowing });
        }
      }
    })();
  };

  const togglePin = async () => {
    if (!detail) return;
    try {
      await api.discussions.pin(detail.id);
      await load();
      flash(detail.isPinned ? "Unpinned" : "Pinned");
    } catch {
      flash("Could not update pin state. Please try again.");
    }
  };

  const toggleLock = async () => {
    if (!detail) return;
    try {
      await api.discussions.lock(detail.id);
      await load();
      flash(detail.isLocked ? "Unlocked" : "Locked");
    } catch {
      flash("Could not update lock state. Please try again.");
    }
  };

  const submitEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!detail || busy) return;
    setBusy(true);
    try {
      await api.discussions.update(detail.id, { title: editTitle, bodyMarkdown: editBody, bodyFormat: editFormat });
      setEditing(false);
      await load();
      flash("Discussion updated");
    } catch {
      flash("Could not save changes. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!detail || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.discussions.del(detail.id);
      window.location.href = "/";
    } catch {
      // 失败时弹窗保持打开，让用户看到原因，而不是无声关掉
      setDeleteError("Could not delete this discussion. Please try again.");
      setDeleteBusy(false);
    }
  };

  const submitReply = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!detail || busy || !replyText.trim()) return;
    setBusy(true);
    try {
      await api.discussions.createReply(detail.id, {
        bodyMarkdown: replyText.trim(),
        bodyFormat: replyFormat,
        parentReplyId: replyingTo,
      });
      setReplyText("");
      setReplyingTo(null);
      await load();
      flash("Reply posted");
    } catch {
      flash("Could not post this reply. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const removeReply = async (reply: ReplyDTO) => {
    try {
      await api.discussions.delReply(reply.id);
      await load();
    } catch {
      flash("Could not delete this reply.");
    }
  };

  const replyTo = (reply: ReplyDTO) => {
    setReplyingTo(reply.id);
    replyInputRef.current?.focus();
  };

  const replyIds = new Set(replies.map((reply) => reply.id));
  const repliesByParent = replies.reduce<Map<number | null, ReplyDTO[]>>((groups, reply) => {
    const parentId = reply.parentReplyId !== null && !replyIds.has(reply.parentReplyId) ? null : reply.parentReplyId;
    const group = groups.get(parentId) ?? [];
    group.push(reply);
    groups.set(parentId, group);
    return groups;
  }, new Map());

  const renderReplies = (parentReplyId: number | null, depth = 0): ReactNode => (
    <>
      {(repliesByParent.get(parentReplyId) ?? []).map((reply) => (
        <div className="reply-branch" key={reply.id} style={{ "--reply-depth": depth } as CSSProperties}>
          <div className={`reply ${depth > 0 ? "is-thread" : ""}`}>
            <div className="reply-head">
              <a className="sender" href={`/profile?username=${encodeURIComponent(reply.author.username)}`}>{reply.author.displayName}</a>
              <a className="muted-link" href={`/profile?username=${encodeURIComponent(reply.author.username)}`}>@{reply.author.handle}</a>
              <span className="dot" />
              <span>{formatTime(reply.createdAt)}</span>
              {!reply.isDeleted && user && !detail?.isLocked && (
                <button className="reply-action" type="button" onClick={() => replyTo(reply)}>Reply</button>
              )}
              {(isStaff || user?.id === reply.author.id) && (
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="reply-delete" type="button">Delete</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">Delete this reply?</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        This cannot be undone. The reply will be permanently removed.
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">Cancel</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void removeReply(reply)}>Delete</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              )}
            </div>
            {reply.isDeleted ? (
              <p className="reply-deleted">This reply was removed.</p>
            ) : reply.bodyHtml ? (
              <div className="reply-body" dangerouslySetInnerHTML={{ __html: reply.bodyHtml }} />
            ) : (
              <p className="reply-body plain">{reply.bodyMarkdown}</p>
            )}
          </div>
          {renderReplies(reply.id, Math.min(depth + 1, MAX_REPLY_DEPTH))}
        </div>
      ))}
    </>
  );

  if (loading) {
    return (
      <AppShell>
        <main className="shell thread-layout" id="main-content">
          {initialTitle ? (
            <article className="thread-article thread-loading-article" aria-label="Loading discussion">
              <div className="thread-flags-placeholder" aria-hidden="true" />
              <h1 className="thread-detail-title thread-shared-title">{initialTitle}</h1>
              <Loading />
            </article>
          ) : <Loading />}
        </main>
      </AppShell>
    );
  }
  if (notFound || !detail) {
    return <AppShell><div className="empty-state content-fade">This discussion could not be found.</div></AppShell>;
  }

  return (
    <AppShell>
      <main className="shell thread-layout" id="main-content">
        <article className="thread-article thread-article-enter" aria-labelledby="thread-title">
          <div className="thread-flags">
            <a className="tag" href={`/?board=${encodeURIComponent(detail.board.slug)}`}>{detail.board.name}</a>
            {detail.isPinned && <span className="flag">Pinned</span>}
            {detail.isLocked && <span className="flag">Locked</span>}
          </div>

          {editing ? (
            <form className="post-form" onSubmit={submitEdit} noValidate>
              <label className="form-field">
                <span className="sr-only">Title</span>
                <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} maxLength={100} autoFocus />
              </label>
              <label className="form-field body-field">
                <div className="body-field-head">
                  <span>Message</span>
                  <div className="format-toggle" role="group" aria-label="Text format">
                    <button type="button" className={`format-toggle-btn ${editFormat === "markdown" ? "active" : ""}`} aria-pressed={editFormat === "markdown"} onClick={() => setEditFormat("markdown")}>Markdown</button>
                    <button type="button" className={`format-toggle-btn ${editFormat === "text" ? "active" : ""}`} aria-pressed={editFormat === "text"} onClick={() => setEditFormat("text")}>Plain text</button>
                  </div>
                </div>
                <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} rows={10} />
              </label>
              <div className="submit-actions">
                <button className="draft-action" type="button" onClick={() => setEditing(false)}>Cancel</button>
                <button className="primary-action" type="submit" disabled={busy || !editTitle.trim() || !editBody.trim()}>Save changes</button>
              </div>
            </form>
          ) : (
            <>
              <h1 className={`thread-detail-title ${initialTitle ? "thread-shared-title" : ""}`} id="thread-title">{detail.title}</h1>
              <div className="thread-detail-meta">
                <a className="sender" href={`/profile?username=${encodeURIComponent(detail.author.username)}`}>{detail.author.displayName}</a>
                <a className="muted-link" href={`/profile?username=${encodeURIComponent(detail.author.username)}`}>@{detail.author.handle}</a>
                <span className="dot" />
                <span>{formatTime(detail.createdAt)}</span>
              </div>
              {detail.bodyHtml ? (
                <div className="thread-detail-body" dangerouslySetInnerHTML={{ __html: detail.bodyHtml }} />
              ) : (
                <p className="thread-detail-body plain">{detail.bodyMarkdown}</p>
              )}
            </>
          )}

          <div className="thread-actions" role="group" aria-label="Discussion actions" ref={actionsRef}>
            {user && (
              <>
                <button ref={saveBtnRef} type="button" className={`action-btn ${detail.isSaved ? "active" : ""}`} onClick={toggleSave}>
                  {/* span 常驻不 remount，文字 blur 由 JS animate 驱动（可打断接管） */}
                  <span ref={saveLabelRef} className="action-label">
                    {detail.isSaved ? "Saved" : "Save"} · {detail.saveCount}
                  </span>
                </button>
                <button ref={followBtnRef} type="button" className={`action-btn ${detail.isFollowing ? "active" : ""}`} onClick={toggleFollow}>
                  <span ref={followLabelRef} className="action-label">
                    {detail.isFollowing ? "Following" : "Follow"}
                  </span>
                </button>
              </>
            )}
            {isStaff && (
              <>
                <button type="button" className="action-btn" onClick={togglePin}>{detail.isPinned ? "Unpin" : "Pin"}</button>
                <button type="button" className="action-btn" onClick={toggleLock}>{detail.isLocked ? "Unlock" : "Lock"}</button>
              </>
            )}
            {detail.can.update && !editing && (
              <button type="button" className="action-btn" onClick={() => { setEditTitle(detail.title); setEditBody(detail.bodyMarkdown); setEditFormat(detail.bodyFormat); setEditing(true); }}>Edit</button>
            )}
            {detail.can.delete && (
              <AlertDialog.Root
                open={deleteOpen}
                onOpenChange={(open) => {
                  if (deleteBusy) return;
                  setDeleteOpen(open);
                  if (!open) {
                    setDeleteError(null);
                    setDeleteBusy(false);
                  }
                }}
              >
                <AlertDialog.Trigger asChild>
                  <button type="button" className="action-btn danger">Delete</button>
                </AlertDialog.Trigger>
                <AlertDialog.Portal>
                  <AlertDialog.Overlay className="dialog-overlay" />
                  <AlertDialog.Content className="dialog-content">
                    <AlertDialog.Title className="dialog-title">Delete this discussion?</AlertDialog.Title>
                    <AlertDialog.Description className="dialog-description">
                      This cannot be undone. The discussion and all of its replies will be permanently removed.
                    </AlertDialog.Description>
                    {deleteError && <p className="dialog-error" role="alert">{deleteError}</p>}
                    <div className="dialog-actions">
                      <AlertDialog.Cancel asChild>
                        <button type="button" className="action-btn" disabled={deleteBusy}>Cancel</button>
                      </AlertDialog.Cancel>
                      {/* 用普通按钮而非 Action：删除期间保持弹窗打开，失败时能留在原地显示错误 */}
                      <button type="button" className="dialog-danger" disabled={deleteBusy} onClick={() => void remove()}>
                        {deleteBusy ? "Deleting…" : "Delete"}
                      </button>
                    </div>
                  </AlertDialog.Content>
                </AlertDialog.Portal>
              </AlertDialog.Root>
            )}
          </div>
          {notice && <p className="notice" role="status">{notice}</p>}

          <section className="replies" aria-labelledby="replies-title">
            <h2 className="replies-title" id="replies-title">{replies.length} {replies.length === 1 ? "reply" : "replies"}</h2>
            {replies.length === 0 && <p className="empty-state">No replies yet. Start the conversation.</p>}
            <div className="reply-list">
              {renderReplies(null)}
            </div>

            {!user ? (
              <p className="empty-state">Sign in to join the conversation. <a className="sender" href="/login">Sign in</a></p>
            ) : detail.isLocked ? (
              <p className="empty-state">This discussion is locked.</p>
            ) : (
              <form className="reply-form" onSubmit={submitReply} noValidate>
                {replyingTo !== null && (
                  <div className="replying-banner">
                    Replying to @{replies.find((reply) => reply.id === replyingTo)?.author.handle ?? "comment"}
                    <button type="button" className="reply-cancel" onClick={() => setReplyingTo(null)} aria-label="Cancel reply">Cancel</button>
                  </div>
                )}
                <label className="form-field body-field">
                  <div className="body-field-head">
                    <span>Message</span>
                    <div className="format-toggle reply-format-toggle" role="group" aria-label="Text format">
                      <button type="button" className={`format-toggle-btn ${replyFormat === "markdown" ? "active" : ""}`} aria-pressed={replyFormat === "markdown"} onClick={() => setReplyFormat("markdown")}>Markdown</button>
                      <button type="button" className={`format-toggle-btn ${replyFormat === "text" ? "active" : ""}`} aria-pressed={replyFormat === "text"} onClick={() => setReplyFormat("text")}>Plain text</button>
                    </div>
                  </div>
                  <textarea ref={replyInputRef} value={replyText} onChange={(e) => setReplyText(e.target.value)} rows={4} placeholder={replyingTo === null ? "Add to the discussion…" : "Write a reply…"} />
                </label>
                <div className="editor-actions">
                  <button className="attachment-action" type="button" onClick={() => flash("Attachments aren’t wired up yet — plain text works fine.")}>Add attachment</button>
                  <div className="submit-actions">
                    <button className="primary-action" type="submit" disabled={busy || !replyText.trim()}>
                      Reply
                    </button>
                  </div>
                </div>
              </form>
            )}
          </section>
        </article>
      </main>
    </AppShell>
  );
}

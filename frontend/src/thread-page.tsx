import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Loading } from "./loading";
import { api, type DiscussionDetail, type ReplyDTO, type BodyFormat } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatTime } from "./lib/format";
import { useIsomorphicLayoutEffect } from "./lib/use-isomorphic-layout-effect";
import { AppShell } from "./app-shell";
import { AttachmentList } from "./attachment-list";
import { ThreadIcon } from "./icons";

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

  // 可见回复：软删的评论及其整棵子树都不显示（后端只软删父、不级联）。
  // replies 由后端按 created_at 升序返回，父恒先于子，稳定收敛即整棵剪除。
  const shownReplies = useMemo(() => {
    const hidden = new Set<number>();
    let changed = true;
    while (changed) {
      changed = false;
      for (const r of replies) {
        if (r.isDeleted && !hidden.has(r.id)) { hidden.add(r.id); changed = true; continue; }
        if (r.parentReplyId !== null && hidden.has(r.parentReplyId) && !hidden.has(r.id)) { hidden.add(r.id); changed = true; }
      }
    }
    return replies.filter((r) => !hidden.has(r.id));
  }, [replies]);

  // ---- SVG 连接线几何 ----
  // 连接线由一张覆盖整棵回复树的 SVG overlay 依据实测头像几何绘制：
  // 父节点/子树拥有垂直主干，子节点只拥有接入该主干的一段圆角分支。
  const listRef = useRef<HTMLDivElement>(null);
  const avatarRefs = useRef(new Map<number, HTMLSpanElement>());
  const connectorSvgRef = useRef<SVGSVGElement>(null);
  const connectorPathRef = useRef<SVGPathElement>(null);
  const replyAnimations = useRef(new Map<Element, Animation>());
  const connectorFrame = useRef<number | null>(null);
  const hasLoaded = useRef(false);

  const computeConnectors = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    const base = list.getBoundingClientRect();

    // 以回复树容器左上角为原点，测出每个头像的中心/左边。
    const geo = new Map<number, { cx: number; cy: number; left: number }>();
    for (const [id, el] of avatarRefs.current) {
      const r = el.getBoundingClientRect();
      geo.set(id, {
        cx: r.left + r.width / 2 - base.left,
        cy: r.top + r.height / 2 - base.top,
        left: r.left - base.left,
      });
    }

    const kidsOf = new Map<number, ReplyDTO[]>();
    for (const r of shownReplies) {
      if (r.parentReplyId === null) continue;
      const arr = kidsOf.get(r.parentReplyId) ?? [];
      arr.push(r);
      kidsOf.set(r.parentReplyId, arr);
    }

    const GAP = 8;     // 分支在头像前停下的间距
    const RADIUS = 12; // 弯头圆角半径
    const paths: { d: string }[] = [];

    for (const [parentId, kids] of kidsOf) {
      const parent = geo.get(parentId);
      if (!parent) continue;

      // 使用实际可见坐标排序；没有可用水平空间的节点不生成弯头。
      const branches = kids.flatMap((kid) => {
        const child = geo.get(kid.id);
        if (!child) return [];
        const targetX = child.left - GAP;
        const span = targetX - parent.cx;
        const rise = child.cy - parent.cy;
        if (span <= 0 || rise <= 0) return [];
        // 半径不能大于水平/垂直空间，不设可能导致反向折返的下限。
        const rad = Math.min(RADIUS, span, rise);
        return [{ cy: child.cy, targetX, rad }];
      }).sort((a, b) => a.cy - b.cy);
      if (branches.length === 0) continue;

      // 主干和最后一个弯头是一条连续路径：拐弯后不再向下延伸。
      const last = branches[branches.length - 1];
      paths.push({
        d: `M ${parent.cx} ${parent.cy} V ${last.cy - last.rad}` +
          ` Q ${parent.cx} ${last.cy} ${parent.cx + last.rad} ${last.cy}` +
          ` H ${last.targetX}`,
      });

      // 中间分支保留继续向下的主干，所有子路径在同一次 stroke 中绘制。
      for (const branch of branches.slice(0, -1)) {
        paths.push({
          d: `M ${parent.cx} ${branch.cy - branch.rad}` +
            ` Q ${parent.cx} ${branch.cy} ${parent.cx + branch.rad} ${branch.cy}` +
            ` H ${branch.targetX}`,
        });
      }
    }

    // SVG 由此函数统一写入，动画帧不触发整页 React render。
    connectorSvgRef.current?.setAttribute("width", String(base.width));
    connectorSvgRef.current?.setAttribute("height", String(base.height));
    connectorPathRef.current?.setAttribute("d", paths.map((p) => p.d).join(" "));
  }, [shownReplies]);

  useLayoutEffect(() => {
    computeConnectors();
  }, [computeConnectors, replyingTo]);

  // 输入框移动后再聚焦，避免先聚焦旧的底部输入框导致页面跳动。
  useLayoutEffect(() => {
    if (replyingTo !== null) replyInputRef.current?.focus({ preventScroll: true });
  }, [replyingTo]);

  // 回复对象被删除（或被软删剪除）后，将草稿放回底部。
  useEffect(() => {
    if (replyingTo !== null && !shownReplies.some((r) => r.id === replyingTo)) {
      captureReplies();
      setReplyingTo(null);
    }
  }, [shownReplies, replyingTo]);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    let alive = true;
    const recompute = () => {
      if (alive) computeConnectors();
    };
    const ro = new ResizeObserver(recompute);
    ro.observe(list);
    window.addEventListener("resize", recompute);
    document.fonts?.ready.then(recompute).catch(() => {});
    return () => {
      alive = false;
      ro.disconnect();
      window.removeEventListener("resize", recompute);
    };
  }, [computeConnectors]);
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

  // 只移动各自的 rcard；嵌套的 rnode 容器不参与 transform。
  const replyCardRefs = useRef(new Map<number, HTMLDivElement>());
  const repliesFrom = useRef<Map<number, number> | null>(null);

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

  // 回复列表 FLIP：在增删前记录每条回复当前的 top，供删除补位 / 新增让位对比。
  const captureReplies = () => {
    const m = new Map<number, number>();
    const top = listRef.current?.getBoundingClientRect().top ?? 0;
    // 先读当前视觉位置（包括尚未结束的动画），再取消旧动画。
    for (const [id, el] of replyCardRefs.current) m.set(id, el.getBoundingClientRect().top - top);
    for (const animation of replyAnimations.current.values()) animation.cancel();
    replyAnimations.current.clear();
    if (connectorFrame.current !== null) cancelAnimationFrame(connectorFrame.current);
    connectorFrame.current = null;
    repliesFrom.current = m;
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

  // 全部终态坐标读完后再写动画，避免测量受到先启动的动画影响。
  useLayoutEffect(() => {
    const from = repliesFrom.current;
    if (!from) return;
    repliesFrom.current = null;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      computeConnectors();
      return;
    }
    const top = listRef.current?.getBoundingClientRect().top ?? 0;
    const targets = Array.from(replyCardRefs.current, ([replyId, el]) => ({
      el,
      old: from.get(replyId),
      next: el.getBoundingClientRect().top - top,
    }));
    for (const { el, old, next } of targets) {
      const dy = old === undefined ? 0 : old - next;
      if (old !== undefined && Math.abs(dy) <= 0.5) continue;
      const animation = el.animate(
        old === undefined
          ? [{ opacity: 0 }, { opacity: 1 }]
          : [{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }],
        { duration: old === undefined ? 220 : 280, easing: "cubic-bezier(.22, .8, .24, 1)", fill: "both" },
      );
      replyAnimations.current.set(el, animation);
      animation.onfinish = () => {
        if (replyAnimations.current.get(el) !== animation) return;
        replyAnimations.current.delete(el);
        animation.cancel(); // 释放 fill，后续布局与测量恢复到 CSS 基线。
      };
    }
    // transform 不会触发 ResizeObserver；运动期间实测头像来保持连线贴合。
    // 卡片的动画只使用 transform/opacity；SVG path 更新仍在主线程。
    const sync = () => {
      connectorFrame.current = null;
      computeConnectors();
      if (replyAnimations.current.size > 0) {
        connectorFrame.current = requestAnimationFrame(sync);
      }
    };
    sync();
  }, [replies, replyingTo, computeConnectors]);

  useEffect(() => () => {
    if (connectorFrame.current !== null) cancelAnimationFrame(connectorFrame.current);
    for (const animation of replyAnimations.current.values()) animation.cancel();
    replyAnimations.current.clear();
  }, []);

  const load = useCallback(async (options: { animateReplies?: boolean; closeComposer?: boolean; clearReplyTarget?: boolean } = {}) => {
    try {
      const [d, r] = await Promise.all([api.discussions.get(id), api.discussions.replies(id)]);
      // 请求都完成后，紧贴 React 更新捕获旧布局；表单和列表同批提交。
      if (options.animateReplies) captureReplies();
      if (options.closeComposer) {
        setReplyText("");
        setReplyingTo(null);
      }
      if (options.clearReplyTarget) setReplyingTo(null);
      hasLoaded.current = true;
      setNotFound(false);
      setDetail(d);
      setReplies(r.items);
      setEditTitle(d.title);
      setEditBody(d.bodyMarkdown);
      setEditFormat(d.bodyFormat);
    } catch (error) {
      if (hasLoaded.current) throw error;
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load().catch(() => setNotice("Could not refresh this discussion."));
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
    if (!detail || detail.isLocked || busy || !replyText.trim()) return;
    setBusy(true);
    let posted = false;
    try {
      await api.discussions.createReply(detail.id, {
        bodyMarkdown: replyText.trim(),
        bodyFormat: replyFormat,
        parentReplyId: replyingTo,
      });
      posted = true;
      await load({ animateReplies: true, closeComposer: true });
    } catch {
      if (posted) {
        // 服务端已经接收，避免保留可再次提交的相同草稿。
        captureReplies();
        setReplyText("");
        setReplyingTo(null);
      }
      flash(posted ? "Reply posted, but the list could not refresh. Reload the page." : "Could not post your reply. Your draft is still here.");
    } finally {
      setBusy(false);
    }
  };

  const removeReply = async (reply: ReplyDTO) => {
    if (busy) return;
    setBusy(true);
    let removed = false;
    try {
      await api.discussions.delReply(reply.id);
      removed = true;
      await load({ animateReplies: true, clearReplyTarget: replyingTo === reply.id });
    } catch {
      flash(removed ? "Reply deleted, but the list could not refresh. Reload the page." : "Could not delete this reply.");
    } finally {
      setBusy(false);
    }
  };

  const changeReplyTarget = (target: number | null) => {
    if (busy) return;
    if (replyingTo === target) {
      replyInputRef.current?.focus({ preventScroll: true });
      return;
    }
    captureReplies();
    setReplyingTo(target);
  };

  const replyTo = (reply: ReplyDTO) => changeReplyTarget(reply.id);

  const replyIds = new Set(shownReplies.map((reply) => reply.id));
  const repliesByParent = shownReplies.reduce<Map<number | null, ReplyDTO[]>>((groups, reply) => {
    const parentId = reply.parentReplyId !== null && !replyIds.has(reply.parentReplyId) ? null : reply.parentReplyId;
    const group = groups.get(parentId) ?? [];
    group.push(reply);
    groups.set(parentId, group);
    return groups;
  }, new Map());

  // 首字母头像（沿用项目“首字母圆形”约定；无真实头像图源）
  const initialOf = (r: ReplyDTO) =>
    (r.author.displayName || r.author.handle || r.author.username || "?").trim().charAt(0).toUpperCase();

  // 普通渲染函数：共享一个草稿，切换回复对象时不会丢失文字。
  const renderReplyForm = (target?: ReplyDTO): ReactNode => (
    <form className={target ? "reply-form reply-form-inline content-fade" : "reply-form"} onSubmit={submitReply} noValidate>
      {target && (
        <div className="replying-banner">
          Replying to @{target.author.handle}
          <button type="button" className="reply-cancel" disabled={busy} onClick={() => changeReplyTarget(null)} aria-label="Cancel reply">Cancel</button>
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
        <textarea ref={replyInputRef} value={replyText} onChange={(e) => setReplyText(e.target.value)} disabled={busy} rows={target ? 3 : 4} placeholder={!target ? "Add to the discussion…" : "Write a reply…"} />
      </label>
      <div className="submit-actions">
        <button className="primary-action" type="submit" disabled={busy || !replyText.trim()}>
          <ThreadIcon /> Reply
        </button>
      </div>
    </form>
  );

  // YouTube 式树节点：avatar 即树节点。一条回复 = 一个节点；
  // 这里只渲染结构与逻辑，并把头像挂到 avatarRefs 供 SVG overlay 实测几何。
  const renderReplyNode = (reply: ReplyDTO, depth: number, index: number, siblings: ReplyDTO[]): ReactNode => {
    const kids = repliesByParent.get(reply.id) ?? [];
    const hasKids = kids.length > 0;
    const isTop = depth === 0;
    const isLast = index === siblings.length - 1;
    const cls = [
      "rnode",
      isTop ? "top" : "nested",
      hasKids ? "has-kids" : "leaf",
      isLast ? "last" : "",
      `d${Math.min(depth, 4)}`,
    ].filter(Boolean).join(" ");
    const canDelete = isStaff || user?.id === reply.author.id;
    return (
      <div className={cls} key={reply.id}>
        <div
          className={`rcard${hasKids ? " has-kids" : ""}`}
          ref={(el) => {
            if (el) replyCardRefs.current.set(reply.id, el);
            else replyCardRefs.current.delete(reply.id);
          }}
        >
          <span
            className="ravatar"
            aria-hidden="true"
            ref={(el) => {
              if (el) avatarRefs.current.set(reply.id, el);
              else avatarRefs.current.delete(reply.id);
            }}
          >
            {initialOf(reply)}
          </span>
          <div className="rcnt">
            <div className="ra-head">
              <a className="sender" href={`/profile?username=${encodeURIComponent(reply.author.username)}`}>{reply.author.displayName}</a>
              <a className="muted-link" href={`/profile?username=${encodeURIComponent(reply.author.username)}`}>@{reply.author.handle}</a>
              <span className="dot" />
              <span className="ra-time">{formatTime(reply.createdAt)}</span>
            </div>
            {reply.isDeleted ? (
              <p className="ra-deleted">This reply was removed.</p>
            ) : reply.bodyHtml ? (
              <div className="ra-body" dangerouslySetInnerHTML={{ __html: reply.bodyHtml }} />
            ) : (
              <p className="ra-body plain">{reply.bodyMarkdown}</p>
            )}
            <div className="ra-actions">
              {!reply.isDeleted && user && !detail?.isLocked && (
                <button className="ra-btn" type="button" disabled={busy} aria-expanded={replyingTo === reply.id} onClick={() => replyTo(reply)}>Reply</button>
              )}
              {canDelete && (
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="ra-btn danger" type="button" disabled={busy}>Delete</button>
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
                          <button type="button" className="dialog-danger" disabled={busy} onClick={() => void removeReply(reply)}>Delete</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              )}
            </div>
            {replyingTo === reply.id && !reply.isDeleted && user && !detail?.isLocked && renderReplyForm(reply)}
          </div>
        </div>
        {hasKids && renderReplies(reply.id, depth + 1)}
      </div>
    );
  };

  const renderReplies = (parentReplyId: number | null, depth = 0): ReactNode => {
    const children = repliesByParent.get(parentReplyId) ?? [];
    if (children.length === 0) return null;
    return (
      <div className={parentReplyId === null ? "reply-children is-root" : "reply-children"}>
        {children.map((reply, i) => renderReplyNode(reply, depth, i, children))}
      </div>
    );
  };

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

          <AttachmentList items={detail.attachments} />

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
            <h2 className="replies-title" id="replies-title">{shownReplies.length} {shownReplies.length === 1 ? "reply" : "replies"}</h2>
            {shownReplies.length === 0 && <p className="empty-state">No replies yet. Start the conversation.</p>}
            <div className="reply-list" ref={listRef}>
              <svg ref={connectorSvgRef} className="reply-connectors" width={0} height={0} aria-hidden="true">
                {/* 单次描边避免半透明分支在接缝处重复叠色。 */}
                <path
                  ref={connectorPathRef}
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {renderReplies(null)}
            </div>

            {!user ? (
              <p className="empty-state">Sign in to join the conversation. <a className="sender" href="/login">Sign in</a></p>
            ) : detail.isLocked ? (
              <p className="empty-state">This discussion is locked.</p>
            ) : (
              replyingTo === null ? renderReplyForm() : null
            )}
          </section>
        </article>
      </main>
    </AppShell>
  );
}

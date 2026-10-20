import { LakoButton } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { api, ApiError, type FileResourceDetail } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatBytes } from "./lib/format";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { MarkdownText } from "./lib/markdown-text";
import { FileMetadataDialog } from "./file-metadata-dialog";
import { ConfirmDialog } from "./ui-commons";

// 文件服务详情页。
// File-service detail page.
//
// 结构取舍：元信息区做成结构化的键值表（借鉴 GitHub Releases 的附件元信息呈现），
// 因为"多大、什么格式、谁传的、多久前更新"是学生判断"这份资料值不值得下载"的全部依据。
// Layout decision: the metadata block is a structured key/value table (following how
// GitHub Releases presents asset metadata), because size, format, uploader and recency are
// the complete basis on which a student decides whether a resource is worth downloading.

const VISIBILITY_KEYS: Record<string, I18nKey> = {
  public: "file.visibilityPublic",
  members: "file.visibilityMembers",
  private: "file.visibilityPrivate",
};

// 可内联预览的扩展名：与后端 files/router.py 的 INLINE_EXTENSIONS 保持一致。
// Extensions previewable inline, kept in step with INLINE_EXTENSIONS in the backend's
// files/router.py.
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"];
const TEXT_EXTENSIONS = [".txt", ".md", ".csv"];
// 音视频：浏览器能原生解码的容器。与后端 INLINE_EXTENSIONS 的媒体子集一致——
// 后端决定"能不能内联取字节"，前端决定"用哪种播放器呈现"。
// A/V: containers browsers decode natively. Mirrors the media subset of the backend's
// INLINE_EXTENSIONS: the backend decides whether bytes may be fetched inline, the client
// decides which player renders them.
const VIDEO_EXTENSIONS = [".mp4", ".m4v", ".webm", ".ogv", ".mov"];
const AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"];
const TEXT_PREVIEW_LIMIT = 4000;

type PreviewKind = "pdf" | "image" | "text" | "video" | "audio";

// 单一判定入口：三处（取字节、能否预览、渲染哪个元素）都走它，避免三份白名单漂移。
// One place decides the preview strategy, so the three call sites (fetch bytes, show the
// preview section, pick the element) cannot drift apart.
function previewKindOf(extension: string): PreviewKind | null {
  if (extension === ".pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  if (TEXT_EXTENSIONS.includes(extension)) return "text";
  if (VIDEO_EXTENSIONS.includes(extension)) return "video";
  if (AUDIO_EXTENSIONS.includes(extension)) return "audio";
  return null;
}

function Stars({
  value,
  disabled,
  onPick,
  ariaLabel,
}: {
  value: number;
  disabled: boolean;
  onPick: (score: number) => void;
  ariaLabel: string;
}) {
  return (
    <span className="files-stars" role="group" aria-label={ariaLabel}>
      {[1, 2, 3, 4, 5].map((score) => (
        <LakoButton
          key={score}
          type="button"
          className={`files-star ${score <= value ? "filled" : ""}`}
          disabled={disabled}
          aria-label={`${score}`}
          aria-pressed={score <= value}
          onClick={() => onPick(score)}
        >
          {/* 用 SVG 星形而不是星号字符：字符在不同字体下可能被渲染成彩色 emoji。
              An SVG star rather than a star glyph, which some fonts render as a colour emoji. */}
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z" />
          </svg>
        </LakoButton>
      ))}
    </span>
  );
}

// 点星之后就地预测新的评分状态，让星标与均分立刻变化，而不是等一次网络往返。
// 均分是"总分 / 人数"四舍五入到两位，所以可以从均分反推整数总分：
// |avg * count - sum| <= 0.005 * count，因此人数少于 100 时反推必然精确；样本更大时最多差 1 分，
// 并且服务器返回值一到就被覆盖。
// Predicted rating state applied the moment a star is clicked, so the stars and the average move
// immediately instead of waiting for a round trip. The average is the sum over the count rounded to
// two decimals, so the integer sum is recovered from it: |avg * count - sum| <= 0.005 * count,
// which makes the recovery exact below 100 ratings and at worst one point off above that, and the
// server's own values replace the prediction as soon as the response lands.
function predictRating(
  current: { myRating: number | null; ratingAvg: number | null; ratingCount: number },
  score: number,
): { myRating: number; ratingAvg: number | null; ratingCount: number } {
  const previous = current.myRating;
  const sum = current.ratingAvg === null || current.ratingCount === 0
    ? 0
    : Math.round(current.ratingAvg * current.ratingCount);
  const nextSum = sum + score - (previous ?? 0);
  // 改分是覆盖式、不新增人数；只有首次评分才 +1。
  // Changing one's own score overwrites it and adds no vote; only a first rating adds one.
  const nextCount = previous === null ? current.ratingCount + 1 : current.ratingCount;
  return {
    myRating: score,
    ratingCount: nextCount,
    ratingAvg: nextCount === 0 ? null : Math.round((nextSum / nextCount) * 100) / 100,
  };
}

export function FileDetailPage({ id }: { id: number }) {
  const { t, locale } = useI18n();
  const { user, loading: authLoading } = useAuth();

  const [detail, setDetail] = useState<FileResourceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [actionError, setActionError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewText, setPreviewText] = useState("");
  // 播放器报错（容器/编码浏览器解不了）：退化成"请下载后观看"，而不是留一块黑框。
  // A player error (container or codec the browser cannot decode) degrades to "download to
  // view" instead of leaving a blank black box.
  const [mediaBroken, setMediaBroken] = useState(false);
  // 文档预览能力，由 /api/files/config 下发（后端装了转换器才为 true）。null = 还没拿到：
  // 这期间既不说"支持"也不说"不支持"，避免先闪一下"无法预览"再变成 iframe。
  // Document-preview capability comes from /api/files/config (true only when the backend
  // actually has a converter). null means "not known yet", so the UI does not flash
  // "cannot preview" and then swap to an iframe.
  const [docPreview, setDocPreview] = useState<{ available: boolean; extensions: string[] } | null>(null);
  const [busyDownload, setBusyDownload] = useState(false);
  // 元数据编辑弹窗是否打开。入口只在 detail.can.update 为真时渲染（= 后端 FILE_UPDATE）。
  // Whether the metadata dialog is open. The entry point renders only when detail.can.update is
  // true, which is the backend's FILE_UPDATE.
  const [metaOpen, setMetaOpen] = useState(false);
  // 评分请求进行中：用于禁用星标、避免重复提交并驱动 aria-busy。
  // A rating request is in flight: disables the stars, prevents double submission and drives
  // aria-busy.
  const [ratingBusy, setRatingBusy] = useState(false);
  // Open to Public 切换请求进行中：用于禁用开关、避免重复提交。
  // The Open to Public toggle request is in flight: disables the switch and prevents double submit.
  const [publicBusy, setPublicBusy] = useState(false);
  // 「设为公开」的二次确认弹窗是否打开。取消公开是低风险方向，直接执行。
  // Whether the "make public" confirmation dialog is open. Un-publishing is the low-risk direction
  // and runs directly.
  const [publicConfirmOpen, setPublicConfirmOpen] = useState(false);
  // 公开切换成功后的可见反馈（与 actionError 的失败反馈互补）。
  // The visible success feedback after a public toggle (complementing the failure feedback in
  // actionError).
  const [publicNotice, setPublicNotice] = useState("");

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 取一次文档预览能力。失败当作"不支持"：宁可少一个增强功能，也不挂一个必然失败的 iframe。
  // Fetched once. A failure is treated as "unsupported": better to lose an enhancement than to
  // mount an iframe that is guaranteed to fail.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await api.files.config();
        if (!cancelled) {
          setDocPreview({ available: cfg.documentPreview, extensions: cfg.convertibleExtensions });
        }
      } catch {
        if (!cancelled) setDocPreview({ available: false, extensions: [] });
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setLoading(true);
    setNotFound(false);
    void (async () => {
      try {
        const data = await api.files.get(id);
        if (mountedRef.current && !cancelled) setDetail(data);
      } catch (error) {
        // 404 涵盖"不存在"与"无权查看"两种情况，刻意不区分。
        // A 404 covers both "missing" and "not allowed", deliberately indistinguishable.
        if (mountedRef.current && !cancelled) setNotFound(error instanceof ApiError && error.status === 404);
      } finally {
        if (mountedRef.current && !cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id, user?.id]);

  // 预览：单独取一个不计数的签名地址，避免"看一眼"被算成下载。
  // Preview: fetch a signed URL that is not counted, so a glance is not logged as a
  // download.
  useEffect(() => {
    let cancelled = false;
    setPreviewUrl("");
    setPreviewText("");
    setMediaBroken(false);
    if (!detail) return;
    const extension = detail.extension;
    const kind = previewKindOf(extension);
    if (!kind) return;
    void (async () => {
      try {
        const ticket = await api.files.preview(detail.id);
        if (!mountedRef.current || cancelled) return;
        if (kind === "text") {
          const response = await fetch(ticket.downloadUrl, { credentials: "same-origin" });
          if (!response.ok) return;
          const text = await response.text();
          if (mountedRef.current && !cancelled) setPreviewText(text.slice(0, TEXT_PREVIEW_LIMIT));
        } else {
          setPreviewUrl(ticket.downloadUrl);
        }
      } catch {
        // 预览失败是纯增强功能，不该打断页面：静默降级为"请下载后查看"。
        // A failed preview is a pure enhancement and must not break the page: degrade
        // silently to "download to view".
        if (mountedRef.current && !cancelled) setPreviewUrl("");
      }
    })();
    return () => { cancelled = true; };
  }, [detail]);

  const onDownload = async () => {
    if (!detail) return;
    setActionError("");
    setBusyDownload(true);
    try {
      const ticket = await api.files.download(detail.id);
      const anchor = document.createElement("a");
      anchor.href = ticket.downloadUrl;
      anchor.download = ticket.originalFilename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setDetail({ ...detail, downloadCount: detail.downloadCount + 1 });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setActionError(t("file.downloadSignIn"));
      else setActionError(t("file.downloadFail"));
    } finally {
      setBusyDownload(false);
    }
  };

  const onToggleFavorite = async () => {
    if (!detail) return;
    if (!user) {
      setActionError(t("file.signInToSave"));
      return;
    }
    setActionError("");
    try {
      const next = await api.files.setFavorite(detail.id, !detail.isFavorited);
      setDetail({ ...detail, isFavorited: next.isFavorited, favoriteCount: next.favoriteCount });
    } catch {
      setActionError(t("file.saveFail"));
    }
  };

  const onRate = async (score: number) => {
    if (!detail) return;
    if (!user) {
      setActionError(t("file.signInToSave"));
      return;
    }
    // 进行中的请求不再受理第二次点击：否则并发返回的顺序会让界面停在旧分数上。
    // A click is ignored while a request is in flight, or out-of-order responses would leave the
    // page showing a stale score.
    if (ratingBusy) return;
    setActionError("");
    const previous = { myRating: detail.myRating, ratingAvg: detail.ratingAvg, ratingCount: detail.ratingCount };
    // 先就地更新：点星立刻有视觉反馈，不必等往返；服务器返回后再用权威值校正。
    // Update in place first, so a click gives immediate visual feedback rather than waiting for the
    // round trip, and reconcile with the authoritative values once the response arrives.
    setDetail((row) => (row ? { ...row, ...predictRating(previous, score) } : row));
    setRatingBusy(true);
    try {
      const next = await api.files.setRating(detail.id, score);
      setDetail((row) =>
        row ? { ...row, myRating: next.myRating, ratingAvg: next.ratingAvg, ratingCount: next.ratingCount } : row,
      );
    } catch {
      // 失败必须回滚并给出可见提示，否则界面会停在服务器并不认可的那个分数上。
      // A failure has to roll back and say so, otherwise the page would sit on a score the server
      // never accepted.
      setDetail((row) => (row ? { ...row, ...previous } : row));
      setActionError(t("file.ratingFail"));
    } finally {
      setRatingBusy(false);
    }
  };

  // Open to Public 切换（管理员专属）：PATCH 后用响应体整体回填详情，切换后立即生效。
  // 普通用户看不到这个开关（下方渲染处按 user?.role === "admin" 过滤），后端 require_admin 是
  // 真正的安全边界。
  // The Open to Public toggle (admins only): after the PATCH the whole detail is replaced from the
  // response, so the switch takes effect immediately. Plain users never see the switch (the render
  // below gates on user?.role === "admin"), and the backend's require_admin is the real boundary.
  const applyPublic = async (target: boolean) => {
    if (!detail) return;
    setActionError("");
    setPublicNotice("");
    setPublicBusy(true);
    try {
      const next = await api.files.setPublic(detail.id, target);
      setDetail(next);
      setPublicNotice(target ? t("file.setPublicDone") : t("file.unsetPublicDone"));
    } catch {
      setActionError(t("file.setPublicFail"));
    } finally {
      setPublicBusy(false);
    }
  };

  const onTogglePublic = (target: boolean) => {
    if (!detail) return;
    // 设为公开是高风险的、难以撤销的暴露，必须二次确认；取消公开是低风险方向，直接执行。
    // Making a resource public is a high-risk, hard-to-reverse exposure and must be confirmed;
    // un-publishing is the low-risk direction and runs directly.
    if (target && !detail.isPublic) {
      setPublicConfirmOpen(true);
      return;
    }
    void applyPublic(target);
  };

  if (loading || authLoading) {
    return (
      <AppShell current="files">
        <main className="shell files-layout">
          <Loading label={t("common.loading")} />
        </main>
      </AppShell>
    );
  }

  if (notFound || !detail) {
    return (
      <AppShell current="files">
        <main className="shell files-layout">
          <div className="empty-state">
            <p>{t("file.notFound")}</p>
            <a className="files-link" href="/files">{t("file.backToList")}</a>
          </div>
        </main>
      </AppShell>
    );
  }

  const extension = detail.extension;
  const previewKind = previewKindOf(extension);
  // Office 文档：后端转成 PDF，iframe 直接指到转换端点（它用会话 Cookie 鉴权，
  // 不必把一次性签名拼进 iframe 地址）。只有后端声明支持且该后缀在白名单里才挂。
  // Office documents: the backend converts to PDF and the iframe points straight at the
  // conversion endpoint, which authenticates by session cookie — no one-shot signature in the
  // URL. Mounted only when the backend reports support and the extension is allow-listed.
  const documentPreviewUrl =
    previewKind === null && docPreview?.available === true && docPreview.extensions.includes(extension)
      ? `/api/files/resources/${detail.id}/document-preview`
      : "";
  // 能力未知且不是原生可预览类型：处于"可能支持"的中间态，先显示加载而不是"无法预览"。
  const resolvingDocument = previewKind === null && docPreview === null && !mediaBroken;
  const canPreviewInline = previewKind !== null || documentPreviewUrl !== "";

  return (
    <AppShell current="files">
      <main className="shell files-layout">
        <nav className="files-breadcrumb" aria-label={t("file.title")}>
          <a href="/files">{t("file.title")}</a>
          <span aria-hidden="true">/</span>
          <a href={`/files?category=${encodeURIComponent(detail.category.slug)}`}>{detail.category.name}</a>
        </nav>

        <header className="files-detail-header">
          <h1 className="files-detail-title">{detail.title}</h1>
          <div className="files-detail-badges">
            <span className="files-badge">{detail.category.name}</span>
            <span className="files-badge">{t(VISIBILITY_KEYS[detail.visibility] ?? "file.visibilityMembers")}</span>
            {detail.isPublic ? <span className="files-badge files-badge-public">{t("file.openToPublic")}</span> : null}
          </div>
        </header>

        <section className="files-detail-actions">
          {/* 元数据编辑入口：只出现在"能写这份资料"的人面前（管理员 或 该资料的 owner）。
              这正是后端 authz 的 FILE_UPDATE，前端不另发明一套判定。 */}
          {/* The metadata editing entry point appears only to someone who may write this resource
              (an admin or its owner), which is exactly the backend's FILE_UPDATE; no separate rule
              is invented on the client. */}
          {detail.can.update ? (
            <LakoButton type="button" variant="secondary" size="sm" onClick={() => setMetaOpen(true)}>
              {t("file.metaEdit")}
            </LakoButton>
          ) : null}
          {user || detail.visibility === "public" ? (
            <LakoButton type="button" variant="primary" size="sm" onClick={() => void onDownload()} disabled={busyDownload}>
              {t("file.download")}
            </LakoButton>
          ) : (
            // 下载权限只看"这条资料本身是否对当前访客开放"，不能只看"是否登录"。
            // 公开资料对匿名访客本就允许下载（列表页也给匿名访客下载入口、后端也放行），
            // 详情页若只按 user 判断就会出现"列表能下、详情只给登录链接"的自相矛盾。
            // Download eligibility depends on whether this resource is open to the current
            // visitor, not on whether they are signed in. A public resource is downloadable by a
            // signed-out visitor (the list page offers it and the backend allows it), so keying
            // only off `user` produced the contradiction of a list that downloads while the detail
            // page offers nothing but a sign-in link.
            <a className="files-primary" href="/login">{t("file.downloadSignIn")}</a>
          )}
          <LakoButton
            type="button"
            className={`files-secondary ${detail.isFavorited ? "active" : ""}`}
            aria-pressed={detail.isFavorited}
            onClick={() => void onToggleFavorite()}
          >
            {detail.isFavorited ? t("file.favorited") : t("file.favorite")}
            <span className="files-chip-count">{detail.favoriteCount}</span>
          </LakoButton>
          <span className="files-rate" aria-busy={ratingBusy}>
            <span className="files-muted">{t("file.ratingLabel")}</span>
            <Stars
              value={detail.myRating ?? 0}
              disabled={!user || ratingBusy}
              onPick={(score) => void onRate(score)}
              ariaLabel={t("file.ratingLabel")}
            />
            <span className="files-muted">
              {detail.ratingAvg === null || detail.ratingCount === 0
                ? t("file.ratingNone")
                : `${t("file.ratingValue", { value: detail.ratingAvg.toFixed(1) })} · ${t("file.ratingCount", { count: detail.ratingCount })}`}
            </span>
          </span>
        </section>

        {actionError ? <div className="files-banner files-banner-error" role="status">{actionError}</div> : null}

        <div className="files-detail-grid">
          <section className="files-detail-main">
            <h2 className="files-section-title">{t("file.description")}</h2>
            {detail.descriptionMarkdown ? (
              <MarkdownText source={detail.descriptionMarkdown} className="files-description" />
            ) : (
              <p className="files-muted">{t("file.noDescription")}</p>
            )}

            <h2 className="files-section-title">{t("file.preview")}</h2>
            {mediaBroken ? (
              <p className="files-muted">{t("file.mediaUnsupported")}</p>
            ) : documentPreviewUrl ? (
              <>
                <p className="files-muted">{t("file.docPreviewHint")}</p>
                <iframe className="files-preview-frame" src={documentPreviewUrl} title={detail.title} />
                <p className="files-muted">
                  <a href={documentPreviewUrl} target="_blank" rel="noreferrer">{t("file.openPreviewInTab")}</a>
                </p>
              </>
            ) : !canPreviewInline && !resolvingDocument ? (
              <p className="files-muted">{t("file.previewUnsupported")}</p>
            ) : previewKind === "pdf" && previewUrl ? (
              <>
                <iframe className="files-preview-frame" src={previewUrl} title={detail.title} />
                {/* 移动端 Safari 等场景 iframe 里的 PDF 可能整块空白，给一个原生查看器入口。
                    Mobile Safari and friends can render an empty iframe instead of the PDF, so
                    offer the native viewer as an escape hatch. */}
                <p className="files-muted">
                  <a href={previewUrl} target="_blank" rel="noreferrer">{t("file.openPreviewInTab")}</a>
                </p>
              </>
            ) : previewKind === "video" && previewUrl ? (
              <video
                className="files-preview-video"
                src={previewUrl}
                controls
                playsInline
                preload="metadata"
                onError={() => setMediaBroken(true)}
              />
            ) : previewKind === "audio" && previewUrl ? (
              <audio
                className="files-preview-audio"
                src={previewUrl}
                controls
                preload="metadata"
                onError={() => setMediaBroken(true)}
              />
            ) : previewKind === "image" && previewUrl ? (
              <img className="files-preview-image" src={previewUrl} alt={detail.title} />
            ) : previewKind === "text" && previewText ? (
              <pre className="files-preview-text">{previewText}</pre>
            ) : (
              <p className="files-muted">{t("common.loading")}</p>
            )}
          </section>

          <aside className="files-detail-side">
            <dl className="files-meta">
              <dt>{t("file.chooseFile")}</dt>
              <dd className="files-meta-filename">{detail.originalFilename}</dd>
              <dt>{t("file.tableFormat")}</dt>
              <dd>{(extension || "?").replace(".", "").toUpperCase()}</dd>
              <dt>{t("file.tableSize")}</dt>
              <dd>{formatBytes(detail.sizeBytes)}</dd>
              <dt>{t("file.by")}</dt>
              <dd>{detail.uploader.displayName}</dd>
              <dt>{t("file.uploadedAt")}</dt>
              <dd>{timeAgo(detail.createdAt, locale)}</dd>
              <dt>{t("file.tableDownloads")}</dt>
              <dd>{detail.downloadCount}</dd>
              {detail.sha256 ? (
                <>
                  <dt>{t("file.checksum")}</dt>
                  <dd className="files-meta-hash">{detail.sha256.slice(0, 12)}</dd>
                </>
              ) : null}
            </dl>
            {detail.tags.length > 0 ? (
              <div className="files-detail-tags">
                {detail.tags.map((entry) => (
                  <a key={entry} className="files-tag" href={`/files?tag=${encodeURIComponent(entry)}`}>{entry}</a>
                ))}
              </div>
            ) : null}
            {/* Open to Public 开关：仅管理员可见（后端 FILE_SET_PUBLIC 才是真边界）。
                普通用户与匿名访客这里什么都不渲染，只读地看不到开关。 */}
            {/* The Open to Public switch is visible to admins only (the backend's FILE_SET_PUBLIC is
                the real boundary). Plain users and signed-out visitors get nothing rendered here. */}
            {user?.role === "admin" ? (
              <div className="files-public-toggle">
                <label className="files-public-toggle-label">
                  <input
                    type="checkbox"
                    checked={detail.isPublic}
                    disabled={publicBusy}
                    onChange={(event) => onTogglePublic(event.target.checked)}
                  />
                  <span>{t("file.openToPublic")}</span>
                </label>
                {publicNotice ? <p className="files-muted">{publicNotice}</p> : null}
                {detail.publicSetAt != null ? (
                  <p className="files-muted">{t("file.publicLastChanged", { time: timeAgo(detail.publicSetAt, locale) })}</p>
                ) : null}
              </div>
            ) : null}
          </aside>
        </div>

        {/* 保存成功后用响应体回填详情：后端会做归一化（去重、小写、截断），
            用本地输入回填会让界面显示出与服务端不一致的标签。 */}
        {/* The saved response is written back into the detail: the backend normalises (de-duplicates,
            lower-cases, truncates), and echoing the local input would show tags that disagree with
            what was actually stored. */}
        {metaOpen && detail.can.update ? (
          <FileMetadataDialog
            detail={detail}
            onClose={() => setMetaOpen(false)}
            onSaved={(saved) => setDetail(saved)}
          />
        ) : null}

        {/* 「设为公开」二次确认：写清暴露风险，确认后才真正调后端。 */}
        {/* The "make public" confirmation spells out the exposure risk and only calls the backend
            once confirmed. */}
        {publicConfirmOpen ? (
          <ConfirmDialog
            open={publicConfirmOpen}
            onOpenChange={setPublicConfirmOpen}
            title={t("file.openToPublic")}
            description={t("file.openToPublicWarning")}
            confirmLabel={t("file.openToPublic")}
            cancelLabel={t("common.cancel")}
            danger={false}
            pending={publicBusy}
            onConfirm={() => {
              setPublicConfirmOpen(false);
              void applyPublic(true);
            }}
          />
        ) : null}
      </main>
    </AppShell>
  );
}

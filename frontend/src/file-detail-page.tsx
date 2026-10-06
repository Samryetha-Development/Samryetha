import { useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { api, ApiError, type FileResourceDetail } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatBytes } from "./lib/format";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { MarkdownText } from "./lib/markdown-text";

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

// 可内联预览的扩展名：与后端 routers/files.py 的 INLINE_EXTENSIONS 保持一致。
// Extensions previewable inline, kept in step with INLINE_EXTENSIONS in the backend's
// routers/files.py.
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"];
const TEXT_EXTENSIONS = [".txt", ".md", ".csv"];
const TEXT_PREVIEW_LIMIT = 4000;

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
        <button
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
        </button>
      ))}
    </span>
  );
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
  const [busyDownload, setBusyDownload] = useState(false);

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    void (async () => {
      try {
        const data = await api.files.get(id);
        if (mountedRef.current) setDetail(data);
      } catch (error) {
        // 404 涵盖"不存在"与"无权查看"两种情况，刻意不区分。
        // A 404 covers both "missing" and "not allowed", deliberately indistinguishable.
        if (mountedRef.current) setNotFound(error instanceof ApiError && error.status === 404);
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    })();
  }, [id, user?.id]);

  // 预览：单独取一个不计数的签名地址，避免"看一眼"被算成下载。
  // Preview: fetch a signed URL that is not counted, so a glance is not logged as a
  // download.
  useEffect(() => {
    setPreviewUrl("");
    setPreviewText("");
    if (!detail) return;
    const extension = detail.extension;
    const inline = extension === ".pdf" || IMAGE_EXTENSIONS.includes(extension) || TEXT_EXTENSIONS.includes(extension);
    if (!inline) return;
    void (async () => {
      try {
        const ticket = await api.files.preview(detail.id);
        if (!mountedRef.current) return;
        if (TEXT_EXTENSIONS.includes(extension)) {
          const response = await fetch(ticket.downloadUrl, { credentials: "same-origin" });
          if (!response.ok) return;
          const text = await response.text();
          if (mountedRef.current) setPreviewText(text.slice(0, TEXT_PREVIEW_LIMIT));
        } else {
          setPreviewUrl(ticket.downloadUrl);
        }
      } catch {
        // 预览失败是纯增强功能，不该打断页面：静默降级为"请下载后查看"。
        // A failed preview is a pure enhancement and must not break the page: degrade
        // silently to "download to view".
        if (mountedRef.current) setPreviewUrl("");
      }
    })();
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
    setActionError("");
    try {
      const next = await api.files.setRating(detail.id, score);
      setDetail({
        ...detail,
        myRating: next.myRating,
        ratingAvg: next.ratingAvg,
        ratingCount: next.ratingCount,
      });
    } catch {
      setActionError(t("file.ratingFail"));
    }
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
  const canPreviewInline =
    extension === ".pdf" || IMAGE_EXTENSIONS.includes(extension) || TEXT_EXTENSIONS.includes(extension);

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
            {detail.moderationStatus !== "approved" ? (
              <span className="files-badge files-badge-pending">{t("file.moderationPending")}</span>
            ) : null}
          </div>
          {detail.moderationStatus !== "approved" ? (
            <p className="files-muted">{t("file.pendingHint")}</p>
          ) : null}
        </header>

        <section className="files-detail-actions">
          {user ? (
            <button type="button" className="files-primary" onClick={() => void onDownload()} disabled={busyDownload}>
              {t("file.download")}
            </button>
          ) : (
            // 未登录时不给一个点不动的死按钮，而是直接把它变成登录入口。
            // A signed-out visitor gets a real sign-in link rather than a dead button.
            <a className="files-primary" href="/login">{t("file.downloadSignIn")}</a>
          )}
          <button
            type="button"
            className={`files-secondary ${detail.isFavorited ? "active" : ""}`}
            aria-pressed={detail.isFavorited}
            onClick={() => void onToggleFavorite()}
          >
            {detail.isFavorited ? t("file.favorited") : t("file.favorite")}
            <span className="files-chip-count">{detail.favoriteCount}</span>
          </button>
          <span className="files-rate">
            <span className="files-muted">{t("file.ratingLabel")}</span>
            <Stars
              value={detail.myRating ?? 0}
              disabled={!user}
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
            {!canPreviewInline ? (
              <p className="files-muted">{t("file.previewUnsupported")}</p>
            ) : previewUrl && extension === ".pdf" ? (
              <iframe className="files-preview-frame" src={previewUrl} title={detail.title} />
            ) : previewUrl ? (
              <img className="files-preview-image" src={previewUrl} alt={detail.title} />
            ) : previewText ? (
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
          </aside>
        </div>
      </main>
    </AppShell>
  );
}

// 附件列表组件：图片内联缩略图 + 点击灯箱放大；文件只提供下载（不内联打开，防注入/可执行内容）。
// Attachment list: images render as inline thumbnails with a click-to-zoom lightbox;
// files render as download-only links (never opened inline, preventing injection / executable content).
import { useCallback, useState } from "react";
import { useEscapeKey, useModalScrollLock } from "./ui-commons";
import type { AttachmentRef } from "./lib/api";
import { formatBytes } from "./lib/format";
import { useI18n } from "./lib/i18n";

export function AttachmentList({
  items,
  onRemove,
  onPromote,
  promoted,
}: {
  items: AttachmentRef[];
  onRemove?: (id: number) => void;
  // 管理员专属入口：调用方按既有身份来源决定传不传这个回调，
  // 普通用户不传 => 按钮根本不进 DOM（不是 disabled，也不是 CSS 隐藏）。
  // The admin-only entry point: the caller decides whether to pass this callback based on the
  // existing identity source. A normal user passes nothing, so the button never reaches the
  // DOM at all (it is not disabled and not hidden with CSS).
  onPromote?: (attachment: AttachmentRef) => void;
  // 已转入文件服务的附件 -> 新建的资料：用于在附件行就地显示"已转入"徽标并直达资料。
  // Attachment id -> the resource it was promoted into, so the row can show an in-place
  // "promoted" badge that links straight to the resource.
  promoted?: Record<number, { id: number; title: string }>;
}) {
  const { t } = useI18n();
  const [lightbox, setLightbox] = useState<string | null>(null);
  // 缩略图加载失败（通常是签名 URL 过期）的 id：回退为文件下载行，而非裂图
  const [broken, setBroken] = useState<Set<number>>(new Set());
  // 防御旧缓存/混用数据：attachments 缺失时视为空列表而非崩溃
  const list = items ?? [];

  // 灯箱打开时锁定背景滚动 + Esc 关闭。改用共享 hook，不再内联一份拷贝。
  const closeLightbox = useCallback(() => setLightbox(null), []);
  useModalScrollLock(lightbox !== null);
  useEscapeKey(lightbox !== null, closeLightbox);

  if (list.length === 0) return null;

  const markBroken = (id: number) =>
    setBroken((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });

  // 已转入的附件显示"已转入"徽标 + 直达链接；未转入的才显示转入按钮。
  // 这样转换成功后附件行当场变化（不留下"点了没反应"的死角），且不会重复触发 409。
  // A promoted attachment shows the "promoted" badge with a direct link; only a not-yet
  // promoted one shows the button. The row therefore changes the moment the promotion
  // succeeds (no dead "clicked but nothing happened" state) and a repeat 409 cannot be
  // triggered by accident.
  const renderPromotion = (att: AttachmentRef) => {
    const entry = promoted?.[att.id];
    if (entry) {
      return (
        <span className="attachment-promoted">
          <span className="attachment-promoted-label">{t("attach.promoted")}</span>
          <a className="attachment-promoted-link" href={`/files/${entry.id}`}>{t("attach.promotedView")}</a>
        </span>
      );
    }
    if (!onPromote) return null;
    return (
      <button
        type="button"
        className="attachment-promote"
        onClick={() => onPromote(att)}
        aria-label={t("attach.promoteFor", { name: att.originalFilename })}
      >
        {t("attach.promote")}
      </button>
    );
  };

  return (
    <>
      <ul className="attachment-list">
        {list.map((att) =>
          att.isImage && !broken.has(att.id) ? (
            <li className="attachment-item" key={att.id}>
              <button
                type="button"
                className="attachment-thumb"
                onClick={() => setLightbox(att.downloadUrl)}
                aria-label={t("attach.preview", { name: att.originalFilename })}
              >
                <img
                  src={att.downloadUrl}
                  alt={att.originalFilename}
                  loading="lazy"
                  onError={() => markBroken(att.id)}
                />
              </button>
              {onRemove && (
                <button type="button" className="attachment-remove" onClick={() => onRemove(att.id)} aria-label={t("attach.removeFile", { name: att.originalFilename })}>
                  {t("attach.remove")}
                </button>
              )}
              {renderPromotion(att)}
            </li>
          ) : (
            <li className="attachment-item attachment-item-file" key={att.id}>
              <a className="attachment-file" href={att.downloadUrl}>
                <span className="attachment-file-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true"><path d="M6 3h7l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M13 3v4h4" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg></span>
                <span className="attachment-file-meta">
                  <span className="attachment-file-name">{att.originalFilename}</span>
                  <span className="attachment-file-size">{formatBytes(att.sizeBytes)}</span>
                </span>
              </a>
              {onRemove && (
                <button type="button" className="attachment-remove" onClick={() => onRemove(att.id)} aria-label={t("attach.removeFile", { name: att.originalFilename })}>
                  {t("attach.remove")}
                </button>
              )}
              {renderPromotion(att)}
            </li>
          ),
        )}
      </ul>

      {lightbox && (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={t("attach.imagePreview")} onClick={() => setLightbox(null)}>
          <button type="button" className="lightbox-close" aria-label={t("attach.closePreview")} onClick={() => setLightbox(null)}>×</button>
          <img className="lightbox-img" src={lightbox} alt="" onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </>
  );
}

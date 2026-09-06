// 附件列表组件：图片内联缩略图 + 点击灯箱放大；文件只提供下载（不内联打开，防注入/可执行内容）。
// Attachment list: images render as inline thumbnails with a click-to-zoom lightbox;
// files render as download-only links (never opened inline, preventing injection / executable content).
import { useEffect, useState } from "react";
import type { AttachmentRef } from "./lib/api";
import { formatBytes } from "./lib/format";
import { useI18n } from "./lib/i18n";

export function AttachmentList({
  items,
  onRemove,
}: {
  items: AttachmentRef[];
  onRemove?: (id: number) => void;
}) {
  const { t } = useI18n();
  const [lightbox, setLightbox] = useState<string | null>(null);
  // 缩略图加载失败（通常是签名 URL 过期）的 id：回退为文件下载行，而非裂图
  const [broken, setBroken] = useState<Set<number>>(new Set());
  // 防御旧缓存/混用数据：attachments 缺失时视为空列表而非崩溃
  const list = items ?? [];

  // 灯箱打开时锁定背景滚动；Esc 关闭；恢复调用前的 overflow 原值
  // Lock body scroll while the lightbox is open; close on Escape; restore prior overflow
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLightbox(null);
    };
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [lightbox]);

  if (list.length === 0) return null;

  const markBroken = (id: number) =>
    setBroken((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });

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
            </li>
          ) : (
            <li className="attachment-item attachment-item-file" key={att.id}>
              <a className="attachment-file" href={att.downloadUrl}>
                <span className="attachment-file-icon" aria-hidden="true">file</span>
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

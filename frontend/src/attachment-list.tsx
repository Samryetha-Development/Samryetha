// 附件列表组件：图片内联缩略图 + 点击灯箱放大；文件只提供下载（不内联打开，防注入/可执行内容）。
// Attachment list: images render as inline thumbnails with a click-to-zoom lightbox;
// files render as download-only links (never opened inline, preventing injection / executable content).
import { useEffect, useState } from "react";
import type { AttachmentRef } from "./lib/api";
import { formatBytes } from "./lib/format";

export function AttachmentList({
  items,
  onRemove,
}: {
  items?: AttachmentRef[];
  onRemove?: (id: number) => void;
}) {
  const [lightbox, setLightbox] = useState<string | null>(null);

  // 灯箱打开时锁定背景滚动；Esc 关闭
  // Lock body scroll while the lightbox is open; close on Escape
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLightbox(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKey);
    };
  }, [lightbox]);

  const list = items ?? []; // 防御空值：detail.attachments 理论上总被后端注入，但防御性兜底防 undefined 崩溃（F11）

  if (list.length === 0) return null;

  return (
    <>
      <ul className="attachment-list">
        {list.map((att) =>
          att.isImage ? (
            <li className="attachment-item" key={att.id}>
              <button
                type="button"
                className="attachment-thumb"
                onClick={() => setLightbox(att.downloadUrl)}
                aria-label={`Preview ${att.originalFilename}`}
              >
                <img src={att.downloadUrl} alt={att.originalFilename} loading="lazy" />
              </button>
              {onRemove && (
                <button type="button" className="attachment-remove" onClick={() => onRemove(att.id)} aria-label={`Remove ${att.originalFilename}`}>
                  Remove
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
                <button type="button" className="attachment-remove" onClick={() => onRemove(att.id)} aria-label={`Remove ${att.originalFilename}`}>
                  Remove
                </button>
              )}
            </li>
          ),
        )}
      </ul>

      {lightbox && (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label="Image preview" onClick={() => setLightbox(null)}>
          <button type="button" className="lightbox-close" aria-label="Close preview" onClick={() => setLightbox(null)}>×</button>
          <img className="lightbox-img" src={lightbox} alt="" onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </>
  );
}

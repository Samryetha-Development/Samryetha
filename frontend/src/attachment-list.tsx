import { useEffect, useRef, useState } from "react";
import type { AttachmentRef } from "./lib/api";

export function AttachmentList({ items, onRemove }: { items?: AttachmentRef[] | null; onRemove?: (id: number) => void }) {
  const attachments = items ?? [];
  const [lightbox, setLightbox] = useState<AttachmentRef | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!lightbox) return;
    const previousOverflow = document.body.style.overflow;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLightbox(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKey);
      triggerRef.current?.focus();
    };
  }, [lightbox]);

  if (attachments.length === 0) return null;
  return <>
    <ul className="attachment-list">
      {attachments.map((att) => <li className={`attachment-item ${att.isImage ? "" : "attachment-item-file"}`} key={att.id}>
        {att.isImage ? <button type="button" className="attachment-thumb" onClick={(event) => { triggerRef.current = event.currentTarget; setLightbox(att); }} aria-label={`Preview ${att.originalFilename}`}><img src={att.downloadUrl} alt={att.originalFilename} loading="lazy" /></button>
          : <a className="attachment-file" href={att.downloadUrl} download><span className="attachment-file-icon" aria-hidden="true">file</span><span className="attachment-file-name">{att.originalFilename}</span></a>}
        {onRemove && <button type="button" className="attachment-remove" onClick={() => onRemove(att.id)} aria-label={`Remove ${att.originalFilename}`}>Remove</button>}
      </li>)}
    </ul>
    {lightbox && <div className="lightbox" role="dialog" aria-modal="true" aria-label="Image preview" onClick={() => setLightbox(null)}><button className="lightbox-close" type="button" autoFocus aria-label="Close preview" onClick={() => setLightbox(null)}>×</button><img className="lightbox-img" src={lightbox.downloadUrl} alt={lightbox.originalFilename} onClick={(event) => event.stopPropagation()} /></div>}
  </>;
}

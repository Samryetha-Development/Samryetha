// Shared post/reply/edit field with a preview using the publication renderer.
import { useEffect, useId, useMemo, useRef, useState, type RefObject, type ReactNode } from "react";
import { useIsomorphicLayoutEffect } from "./lib/use-isomorphic-layout-effect";
import { api, type BodyFormat } from "./lib/api";
import { useI18n } from "./lib/i18n";
import { renderMathInHtml } from "./lib/math-text";

type Preview = { source: string; format: BodyFormat; html: string; status: "ready" | "error" };

export function EditorField({
  value,
  onChange,
  format,
  onFormatChange,
  rows = 6,
  maxLength = 40000,
  placeholder,
  disabled,
  autoFocus,
  textareaRef,
  toggleClassName,
  tools,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  format: BodyFormat;
  onFormatChange: (format: BodyFormat) => void;
  rows?: number;
  maxLength?: number;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  toggleClassName?: string;
  tools?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const textareaId = useId();
  const previewId = useId();
  const [showPreview, setShowPreview] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [retry, setRetry] = useState(0);
  const empty = !value.trim();
  const current = preview?.source === value && preview.format === format ? preview : null;
  const previewHtml = useMemo(() => renderMathInHtml(current?.html ?? ""), [current?.html]);

  useEffect(() => {
    if (!showPreview || empty) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void api.discussions.preview({ bodyMarkdown: value, bodyFormat: format }, controller.signal)
        .then(({ bodyHtml }) => {
          if (!controller.signal.aborted) setPreview({ source: value, format, html: bodyHtml, status: "ready" });
        })
        .catch(() => {
          if (!controller.signal.aborted) setPreview({ source: value, format, html: "", status: "error" });
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [showPreview, empty, value, format, retry]);

  const toggleRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);
  useIsomorphicLayoutEffect(() => {
    const toggle = toggleRef.current;
    if (!toggle) return;
    const measure = () => {
      const active = toggle.querySelector<HTMLButtonElement>(".format-toggle-btn.active");
      if (active) setIndicator({ left: active.offsetLeft, width: active.offsetWidth });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(toggle);
    for (const button of toggle.querySelectorAll("button")) observer.observe(button);
    return () => observer.disconnect();
  }, [format, t]);
  return (
    <div className="form-field body-field">
      <div className="body-field-head">
        <label htmlFor={textareaId}>{t("thread.message")}</label>
        <div className="body-field-tools">
          {tools}
          <button type="button" className="editor-preview-toggle" aria-expanded={showPreview} aria-controls={previewId} onClick={() => setShowPreview((shown) => !shown)} disabled={disabled}>{t(showPreview ? "editor.hidePreview" : "editor.preview")}</button>
          <div ref={toggleRef} className={`format-toggle${indicator ? " has-indicator" : ""}${toggleClassName ? ` ${toggleClassName}` : ""}`} role="group" aria-label={t("thread.textFormat")}>
            {indicator && <span className="format-toggle-indicator" aria-hidden="true" style={{ width: indicator.width, transform: `translateX(${indicator.left}px)` }} />}
            <button type="button" className={`format-toggle-btn ${format === "markdown" ? "active" : ""}`} aria-pressed={format === "markdown"} onClick={() => onFormatChange("markdown")} disabled={disabled}>{t("thread.markdown")}</button>
            <button type="button" className={`format-toggle-btn ${format === "text" ? "active" : ""}`} aria-pressed={format === "text"} onClick={() => onFormatChange("text")} disabled={disabled}>{t("thread.plainText")}</button>
          </div>
        </div>
      </div>
      {children}
      <textarea id={textareaId} ref={textareaRef} value={value} onChange={(e) => onChange(e.target.value)} rows={rows} maxLength={maxLength} placeholder={placeholder} disabled={disabled} autoFocus={autoFocus} />
      {format === "markdown" && <p className="editor-math-hint">{t("editor.mathHint")}</p>}
      {showPreview && (
        <section id={previewId} className="editor-preview" aria-label={t("editor.preview")} aria-busy={!empty && !current}>
          <div className="editor-preview-head">{t("editor.preview")}</div>
          {empty ? <p className="editor-preview-status">{t("editor.previewEmpty")}</p> : !current ? (
            <p className="editor-preview-status" role="status">{t("editor.previewLoading")}</p>
          ) : current.status === "error" ? (
            <div className="editor-preview-status" role="status">
              <p>{t("editor.previewError")}</p>
              <button type="button" className="editor-preview-toggle" onClick={() => { setPreview(null); setRetry((count) => count + 1); }} disabled={disabled}>{t("editor.previewRetry")}</button>
            </div>
          ) : <div className="thread-detail-body editor-preview-body" dangerouslySetInnerHTML={{ __html: previewHtml }} />}
        </section>
      )}
    </div>
  );
}

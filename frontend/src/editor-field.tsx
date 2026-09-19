// 可复用正文编辑器：正文 textarea + Markdown/Plain 格式切换。
// 发帖、回复、编辑三处共用，统一「输入框 + 格式切换」结构与 i18n。
// Reusable editor field: body textarea + Markdown/Plain toggle, shared by post/reply/edit.
import { useRef, useState, type RefObject } from "react";
import { useIsomorphicLayoutEffect } from "./lib/use-isomorphic-layout-effect";
import type { BodyFormat } from "./lib/api";
import { useI18n } from "./lib/i18n";

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
}) {
  const { t } = useI18n();
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
    <label className="form-field body-field">
      <div className="body-field-head">
        <span>{t("thread.message")}</span>
        <div ref={toggleRef} className={`format-toggle${indicator ? " has-indicator" : ""}${toggleClassName ? ` ${toggleClassName}` : ""}`} role="group" aria-label={t("thread.textFormat")}>
          {indicator && <span className="format-toggle-indicator" aria-hidden="true" style={{ width: indicator.width, transform: `translateX(${indicator.left}px)` }} />}
          <button type="button" className={`format-toggle-btn ${format === "markdown" ? "active" : ""}`} aria-pressed={format === "markdown"} onClick={() => onFormatChange("markdown")}>{t("thread.markdown")}</button>
          <button type="button" className={`format-toggle-btn ${format === "text" ? "active" : ""}`} aria-pressed={format === "text"} onClick={() => onFormatChange("text")}>{t("thread.plainText")}</button>
        </div>
      </div>
      <textarea ref={textareaRef} value={value} onChange={(e) => onChange(e.target.value)} rows={rows} maxLength={maxLength} placeholder={placeholder} disabled={disabled} autoFocus={autoFocus} />
    </label>
  );
}

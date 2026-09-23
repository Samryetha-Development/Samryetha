import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const EXIT_MS = 180;

export type LakoDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  dismissible?: boolean;
  ariaLabel?: string;
  className?: string;
};

export function LakoDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  actions,
  dismissible = true,
  ariaLabel,
  className = "",
}: LakoDialogProps) {
  const contentRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const exitTimerRef = useRef<number | undefined>(undefined);
  const [rendered, setRendered] = useState(open);
  const [closing, setClosing] = useState(false);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  const titleId = `lako-dialog-title-${useId().replace(/:/g, "")}`;
  const descriptionId = `lako-dialog-description-${useId().replace(/:/g, "")}`;

  useEffect(() => {
    if (exitTimerRef.current) window.clearTimeout(exitTimerRef.current);
    if (open) {
      setRendered(true);
      setClosing(false);
      return;
    }
    if (!rendered) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setRendered(false);
      setClosing(false);
      return;
    }
    setClosing(true);
    exitTimerRef.current = window.setTimeout(() => {
      setRendered(false);
      setClosing(false);
      exitTimerRef.current = undefined;
    }, EXIT_MS);
    return () => {
      if (exitTimerRef.current) window.clearTimeout(exitTimerRef.current);
    };
  }, [open, rendered]);

  useEffect(() => {
    if (!rendered) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => {
      const first = contentRef.current?.querySelector<HTMLElement>(
        "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
      );
      (first ?? contentRef.current)?.focus();
    });

    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && dismissible && open) {
        event.preventDefault();
        onOpenChangeRef.current(false);
        return;
      }
      if (event.key !== "Tab" || !contentRef.current) return;
      const focusable = [...contentRef.current.querySelectorAll<HTMLElement>(
        "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
      )];
      if (focusable.length === 0) {
        event.preventDefault();
        contentRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", keydown);
      returnFocusRef.current?.focus();
    };
  }, [dismissible, open, rendered]);

  if (!rendered || typeof document === "undefined") return null;
  return createPortal(
    <div
      className={`lako-ui-dialog-overlay${closing ? " closing" : ""}`}
      onPointerDown={(event) => {
        if (open && dismissible && event.target === event.currentTarget) onOpenChange(false);
      }}
    >
      <div
        className={`lako-ui-dialog ${className}`.trim()}
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        aria-label={!title ? ariaLabel : undefined}
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
      >
        {title && <h2 id={titleId}>{title}</h2>}
        {description && <p id={descriptionId}>{description}</p>}
        {children && <div className="lako-ui-dialog-body">{children}</div>}
        {actions && <div className="lako-ui-dialog-actions">{actions}</div>}
      </div>
    </div>,
    document.body,
  );
}

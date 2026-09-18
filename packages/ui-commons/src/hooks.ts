import { useEffect } from "react";

/**
 * 自绘弹层（非 Radix Dialog）打开时锁定 body 滚动，关闭/卸载时还原原值。
 * 用 Dialog / ConfirmDialog 的地方不需要它 —— Radix 自己处理滚动锁。
 */
export function useModalScrollLock(open: boolean) {
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);
}

/** Esc 关闭。同样只给自绘弹层用。 */
export function useEscapeKey(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

import { useEffect, useRef, useState } from "react";

// Let the modal primitive finish its exit before its owner discards the data
// that conditionally mounts it. Keep the latest callback without restarting exit.
export function useDialogExit(onClosed: () => void) {
  const [visible, setVisible] = useState(true);
  const callback = useRef(onClosed);
  callback.current = onClosed;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const close = () => {
    if (timer.current) return;
    setVisible(false);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.reduceMotion === "true";
    if (reduced) callback.current();
    else timer.current = setTimeout(() => callback.current(), 220);
  };
  return { visible, close };
}

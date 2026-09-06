let installed = false;

function isField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target.isContentEditable
  );
}

// html 全局 scroll-behavior: smooth，抖动必须显式 instant，否则 1px 修复变成可见的平滑滚动
function jitterViewport() {
  const y = window.scrollY;
  window.scrollTo({ top: y + 1, behavior: "instant" });
  requestAnimationFrame(() => window.scrollTo({ top: y, behavior: "instant" }));
}

// iPadOS 26 Safari 视口卡位缓解（WebKit #297779）：键盘收起或地址栏收缩后
// visualViewport.offsetTop 可能残留非零，导致 fixed 顶栏命中区域与视觉错位。
export function installSafariViewportFix(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;

  let savedScrollY: number | null = null;
  let lastFixAt = 0;

  const onFocusIn = (event: FocusEvent) => {
    if (isField(event.target)) savedScrollY = window.scrollY;
  };

  const onFocusOut = (event: FocusEvent) => {
    if (!isField(event.target) || savedScrollY === null) return;
    const y = savedScrollY;
    savedScrollY = null;
    requestAnimationFrame(() => {
      window.scrollTo({ top: y, behavior: "instant" });
      jitterViewport();
    });
  };

  const onViewportResize = () => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    if (viewport.height < window.innerHeight - 80) return;
    if (Math.abs(viewport.offsetTop) <= 2) return;
    const now = Date.now();
    if (now - lastFixAt < 200) return;
    lastFixAt = now;
    jitterViewport();
  };

  document.addEventListener("focusin", onFocusIn, true);
  document.addEventListener("focusout", onFocusOut, true);
  window.visualViewport?.addEventListener("resize", onViewportResize);
}

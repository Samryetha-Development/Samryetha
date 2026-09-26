import { useRef, useState, type RefObject } from "react";
import { useIsomorphicLayoutEffect } from "./use-isomorphic-layout-effect";

type IndicatorRect = { width: number; height: number; x: number; y: number; ready: boolean; animate: boolean };

// 测量 tab 容器里当前激活项的位置/尺寸，给滑动指示器用，并随 resize 重新测量。
export function useTabIndicator<T>(
  containerRef: RefObject<HTMLElement | null>,
  selector: (active: T) => string,
  active: T,
  { enabled = true, measureDeps = [] as unknown[] } = {},
) {
  const [rect, setRect] = useState<IndicatorRect>({ width: 0, height: 0, x: 0, y: 0, ready: false, animate: false });
  const previousActiveRef = useRef<{ hasValue: boolean; value?: T }>({ hasValue: false });

  // selector 是调用方每次渲染新建的箭头函数（引用不稳定），直接进依赖数组会导致
  // layout effect 每次渲染都重跑 → setRect 传新对象 → 无限重渲染（Maximum update depth）。
  // 用 ref 存最新引用，effect 只依赖稳定值（active 变化时照样重新测量）。
  const selectorRef = useRef(selector);
  selectorRef.current = selector;

  useIsomorphicLayoutEffect(() => {
    if (!enabled) return;
    const readRect = (element: HTMLElement, animate: boolean): IndicatorRect => ({
      width: element.offsetWidth,
      height: element.offsetHeight,
      x: element.offsetLeft,
      y: element.offsetTop,
      ready: true,
      animate,
    });
    const move = (animate: boolean) => {
      const el = containerRef.current?.querySelector<HTMLElement>(selectorRef.current(active));
      if (el) setRect(readRect(el, animate));
    };
    const previousActive = previousActiveRef.current;
    const activeChanged = previousActive.hasValue && !Object.is(previousActive.value, active);
    previousActiveRef.current = { hasValue: true, value: active };
    // 首次挂载直接放到当前项；跨页面的起点由同名 View Transition 元素负责。
    // 只有 active 真正变化才启用 CSS transition；StrictMode 重放首次 effect
    // 时 active 未变，不会在共享元素落位后又补一次 transition。
    move(activeChanged);
    const onResize = () => move(false);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [containerRef, active, enabled, ...measureDeps]);

  return rect;
}

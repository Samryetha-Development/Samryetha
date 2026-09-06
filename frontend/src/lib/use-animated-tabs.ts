import { useCallback, useEffect, useRef, useState } from "react";

export type TabPhase = "" | "is-leaving" | "is-entering";

type Options<T extends string> = {
  initial: T;
  /** 退出动画时长，默认 125ms */
  duration?: number;
  /** 默认跟随 prefers-reduced-motion */
  reduceMotion?: () => boolean;
  /** 点击瞬间触发（比如通知父组件/路由） */
  onSelect?: (next: T) => void;
  /** committed 真正切换时触发（比如联动重置其它 tab） */
  onCommit?: (next: T) => void;
};

// 封装「active（高亮） + committed（渲染） + phase + token」的 tab 切换动画。
// 每次 setActive 用自增 token 守卫 setTimeout，避免过期回调覆盖新状态。
export function useAnimatedTabs<T extends string>(options: Options<T>) {
  const { initial, duration = 125, onSelect, onCommit } = options;
  const reduceMotion = options.reduceMotion ?? (() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const [active, setActiveState] = useState<T>(initial);        // 高亮的那个（指示器/aria）
  const [committed, setCommittedState] = useState<T>(initial);  // 真正渲染数据的那个
  const [phase, setPhase] = useState<TabPhase>("");
  const token = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const commit = useCallback(
    (next: T) => {
      setCommittedState(next);
      setPhase("is-entering");
      requestAnimationFrame(() => {
        if (mounted.current) setPhase("");
      });
      onCommit?.(next);
    },
    [onCommit],
  );

  /** 瞬时切换（也用于取消挂起中的动画） */
  const jumpTo = useCallback(
    (next: T) => {
      token.current += 1;
      setActiveState(next);
      setCommittedState(next);
      setPhase("");
      onCommit?.(next);
    },
    [onCommit],
  );

  const setActive = useCallback(
    (next: T) => {
      if (next === active) return;
      setActiveState(next);
      onSelect?.(next);
      if (reduceMotion()) {
        commit(next);
        return;
      }
      const t = ++token.current;
      setPhase("is-leaving");
      window.setTimeout(() => {
        if (!mounted.current || t !== token.current) return;
        commit(next);
      }, duration);
    },
    [active, reduceMotion, duration, commit, onSelect],
  );

  return { active, committed, phase, setActive, jumpTo };
}

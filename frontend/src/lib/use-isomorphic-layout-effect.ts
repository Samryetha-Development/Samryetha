import { useEffect, useLayoutEffect } from "react";

// SSR 下 useLayoutEffect 会触发 React 告警，服务端退回 useEffect。
export const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

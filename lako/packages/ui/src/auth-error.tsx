import { useEffect, useState } from "react";

/**
 * 延迟清空内容，让收起动画（.lako-auth-error 的 260ms 过渡）有东西可动。
 * 直接把文本清掉的话高度会瞬间塌陷，动画看不出来。
 */
export function LakoAuthError({ message }: { message: string }) {
  const [rendered, setRendered] = useState(message);

  useEffect(() => {
    if (message) {
      setRendered(message);
      return;
    }
    const timer = window.setTimeout(() => setRendered(""), 260);
    return () => window.clearTimeout(timer);
  }, [message]);

  return (
    <div className="lako-form-error lako-auth-error" data-visible={Boolean(message)} aria-live="polite">
      <div>{rendered}</div>
    </div>
  );
}

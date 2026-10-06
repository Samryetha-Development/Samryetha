// 服务端渲染好的 bodyHtml 注入点：一次性完成公式填充 + Mermaid 画图。
//
// 为什么需要组件而不是一个纯函数：
//   - 公式（KaTeX）是纯字符串变换，注入前算好即可；
//   - Mermaid 必须**在 DOM 就绪后**异步渲染，还得能随内容切换重跑 —— 那是 effect 的活。
//
// 水合安全：`renderMathInHtml` 在 SSR 阶段原样返回（服务端不跑 KaTeX），
// dangerouslySetInnerHTML 的服务端输出 = 客户端首帧输出，挂载后 effect 再补渲染。

import { useEffect, useMemo, useRef } from "react";
import { renderMathInHtml } from "./math-text";
import { renderMermaidIn } from "./mermaid-block";

export function RichBody({
  html,
  className,
}: {
  html: string;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // 公式是纯字符串变换：用 useMemo 避免每次渲染都重跑一遍 DOMParser。
  const compiled = useMemo(() => renderMathInHtml(html), [html]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    // 图来自 dangerouslySetInnerHTML，React 不认这些节点，所以直接操作 DOM 是安全的。
    void renderMermaidIn(node);
  }, [compiled]);

  return <div className={className} ref={ref} dangerouslySetInnerHTML={{ __html: compiled }} />;
}

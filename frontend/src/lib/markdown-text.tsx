// 渲染一小段 Markdown + LaTeX 文本（用户简介、预览、评论/备注）。
//
// **水合安全的做法**：Markdown/公式只在浏览器里渲染，服务端输出纯文本、首帧客户端
// 也输出同样的纯文本，挂载后再把渲染结果换进 DOM —— 所以不会出现 hydration
// mismatch（服务端与首帧客户端一致），也不会把 KaTeX 的 MathML 塞进 SSR 输出
// （后端净化器会剥掉 <math>）。
//
// 为什么不走帖子的 bodyHtml：帖子正文由后端渲染并存 body_html，这里服务的是
// **没有**对应 HTML 列的短文本；而且把服务端渲染过的 HTML 交给
// dangerouslySetInnerHTML 时，SSR 会先把它转义成实体，客户端再解析就只剩
// "&lt;p&gt;" 了。
//
// `as="span"` 用于必须待在行内上下文里的场合（例如本来就在 <span> 里的备注）；
// 此时回退节点也用 <span>，避免 div 嵌 span 的非法嵌套。

import { useEffect, useMemo, useRef, type ElementType } from "react";
import { renderMarkdownFragment } from "./markdown-lite";

export function MarkdownText({
  source,
  className,
  empty,
  as: Tag = "div",
}: {
  source: string | null | undefined;
  className?: string;
  /** 源文为空时的占位内容（服务端与客户端都会显示它）。 */
  empty?: React.ReactNode;
  /** 外层标签；行内上下文里传 "span"。 */
  as?: ElementType;
}) {
  const text = source ?? "";
  const ref = useRef<HTMLElement | null>(null);
  // 只在浏览器里编译；服务端为 null，走下面的纯文本分支。
  const fragment = useMemo(() => renderMarkdownFragment(text), [text]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (fragment) {
      node.replaceChildren(fragment);
    } else {
      node.textContent = text;
    }
  }, [fragment, text]);

  const fallback = text ? <span className="markdown-text-fallback">{text}</span> : empty;
  return (
    <Tag className={className ? `markdown-text ${className}` : "markdown-text"} ref={ref}>
      {/* SSR 与首帧：纯文本（公式与 Markdown 记号原样显示），挂载后由 effect 替换。 */}
      {fallback}
    </Tag>
  );
}

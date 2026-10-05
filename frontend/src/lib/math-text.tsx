import { useMemo, type ReactNode } from "react";
import katex from "katex";

// 纯文本内联 LaTeX 渲染：支持 $$...$$ / \[...\]（块级）与 $...$ / \(...\)（行内），
// \$ 转义为字面美元。非公式部分按普通文本渲染（React 自动转义），公式用 KaTeX
// renderToString（trust: false，禁 \href 等），可安全地 innerHTML。
// Inline LaTeX in plain text: blocks via $$...$$ / \[...\], inline via $...$ / \(...\).
// KaTeX is rendered with trust:false, so its HTML is safe to inject.

type Segment = { type: "text"; value: string } | { type: "math"; value: string; display: boolean };

function isSpace(ch: string | undefined): boolean {
  return ch === undefined || /\s/.test(ch);
}

// 把纯文本切成 文本 / 公式 段。行内 $ 要求紧跟非空白、闭合 $ 前为非空白，
// 避免把 "$5 and $10" 这类普通文本误判成公式。
// 导出给 markdown-lite 复用：公式切分只有这一份实现，避免两处规则漂移。
export function splitMath(input: string): Segment[] {
  const segments: Segment[] = [];
  let text = "";
  let i = 0;
  const n = input.length;
  const flushText = () => {
    if (text) {
      segments.push({ type: "text", value: text });
      text = "";
    }
  };
  const pushMath = (value: string, display: boolean, end: number) => {
    flushText();
    segments.push({ type: "math", value, display });
    i = end;
  };

  while (i < n) {
    const ch = input[i];

    if (ch === "\\" && input[i + 1] === "$") {
      text += "$";
      i += 2;
      continue;
    }
    if (ch === "$" && input[i + 1] === "$") {
      const end = input.indexOf("$$", i + 2);
      if (end !== -1 && end > i + 2) {
        pushMath(input.slice(i + 2, end), true, end + 2);
        continue;
      }
    }
    if (ch === "\\" && input[i + 1] === "[") {
      const end = input.indexOf("\\]", i + 2);
      if (end !== -1) {
        pushMath(input.slice(i + 2, end), true, end + 2);
        continue;
      }
    }
    if (ch === "\\" && input[i + 1] === "(") {
      const end = input.indexOf("\\)", i + 2);
      if (end !== -1) {
        pushMath(input.slice(i + 2, end), false, end + 2);
        continue;
      }
    }
    if (ch === "$" && !isSpace(input[i + 1]) && input[i + 1] !== "$") {
      let j = i + 1;
      let close = -1;
      while (j < n) {
        const cj = input[j];
        if (cj === "\\") {
          j += 2;
          continue;
        }
        if (cj === "\n") break;
        if (cj === "$") {
          if (!isSpace(input[j - 1])) close = j;
          break;
        }
        j += 1;
      }
      if (close !== -1) {
        pushMath(input.slice(i + 1, close), false, close + 1);
        continue;
      }
    }

    text += ch;
    i += 1;
  }
  flushText();
  return segments;
}

function renderMath(value: string, display: boolean): string {
  try {
    return katex.renderToString(value, {
      displayMode: display,
      throwOnError: false,
      strict: false,
      trust: false,
      output: "html",
    });
  } catch {
    return "";
  }
}

/**
 * 在**已消毒的服务端 HTML**（帖子/回复的 bodyHtml）里渲染 LaTeX。
 *
 * 后端已经把公式切成空的 `<span class="math-{inline,block}" data-tex="…">`，所以
 * 这里只需按容器填充 —— 不必再去扫文本节点。这个分工是必要的：`$$…$$` 若以普通
 * 文本流下来，`breaks=True` 会把其中的换行变成 `<br>`，一个公式被拆进多个文本节点，
 * 客户端再怎么扫也拼不回完整公式（多行展示公式以前就是这样彻底不渲染的）。
 *
 * 仍然保留对老数据的文本节点扫描：本次改动之前入库的 body_html 里公式还是裸
 * `$…$` 文本，那些行不会重算（见 markdown.py 的说明），只能在这里兜住。
 *
 * Browser-only; returns the input unchanged during SSR.
 */
export function renderMathInHtml(html: string): string {
  if (typeof window === "undefined" || typeof DOMParser === "undefined") return html;
  const doc = new DOMParser().parseFromString(`<div id="__math_root__">${html}</div>`, "text/html");
  const root = doc.getElementById("__math_root__");
  if (!root) return html;

  // 1) 新数据：后端已经切好的公式容器。
  for (const span of Array.from(root.querySelectorAll<HTMLElement>("span[data-tex]"))) {
    const display = span.classList.contains("math-block");
    const tex = span.getAttribute("data-tex") ?? "";
    span.innerHTML = renderMath(decodeHtmlEntities(tex), display);
  }

  // 2) 老数据：裸文本里的 $…$（跳过 code/pre，跳过已经处理过的容器内部）。
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    const value = node.nodeValue ?? "";
    if (!value.includes("$") && !value.includes("\\(") && !value.includes("\\[")) continue;
    let skip = false;
    for (let parent = node.parentElement; parent && parent !== root; parent = parent.parentElement) {
      if (parent.tagName === "CODE" || parent.tagName === "PRE" || parent.hasAttribute("data-tex")) { skip = true; break; }
    }
    if (!skip) nodes.push(node);
  }
  for (const node of nodes) {
    const segments = splitMath(node.nodeValue ?? "");
    // 整段文本就是一条公式时 segments 也只有 1 段（math）——必须按"含 math 段"判断，
    // 不能用长度 <= 1 跳过，否则 <p>$a_x$</p> 这种最常见的情况不渲染。
    if (!segments.some((segment) => segment.type === "math")) continue;
    const fragment = doc.createDocumentFragment();
    for (const segment of segments) {
      if (segment.type === "text") {
        fragment.appendChild(doc.createTextNode(segment.value));
      } else {
        const span = doc.createElement("span");
        span.className = segment.display ? "math-block" : "math-inline";
        span.innerHTML = renderMath(segment.value, segment.display);
        fragment.appendChild(span);
      }
    }
    node.parentNode?.replaceChild(fragment, node);
  }
  return root.innerHTML;
}

/** 解析 attribute 里的实体（后端用 html.escape 写过 data-tex）。 */
function decodeHtmlEntities(value: string): string {
  const doc = new DOMParser().parseFromString(`<textarea>${value}</textarea>`, "text/html");
  return doc.querySelector("textarea")?.textContent ?? value;
}

/** 渲染一段可能含 LaTeX 的纯文本。 */
export function MathText({ children }: { children: string | null | undefined }): ReactNode {
  const source = children ?? "";
  const segments = useMemo(() => splitMath(source), [source]);
  return (
    <>
      {segments.map((segment, index) =>
        segment.type === "text" ? (
          <span key={index}>{segment.value}</span>
        ) : (
          <span
            key={index}
            className={segment.display ? "math-block" : "math-inline"}
            dangerouslySetInnerHTML={{ __html: renderMath(segment.value, segment.display) }}
          />
        ),
      )}
    </>
  );
}

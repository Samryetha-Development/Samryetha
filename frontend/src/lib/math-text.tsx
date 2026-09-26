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
function splitMath(input: string): Segment[] {
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

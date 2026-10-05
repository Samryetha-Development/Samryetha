// 客户端 Markdown → 净化 HTML 渲染器 —— 给**后端没有存 bodyHtml 的短文本**用
// （目前是用户简介 bio）。帖子/回复仍然以后端渲染的 bodyHtml 为准。
//
// 为什么不复用后端的 markdown-it：
//   1. 后端是 Python（markdown_it + nh3），前端不该为了 500 字的简介引一整套解析器
//      依赖；这里用浏览器自带的 DOM + 一份最小 Markdown 子集实现。
//   2. **渲染必须留给客户端**：简介正文会经过 react-dom 的 SSR 文本转义，如果在服务端
//      渲染成标签再 hydrate，客户端拿到的就已经是实体字符串了。所以调用方按需渲染，
//      不要把结果塞进 SSR 输出。
//
// 安全性：文本走 createTextNode，属性只放行 http/https/mailto 链接、且强制
// target=_blank + rel=noopener noreferrer nofollow；公式用 KaTeX renderToString
// （trust:false）。因此这里产出的 DOM 不需要再消毒。
//
// 与原帖渲染的已知差异（有意）：只支持后端同一套白名单子集的 Markdown ——
// 标题、围栏代码、表格、图片不渲染；普通文本、**加粗**、*斜体*、~~删除~~、`代码`、
// 链接、列表、引用、分隔线、$行内$ / $$块级$$ 公式都渲染。
// 换行策略与后端 render_markdown(breaks=True) 对齐。

import katex from "katex";
import { splitMath } from "./math-text";

function appendInline(target: Node, text: string, doc: Document): void {
  // 行内规则：代码 → 链接 → 加粗/斜体/删除。刻意不做嵌套解析（简介不是长文，
  // 嵌套只会让"什么时候渲染失败"变得难以预测）。
  //
  // 每个分支自带 tag 与定界符宽度：定界符是 `**`/`__`/`~~` 这类双字符的，切片要
  // 按宽度算，不能统一 slice(1, -1)（否则 ~~x~~ 会渲染成 <del>~x~</del>）。
  const pattern =
    /(`[^`\n]+`)|(\[([^\]\n]+)\]\((https?:|mailto:)[^)\s]+\))|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(~~[^~\n]+~~)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) target.appendChild(doc.createTextNode(text.slice(last, match.index)));
    const full = match[0];
    const rules: [number, string, number][] = [
      // [捕获组序号, 标签名, 定界符宽度]
      [1, "code", 1],
      [2, "a", 2],
      [5, "strong", 2],
      [6, "strong", 2],
      [7, "em", 1],
      [8, "em", 1],
      [9, "del", 2],
    ];
    const rule = rules.find(([group]) => match![group] !== undefined);
    if (!rule) {
      target.appendChild(doc.createTextNode(full));
      last = match.index + full.length;
      continue;
    }
    const [, tag, width] = rule;
    const element = doc.createElement(tag);
    if (tag === "a") {
      // href 直接取括号里那段（协议已在正则里限定为 http/https/mailto）。
      element.setAttribute("href", full.slice(full.indexOf("(") + 1, -1));
      element.setAttribute("target", "_blank");
      element.setAttribute("rel", "noopener noreferrer nofollow");
      element.textContent = match[3] ?? "";
    } else {
      element.textContent = full.slice(width, -width);
    }
    target.appendChild(element);
    last = match.index + full.length;
  }
  if (last < text.length) target.appendChild(doc.createTextNode(text.slice(last)));
}

function mathElement(value: string, display: boolean, doc: Document): Node {
  const span = doc.createElement("span");
  span.className = display ? "math-block" : "math-inline";
  try {
    span.innerHTML = katex.renderToString(value, {
      displayMode: display,
      throwOnError: false,
      strict: false,
      trust: false,
      output: "html",
    });
  } catch {
    span.textContent = value;
  }
  return span;
}

/** 把一段文本渲染进容器：先切公式段，非公式段再走行内 Markdown。 */
function renderInlineFragment(target: Node, text: string, doc: Document): void {
  for (const segment of splitMath(text)) {
    if (segment.type === "math") target.appendChild(mathElement(segment.value, segment.display, doc));
    else appendInline(target, segment.value, doc);
  }
}

const LIST_ITEM = /^\s*[-*]\s+(.*)$/;

/**
 * 把 Markdown 源文渲染成已净化的 DOM 片段。
 * Browser-only：服务端（无 document）返回 `null`，调用方应渲染纯文本回退。
 */
export function renderMarkdownFragment(source: string): DocumentFragment | null {
  if (typeof document === "undefined") return null;
  const doc = document;
  const fragment = doc.createDocumentFragment();
  const lines = (source ?? "").replace(/\r\n?/g, "\n").split("\n");

  // 逐行处理块级结构；行内交给 renderInlineFragment。
  let index = 0;
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const element = doc.createElement("p");
    paragraph.forEach((line, position) => {
      if (position) element.appendChild(doc.createElement("br"));
      renderInlineFragment(element, line, doc);
    });
    fragment.appendChild(element);
    paragraph = [];
  };

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      flushParagraph();
      index += 1;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph();
      fragment.appendChild(doc.createElement("hr"));
      index += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushParagraph();
      const quote = doc.createElement("blockquote");
      const collected: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        collected.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      renderInlineFragment(quote, collected.join("\n"), doc);
      fragment.appendChild(quote);
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flushParagraph();
      const list = doc.createElement("ul");
      while (index < lines.length && LIST_ITEM.test(lines[index])) {
        const item = doc.createElement("li");
        renderInlineFragment(item, LIST_ITEM.exec(lines[index])![1], doc);
        list.appendChild(item);
        index += 1;
      }
      fragment.appendChild(list);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushParagraph();
      const list = doc.createElement("ol");
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        const item = doc.createElement("li");
        renderInlineFragment(item, lines[index].replace(/^\s*\d+[.)]\s+/, ""), doc);
        list.appendChild(item);
        index += 1;
      }
      fragment.appendChild(list);
      continue;
    }
    paragraph.push(line);
    index += 1;
  }
  flushParagraph();
  return fragment;
}

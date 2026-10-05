"""Markdown → 净化 HTML — 镜像 infrastructure/markdown.ts。

客户端只提交 canonical Markdown；服务端渲染 + 净化后存 body_html。
净化白名单/属性/scheme/链接强制 rel+target 与 TS 端一致；渲染引擎不同，
HTML 输出允许观感级差异（sanitize 语义等价），存量行的 body_html 不重算。

**数学公式在这里不做 KaTeX 排版**，只切成带 `data-tex` 的空容器，由浏览器用
KaTeX 填充。原因有两个：

1. KaTeX 的输出带 MathML（`<math>`）与大量内联样式，净化器必须整片放行才能保住
   它，等于给攻击面开一个大口子；而只放行一个 `data-tex` 属性，风险面就是一个
   纯文本属性。
2. 更关键的是**结构**：如果让 `$$…$$` 以普通文本流下去，`breaks=True` 会把公式里
   的换行变成 `<br>`，一个 `$$…$$` 就被拆到多个文本节点里，客户端再也拼不回一个
   完整公式（多行公式在旧实现里根本不渲染）。在这里一次性切出来，客户端拿到的
   永远是一个完整、独立的容器。
"""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
import nh3

# 服务端代码高亮：Pygments 把代码切成 `<span class="k">` 这类 token，配色交给 CSS
# （见 frontend/src/globals.css 的 .code-highlight 规则），所以这里只负责分词。
# 用 nowrap=True：外层 <pre><code> 由 markdown-it 生成，Pygments 不该再包一层。
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "em", "s", "u", "del", "ins",
    "a", "ul", "ol", "li", "blockquote", "code", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
    "span", "div",
    # GFM 任务列表的复选框（禁用态、纯展示）。
    "input",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class", "data-lang"},
    "span": {"class", "data-tex"},
    "div": {"class"},
    "th": {"align", "colspan"},
    "td": {"align", "colspan"},
    # 标题锚点：让 `[Features](#features)` 这类目录链接可用。
    "h1": {"id"},
    "h2": {"id"},
    "h3": {"id"},
    "h4": {"id"},
    "h5": {"id"},
    "h6": {"id"},
    # GFM 任务列表
    "input": {"type", "checked", "disabled"},
    "li": {"class"},
}

_PYGMENTS_FORMATTER = HtmlFormatter(nowrap=True)

# 语言标签 → 是否走 Pygments。mermaid 交给前端画图，不在这里分词。
_MERMAID_ALIASES = {"mermaid", "mmd"}
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _is_escaped(src: str, pos: int) -> bool:
    """True when the character at ``pos`` is preceded by an odd number of backslashes."""
    backslashes = 0
    index = pos - 1
    while index >= 0 and src[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _math_attr(tex: str) -> str:
    """``data-tex`` value for a formula, safe to drop into an HTML attribute."""
    return html.escape(tex, quote=True)


def _math_token(state, tex: str, *, display: bool):
    token = state.push("html_inline", "", 0)
    kind = "math-block" if display else "math-inline"
    token.content = f'<span class="{kind}" data-tex="{_math_attr(tex)}"></span>'
    return token


def _math_rule(state, silent: bool) -> bool:
    """Inline rule: ``$…$`` and ``$$…$$`` / ``\\(…\\)`` and ``\\[…\\]``.

    Delimiters are only recognised when the opening one is followed by a
    non-space and the closing one is preceded by a non-space — otherwise
    "I have $5 and $10" would turn into a formula. ``\\$`` escapes to a literal
    dollar and never opens a formula.
    """
    src = state.src
    pos = state.pos
    char = src[pos]

    if char == "\\":
        nxt = src[pos + 1] if pos + 1 < len(src) else ""
        if nxt in "([" and not _is_escaped(src, pos):
            closing = "\\]" if nxt == "[" else "\\)"
            end = src.find(closing, pos + 2)
            if end != -1:
                if not silent:
                    _math_token(state, src[pos + 2 : end], display=nxt == "[")
                state.pos = end + 2
                return True
        return False

    if char != "$":
        return False

    display = src.startswith("$$", pos)
    opener = 2 if display else 1
    delimiter = "$$" if display else "$"
    end = src.find(delimiter, pos + opener)
    if end == -1 or end == pos + opener:
        return False
    if not display:
        # 行内 `$…$` 两端的空白守卫：`$$` 没有它，因为多行展示公式
        # 写成 `$$\n…\n$$` 是惯用写法。
        if src[pos + opener].isspace() or src[end - 1].isspace():
            return False
        if "$" in src[pos + opener : end]:
            # A lone ``$`` never spans another ``$``; bail out so the text is kept.
            return False
    else:
        # `$$…$$` 只允许跨行，不允许再嵌套一个 `$$`。
        if "$$" in src[pos + opener : end]:
            return False
    if not silent:
        _math_token(state, src[pos + opener : end], display=display)
    state.pos = end + opener
    return True


def _slugify(text: str) -> str:
    """GitHub 风格锚点：小写、空格转连字符、去掉标点。中文等非 ASCII 保留。"""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or "section"


def _highlight_code(code: str, lang: str | None, _attrs: str | None = None) -> str:
    """markdown-it 的 highlight 回调：返回空串表示"用默认渲染"。

    未知语言（含 mermaid）回退成转义后的纯文本——总不能因为标签写错就让代码消失。
    """
    if not lang:
        return ""
    if lang.strip().lower() in _MERMAID_ALIASES:
        return ""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False)
    except ClassNotFound:
        return ""
    # Pygments 输出的是已转义的 HTML 片段；toolbar 会加上 <div class="highlight">
    # 包装，这里去掉，只保留 token span（外层 <pre><code> 由 markdown-it 提供）。
    return pygments_highlight(code, lexer, _PYGMENTS_FORMATTER)


def _add_heading_ids(md: MarkdownIt) -> None:
    """给标题加 id，供目录锚点跳转。重名时追加 -1、-2，与 GitHub 一致。"""

    def rule(state) -> None:
        tokens = state.tokens
        seen: dict[str, int] = {}
        for index, token in enumerate(tokens):
            if token.type != "heading_open":
                continue
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = inline.content if inline is not None else ""
            slug = _slugify(text)
            if slug in seen:
                seen[slug] += 1
                slug = f"{slug}-{seen[slug]}"
            else:
                seen[slug] = 0
            token.attrSet("id", slug)

    # 放在 inline 之后：那时标题的 inline token 已经有 content 可用来做锚点。
    md.core.ruler.push("heading_ids", rule)


def _add_task_list_items(md: MarkdownIt) -> None:
    """GFM 任务列表：把 `- [x] foo` 渲染成禁用状态的复选框。

    认的是**列表项第一个 inline 文本 token 的行首**（`[x] ` / `[ ] `），所以正文里
    写 "see [x] above" 不受影响。记号就地裁掉，复选框作为新的 html_inline 子 token
    插到最前面——不改动用户其余文本。
    """
    pattern = re.compile(r"^\[([ xX])\](?=\s|$)")

    def rule(state) -> None:
        tokens = state.tokens
        for index, token in enumerate(tokens):
            if token.type != "inline" or not token.children:
                continue
            # 必须处在列表项里：<li><p>inline</p></li>
            if index < 2 or tokens[index - 1].type != "paragraph_open":
                continue
            if tokens[index - 2].type != "list_item_open":
                continue
            first = token.children[0]
            if first.type != "text":
                continue
            match = pattern.match(first.content)
            if match is None:
                continue
            checked = match.group(1).lower() == "x"
            first.content = first.content[match.end() :].lstrip()
            # 直接造 html_inline token：解析器开了 html=False，走 parseInline 的话
            # 这段标签会被当成文本转义掉。
            box = Token("html_inline", "", 0)
            box.content = f'<input type="checkbox" disabled{" checked" if checked else ""}>'
            token.children.insert(0, box)
            tokens[index - 2].attrSet("class", "task-list-item")
            tokens[index - 1].attrSet("class", "task-list-item-body")

    md.core.ruler.push("task_lists", rule)


def _md() -> MarkdownIt:
    md = MarkdownIt(
        "default",
        {"html": False, "breaks": True, "linkify": False, "highlight": _highlight_code},
    )
    md.enable(["table", "strikethrough"])
    # 放在 escape 之前：反斜杠转义（\$、\\[）必须在公式规则之前有机会处理，
    # 但公式规则自己会先判断该反斜杠是否真的在转义。
    md.inline.ruler.before("escape", "lako_math", _math_rule)
    _add_heading_ids(md)
    _add_task_list_items(md)
    return md


# 为 <a> 强制 target/rel（对应 sanitize-html transformTags）
_ANCHOR_RE = re.compile(r"<a(?=[\s>])", re.IGNORECASE)


def render_markdown(markdown: str) -> str:
    raw = _md().render(markdown or "")
    clean = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
    )
    # 为 <a> 强制 rel + target（镜像 sanitize-html transformTags）
    clean = _ANCHOR_RE.sub('<a target="_blank" rel="noopener noreferrer nofollow"', clean)
    return clean


def render_plain_text(text: str) -> str:
    # 普通文本模式：HTML 转义后把换行转为 <br>，保留换行与空行
    # Plain-text mode: HTML-escape then convert newlines to <br>, preserving line breaks and blank lines
    escaped = html.escape(text or "", quote=True)
    return f"<p>{escaped.replace(chr(10), '<br>')}</p>"


def render_body(text: str, fmt: str = "markdown") -> str:
    # 按格式分发：markdown 走 markdown-it，text 走普通文本转义
    # Dispatch by format: markdown via markdown-it, text via plain-text escape
    if fmt == "text":
        return render_plain_text(text)
    return render_markdown(text)

"""Markdown → 净化 HTML — 镜像 infrastructure/markdown.ts。

客户端只提交 canonical Markdown；服务端渲染 + 净化后存 body_html。
净化白名单/属性/scheme/链接强制 rel+target 与 TS 端一致；渲染引擎不同，
HTML 输出允许观感级差异（sanitize 语义等价），存量行的 body_html 不重算。
"""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
import nh3

ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "em", "s", "u", "del", "ins",
    "a", "ul", "ol", "li", "blockquote", "code", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
    "span", "div",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class"},
    "span": {"class"},
    "div": {"class"},
    "th": {"align", "colspan"},
    "td": {"align", "colspan"},
}


def _md() -> MarkdownIt:
    md = MarkdownIt("default", {"html": False, "breaks": True, "linkify": False})
    md.enable(["table", "strikethrough"])
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

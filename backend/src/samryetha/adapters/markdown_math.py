"""Preserve TeX before Markdown parses escapes, emphasis, or line breaks."""

from __future__ import annotations

import html
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict


def _closing(source: str, marker: str, start: int, end: int) -> int:
    pos = source.find(marker, start, end)
    while pos != -1:
        slashes = 0
        cursor = pos - 1
        while cursor >= 0 and source[cursor] == "\\":
            slashes += 1
            cursor -= 1
        if slashes % 2 == 0:
            return pos
        pos = source.find(marker, pos + len(marker), end)
    return -1


def _inline(state: StateInline, silent: bool) -> bool:
    start = state.pos
    source = state.src
    if source.startswith((r"\$", r"\\"), start):
        if not silent:
            token = state.push("math_literal", "span", 0)
            token.content = source[start + 1]
        state.pos += 2
        return True
    if source.startswith("$$", start):
        opening, closing, display = "$$", "$$", True
    elif source.startswith(r"\[", start):
        opening, closing, display = r"\[", r"\]", True
    elif source.startswith(r"\(", start):
        opening, closing, display = r"\(", r"\)", False
    elif source[start] == "$" and (start == 0 or source[start - 1] != "$"):
        opening, closing, display = "$", "$", False
    else:
        return False

    content_start = start + len(opening)
    end = _closing(source, closing, content_start, state.posMax)
    if end == -1 or not source[content_start:end].strip():
        return False
    if opening == "$":
        # Currency, escaped dollars, and unfinished formulas stay literal.
        if source[content_start].isspace() or source[end - 1].isspace():
            return False
        if "\n" in source[content_start:end] or source.startswith("$$", end):
            return False
        if end + 1 < state.posMax and source[end + 1].isdigit():
            return False

    if not silent:
        token = state.push("math_display" if display else "math_inline", "", 0)
        token.content = source[content_start:end]
    state.pos = end + len(closing)
    return True


def _block(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    if state.is_code_block(start):
        return False
    pos = state.bMarks[start] + state.tShift[start]
    first = state.src[pos:state.eMarks[start]]
    if first.startswith("$$"):
        opening, closing = "$$", "$$"
    elif first.startswith(r"\["):
        opening, closing = r"\[", r"\]"
    else:
        return False

    lines: list[str] = []
    for line in range(start, end):
        if line > start and state.sCount[line] < state.blkIndent and not state.isEmpty(line):
            return False
        offset = state.bMarks[line] + state.tShift[line]
        text = state.src[offset:state.eMarks[line]]
        if line == start:
            text = text[len(opening):]
        close = _closing(text, closing, 0, len(text))
        if close != -1:
            if text[close + len(closing):].strip():
                return False
            lines.append(text[:close])
            content = "\n".join(lines).strip()
            if not content:
                return False
            if not silent:
                token = state.push("math_block", "div", 0)
                token.block = True
                token.content = content
                token.map = [start, line + 1]
                state.line = line + 1
            return True
        lines.append(text)
    return False


def math_plugin(md: MarkdownIt) -> None:
    md.inline.ruler.before("escape", "math", _inline)
    md.block.ruler.before("fence", "math", _block, {"alt": ["paragraph", "reference", "blockquote"]})

    def render_math(
        _renderer: RendererHTML,
        tokens: Sequence[Token],
        idx: int,
        _options: OptionsDict,
        _env: EnvType,
    ) -> str:
        token = tokens[idx]
        display = token.type != "math_inline"
        style = "math-block" if display else "math-inline"
        # Preserve the deployed HTML contract so existing posts and previews
        # use the same client renderer; TeX stays plain text in one attribute.
        content = html.escape(token.content, quote=True)
        suffix = "\n" if token.block else ""
        return f'<span class="{style}" data-tex="{content}"></span>{suffix}'

    for name in ("math_inline", "math_display", "math_block"):
        md.add_render_rule(name, render_math)

    def render_literal(
        _renderer: RendererHTML,
        tokens: Sequence[Token],
        idx: int,
        _options: OptionsDict,
        _env: EnvType,
    ) -> str:
        return f'<span class="math-literal">{html.escape(tokens[idx].content)}</span>'

    md.add_render_rule("math_literal", render_literal)

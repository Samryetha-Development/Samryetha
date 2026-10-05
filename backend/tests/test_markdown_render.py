"""Markdown 渲染：公式切分、代码高亮、任务列表、标题锚点。

这些行为此前没有测试，而它们全部踩过坑：
  - 多行 $$…$$ 因为 breaks 把换行变成 <br> 而被拆散，历史上完全不渲染；
  - \\[…\\] 的反斜杠被 markdown-it 当转义吃掉；
  - 未知语言标签曾经让代码整段消失；
  - 任务列表的复选框因为 input 不在净化白名单里被静默剥掉。
"""

from __future__ import annotations

import html as html_module
import re

from samryetha.markdown import render_body, render_markdown


def math_spans(html: str) -> list[tuple[str, str]]:
    """返回 [(kind, 未转义的 tex)]。"""
    return [
        (match.group(1), html_module.unescape(match.group(2)))
        for match in re.finditer(r'<span class="(math-inline|math-block)" data-tex="([^"]*)"', html)
    ]


# ---------------------------------------------------------------- 公式


def test_inline_math_becomes_a_fillable_container():
    assert math_spans(render_markdown("value $x^2$ here")) == [("math-inline", "x^2")]


def test_display_math_single_line():
    assert math_spans(render_markdown("$$x^2$$")) == [("math-block", "x^2")]


def test_display_math_survives_a_multiline_formula():
    """回归：breaks=True 会把公式内的换行变成 <br>，把 $$…$$ 拆进多个文本节点。"""
    out = render_markdown("$$\n\\int_0^1 x\\,dx\n$$")
    spans = math_spans(out)
    assert len(spans) == 1
    kind, tex = spans[0]
    assert kind == "math-block"
    # TeX 必须完整（换行保留），且不能混进 <br>
    assert "\\int_0^1 x\\,dx" in tex
    assert "<br" not in out


def test_bracket_and_paren_delimiters():
    assert math_spans(render_markdown("\\[x^2\\]")) == [("math-block", "x^2")]
    assert math_spans(render_markdown("\\(y\\)")) == [("math-inline", "y")]


def test_currency_is_not_a_formula():
    """`$5 and $10` 不能被当成公式——两端空白守卫的意义。"""
    out = render_markdown("I have $5 and $10")
    assert math_spans(out) == []
    assert "$5" in out and "$10" in out


def test_escaped_dollar_stays_literal():
    out = render_markdown("costs \\$5 today")
    assert math_spans(out) == []
    assert "$5" in out


def test_math_inside_code_is_not_extracted():
    assert math_spans(render_markdown("use `$x$` literal")) == []
    assert math_spans(render_markdown("```\n$$y$$\n```")) == []


def test_tex_with_quotes_is_attribute_escaped():
    out = render_markdown('$\\text{"hi"}$')
    spans = math_spans(out)
    assert len(spans) == 1
    assert spans[0][1] == '\\text{"hi"}'
    # 原样输出会破坏属性，必须被转义过
    assert "&quot;" in out


# ---------------------------------------------------------------- 代码高亮


def test_known_language_is_tokenized():
    out = render_markdown("```python\ndef fib(n):\n    return n  # c\n```")
    assert 'class="language-python"' in out
    assert '<span class="k">def</span>' in out
    assert '<span class="c1">' in out


def test_unknown_language_falls_back_to_escaped_text():
    """回归：未知标签不能让代码消失。"""
    out = render_markdown("```unknownlang\na < b & c\n```")
    assert 'class="language-unknownlang"' in out
    assert "a &lt; b &amp; c" in out


def test_code_without_language_is_not_highlighted():
    out = render_markdown("```\nplain\n```")
    assert "<pre><code>" in out
    assert "plain" in out


def test_mermaid_is_left_as_a_plain_code_block_for_the_client():
    out = render_markdown("```mermaid\ngraph LR\n  A --> B\n```")
    assert 'class="language-mermaid"' in out
    assert "A --&gt; B" in out


# ---------------------------------------------------------------- 任务列表


def test_task_list_items_become_checkboxes():
    out = render_markdown("- [x] done\n- [ ] todo\n")
    assert out.count('class="task-list-item"') == 2
    assert '<input type="checkbox" disabled="" checked="">' in out
    assert '<input type="checkbox" disabled="">' in out
    # 记号本身不该留在正文里
    assert "[x]" not in out and "[ ]" not in out


def test_bracketed_text_outside_a_list_is_untouched():
    out = render_markdown("Text with [x] inline.\n\n- see [x] above\n")
    assert "task-list-item" not in out
    assert "[x]" in out


# ---------------------------------------------------------------- 标题锚点


def test_headings_get_github_style_ids():
    out = render_markdown("# Features\n\n## Text Formatting\n")
    assert '<h1 id="features">' in out
    assert '<h2 id="text-formatting">' in out


def test_duplicate_headings_get_unique_ids():
    out = render_markdown("# Features\n\n## Features\n")
    assert '<h1 id="features">' in out
    assert '<h2 id="features-1">' in out


def test_heading_ids_survive_non_ascii():
    assert 'id="' in render_markdown("## 中文标题\n")


# ---------------------------------------------------------------- 既有行为不回退


def test_plain_text_format_is_still_escaped():
    out = render_body("line1\n\n<b>", "text")
    assert out == "<p>line1<br><br>&lt;b&gt;</p>"


def test_anchors_get_target_and_rel():
    out = render_markdown("[x](https://example.com)")
    assert 'rel="noopener noreferrer nofollow"' in out
    assert 'target="_blank"' in out


def test_script_tags_are_stripped():
    out = render_markdown("<script>alert(1)</script>")
    assert "<script" not in out

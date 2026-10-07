"""Preview parity, safe HTML, and TeX preservation through Markdown parsing."""

import html
import re

import pytest

from samryetha.adapters.markdown import render_body, render_markdown


@pytest.mark.parametrize(
    "source",
    [
        r"$a_1 + b_2$",
        r"\(a_1 + b_2\)",
        r"$$\frac{a_1}{b_2}$$",
        r"\[\frac{a_1}{b_2}\]",
        "$a_1 + b_2$ in **bold** text",
    ],
)
def test_math_is_preserved_before_markdown(source):
    rendered = render_markdown(source)
    assert 'data-tex="' in rendered
    assert "<em>" not in rendered
    assert "a_1" in rendered and "b_2" in rendered


@pytest.mark.parametrize("opening,closing", [("$$", "$$"), (r"\[", r"\]")])
def test_multiline_display_math(opening, closing):
    formula = "\\begin{aligned}\na_1 &= b_2 \\\\\nc_3 &= d_4\n\\end{aligned}"
    rendered = render_markdown(f"Before\n\n{opening}\n{formula}\n{closing}\n\nAfter")
    assert f'<span class="math-block" data-tex="{html.escape(formula, quote=True)}"></span>' in rendered
    assert "<br>" not in rendered
    assert "<p>Before</p>" in rendered and "<p>After</p>" in rendered


def test_inline_display_preserves_newlines():
    rendered = render_markdown("Equation $$a_1 +\nb_2$$ done")
    assert '<span class="math-block" data-tex="a_1 +\nb_2"></span>' in rendered


def test_math_in_quotes_and_lists():
    rendered = render_markdown("> $$\n> a_1 + b_2\n> $$\n\n- \\(c_3 + d_4\\)")
    assert '<span class="math-block" data-tex="a_1 + b_2"></span>' in rendered
    assert '<span class="math-inline" data-tex="c_3 + d_4"></span>' in rendered


@pytest.mark.parametrize(
    "source",
    [
        "`$a_1 + b_2$`",
        "```tex\n\\[a_1 + b_2\\]\n```",
        "    $$a_1 + b_2$$",
        r"\$a_1 + b_2\$",
        r"\\(a_1 + b_2\\)",
        "$5 and $10",
        "$ unfinished",
        "$$unfinished",
        r"\[unfinished",
    ],
)
def test_code_currency_escapes_and_unfinished_math_stay_literal(source):
    assert "data-tex" not in render_markdown(source)


def test_math_html_is_escaped_and_links_are_sanitized():
    rendered = render_markdown("$<img src=x onerror=alert(1)>$ [bad](javascript:alert(1)) <script>alert(1)</script>")
    assert "<img" not in rendered and "<script" not in rendered
    assert "&lt;img" in rendered
    assert 'href="javascript:' not in rendered


@pytest.mark.parametrize("formula", [r'\text{"<&lt; &gt; &amp;>"}', r'\text{"><img src=x onerror=alert(1)>}'])
def test_deployed_math_attribute_preserves_entities_and_quotes(formula):
    rendered = render_markdown(f"${formula}$")
    match = re.search(r'<span class="math-inline" data-tex="([^"]*)"></span>', rendered)
    assert match is not None
    assert html.unescape(match.group(1)) == formula
    assert "math-source" not in rendered and "<img" not in rendered


def test_escaped_delimiters_are_protected_from_legacy_math_rendering():
    assert (
        render_markdown(r"\$a\$") == '<p><span class="math-literal">$</span>a<span class="math-literal">$</span></p>\n'
    )
    assert 'class="math-literal">\\</span>' in render_markdown(r"\\(a\\)")


@pytest.mark.parametrize("fmt", ["markdown", "text"])
def test_preview_matches_published_body_without_creating_a_post(api, fmt):
    api.login_dev()
    api.c.post("/api/boards", json={"name": "Preview", "slug": "preview"})
    source = "# Heading\n\n**Bold** and \\(a_1 + b_2\\)\n\n$$\n\\frac{1}{2}\n$$"
    body = {"bodyMarkdown": source, "bodyFormat": fmt}
    before = api.c.get("/api/discussions").json()
    preview = api.c.post("/api/discussions/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"bodyHtml": render_body(source, fmt)}
    assert api.c.get("/api/discussions").json() == before
    posted = api.c.post("/api/discussions", json={"boardSlug": "preview", **body})
    assert posted.status_code == 201, posted.text
    assert preview.json()["bodyHtml"] == posted.json()["bodyHtml"]


@pytest.mark.parametrize("status", ["pending", "banned"])
def test_preview_requires_an_active_session(api, status):
    from sqlalchemy import update
    from samryetha.core.schema import users

    body = {"bodyMarkdown": "hello"}
    assert api.c.post("/api/discussions/preview", json=body).status_code == 401
    api.mkuser("student")
    api.login("student")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "student").values(status=status))
    assert api.c.post("/api/discussions/preview", json=body).status_code == 403


def test_preview_validates_format_and_length_and_accepts_empty_drafts(api):
    api.login_dev()
    assert api.c.post("/api/discussions/preview", json={"bodyMarkdown": ""}).status_code == 200
    for body in (
        {"bodyMarkdown": "x", "bodyFormat": "html"},
        {"bodyMarkdown": "x" * 40001},
        {},
    ):
        assert api.c.post("/api/discussions/preview", json=body).status_code == 422

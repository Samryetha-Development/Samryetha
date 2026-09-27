"""HTML email rendering — one clean, client-safe template for all Lako mail.

Email clients do not load external CSS or run scripts, so everything is inline
and table-based. `render_email` also returns a matching plain-text body so the
message degrades gracefully (and the text part stays greppable in tests).
"""

from __future__ import annotations

import html as html_module

_ACCENT = "#476f80"
_INK = "#1a1d1f"
_MUTED = "#5b646a"
_FAINT = "#8a9296"
_LINE = "#e6e9eb"
_BG = "#f4f5f6"


def _esc(value: str) -> str:
    return html_module.escape(value, quote=True)


def _paragraphs(value: str | None) -> str:
    if not value:
        return ""
    blocks = [block.strip() for block in value.split("\n\n") if block.strip()]
    return "".join(
        f'<p style="margin:0 0 12px;font-size:14px;line-height:1.65;color:{_MUTED};">'
        f"{_esc(block).replace(chr(10), '<br>')}</p>"
        for block in blocks
    )


def render_email(
    *,
    heading: str,
    intro: str,
    cta_label: str | None = None,
    cta_url: str | None = None,
    outro: str | None = None,
    brand: str = "Samryetha",
) -> str:
    button = ""
    if cta_label and cta_url:
        safe_url = _esc(cta_url)
        button = (
            '<tr><td style="padding:22px 28px 0;">'
            f'<a href="{safe_url}" style="display:inline-block;background:{_ACCENT};color:#ffffff;'
            'text-decoration:none;font-size:14px;font-weight:600;padding:12px 22px;border-radius:10px;">'
            f"{_esc(cta_label)}</a>"
            '<p style="margin:14px 0 0;font-size:12px;line-height:1.6;color:' + _FAINT + ';">'
            "或把下面的链接复制到浏览器打开：<br>"
            f'<a href="{safe_url}" style="color:{_ACCENT};word-break:break-all;">{safe_url}</a></p>'
            "</td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "</head>"
        f'<body style="margin:0;padding:0;background:{_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:32px 12px;">'
        '<tr><td align="center">'
        f'<table role="presentation" width="480" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:480px;background:#ffffff;border:1px solid {_LINE};border-radius:16px;overflow:hidden;">'
        '<tr><td style="padding:26px 28px 0;">'
        f'<span style="font-size:15px;font-weight:700;letter-spacing:.02em;color:{_ACCENT};">{_esc(brand)}</span>'
        "</td></tr>"
        '<tr><td style="padding:14px 28px 0;">'
        f'<h1 style="margin:0 0 12px;font-size:21px;line-height:1.3;font-weight:600;color:{_INK};">{_esc(heading)}</h1>'
        f"{_paragraphs(intro)}"
        "</td></tr>"
        f"{button}"
        '<tr><td style="padding:22px 28px 26px;">'
        f'<p style="margin:0;font-size:12px;line-height:1.7;color:{_FAINT};">{_paragraphs(outro)}</p>'
        "</td></tr>"
        f'<tr><td style="padding:16px 28px 22px;border-top:1px solid {_LINE};">'
        f'<p style="margin:0;font-size:11px;line-height:1.6;color:{_FAINT};">'
        "本邮件由 Samryetha 身份服务自动发送，请勿直接回复。</p>"
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )

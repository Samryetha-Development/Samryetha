"""开发态邮件输出 — 镜像 infrastructure/email/console.ts。

生产可换 SMTP（按 SMTP_URL）；本地把结构打到日志便于断言，但**不落正文**——
正文可能含密码重置令牌等敏感信息，绝不允许进日志。
"""

from __future__ import annotations

import html as html_module
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import unquote, urlparse

logger = logging.getLogger("samryetha.mail")


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None: ...

# 邮件客户端不加载外部 CSS、不执行脚本，所以全部内联、table 布局。
# Email clients load no external CSS and run no scripts: inline, table-based.
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
    """品牌化 HTML 邮件（与 Lako 侧同一套版式）。"""
    button = ""
    if cta_label and cta_url:
        safe_url = _esc(cta_url)
        button = (
            '<tr><td style="padding:22px 28px 0;">'
            f'<a href="{safe_url}" style="display:inline-block;background:{_ACCENT};color:#ffffff;'
            'text-decoration:none;font-size:14px;font-weight:600;padding:12px 22px;border-radius:10px;">'
            f"{_esc(cta_label)}</a>"
            f'<p style="margin:14px 0 0;font-size:12px;line-height:1.6;color:{_FAINT};">'
            "或把下面的链接复制到浏览器打开：<br>"
            f'<a href="{safe_url}" style="color:{_ACCENT};word-break:break-all;">{safe_url}</a></p>'
            "</td></tr>"
        )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:{_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:32px 12px;">'
        '<tr><td align="center">'
        '<table role="presentation" width="480" cellpadding="0" cellspacing="0" '
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
        "本邮件由 Samryetha 自动发送，请勿直接回复。</p>"
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


class ConsoleMailer:
    """开发 / 未接线 SMTP 时的兜底：只记 to/subject，绝不记正文。"""

    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        logger.info("[mail] to=%s subject=%s (body omitted, %d chars)", to, subject, len(text))


class SmtpMailer:
    """按 SMTP_URL 用 smtplib 发信。smtp_url 形如 smtps://user:pass@host:port。"""

    def __init__(self, smtp_url: str, smtp_from: str, *, timeout: int = 15) -> None:
        parsed = urlparse(smtp_url)
        scheme = parsed.scheme or "smtp"
        if scheme not in ("smtp", "smtps"):
            raise ValueError(f"Unsupported SMTP scheme: {scheme}")
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or (465 if scheme == "smtps" else 587)
        self._username = unquote(parsed.username) if parsed.username else None
        self._password = unquote(parsed.password) if parsed.password else None
        self._use_tls = scheme == "smtps"
        self._smtp_from = smtp_from
        self._timeout = timeout

    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._smtp_from
        msg["To"] = to
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        if self._use_tls:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout) as smtp:
                self._login(smtp)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                self._login(smtp)
                smtp.send_message(msg)

    def _login(self, smtp: smtplib.SMTP) -> None:
        if self._username and self._password:
            smtp.login(self._username, self._password)


def build_mailer(smtp_url: str | None, smtp_from: str, *, is_production: bool) -> Mailer:
    """按 smtp_url 选 SMTP 或 Console；未接线 SMTP 时在生产打印醒目告警。"""
    if smtp_url:
        return SmtpMailer(smtp_url, smtp_from)
    if is_production:
        logger.warning("SMTP_URL is not set — emails will NOT be delivered (ConsoleMailer)")
    return ConsoleMailer()


def ban_notification_text(reason: str | None, banned_until_iso: str | None) -> str:
    """镜像 moderation/routes.ts user.banned 的邮件正文。"""
    suffix = f"（至 {banned_until_iso}）" if banned_until_iso else ""
    return f"你的账号已被封禁{suffix}" + (f"。原因：{reason}" if reason else "")


def ban_notification_email(*, reason: str | None, banned_until_iso: str | None) -> str:
    return render_email(
        heading="账号封禁通知",
        intro=ban_notification_text(reason, banned_until_iso),
        outro="如有异议，请通过论坛反馈渠道联系管理员。",
    )


def password_reset_email(*, link: str, display_name: str) -> tuple[str, str, str]:
    """返回 (subject, text, html)。"""
    subject = "Reset your Samryetha password"
    text = f"Hi {display_name},\n\nClick the link below to reset your password (expires in 1 hour):\n\n{link}"
    html = render_email(
        heading="Reset your password",
        intro=f"Hi {display_name},\n\nSomeone requested a password reset for your Samryetha account. This link is valid for 1 hour.",
        cta_label="Reset password",
        cta_url=link,
        outro="If this wasn't you, you can safely ignore this email — your password will not change.",
    )
    return subject, text, html


def qr_signin_confirmation_email(*, code: str, display_name: str) -> tuple[str, str, str]:
    """扫码登录二次确认码：返回 (subject, text, html)。"""
    subject = "Confirm your Samryetha sign-in"
    text = (
        f"Hi {display_name},\n\n"
        "Someone is signing in to your Samryetha account on a new device. "
        f"Enter this confirmation code on your phone to approve it (expires in 10 minutes):\n\n{code}"
    )
    html = render_email(
        heading="Confirm your sign-in",
        intro=(
            f"Hi {display_name},\n\n"
            "Someone is signing in to your Samryetha account on a new device. "
            f"Enter this code on your phone to approve it:\n\n{code}"
        ),
        outro="This code expires in 10 minutes. If this wasn't you, you can safely ignore this email — the sign-in will not proceed.",
    )
    return subject, text, html

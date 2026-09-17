"""开发态邮件输出 — 镜像 infrastructure/email/console.ts。

生产可换 SMTP（按 SMTP_URL）；本地把结构打到日志便于断言，但**不落正文**——
正文可能含密码重置令牌等敏感信息，绝不允许进日志。
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

logger = logging.getLogger("samryetha.mail")


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

    def _login(self, smtp) -> None:
        if self._username and self._password:
            smtp.login(self._username, self._password)


def build_mailer(smtp_url: str | None, smtp_from: str, *, is_production: bool):
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


def password_reset_email_text(*, link: str, display_name: str) -> tuple[str, str]:
    return (
        "Reset your Samryetha password",
        f"Hi {display_name},\n\nClick the link below to reset your password (expires in 1 hour):\n\n{link}",
    )

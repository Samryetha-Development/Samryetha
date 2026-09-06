"""开发态邮件输出 — 镜像 infrastructure/email/console.ts。

生产可换 SMTP；本地把结构打到日志便于断言。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("samryetha.mail")


class ConsoleMailer:
    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        logger.info("[mail] to=%s subject=%s\n%s", to, subject, text)


def ban_notification_text(reason: str | None, banned_until_iso: str | None) -> str:
    """镜像 moderation/routes.ts user.banned 的邮件正文。"""
    suffix = f"（至 {banned_until_iso}）" if banned_until_iso else ""
    return f"你的账号已被封禁{suffix}" + (f"。原因：{reason}" if reason else "")


def password_reset_email_text(*, link: str, display_name: str) -> tuple[str, str]:
    return (
        "Reset your Samryetha password",
        f"Hi {display_name},\n\nClick the link below to reset your password (expires in 1 hour):\n\n{link}",
    )

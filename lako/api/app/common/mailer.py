"""Outgoing mail. SmtpMailer in production; NullMailer (log only) when SMTP is
unconfigured (local dev); DummyMailer captures into memory for tests.

Override per-test with ``app.state.mailer = DummyMailer()``.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger("lako.mailer")


class Mailer(Protocol):
    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None: ...


class NullMailer:
    """Log-only fallback for environments without SMTP (local dev)."""

    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        logger.warning("[mail:noconfig] to=%s subject=%s\n%s", to, subject, text)


class DummyMailer:
    """In-memory outbox for tests."""

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html})


class SmtpMailer:
    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        sender: str,
        use_tls: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls
        self._timeout = timeout_seconds

    def _deliver(self, message: EmailMessage) -> None:
        session = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        try:
            session.ehlo()
            if self._use_tls:
                session.starttls()
                session.ehlo()
            if self._username:
                session.login(self._username, self._password or "")
            session.send_message(message)
        finally:
            try:
                session.quit()
            except smtplib.SMTPException:
                pass

    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        await asyncio.to_thread(self._deliver, message)


def build_mailer(settings) -> Mailer:
    if settings.smtp_host:
        return SmtpMailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.smtp_from or f"noreply@{settings.smtp_host}",
            use_tls=settings.smtp_use_tls,
            timeout_seconds=settings.smtp_timeout_seconds,
        )
    return NullMailer()

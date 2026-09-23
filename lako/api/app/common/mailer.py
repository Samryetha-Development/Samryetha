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

    async def ping(self) -> None:
        """Liveness probe: log-only delivery never fails."""


class DummyMailer:
    """In-memory outbox for tests."""

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html})

    async def ping(self) -> None:
        """Liveness probe: in-memory delivery never fails."""


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
        session = None
        try:
            session = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
            session.ehlo()
            if self._use_tls:
                session.starttls()
                session.ehlo()
            if self._username:
                session.login(self._username, self._password or "")
            session.send_message(message)
        finally:
            if session is not None:
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

    async def ping(self) -> None:
        """Non-delivering liveness check: connect + EHLO, then quit."""

        def _check() -> None:
            session = None
            try:
                session = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
                session.ehlo()
            finally:
                if session is not None:
                    try:
                        session.quit()
                    except smtplib.SMTPException:
                        pass

        await asyncio.to_thread(_check)


async def ensure_available(mailer: object) -> None:
    """Fail like ``send`` would when the mail backend is down, without delivering.

    Lets callers that have nothing to send (unknown reset accounts) surface a
    mail outage identically instead of returning 200 while known accounts get
    503. Mailers without ``ping`` (minimal test doubles) are assumed healthy.
    """
    ping = getattr(mailer, "ping", None)
    if ping is not None:
        await ping()


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

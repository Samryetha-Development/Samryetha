"""Transactional email rendering: plain-text stays intact and HTML is branded."""

from __future__ import annotations

from samryetha.adapters.mailer import ban_notification_email, password_reset_email


def test_password_reset_email_returns_text_and_html():
    link = "https://samryetha.com/reset-password?token=abc123"
    subject, text, html = password_reset_email(link=link, display_name="Avocado")
    assert subject == "Reset your Samryetha password"
    assert link in text
    assert "Avocado" in text
    assert link in html
    assert "Reset your password" in html
    assert "<a href=" in html


def test_ban_notification_email_is_branded():
    html = ban_notification_email(reason="spam", banned_until_iso=None)
    assert "账号封禁通知" in html
    assert "spam" in html
    assert "Samryetha" in html

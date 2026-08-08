"""Unit tests for transactional email delivery (auth.md A1/A4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mesh.auth.mailer import (
    DevMailDelivery,
    NullMailDelivery,
    SmtpMailDelivery,
    build_mailer,
)
from mesh.config import load_settings

pytestmark = pytest.mark.unit

REQUIRED = {"database_url": "postgresql+asyncpg://u:p@h:5432/db", "redis_url": "redis://h:6379/0"}


def _settings(**overrides):
    return load_settings(**REQUIRED, **overrides)


# --- dev mailbox -------------------------------------------------------------


async def test_dev_mail_delivery_writes_redis(redis_client):
    delivery = DevMailDelivery(redis_client)
    await delivery.deliver("a@corp.com", "password_reset", "tok-123")
    assert await redis_client.get("mesh:devmail:password_reset:a@corp.com") == "tok-123"


# --- SMTP compose ------------------------------------------------------------


def test_compose_verification_email():
    mailer = SmtpMailDelivery(_settings(auth_mode="production", smtp_host="mail.corp.com"))
    msg = mailer.compose("a@corp.com", "email_verification", "v-token")
    assert msg["Subject"] == "Verify your Mesh email"
    assert msg["To"] == "a@corp.com"
    assert msg["From"] == "noreply@mesh.local"
    body = msg.get_content()
    assert "v-token" in body


def test_compose_reset_email_includes_link_when_base_url_set():
    mailer = SmtpMailDelivery(
        _settings(
            auth_mode="production",
            smtp_host="mail.corp.com",
            app_base_url="https://mesh.example.com",
        )
    )
    msg = mailer.compose("a@corp.com", "password_reset", "r-token")
    body = msg.get_content()
    assert "r-token" in body
    assert "https://mesh.example.com/reset-password?token=r-token" in body


def test_compose_without_base_url_has_no_link():
    mailer = SmtpMailDelivery(_settings(auth_mode="production", smtp_host="mail.corp.com"))
    body = mailer.compose("a@corp.com", "password_reset", "r-token").get_content()
    assert "http" not in body


@pytest.mark.parametrize("kind", ["notification_digest", "notification_realtime"])
def test_compose_notification_kinds_pass_body_through_verbatim(kind):
    # Notification mails arrive fully rendered (locale chrome + escaped
    # previews + deep link — comment-inbox.md §4.4); the ``token`` slot is
    # the body itself and must never be wrapped in one-time-code chrome.
    mailer = SmtpMailDelivery(
        _settings(
            auth_mode="production",
            smtp_host="mail.corp.com",
            app_base_url="https://mesh.example.com",
        )
    )
    rendered = "Mesh notification digest (1 items)\n\nPreview: hello\n"
    msg = mailer.compose("a@corp.com", kind, rendered)
    assert msg["Subject"] == "Mesh notification"
    assert msg.get_content() == rendered


# --- SMTP send ---------------------------------------------------------------


async def test_smtp_deliver_sends_via_smtplib():
    mailer = SmtpMailDelivery(
        _settings(
            auth_mode="production",
            smtp_host="mail.corp.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
        )
    )
    fake_smtp = MagicMock()
    with patch("mesh.auth.mailer.smtplib.SMTP", return_value=fake_smtp) as smtp_cls:
        # SMTP is a context manager
        fake_smtp.__enter__.return_value = fake_smtp
        await mailer.deliver("a@corp.com", "email_verification", "v-token")
    smtp_cls.assert_called_once_with("mail.corp.com", 587, timeout=10.0)
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("user", "pass")
    fake_smtp.send_message.assert_called_once()


async def test_smtp_deliver_skips_login_when_no_credentials():
    mailer = SmtpMailDelivery(
        _settings(auth_mode="production", smtp_host="mail.corp.com", smtp_use_tls=False)
    )
    fake_smtp = MagicMock()
    fake_smtp.__enter__.return_value = fake_smtp
    with patch("mesh.auth.mailer.smtplib.SMTP", return_value=fake_smtp):
        await mailer.deliver("a@corp.com", "password_reset", "r-token")
    fake_smtp.starttls.assert_not_called()
    fake_smtp.login.assert_not_called()
    fake_smtp.send_message.assert_called_once()


# --- null + factory ----------------------------------------------------------


async def test_null_delivery_is_noop():
    await NullMailDelivery().deliver("a@corp.com", "password_reset", "tok")  # no raise


def test_build_mailer_selects_dev_in_dev_mode(redis_client):
    mailer = build_mailer(_settings(auth_mode="dev"), redis_client)
    assert isinstance(mailer, DevMailDelivery)


def test_build_mailer_selects_smtp_in_production_with_host(redis_client):
    mailer = build_mailer(
        _settings(auth_mode="production", smtp_host="mail.corp.com"), redis_client
    )
    assert isinstance(mailer, SmtpMailDelivery)


def test_build_mailer_selects_null_in_production_without_host(redis_client):
    mailer = build_mailer(_settings(auth_mode="production"), redis_client)
    assert isinstance(mailer, NullMailDelivery)

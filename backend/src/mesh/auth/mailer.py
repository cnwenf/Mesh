"""Transactional email delivery (auth.md A1/A4 — verification & reset).

One interface, three backends selected by :func:`build_mailer`:

- **DevMailDelivery** — ``auth_mode=dev``: stash the one-time token in a Redis
  dev-mailbox so tests/dev can fetch it (the database stores only the hash).
- **SmtpMailDelivery** — production: send a real email over SMTP (env-configured
  ``MESH_SMTP_*``). The blocking ``smtplib`` call runs in a worker thread so the
  async event loop is never stalled.
- **NullMailDelivery** — production with no SMTP configured: a logged no-op so
  the API still boots; the operator must configure SMTP for closed-loop email.

Email bodies are vendor-neutral (no third-party branding — Mesh is original).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

from mesh.config import Settings

logger = logging.getLogger("mesh.mailer")

DEVMAIL_TTL_SECONDS = 3600

_SUBJECTS = {
    "email_verification": "Verify your Mesh email",
    "password_reset": "Reset your Mesh password",
}
_LINK_PATHS = {
    "email_verification": "/verify-email",
    "password_reset": "/reset-password",
}
_BODIES = {
    "email_verification": (
        "Hi,\n\nPlease confirm your email address to finish setting up your Mesh "
        "account.\n\nYour verification code is: {token}\n"
    ),
    "password_reset": (
        "Hi,\n\nWe received a request to reset your Mesh password. Use the code "
        "below to choose a new one. If you did not request this, you can ignore "
        "this email.\n\nYour reset code is: {token}\n"
    ),
}


@runtime_checkable
class Delivery(Protocol):
    """Deliver a one-time ``token`` of ``kind`` to ``email``."""

    async def deliver(self, email: str, kind: str, token: str) -> None: ...


class DevMailDelivery:
    """Dev/test delivery: write the token to a Redis dev-mailbox key."""

    def __init__(self, redis) -> None:
        self._redis = redis

    async def deliver(self, email: str, kind: str, token: str) -> None:
        await self._redis.set(
            f"mesh:devmail:{kind}:{email}", token, ex=DEVMAIL_TTL_SECONDS
        )


class NullMailDelivery:
    """No-op delivery (production without SMTP configured). Logs so the gap is
    visible; the API keeps working but email is not actually sent."""

    async def deliver(self, email: str, kind: str, token: str) -> None:  # noqa: ARG002
        logger.warning(
            "email delivery is not configured (set MESH_SMTP_HOST); dropping %s mail",
            kind,
        )


class SmtpMailDelivery:
    """Production delivery over SMTP (env-configured)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def compose(self, email: str, kind: str, token: str) -> EmailMessage:
        """Build the EmailMessage for ``kind`` (pure — unit-testable)."""
        msg = EmailMessage()
        msg["Subject"] = _SUBJECTS.get(kind, "Mesh notification")
        msg["From"] = self._settings.smtp_from
        msg["To"] = email
        body = _BODIES.get(kind, "Your code is: {token}\n").format(token=token)
        base = self._settings.app_base_url
        path = _LINK_PATHS.get(kind)
        if base and path:
            body += f"\nOr open: {base.rstrip('/')}{path}?token={token}\n"
        msg.set_content(body)
        return msg

    def _send_blocking(self, msg: EmailMessage) -> None:
        s = self._settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout) as smtp:
            if s.smtp_use_tls:
                smtp.starttls()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)

    async def deliver(self, email: str, kind: str, token: str) -> None:
        msg = self.compose(email, kind, token)
        # smtplib is blocking — run it off the event loop.
        await asyncio.to_thread(self._send_blocking, msg)


def build_mailer(settings: Settings, redis) -> Delivery:
    """Pick the delivery backend from settings (see module docstring)."""
    if settings.auth_mode == "dev":
        return DevMailDelivery(redis)
    if settings.smtp_host:
        return SmtpMailDelivery(settings)
    return NullMailDelivery()

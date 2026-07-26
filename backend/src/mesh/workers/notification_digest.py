"""Notification email digest sweep (comment-inbox.md §4.4 邮件摘要).

A supervised worker loop: every interval it drains pending ``email``
delivery-ledger rows, aggregates them per recipient into one digest mail,
and marks the rows ``sent`` (or ``failed`` with the reason only — R3 never
mixes routing data into ``error``). ``uq_delivery`` keeps the sweep
idempotent: a crash mid-batch re-sends nothing that was already marked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from mesh.comment_inbox.notifications import send_digest_emails

logger = logging.getLogger("mesh.workers.notification_digest")


async def notification_digest_loop(
    session_factory,
    *,
    mailer,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """Run the digest sweep every ``interval`` seconds until ``stop``."""
    while not stop.is_set():
        try:
            async with session_factory() as session, session.begin():
                sent = await send_digest_emails(session, mailer=mailer)
            if sent:
                logger.info("notification digest sweep sent %s emails", sent)
        except Exception:
            logger.exception("notification digest sweep failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


__all__ = ["notification_digest_loop"]

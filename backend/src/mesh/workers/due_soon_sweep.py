"""Due-soon notification sweep loop (comment-inbox.md §2.2 ``due_soon``).

Supervised worker loop: every interval it scans for open issues whose due
date falls inside the reminder horizon and registers one ``due_soon``
fan-out per issue+due-date (de-duped against existing notification rows and
an outbox ``idempotency_key``). The §6.13 matrix / routing / quiet hours all
apply relay-side — this loop is a producer only (H1 / I-series completeness).
Runs as the cross-tenant worker role like the other sweeps (README §2.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from mesh.comment_inbox.notifications import emit_due_soon_notifications

logger = logging.getLogger("mesh.workers.due_soon_sweep")


async def due_soon_sweep_loop(
    session_factory,
    *,
    interval: float,
    horizon_hours: float,
    stop: asyncio.Event,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Run the due-soon sweep every ``interval`` seconds until ``stop``."""
    horizon = timedelta(hours=horizon_hours)
    logger.info(
        "due-soon sweep loop started (interval=%.1fs, horizon=%.1fh)",
        interval,
        horizon_hours,
    )
    while not stop.is_set():
        try:
            moment = clock() if clock is not None else datetime.now(UTC)
            async with session_factory() as session, session.begin():
                emitted = await emit_due_soon_notifications(
                    session, horizon=horizon, now=moment
                )
            if emitted:
                logger.info("due-soon sweep registered %d fan-out(s)", emitted)
        except Exception:
            logger.exception("due-soon sweep iteration failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


__all__ = ["due_soon_sweep_loop"]

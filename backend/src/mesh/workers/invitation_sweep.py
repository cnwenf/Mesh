"""Invitation expiry sweep loop (workspace.md §4.4 timed expiry).

The accept/preview paths evaluate expiry lazily; this loop is the timed
complement that flips past-due active links to ``expired`` so listings and
queries read the terminal state without touching each row. Runs as the
cross-tenant worker role (owner), decoupled from the other loops via the
supervisor (README §2.2).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from mesh.workspace.invitations import InvitationService

logger = logging.getLogger("mesh.workers.invitation_sweep")


async def invitation_sweep_loop(
    session_factory,
    *,
    interval: float,
    stop: asyncio.Event,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Sweep expired invitations every ``interval`` seconds until ``stop``."""
    service = InvitationService(session_factory)
    logger.info("invitation sweep loop started (interval=%.1fs)", interval)
    while not stop.is_set():
        try:
            swept = await service.sweep_expired()
            if swept:
                moment = clock() if clock is not None else datetime.now(UTC)
                logger.info("invitation sweep expired %d link(s) at %s", swept, moment)
        except Exception:
            logger.exception("invitation sweep iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

"""Device-authorization expiry sweep loop (auth.md §2.4.2 过期清理).

The poll/approve paths evaluate expiry lazily; this loop is the timed
complement that flips past-TTL ``pending``/``approved`` grants to the terminal
``expired`` state so the active user_code partial-unique index sheds old codes
and listings read the terminal state without touching each row. Runs as the
cross-tenant worker role via the supervisor (README §2.2).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from mesh.auth.device_codes import DeviceCodeService

logger = logging.getLogger("mesh.workers.device_auth_sweep")


async def device_auth_sweep_loop(
    session_factory,
    *,
    settings,
    interval: float,
    stop: asyncio.Event,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Sweep expired device grants every ``interval`` seconds until ``stop``."""
    service = DeviceCodeService(session_factory, settings)
    logger.info("device authorization sweep loop started (interval=%.1fs)", interval)
    while not stop.is_set():
        try:
            swept = await service.sweep_expired()
            if swept:
                moment = clock() if clock is not None else datetime.now(UTC)
                logger.info("device sweep expired %d grant(s) at %s", swept, moment)
        except Exception:
            logger.exception("device authorization sweep iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

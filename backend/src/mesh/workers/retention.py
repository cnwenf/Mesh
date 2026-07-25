"""Realtime event retention purge (README §6.7: default 7 days, configurable).

Deletes expired ``realtime_events`` using the ``(workspace_id, created_at)``
index. Replay older than the window is answered with ``resync_required``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from mesh.db.models.realtime import RealtimeEvent

logger = logging.getLogger("mesh.workers.retention")


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def purge_expired_events(session_factory, *, retention: timedelta, now: datetime) -> int:
    """Delete realtime events older than ``retention``; returns the row count."""
    cutoff = now - retention
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                delete(RealtimeEvent).where(RealtimeEvent.created_at < cutoff)
            )
            deleted = result.rowcount or 0
    if deleted:
        logger.info("purged %d expired realtime events (cutoff=%s)", deleted, cutoff.isoformat())
    return deleted


async def retention_loop(
    session_factory,
    *,
    retention: timedelta,
    interval: float,
    stop: asyncio.Event,
    clock=_utcnow,
) -> None:
    """Periodically purge expired events until ``stop`` is set."""
    while not stop.is_set():
        await purge_expired_events(session_factory, retention=retention, now=clock())
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

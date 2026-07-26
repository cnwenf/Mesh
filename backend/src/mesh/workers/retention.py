"""Retention purges (README §6.7 realtime window, §6.6 outbox cleanup).

Two independent loops run under the worker supervisor:

* ``retention_loop`` deletes expired ``realtime_events`` using the
  ``(workspace_id, created_at)`` index. Replay older than the window is
  answered with ``resync_required``.
* ``outbox_retention_loop`` deletes terminal (``published``/``failed``)
  ``outbox_events`` older than the outbox retention window so the table —
  including the ``idempotency_key`` unique index — does not grow without
  bound. ``pending`` rows are never touched (purging them would silently
  drop queued work). A ``failed`` row is only deleted once it is older than
  the whole retention window, which is far beyond the relay's retry budget,
  so the §6.6 permanent-failure alert has necessarily fired before cleanup.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.db.models.realtime import RealtimeEvent

logger = logging.getLogger("mesh.workers.retention")

# Upper bound on rows removed per outbox purge pass — keeps each purge
# transaction short regardless of backlog size; the loop reclaims any
# remainder on subsequent ticks.
OUTBOX_PURGE_BATCH_LIMIT = 10_000


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
    """Periodically purge expired realtime events until ``stop`` is set."""
    while not stop.is_set():
        await purge_expired_events(session_factory, retention=retention, now=clock())
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def purge_processed_outbox_events(
    session_factory,
    *,
    retention: timedelta,
    now: datetime,
    batch_limit: int = OUTBOX_PURGE_BATCH_LIMIT,
) -> int:
    """Delete terminal outbox rows older than ``retention``; returns the count.

    Only ``published``/``failed`` rows are eligible — a ``pending`` row older
    than the window is a stuck backlog the relay must still process, never
    silently delete. Deletion is capped at ``batch_limit`` rows per call so a
    large backlog is reclaimed incrementally without long transactions.
    """
    cutoff = now - retention
    expired_ids = (
        select(OutboxEvent.id)
        .where(
            OutboxEvent.status.in_((OUTBOX_STATUS_PUBLISHED, OUTBOX_STATUS_FAILED)),
            OutboxEvent.created_at < cutoff,
        )
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_limit)
    )
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                delete(OutboxEvent).where(OutboxEvent.id.in_(expired_ids))
            )
            deleted = result.rowcount or 0
    if deleted:
        logger.info(
            "purged %d processed outbox events (cutoff=%s)", deleted, cutoff.isoformat()
        )
    return deleted


async def outbox_retention_loop(
    session_factory,
    *,
    retention: timedelta,
    interval: float,
    stop: asyncio.Event,
    clock=_utcnow,
) -> None:
    """Periodically purge terminal outbox rows until ``stop`` is set."""
    while not stop.is_set():
        await purge_processed_outbox_events(session_factory, retention=retention, now=clock())
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

"""Outbox relay worker (README §2.2 / §6.6).

Polls ``outbox_events(status='pending')`` with ``FOR UPDATE SKIP LOCKED``
(multiple replicas never process the same row), dispatches each event to the
handler registered for its ``event_type`` in the same transaction, then marks
it ``published``. Failures increment ``delivery_attempts``; exceeding
``max_attempts`` marks the row ``failed`` (alerting concern). Handlers may
return frames to publish on the Redis fan-out AFTER the transaction commits.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.realtime.pubsub import RedisFanOut

logger = logging.getLogger("mesh.outbox.relay")

# handler(session, event) → optional [(channel, frame)] to fan out after commit
Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[list[tuple[str, dict]] | None]]


@dataclass(frozen=True)
class RelayResult:
    """Outcome of one relay pass over a claimed batch."""

    claimed: int = 0
    published: int = 0
    failed: int = 0


class OutboxRelay:
    """Claims pending outbox rows and dispatches them to registered handlers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        handlers: Mapping[str, Handler],
        batch_size: int = 50,
        max_attempts: int = 5,
        poll_interval: float = 1.0,
        fanout: RedisFanOut | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._poll_interval = poll_interval
        self._fanout = fanout

    def register(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type] = handler

    async def claim_batch(self, session: AsyncSession) -> list[OutboxEvent]:
        """Claim up to ``batch_size`` pending rows, oldest first (SKIP LOCKED)."""
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.status == OUTBOX_STATUS_PENDING)
            .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
            .limit(self._batch_size)
            .with_for_update(skip_locked=True)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def _mark_delivery_failure(self, session: AsyncSession, event: OutboxEvent) -> None:
        """Increment delivery_attempts (persisting in the outer transaction); fail at max."""
        event.delivery_attempts += 1
        if event.delivery_attempts >= self._max_attempts:
            event.status = OUTBOX_STATUS_FAILED
            logger.error(
                "outbox event %s failed permanently after %d attempts (type=%s)",
                event.id,
                event.delivery_attempts,
                event.event_type,
            )
        else:
            logger.warning(
                "outbox event %s delivery failed (attempt %d, type=%s)",
                event.id,
                event.delivery_attempts,
                event.event_type,
            )
        await session.flush()

    async def dispatch_one(self, session: AsyncSession, event: OutboxEvent) -> list[tuple[str, dict]]:
        """Dispatch one claimed event; returns frames to fan out after commit.

        The handler runs inside a SAVEPOINT: a handler error (including a
        database error that aborts the savepoint) never poisons the batch
        transaction, and the delivery_attempts increment still persists — a
        poison event reaches ``failed`` instead of blocking newer events.
        """
        handler = self._handlers.get(event.event_type)
        if handler is None:
            await self._mark_delivery_failure(session, event)
            return []
        try:
            async with session.begin_nested():
                frames = await handler(session, event) or []
                event.status = OUTBOX_STATUS_PUBLISHED
                event.published_at = datetime.now(UTC)
        except Exception:
            # Savepoint rolled back; outer transaction still usable.
            await self._mark_delivery_failure(session, event)
            return []
        return list(frames)

    async def run_once(self) -> RelayResult:
        """Claim and dispatch one batch in a single transaction."""
        frames: list[tuple[str, dict]] = []
        published = 0
        failed = 0
        async with self._session_factory() as session:
            async with session.begin():
                events = await self.claim_batch(session)
                for event in events:
                    frames.extend(await self.dispatch_one(session, event))
                    if event.status == OUTBOX_STATUS_PUBLISHED:
                        published += 1
                    elif event.status == OUTBOX_STATUS_FAILED:
                        failed += 1
        if frames and self._fanout is not None:
            await self._fanout.publish_frames(frames)
        return RelayResult(claimed=len(events), published=published, failed=failed)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Loop until ``stop`` is set (or forever when None)."""
        while stop is None or not stop.is_set():
            result = await self.run_once()
            if result.claimed == 0:
                if stop is not None:
                    with _suppress_cancel():
                        await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                else:
                    await asyncio.sleep(self._poll_interval)


class _suppress_cancel:
    """Tiny context helper: swallow CancelledError/TimeoutError on stop wait."""

    def __enter__(self) -> _suppress_cancel:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type in (asyncio.TimeoutError, asyncio.CancelledError, TimeoutError)

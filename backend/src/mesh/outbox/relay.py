"""Outbox relay worker (README §2.2 / §6.6).

Polls ``outbox_events`` with the authority claim predicate
``status='pending' AND available_at <= now()`` (``FOR UPDATE SKIP LOCKED``;
multiple replicas never process the same row), dispatches each event to the
handler registered for its ``event_type`` in the same transaction, then marks
it ``published``. Failures increment ``delivery_attempts``; exceeding
``max_attempts`` marks the row ``failed`` (alerting concern). Handlers may
return frames to publish on the Redis fan-out AFTER the transaction commits.

Retryable NON-failure outcomes (e.g. integrations.md ``token_refresh_busy``,
MES-82 R4-4) raise :class:`RetryableDelay` instead of failing: the relay
moves ``available_at`` forward by a short backoff WITHOUT incrementing
``delivery_attempts`` — the failure budget is never consumed and the row
never reaches a terminal state; the ``available_at`` index filter prevents
hot claim loops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
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


async def isolated_optional_step(
    session: AsyncSession, label: str, event_id: object, step: Awaitable[None]
) -> None:
    """Run a handler's OPTIONAL consumer step in its own savepoint.

    MES-147 deadlock-recovery contract: a failure inside the step rolls back
    to the step's savepoint and never poisons the surrounding batch
    transaction — the failure mode that previously wedged the relay (a
    deadlock swallowed by a bare ``except`` left the transaction aborted;
    every later statement in the batch then failed with "current transaction
    is aborted" and the event could never be marked published).

    - Database errors (deadlock victim, serialization failure, connection
      error) are re-raised AFTER the savepoint rollback: the event falls into
      the relay failure budget and is redelivered whole, so an optional step's
      DB failure never silently drops its side effects, and the batch
      transaction remains usable for the failure bookkeeping.
    - Non-database errors (logic bugs in the optional consumer) are logged and
      swallowed: an optional step must not turn a poison event into a
      permanently failing batch (the primary projection outcome still lands).
    """
    try:
        async with session.begin_nested():
            await step
    except DBAPIError:
        logger.warning(
            "%s hit a database error for event %s; savepoint rolled back, event will retry",
            label,
            event_id,
            exc_info=True,
        )
        raise
    except Exception:  # noqa: BLE001 — optional step: log + skip, keep batch alive
        logger.exception("%s failed for event %s (rolled back to savepoint)", label, event_id)


class RetryableDelay(Exception):
    """Handler outcome: retry later WITHOUT consuming the failure budget.

    README §6.6 (MES-82 R4-4): retryable non-failure results (e.g. an IM
    token refresh already in flight — ``token_refresh_busy``) only move
    ``available_at`` forward by ``delay`` (short backoff); they never
    increment ``delivery_attempts`` and never reach a terminal state.
    """

    def __init__(self, delay: float, reason: str = "") -> None:
        super().__init__(reason or "retryable delay")
        self.delay = max(0.1, float(delay))
        self.reason = reason


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
        error_backoff: float = 1.0,
        fanout: RedisFanOut | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._poll_interval = poll_interval
        self._error_backoff = error_backoff
        self._fanout = fanout

    def register(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type] = handler

    async def claim_batch(self, session: AsyncSession) -> list[OutboxEvent]:
        """Claim up to ``batch_size`` claimable rows, oldest first (SKIP LOCKED).

        README §6.6 authority claim predicate: ``status='pending' AND
        available_at <= now()`` — deferred rows (failure backoff or retryable
        non-failure results) stay invisible until their earliest-claim
        instant, and ``idx_outbox_pending (available_at, created_at)``
        provides the scan order.

        Only event types with a REGISTERED handler are claimable: dedicated
        fast relays own their event types exclusively (integrations.md §3.8
        ``im.send`` ack fast relay) — this relay must neither claim nor
        fail-budget events another consumer delivers.
        """
        if not self._handlers:
            return []
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.status == OUTBOX_STATUS_PENDING)
            .where(OutboxEvent.available_at <= datetime.now(UTC))
            .where(OutboxEvent.event_type.in_(tuple(self._handlers)))
            .order_by(
                OutboxEvent.available_at.asc(),
                OutboxEvent.created_at.asc(),
                OutboxEvent.id.asc(),
            )
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
        except RetryableDelay as delay:
            # Savepoint rolled back; outer transaction still usable.
            await self._defer_retryable(session, event, delay)
            return []
        except Exception:
            # Savepoint rolled back; outer transaction still usable.
            await self._mark_delivery_failure(session, event)
            return []
        return list(frames)

    async def _defer_retryable(
        self, session: AsyncSession, event: OutboxEvent, delay: RetryableDelay
    ) -> None:
        """Move ``available_at`` forward WITHOUT consuming the failure budget.

        README §6.6 (MES-82 R4-4): retryable non-failure results stay
        ``pending`` with ``delivery_attempts`` untouched; the ``available_at``
        index filter both schedules the retry and prevents a hot claim loop.
        """
        event.available_at = datetime.now(UTC) + timedelta(seconds=delay.delay)
        logger.info(
            "outbox event %s deferred %.1fs without failure budget (type=%s, reason=%s)",
            event.id,
            delay.delay,
            event.event_type,
            delay.reason,
        )
        await session.flush()

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
        """Loop until ``stop`` is set (or forever when None).

        A failed pass (transient DB error — deadlock victim, serialization
        failure, connection reset) does NOT terminate the loop: its
        transaction was already rolled back by ``run_once``'s session scope,
        so the relay sleeps a short backoff and retries. Dying here would make
        the outbox publisher depend entirely on the external supervisor for
        recovery (MES-147: a wedged publisher leaves e2e waits pending until
        job timeout).
        """
        while stop is None or not stop.is_set():
            try:
                result = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — transient infra failure: back off + retry
                logger.exception(
                    "outbox relay pass failed; retrying in %.1fs", self._error_backoff
                )
                await self._idle_sleep(stop, self._error_backoff)
                continue
            if result.claimed == 0:
                await self._idle_sleep(stop, self._poll_interval)

    async def _idle_sleep(self, stop: asyncio.Event | None, seconds: float) -> None:
        """Sleep ``seconds``, returning early when ``stop`` is set."""
        if stop is not None:
            with _suppress_cancel():
                await asyncio.wait_for(stop.wait(), timeout=seconds)
        else:
            await asyncio.sleep(seconds)


class _suppress_cancel:
    """Tiny context helper: swallow CancelledError/TimeoutError on stop wait."""

    def __enter__(self) -> _suppress_cancel:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type in (asyncio.TimeoutError, asyncio.CancelledError, TimeoutError)

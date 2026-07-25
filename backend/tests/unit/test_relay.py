"""Outbox relay: SKIP LOCKED claim, dispatch, retry/failed policy (§6.6 / §2.2)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.outbox.relay import OutboxRelay, RelayResult
from mesh.outbox.service import emit_event


async def _seed(session_factory, workspace_id, event_type="test.event", count=1):
    events = []
    async with session_factory() as session, session.begin():
        for i in range(count):
            events.append(
                await emit_event(
                    session,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    payload={"i": i},
                )
            )
    return events


async def test_run_once_publishes_with_handler(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=3)
    seen: list[str] = []

    async def handler(session, event):
        seen.append(str(event.id))
        return None

    relay = OutboxRelay(session_factory, handlers={"test.event": handler})
    result = await relay.run_once()
    assert result == RelayResult(claimed=3, published=3, failed=0)
    assert len(seen) == 3
    async with session_factory() as session:
        statuses = (
            (await session.execute(select(OutboxEvent.status))).scalars().all()
        )
        assert statuses == [OUTBOX_STATUS_PUBLISHED] * 3
        published_ats = (
            (await session.execute(select(OutboxEvent.published_at))).scalars().all()
        )
        assert all(value is not None for value in published_ats)


async def test_concurrent_relays_do_not_double_process(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=10)
    processed: list[str] = []
    lock = asyncio.Lock()

    async def handler(session, event):
        async with lock:
            processed.append(str(event.id))
        await asyncio.sleep(0.01)  # widen the concurrency window
        return None

    relays = [
        OutboxRelay(session_factory, handlers={"test.event": handler}, batch_size=10)
        for _ in range(3)
    ]
    results = await asyncio.gather(*(relay.run_once() for relay in relays))
    assert sum(r.claimed for r in results) == 10
    assert len(processed) == 10
    assert len(set(processed)) == 10  # every event exactly once


async def test_handler_failure_increments_attempts_then_fails(session_factory, workspace_factory):
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)

    async def boom(session, event_):
        raise RuntimeError("downstream exploded")

    relay = OutboxRelay(session_factory, handlers={"test.event": boom}, max_attempts=3)
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 1

    await relay.run_once()
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_FAILED
        assert row.delivery_attempts == 3

    # Failed rows are not re-claimed.
    result = await relay.run_once()
    assert result.claimed == 0


async def test_unknown_event_type_counts_as_failure(session_factory, workspace_factory):
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, event_type="nobody.handles", count=1)
    # Pre-set attempts so one failed pass reaches max_attempts.
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxEvent, event.id)
        row.delivery_attempts = 4
    relay = OutboxRelay(session_factory, handlers={}, max_attempts=5)
    result = await relay.run_once()
    assert result.failed == 1
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_FAILED


async def test_run_once_empty_backlog(session_factory):
    relay = OutboxRelay(session_factory, handlers={})
    assert await relay.run_once() == RelayResult()


async def test_frames_are_fanned_out_after_commit(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=1)
    published_frames: list[tuple[str, dict]] = []

    class FakeFanOut:
        async def publish_frames(self, frames):
            published_frames.extend(frames)

    async def handler(session, event):
        return [("issue:x", {"op": "event", "seq": 1})]

    relay = OutboxRelay(
        session_factory, handlers={"test.event": handler}, fanout=FakeFanOut()
    )
    await relay.run_once()
    assert published_frames == [("issue:x", {"op": "event", "seq": 1})]


async def test_db_error_poison_event_does_not_block_batch(session_factory, workspace_factory):
    """A handler DB error fails only that event (savepoint); the batch proceeds."""
    from sqlalchemy import text

    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=3)
    # Poison exactly one row.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE outbox_events SET payload = '{\"poison\": true}' "
                "WHERE id = (SELECT id FROM outbox_events LIMIT 1)"
            )
        )

    async def handler(session, event):
        if event.payload.get("poison"):
            # Database-level failure: aborts the savepoint, not the batch.
            await session.execute(text("SELECT 1/0"))
        return None

    relay = OutboxRelay(session_factory, handlers={"test.event": handler}, max_attempts=2)
    result = await relay.run_once()
    assert result.claimed == 3
    assert result.published == 2  # healthy events delivered in the same batch

    async with session_factory() as session:
        poison = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.status == OUTBOX_STATUS_PENDING)
            )
        ).scalar_one()
        assert poison.delivery_attempts == 1  # increment persisted despite the error

    # Next pass claims only the poison row and fails it at max_attempts.
    result2 = await relay.run_once()
    assert result2.claimed == 1
    assert result2.failed == 1


async def test_run_forever_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=1)
    processed = []

    async def handler(session, event):
        processed.append(event.id)
        return None

    relay = OutboxRelay(
        session_factory, handlers={"test.event": handler}, poll_interval=0.05
    )
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run_forever(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(processed) == 1

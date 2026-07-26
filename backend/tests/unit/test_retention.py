"""Retention purges (§6.7 realtime window, §6.6 outbox cleanup)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.workers.retention import (
    outbox_retention_loop,
    purge_expired_events,
    purge_processed_outbox_events,
    retention_loop,
)


async def _seed_event(session_factory, workspace_id, channel, seq, age_days):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO realtime_channels (channel, workspace_id, last_seq) "
                "VALUES (:ch, :ws, :seq) ON CONFLICT (channel) DO NOTHING"
            ),
            {"ch": channel, "ws": workspace_id, "seq": seq},
        )
        row = await session.execute(
            text(
                "INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id) "
                "VALUES (:ws, :ch, :seq, 'issue.updated', '{}', gen_random_uuid()) RETURNING id"
            ),
            {"ws": workspace_id, "ch": channel, "seq": seq},
        )
        event_id = row.scalar_one()
        await session.execute(
            text(
                "UPDATE realtime_events "
                "SET created_at = now() - (:days || ' days')::interval WHERE id = :id"
            ),
            {"days": str(age_days), "id": event_id},
        )


async def test_purge_deletes_only_expired_rows(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_event(session_factory, workspace.id, "issue:ret", 1, age_days=10)
    await _seed_event(session_factory, workspace.id, "issue:ret", 2, age_days=1)

    deleted = await purge_expired_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC)
    )
    assert deleted == 1
    async with session_factory() as session:
        remaining = (
            (await session.execute(select(RealtimeEvent.seq))).scalars().all()
        )
        channels = (await session.execute(select(RealtimeChannel.channel))).scalars().all()
    assert remaining == [2]
    assert channels == ["issue:ret"]  # channel row survives event purge


async def test_retention_loop_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_event(session_factory, workspace.id, "issue:loop", 1, age_days=30)
    stop = asyncio.Event()
    task = asyncio.create_task(
        retention_loop(
            session_factory,
            retention=timedelta(days=7),
            interval=0.05,
            stop=stop,
            clock=lambda: datetime.now(UTC),
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with session_factory() as session:
        assert (await session.execute(select(RealtimeEvent))).all() == []


# --- M3: outbox retention purge (§6.6) ---


async def _seed_outbox_event(session_factory, workspace_id, *, status, age_days, key=None):
    async with session_factory() as session, session.begin():
        row = await session.execute(
            text(
                "INSERT INTO outbox_events "
                "(workspace_id, event_type, payload, status, idempotency_key) "
                "VALUES (:ws, 'realtime.publish', '{}', :status, :key) RETURNING id"
            ),
            {"ws": workspace_id, "status": status, "key": key},
        )
        event_id = row.scalar_one()
        await session.execute(
            text(
                "UPDATE outbox_events "
                "SET created_at = now() - (:days || ' days')::interval WHERE id = :id"
            ),
            {"days": str(age_days), "id": event_id},
        )
    return event_id


async def _outbox_ids(session_factory):
    async with session_factory() as session:
        return set((await session.execute(select(OutboxEvent.id))).scalars().all())


async def test_outbox_purge_deletes_only_terminal_expired_rows(session_factory, workspace_factory):
    workspace = await workspace_factory()
    old_published = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=10
    )
    old_failed = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_FAILED, age_days=10
    )
    # A stuck pending row older than the window must survive: purging it would
    # silently drop queued work.
    old_pending = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PENDING, age_days=10
    )
    fresh_published = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=1
    )

    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC)
    )
    assert deleted == 2
    remaining = await _outbox_ids(session_factory)
    assert remaining == {old_pending, fresh_published}


async def test_outbox_purge_respects_batch_limit(session_factory, workspace_factory):
    workspace = await workspace_factory()
    for i in range(5):
        await _seed_outbox_event(
            session_factory,
            workspace.id,
            status=OUTBOX_STATUS_PUBLISHED,
            age_days=10,
            key=f"batch-{i}",
        )
    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC), batch_limit=2
    )
    assert deleted == 2
    async with session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(OutboxEvent))
    assert remaining == 3


async def test_outbox_purge_returns_zero_and_logs_nothing_when_empty(
    session_factory, workspace_factory
):
    workspace = await workspace_factory()
    await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=1
    )
    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC)
    )
    assert deleted == 0


async def test_outbox_retention_loop_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=30
    )
    await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_FAILED, age_days=30
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        outbox_retention_loop(
            session_factory,
            retention=timedelta(days=7),
            interval=0.05,
            stop=stop,
            clock=lambda: datetime.now(UTC),
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with session_factory() as session:
        assert (await session.execute(select(OutboxEvent))).all() == []

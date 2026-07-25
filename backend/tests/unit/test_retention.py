"""Realtime retention purge (§6.7: default 7 days)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.workers.retention import purge_expired_events, retention_loop


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

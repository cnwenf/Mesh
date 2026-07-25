"""Realtime projector e2e (§9 T26 shape): unique write path, no gaps, Redis fan-out."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.realtime import RealtimeEvent
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.outbox.service import emit_realtime
from mesh.realtime.pubsub import RedisSubscriber

pytestmark = pytest.mark.e2e


async def test_projector_crash_restart_keeps_seq_gapless_and_deduped(
    session_factory, workspace_factory, redis_client
):
    """Kill the projector before it runs; restart; exactly-once, no seq gap."""
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        for i in range(3):
            await emit_realtime(
                session,
                workspace_id=workspace.id,
                channel="issue:t26",
                event="issue.updated",
                data={"i": i},
            )

    # Fan-out listener is live before the relay starts.
    subscriber = RedisSubscriber(redis_client)
    await subscriber.start()
    await asyncio.sleep(0.1)

    relay = OutboxRelay(
        session_factory, handlers={REALTIME_PUBLISH: project_realtime_event}
    )
    from mesh.realtime.pubsub import RedisFanOut

    relay._fanout = RedisFanOut(redis_client)  # same wiring as production build_relay

    result = await relay.run_once()
    assert result.published == 3

    async with session_factory() as session:
        seqs = (
            (
                await session.execute(
                    select(RealtimeEvent.seq)
                    .where(RealtimeEvent.channel == "issue:t26")
                    .order_by(RealtimeEvent.seq)
                )
            ).scalars().all()
        )
    assert seqs == [1, 2, 3]  # no gaps, no duplicates

    # Redis received all three fan-out frames.
    frames = []

    async def collect():
        async for _channel, frame in subscriber.frames():
            frames.append(frame)
            if len(frames) == 3:
                return

    await asyncio.wait_for(collect(), timeout=5)
    assert [frame["seq"] for frame in frames] == [1, 2, 3]
    await subscriber.close()

    # Redelivery (simulate relay crash after projection, before publish):
    # reset the outbox rows to pending and re-run — no duplicate records.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE outbox_events SET status = 'pending', published_at = NULL")
        )
    again = await relay.run_once()
    assert again.published == 3  # idempotent re-projection still marks published
    async with session_factory() as session:
        count = len(
            (
                await session.execute(
                    select(RealtimeEvent).where(RealtimeEvent.channel == "issue:t26")
                )
            ).all()
        )
        last_seq = (
            await session.execute(
                text("SELECT last_seq FROM realtime_channels WHERE channel = 'issue:t26'")
            )
        ).scalar_one()
    assert count == 3  # UNIQUE(outbox_event_id) → exactly-once record
    assert last_seq == 3  # no wasted seq


async def test_cross_workspace_channel_cannot_be_hijacked(session_factory, workspace_factory):
    workspace_a = await workspace_factory(name="A", slug="t26-a")
    workspace_b = await workspace_factory(name="B", slug="t26-b")

    # Tenant A legitimately owns the channel.
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session,
            workspace_id=workspace_a.id,
            channel="issue:shared-name",
            event="issue.updated",
            data={"owner": "a"},
        )
    # Tenant B writes a realtime.publish targeting the SAME channel name.
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session,
            workspace_id=workspace_b.id,
            channel="issue:shared-name",
            event="issue.updated",
            data={"owner": "b"},
        )

    relay = OutboxRelay(
        session_factory, handlers={REALTIME_PUBLISH: project_realtime_event}, max_attempts=1
    )
    result = await relay.run_once()
    assert result.published == 1 and result.failed == 1

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(RealtimeEvent.workspace_id, RealtimeEvent.payload).where(
                        RealtimeEvent.channel == "issue:shared-name"
                    )
                )
            ).all()
        )
        failed = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.status == "failed")
                )
            ).scalars().all()
        )
    # Only tenant A's event was recorded; B's was rejected by the tenant guard.
    assert len(rows) == 1
    assert rows[0].workspace_id == workspace_a.id
    assert failed[0].workspace_id == workspace_b.id

"""Realtime projector — the only writer of realtime_events (§6.6/§6.7)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.events.vocab import UnregisteredEventError
from mesh.outbox.projector import ProjectionError, project_realtime_event


async def _outbox_row(session_factory, workspace_id, payload):
    async with session_factory() as session, session.begin():
        event = OutboxEvent(
            workspace_id=workspace_id, event_type="realtime.publish", payload=payload
        )
        session.add(event)
    return event


async def test_projection_assigns_monotonic_per_channel_seq(session_factory, workspace_factory):
    workspace = await workspace_factory()
    for i in range(3):
        event = await _outbox_row(
            session_factory,
            workspace.id,
            {"channel": "issue:p1", "event": "issue.updated", "data": {"i": i}},
        )
        async with session_factory() as session, session.begin():
            frames = await project_realtime_event(session, event)
        expected_frame = {
            "op": "event",
            "channel": "issue:p1",
            "seq": i + 1,
            "event": "issue.updated",
            "payload": {"i": i},
        }
        assert frames == [("issue:p1", expected_frame)]

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(RealtimeEvent.seq)
                    .where(RealtimeEvent.channel == "issue:p1")
                    .order_by(RealtimeEvent.seq)
                )
            ).scalars().all()
        )
        assert rows == [1, 2, 3]
        last_seq = await session.scalar(
            select(RealtimeChannel.last_seq).where(RealtimeChannel.channel == "issue:p1")
        )
        assert last_seq == 3


async def test_channel_seqs_are_independent_per_channel(session_factory, workspace_factory):
    workspace = await workspace_factory()
    for channel in ("issue:a", "issue:b"):
        event = await _outbox_row(
            session_factory, workspace.id, {"channel": channel, "event": "issue.created", "data": {}}
        )
        async with session_factory() as session, session.begin():
            await project_realtime_event(session, event)
    async with session_factory() as session:
        seq_a = await session.scalar(
            select(RealtimeEvent.seq).where(RealtimeEvent.channel == "issue:a")
        )
        seq_b = await session.scalar(
            select(RealtimeEvent.seq).where(RealtimeEvent.channel == "issue:b")
        )
    assert seq_a == 1 and seq_b == 1


async def test_duplicate_delivery_is_idempotent(session_factory, workspace_factory):
    workspace = await workspace_factory()
    event = await _outbox_row(
        session_factory, workspace.id, {"channel": "issue:d", "event": "issue.updated", "data": {}}
    )
    async with session_factory() as session, session.begin():
        first = await project_realtime_event(session, event)
    # Simulate at-least-once redelivery of the same outbox event.
    async with session_factory() as session, session.begin():
        second = await project_realtime_event(session, event)
    assert first == second
    async with session_factory() as session:
        count = len(
            (
                await session.execute(
                    select(RealtimeEvent).where(RealtimeEvent.channel == "issue:d")
                )
            ).all()
        )
        last_seq = await session.scalar(
            select(RealtimeChannel.last_seq).where(RealtimeChannel.channel == "issue:d")
        )
    assert count == 1  # no duplicate record
    assert last_seq == 1  # no seq gap


async def test_malformed_payload_raises_projection_error(session_factory, workspace_factory):
    workspace = await workspace_factory()
    for payload in ({}, {"channel": "issue:x"}, {"channel": "issue:x", "event": "issue.updated"}):
        event = await _outbox_row(session_factory, workspace.id, payload)
        with pytest.raises(ProjectionError):
            async with session_factory() as session, session.begin():
                await project_realtime_event(session, event)


async def test_unregistered_event_name_rejected(session_factory, workspace_factory):
    workspace = await workspace_factory()
    event = await _outbox_row(
        session_factory,
        workspace.id,
        {"channel": "issue:x", "event": "agent.run_started", "data": {}},
    )
    with pytest.raises(UnregisteredEventError):
        async with session_factory() as session, session.begin():
            await project_realtime_event(session, event)
    async with session_factory() as session:
        assert (await session.execute(select(RealtimeEvent))).all() == []


async def test_cross_tenant_channel_rejected(session_factory, workspace_factory):
    ws_a = await workspace_factory(name="A", slug="proj-a")
    ws_b = await workspace_factory(name="B", slug="proj-b")
    # Channel registered to tenant A.
    async with session_factory() as session, session.begin():
        session.add(RealtimeChannel(channel="issue:shared", workspace_id=ws_a.id))
    # Tenant B tries to project onto it.
    event = await _outbox_row(
        session_factory, ws_b.id, {"channel": "issue:shared", "event": "issue.updated", "data": {}}
    )
    with pytest.raises(ProjectionError, match="does not belong"):
        async with session_factory() as session, session.begin():
            await project_realtime_event(session, event)
    async with session_factory() as session:
        # A's watermark untouched, no event recorded.
        last_seq = await session.scalar(
            select(RealtimeChannel.last_seq).where(RealtimeChannel.channel == "issue:shared")
        )
        assert last_seq == 0
        assert (await session.execute(select(RealtimeEvent))).all() == []


async def test_channel_auto_registration_uses_outbox_tenant(session_factory, workspace_factory):
    workspace = await workspace_factory()
    event = await _outbox_row(
        session_factory, workspace.id, {"channel": "issue:new", "event": "issue.created", "data": {}}
    )
    async with session_factory() as session, session.begin():
        await project_realtime_event(session, event)
    async with session_factory() as session:
        channel = await session.scalar(
            select(RealtimeChannel).where(RealtimeChannel.channel == "issue:new")
        )
    assert channel is not None
    assert channel.workspace_id == workspace.id

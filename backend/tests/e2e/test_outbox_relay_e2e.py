"""Outbox crash-recovery e2e (§9 T5 shape): real DB, real relay, real commit."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OUTBOX_STATUS_PUBLISHED, OutboxEvent
from mesh.db.models.realtime import RealtimeEvent
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.outbox.service import emit_event, emit_realtime
from mesh.workers.supervisor import Supervisor, TaskSpec

pytestmark = pytest.mark.e2e


async def test_events_survive_relay_death_and_publish_on_restart(
    session_factory, workspace_factory, redis_client
):
    """Business commits → relay dies (never runs) → relay restarts → no loss."""
    workspace = await workspace_factory()

    # Business transaction commits two realtime events; relay is NOT running.
    async with session_factory() as session, session.begin():
        for i in range(2):
            await emit_realtime(
                session,
                workspace_id=workspace.id,
                channel="issue:t5",
                event="issue.updated",
                data={"i": i},
            )

    # Nothing projected yet — the relay never ran.
    async with session_factory() as session:
        assert (await session.execute(select(RealtimeEvent))).all() == []
        pending = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == REALTIME_PUBLISH)
                )
            ).scalars().all()
        )
        assert len(pending) == 2

    # Relay (re)starts and drains the backlog.
    relay = OutboxRelay(
        session_factory,
        handlers={REALTIME_PUBLISH: project_realtime_event},
        poll_interval=0.05,
    )
    result = await relay.run_once()
    assert result.claimed == 2 and result.published == 2

    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(RealtimeEvent)
                    .where(RealtimeEvent.channel == "issue:t5")
                    .order_by(RealtimeEvent.seq)
                )
            ).scalars().all()
        )
        # Both events projected with gapless seqs; rows emitted in the same
        # transaction share created_at, so payload↔seq pairing is claim-ordered
        # but the full set must arrive intact.
        assert [event.seq for event in events] == [1, 2]
        assert sorted(event.payload["i"] for event in events) == [0, 1]
        statuses = (
            (await session.execute(select(OutboxEvent.status))).scalars().all()
        )
        assert statuses == [OUTBOX_STATUS_PUBLISHED, OUTBOX_STATUS_PUBLISHED]


async def test_relay_dispatches_to_module_handlers(session_factory, workspace_factory):
    """A registered domain handler receives events with the persisted payload."""
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await emit_event(
            session,
            workspace_id=workspace.id,
            event_type="issue.assigned",
            payload={"agent": "a1"},
        )

    received = []

    async def enqueue_execution(session, event):
        received.append((event.event_type, dict(event.payload)))
        return None

    relay = OutboxRelay(session_factory, handlers={"issue.assigned": enqueue_execution})
    result = await relay.run_once()
    assert result.published == 1
    assert received == [("issue.assigned", {"agent": "a1"})]


async def test_supervisor_revives_dead_relay_and_drains_committed_backlog(
    session_factory, workspace_factory
):
    """A dead resident relay is revived within two ticks and drains real rows."""
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        outbox = await emit_realtime(
            session,
            workspace_id=workspace.id,
            channel="issue:relay-watchdog",
            event="issue.updated",
            data={"probe": "relay-watchdog"},
        )

    relay = OutboxRelay(
        session_factory,
        handlers={REALTIME_PUBLISH: project_realtime_event},
        poll_interval=0.01,
    )
    shutdown = asyncio.Event()
    second_start = asyncio.Event()
    attempts = 0
    first_death_at = 0.0
    second_start_at = 0.0

    async def injected_relay_task() -> None:
        nonlocal attempts, first_death_at, second_start_at
        attempts += 1
        if attempts == 1:
            first_death_at = asyncio.get_running_loop().time()
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        second_start_at = asyncio.get_running_loop().time()
        second_start.set()
        await relay.run_forever(shutdown)

    tick = 0.05
    supervisor = Supervisor(
        [TaskSpec("outbox-relay", injected_relay_task)],
        base_backoff=0,
        watchdog_interval=tick,
        shutdown_event=shutdown,
    )
    supervisor_task = asyncio.create_task(supervisor.run())
    try:
        await asyncio.wait_for(second_start.wait(), timeout=1)
        assert second_start_at - first_death_at <= tick * 2

        async with asyncio.timeout(2):
            while True:
                async with session_factory() as session:
                    persisted = await session.scalar(
                        select(OutboxEvent).where(OutboxEvent.id == outbox.id)
                    )
                    projection = await session.scalar(
                        select(RealtimeEvent).where(
                            RealtimeEvent.outbox_event_id == outbox.id
                        )
                    )
                if (
                    persisted is not None
                    and persisted.status == OUTBOX_STATUS_PUBLISHED
                    and projection is not None
                ):
                    break
                await asyncio.sleep(0.01)

        assert supervisor.restart_counts["outbox-relay"] == 1
        assert persisted.delivery_attempts == 0
        assert projection.payload == {"probe": "relay-watchdog"}
    finally:
        shutdown.set()
        await asyncio.wait_for(supervisor_task, timeout=1)

"""Agent capacity snapshots and realtime projection (agent.md §4.9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.runtime.agent_presence import (
    agent_presence_snapshot,
    agent_presence_snapshots,
    emit_agent_presence,
)
from tests.unit.runtime_support import seed_world

pytestmark = pytest.mark.unit


async def _seed_capacity(session_factory, world) -> None:
    async with session_factory() as session, session.begin():
        executions = [
            TaskExecution(
                workspace_id=world["ws_id"],
                agent_id=world["agent_id"],
                status=status,
            )
            for status in (
                "queued",
                "claimed",
                "running",
                "cancelling",
                "awaiting_approval",
                "completed",
                "failed",
            )
        ]
        session.add_all(executions)
        await session.flush()
        session.add(
            Approval(
                workspace_id=world["ws_id"],
                subject_type="tool_call",
                subject_execution_id=executions[4].id,
                requested_by_member_id=world["agent_member_id"],
                action_summary={},
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )


async def test_presence_snapshot_uses_absolute_execution_and_approval_counts(
    session_factory,
):
    world = await seed_world(session_factory)
    await _seed_capacity(session_factory, world)

    async with session_factory() as session:
        single = await agent_presence_snapshot(
            session,
            workspace_id=world["ws_id"],
            agent_id=world["agent_id"],
        )
        missing_id = uuid.uuid4()
        batch = await agent_presence_snapshots(
            session,
            workspace_id=world["ws_id"],
            agent_ids=[world["agent_id"], missing_id],
        )

    expected = {"running": 3, "queued": 1, "awaiting_approval": 1}
    assert single == expected
    assert batch[world["agent_id"]] == expected
    assert batch[missing_id] == {
        "running": 0,
        "queued": 0,
        "awaiting_approval": 0,
    }


async def test_emit_agent_presence_publishes_absolute_snapshot(session_factory):
    world = await seed_world(session_factory)
    await _seed_capacity(session_factory, world)

    async with session_factory() as session, session.begin():
        snapshot = await emit_agent_presence(
            session,
            workspace_id=world["ws_id"],
            agent_id=world["agent_id"],
        )

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == world["ws_id"],
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    event = next(row for row in rows if row.payload["event"] == "agent.presence")
    assert snapshot == {"running": 3, "queued": 1, "awaiting_approval": 1}
    assert event.payload["channel"] == f"agent:{world['agent_id']}:presence"
    assert event.payload["data"] == {
        "agent_id": str(world["agent_id"]),
        **snapshot,
    }

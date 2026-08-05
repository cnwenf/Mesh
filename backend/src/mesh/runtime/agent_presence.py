"""Absolute per-agent execution capacity snapshots.

The REST agent/member projections and every runtime transition publisher use
this module as their single definition of ``running``, ``queued`` and
``awaiting_approval``.  Realtime payloads are absolute snapshots, so clients
can replace local state after reconnects or out-of-order delivery instead of
trying to reconcile deltas.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TypedDict, cast

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.runtime import Approval, TaskExecution
from mesh.outbox.service import emit_realtime

RUNNING_EXECUTION_STATUSES = frozenset({"claimed", "running", "cancelling"})


class AgentPresenceSnapshot(TypedDict):
    """The stable REST/realtime representation of one agent's capacity."""

    running: int
    queued: int
    awaiting_approval: int


def empty_agent_presence() -> AgentPresenceSnapshot:
    """Return a fresh zero snapshot suitable for response serialization."""

    return {"running": 0, "queued": 0, "awaiting_approval": 0}


async def agent_presence_snapshots(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, AgentPresenceSnapshot]:
    """Return absolute capacity snapshots for every requested agent id."""

    ids = tuple(dict.fromkeys(agent_ids))
    snapshots = {agent_id: empty_agent_presence() for agent_id in ids}
    if not ids:
        return snapshots

    execution_rows = (
        await session.execute(
            select(
                TaskExecution.agent_id,
                func.sum(
                    case(
                        (TaskExecution.status.in_(RUNNING_EXECUTION_STATUSES), 1),
                        else_=0,
                    )
                ).label("running"),
                func.sum(case((TaskExecution.status == "queued", 1), else_=0)).label("queued"),
            )
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.agent_id.in_(ids),
            )
            .group_by(TaskExecution.agent_id)
        )
    ).all()
    for agent_id, running, queued in execution_rows:
        resolved_agent_id = cast(uuid.UUID, agent_id)  # ``agent_id.in_(ids)`` excludes NULL
        snapshots[resolved_agent_id]["running"] = int(running or 0)
        snapshots[resolved_agent_id]["queued"] = int(queued or 0)

    approval_rows = (
        await session.execute(
            select(TaskExecution.agent_id, func.count(Approval.id))
            .join(
                Approval,
                (Approval.workspace_id == TaskExecution.workspace_id)
                & (Approval.subject_execution_id == TaskExecution.id),
            )
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.agent_id.in_(ids),
                Approval.subject_type == "tool_call",
                Approval.status == "pending",
            )
            .group_by(TaskExecution.agent_id)
        )
    ).all()
    for agent_id, awaiting_approval in approval_rows:
        resolved_agent_id = cast(uuid.UUID, agent_id)  # ``agent_id.in_(ids)`` excludes NULL
        snapshots[resolved_agent_id]["awaiting_approval"] = int(awaiting_approval or 0)

    return snapshots


async def agent_presence_snapshot(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentPresenceSnapshot:
    """Return one agent's absolute capacity snapshot."""

    return (
        await agent_presence_snapshots(
            session,
            workspace_id=workspace_id,
            agent_ids=(agent_id,),
        )
    )[agent_id]


async def emit_agent_presence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> AgentPresenceSnapshot:
    """Queue an ``agent.presence`` absolute snapshot in the caller transaction."""

    snapshot = await agent_presence_snapshot(
        session,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"agent:{agent_id}:presence",
        event="agent.presence",
        data={"agent_id": str(agent_id), **snapshot},
        idempotency_key=idempotency_key,
    )
    return snapshot


__all__ = [
    "AgentPresenceSnapshot",
    "RUNNING_EXECUTION_STATUSES",
    "agent_presence_snapshot",
    "agent_presence_snapshots",
    "emit_agent_presence",
    "empty_agent_presence",
]

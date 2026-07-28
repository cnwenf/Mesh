"""Shared low-level helpers for the squad module (no intra-module cycles).

Both ``service.py`` and ``tasks.py`` need member-snapshot resolution, activity
recording and the realtime channel name; keeping them here avoids a circular
import between the two higher-level modules.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.db.models.squad import SquadActivity
from mesh.db.models.user import User
from mesh.outbox.service import emit_realtime

SQUAD_CHANNEL = "squad:{squad_id}"
SQUAD_TASK_CHANNEL = "squad_task:{task_id}"


def now_utc(clock: Callable[[], datetime] | None = None) -> datetime:
    return clock() if clock else datetime.now(UTC)


async def load_member_snapshot(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID | None
) -> dict | None:
    """Resolve a member to ``{member_id, member_type, name}`` (computed, README §6.1)."""
    if member_id is None:
        return None
    member = await session.scalar(
        select(Member).where(Member.workspace_id == workspace_id, Member.id == member_id)
    )
    if member is None:
        return None
    user = None
    agent_name = None
    if member.user_id is not None:
        user = await session.scalar(select(User).where(User.id == member.user_id))
    if member.agent_id is not None:
        from mesh.db.models.agent import Agent

        agent_name = await session.scalar(
            select(Agent.name).where(
                Agent.workspace_id == workspace_id, Agent.id == member.agent_id
            )
        )
    from mesh.member.display import resolve_display_name

    return {
        "member_id": str(member.id),
        "member_type": member.member_type,
        "name": resolve_display_name(member=member, user=user, agent_name=agent_name),
    }


async def record_squad_activity(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID | None,
    task_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> SquadActivity:
    """Append one timeline row and broadcast ``squad_activity.created``."""
    actor_kind = "member" if actor_id is not None else "system"
    entry = SquadActivity(
        workspace_id=workspace_id,
        squad_id=squad_id,
        task_id=task_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload or {},
    )
    session.add(entry)
    await session.flush()
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=squad_id),
        event="squad_activity.created",
        data={
            "squad_id": str(squad_id),
            "activity_id": str(entry.id),
            "action": action,
            "task_id": str(task_id) if task_id else None,
        },
        idempotency_key=f"squad-activity:{entry.id}",
    )
    return entry


async def emit_task_stream_frame(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
    event: str,
    data: dict,
    idempotency_key: str,
) -> None:
    """Append one frame to the task's SSE progress stream (§3.2 / §3.5).

    Frames ride the outbox → projector path onto the per-task channel
    ``squad_task:{id}`` where they are persisted with a monotonic per-channel
    ``seq``; the SSE endpoint replays them by seq (``Last-Event-ID``), which
    makes reconnects lossless and duplicate-free even though orchestration
    events are emitted from the worker process while the API serves the stream.
    """
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_TASK_CHANNEL.format(task_id=task_id),
        event=event,
        data=data,
        idempotency_key=idempotency_key,
    )


async def emit_task_status(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
    squad_id: uuid.UUID,
    old_status: str,
    new_status: str,
    idempotency_key: str,
) -> None:
    """Broadcast ``squad_task.status_changed`` (drives tree / board refresh)
    and mirror it as a ``task.status`` frame on the SSE progress stream."""
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=squad_id),
        event="squad_task.status_changed",
        data={
            "task_id": str(task_id),
            "squad_id": str(squad_id),
            "old_status": old_status,
            "new_status": new_status,
        },
        idempotency_key=idempotency_key,
    )
    await emit_task_stream_frame(
        session,
        workspace_id=workspace_id,
        task_id=task_id,
        event="task.status",
        data={"task_id": str(task_id), "status": new_status, "old_status": old_status},
        idempotency_key=f"{idempotency_key}:sse",
    )


__all__ = [
    "SQUAD_CHANNEL",
    "SQUAD_TASK_CHANNEL",
    "now_utc",
    "load_member_snapshot",
    "record_squad_activity",
    "emit_task_status",
    "emit_task_stream_frame",
]

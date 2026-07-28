"""SSE orchestration-progress stream (squad.md §3.2 / §3.5, README §6.8 GET-stream).

A single-resource, one-way progress stream for the "decompose → dispatch →
aggregate" long link. EventSource-compatible: every frame carries the event's
per-channel ``seq`` as its ``id``; clients reconnect with ``Last-Event-ID`` and
the stream replays persisted frames with ``seq > last`` from ``realtime_events``
—the same rows the outbox projector writes for the WebSocket path, which makes
reconnects lossless and duplicate-free across processes (orchestration events
are emitted by the worker relay while the API process serves this stream).

Event types (§3.2): ``task.status`` / ``subtask.created`` / ``subtask.assigned``
/ ``plan.submitted`` / ``task.aggregated``. Created by ``POST .../tasks``
(returns ``stream_url``); consumed by ``GET``.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from mesh.db.models.realtime import RealtimeEvent
from mesh.db.tenant import set_tenant_context
from mesh.squad.common import SQUAD_TASK_CHANNEL
from mesh.squad.tasks import load_task

POLL_INTERVAL_SECONDS = 0.5
KEEPALIVE_EVERY_POLLS = 50  # ~25s of silence → one SSE comment frame
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


def task_stream_response(
    session_factory: async_sessionmaker,
    *,
    workspace_id: uuid.UUID,
    squad_id: uuid.UUID,
    task_id: uuid.UUID,
    last_event_id: int,
) -> StreamingResponse:
    channel = SQUAD_TASK_CHANNEL.format(task_id=task_id)

    async def event_gen():
        seq = last_event_id
        idle_polls = 0
        while True:
            terminal = False
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                try:
                    task = await load_task(
                        session, workspace_id=workspace_id, task_id=task_id
                    )
                except Exception:  # noqa: BLE001 — task removed mid-stream
                    yield f"event: error\ndata: {json.dumps({'error': 'not_found'})}\n\n"
                    return
                terminal = task.status in TERMINAL_STATUSES
                rows = list(
                    (
                        await session.execute(
                            select(RealtimeEvent)
                            .where(
                                RealtimeEvent.workspace_id == workspace_id,
                                RealtimeEvent.channel == channel,
                                RealtimeEvent.seq > seq,
                            )
                            .order_by(RealtimeEvent.seq)
                            .limit(200)
                        )
                    ).scalars()
                )
            if rows:
                idle_polls = 0
                for row in rows:
                    seq = row.seq
                    # The projector persists the inner frame data as payload.
                    data = row.payload or {}
                    yield f"id: {row.seq}\nevent: {row.event}\ndata: {json.dumps(data)}\n\n"
            else:
                idle_polls += 1
                if idle_polls >= KEEPALIVE_EVERY_POLLS:
                    idle_polls = 0
                    yield ": keepalive\n\n"
            # Terminate once the task is terminal AND the buffer is drained.
            if terminal and not rows:
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


__all__ = ["task_stream_response"]

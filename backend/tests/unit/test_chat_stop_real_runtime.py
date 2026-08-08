"""Stop a chat generation through the REAL runtime chain (MES-191 §4.4).

Stop no longer flips rows behind a placeholder engine:

- a LIVE execution (queued/claimed/running) is cancelled through
  ``request_execution_cancel_tx`` — the daemon ack (PATCH cancelled) or the
  §3.6 safety net finalizes the message with its streamed partial content;
- a stale streaming row without a live execution is flipped directly
  (conditional UPDATE + owner-only frames + SETNX SSE terminal frame), with
  the partial content read from the generation's SSE buffer.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from mesh.chat.engine import append_chat_frame
from mesh.chat.service import ChatService
from mesh.db.models.chat import ChatMessage, ChatSession
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from tests.unit.runtime_support import make_execution, seed_chat_world, seed_world

pytestmark = pytest.mark.unit


async def _actor(session_factory, world) -> Member:
    async with session_factory() as session:
        return await session.get(Member, world["member_id"])


async def _load_message(session_factory, message_id) -> ChatMessage:
    async with session_factory() as session:
        return await session.get(ChatMessage, message_id)


async def _frames(redis_client, generation_id) -> list[dict]:
    raw = await redis_client.xrange(f"chat:gen:{generation_id}:events", min="-", max="+")
    return [json.loads(fields["frame"]) for _entry_id, fields in raw]


async def test_stop_cancels_live_queued_chat_execution(session_factory, redis_client):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    service = ChatService(session_factory)

    result = await service.stop_generation(
        actor=await _actor(session_factory, world),
        workspace_id=world["ws_id"],
        session_id=chat["session_id"],
        generation_id=chat["generation_id"],
        redis=redis_client,
    )

    # Queued runs cancel immediately; finalization rides the finished event.
    async with session_factory() as session:
        row = await session.get(TaskExecution, execution.id)
    assert row.status == "cancelled"
    async with session_factory() as session:
        finished = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "execution.finished"
                )
            )
        ).scalars().all()
    assert len(finished) == 1
    # The message stays streaming until the safety net finalizes it.
    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "streaming"
    assert result["generation_id"] == str(chat["generation_id"])


async def test_stop_moves_live_running_chat_execution_to_cancelling(
    session_factory, redis_client
):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        status="running",
        task_spec=chat["task_spec"],
    )
    service = ChatService(session_factory)

    await service.stop_generation(
        actor=await _actor(session_factory, world),
        workspace_id=world["ws_id"],
        session_id=chat["session_id"],
        generation_id=chat["generation_id"],
        redis=redis_client,
    )

    # Two-phase: the daemon learns via heartbeat downlink and acks.
    async with session_factory() as session:
        row = await session.get(TaskExecution, execution.id)
    assert row.status == "cancelling"
    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "streaming"


async def test_stop_without_execution_flips_stale_row_with_buffer_partial(
    session_factory, redis_client
):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    # The stream mirrored partial deltas before the execution vanished.
    await append_chat_frame(
        redis_client,
        generation_id=chat["generation_id"],
        event="message.delta",
        data={"message_id": str(chat["message_id"]), "delta": "部分内容"},
    )
    service = ChatService(session_factory)

    result = await service.stop_generation(
        actor=await _actor(session_factory, world),
        workspace_id=world["ws_id"],
        session_id=chat["session_id"],
        generation_id=chat["generation_id"],
        redis=redis_client,
    )

    assert result["generation_status"] == "interrupted"
    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "interrupted"
    assert message.content == "部分内容"
    async with session_factory() as session:
        chat_session = await session.get(ChatSession, chat["session_id"])
    assert chat_session.last_message_preview == "部分内容"

    frames = await _frames(redis_client, chat["generation_id"])
    assert frames[-1]["event"] == "message.interrupted"
    assert frames[-1]["data"]["partial_content"] == "部分内容"

    # H1 owner-only realtime frames: full data on the session channel, safe
    # payload on the private list channel.
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "realtime.publish",
                    OutboxEvent.payload["event"].astext == "message.interrupted",
                )
            )
        ).scalars().all()
    session_frames = [
        r for r in rows if r.payload["channel"] == f"chat_session:{chat['session_id']}"
    ]
    list_frames = [
        r for r in rows if r.payload["channel"] == f"chat_list:{world['member_id']}"
    ]
    assert len(session_frames) == 1
    assert len(list_frames) == 1
    assert "partial_content" not in list_frames[0].payload["data"]
    # No placeholder-era generation-finished outbox event may appear.
    async with session_factory() as session:
        legacy = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "chat.generation_finished"
                )
            )
        ).scalars().all()
    assert legacy == []


async def test_stop_is_idempotent_when_already_terminal(session_factory, redis_client):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    service = ChatService(session_factory)
    actor = await _actor(session_factory, world)

    first = await service.stop_generation(
        actor=actor,
        workspace_id=world["ws_id"],
        session_id=chat["session_id"],
        generation_id=chat["generation_id"],
        redis=redis_client,
    )
    second = await service.stop_generation(
        actor=actor,
        workspace_id=world["ws_id"],
        session_id=chat["session_id"],
        generation_id=chat["generation_id"],
        redis=redis_client,
    )
    assert second == first

    # Exactly ONE terminal SSE frame despite two stops (SETNX guard).
    frames = await _frames(redis_client, chat["generation_id"])
    terminal = [f for f in frames if f["event"] == "message.interrupted"]
    assert len(terminal) == 1

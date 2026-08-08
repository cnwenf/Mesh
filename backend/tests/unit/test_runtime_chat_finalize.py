"""Chat generation terminal write-back (MES-191, chat-session.md §4.4).

When a chat-triggered execution reaches a terminal attempt state, the same
transaction finalizes the agent message (content = lossless concat of the
mirrored stdout deltas, fallback result summary), bumps the session preview,
auto-titles, and registers the H1 owner-only terminal realtime frames. Chat
terminals never fan out workspace notifications or per-execution frames.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.chat import ChatMessage, ChatSession
from mesh.db.models.outbox import OutboxEvent
from mesh.runtime.attempts import cancel_execution, transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.logs import append_log_lines
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
    valid_result_v1,
)

pytestmark = pytest.mark.unit


async def _chat_world(session_factory, world) -> dict:
    """ChatSession + first user message + streaming agent reply row."""
    async with session_factory() as session, session.begin():
        chat_session = ChatSession(
            workspace_id=world["ws_id"],
            owner_id=world["member_id"],
            agent_id=world["agent_id"],
        )
        session.add(chat_session)
        await session.flush()
        user_message = ChatMessage(
            workspace_id=world["ws_id"],
            session_id=chat_session.id,
            role="user",
            content="帮我看下这个报错",
            generation_status="done",
        )
        session.add(user_message)
        await session.flush()
        generation_id = uuid.uuid4()
        agent_message = ChatMessage(
            workspace_id=world["ws_id"],
            session_id=chat_session.id,
            role="agent",
            content="",
            generation_id=generation_id,
            generation_status="streaming",
            parent_id=user_message.id,
            selected_candidate=True,
        )
        session.add(agent_message)
    return {
        "session_id": chat_session.id,
        "message_id": agent_message.id,
        "generation_id": generation_id,
        "task_spec": {
            "kind": "chat_generation",
            "session_id": str(chat_session.id),
            "message_id": str(agent_message.id),
            "generation_id": str(generation_id),
        },
    }


async def _claim(session_factory, runtime) -> uuid.UUID:
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return uuid.UUID(result.attempt["id"])


async def _frames(redis_client, generation_id) -> list[dict]:
    raw = await redis_client.xrange(f"chat:gen:{generation_id}:events", min="-", max="+")
    return [json.loads(fields["frame"]) for _entry_id, fields in raw]


async def _load_message(session_factory, message_id) -> ChatMessage:
    async with session_factory() as session:
        return await session.get(ChatMessage, message_id)


async def _realtime_rows(session_factory, event_name: str) -> list:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "realtime.publish",
                        OutboxEvent.payload["event"].astext == event_name,
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_completed_chat_attempt_finalizes_message_and_session(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    chat = await _chat_world(session_factory, world)
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    attempt_id = await _claim(session_factory, runtime)
    first = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["你好,", "我是助手。"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )
    assert first["accepted_end_offset"] > 0

    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=valid_result_v1(summary="done"),
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )

    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "done"
    assert message.content == "你好,我是助手。"  # lossless delta concat
    assert message.finished_at is not None
    assert (message.completion_tokens or 0) >= 1
    async with session_factory() as session:
        chat_session = await session.get(ChatSession, chat["session_id"])
    assert chat_session.last_message_preview == "你好,我是助手。"
    assert chat_session.last_message_at is not None
    # Auto-title after the first completed round.
    assert chat_session.title == "帮我看下这个报错"

    frames = await _frames(redis_client, chat["generation_id"])
    assert frames[-1]["event"] == "message.done"
    assert frames[-1]["data"]["message_id"] == str(chat["message_id"])

    # H1: session-channel frame carries full data; list channel a safe payload.
    done_frames = await _realtime_rows(session_factory, "message.done")
    session_frames = [
        f for f in done_frames
        if f.payload["channel"] == f"chat_session:{chat['session_id']}"
    ]
    list_frames = [
        f for f in done_frames
        if f.payload["channel"] == f"chat_list:{world['member_id']}"
    ]
    assert len(session_frames) == 1
    assert len(list_frames) == 1
    assert session_frames[0].payload["data"]["message_id"] == str(chat["message_id"])
    assert "content" not in list_frames[0].payload["data"]
    assert "partial_content" not in list_frames[0].payload["data"]
    assert list_frames[0].payload["data"]["session_id"] == str(chat["session_id"])

    # Privacy: no workspace notification, no per-execution completed frame.
    async with session_factory() as session:
        fanouts = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "notification.fanout"
                )
            )
        ).scalars().all()
    assert fanouts == []
    completed_frames = await _realtime_rows(session_factory, "execution.completed")
    assert completed_frames == []


async def test_failed_chat_attempt_finalizes_with_error_frame(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    chat = await _chat_world(session_factory, world)
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    attempt_id = await _claim(session_factory, runtime)

    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="failed",
        failure_reason="executor_unavailable",
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )

    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "failed"
    assert message.error_message == "executor_unavailable"
    frames = await _frames(redis_client, chat["generation_id"])
    assert frames[-1]["event"] == "error"
    assert frames[-1]["data"]["code"] == "generation_failed"
    # Chat failures surface in the session UI — never a workspace notification.
    async with session_factory() as session:
        fanouts = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "notification.fanout"
                )
            )
        ).scalars().all()
    assert fanouts == []


async def test_cancelled_chat_attempt_finalizes_interrupted_with_partial(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    chat = await _chat_world(session_factory, world)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    attempt_id = await _claim(session_factory, runtime)
    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["部分内容"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )
    await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
    )

    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="cancelled",
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )

    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "interrupted"
    assert message.content == "部分内容"
    frames = await _frames(redis_client, chat["generation_id"])
    assert frames[-1]["event"] == "message.interrupted"
    assert frames[-1]["data"]["partial_content"] == "部分内容"


async def test_completed_chat_with_empty_buffer_falls_back_to_summary(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    chat = await _chat_world(session_factory, world)
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    attempt_id = await _claim(session_factory, runtime)

    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=valid_result_v1(summary="最终建议:重启服务"),
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )

    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "done"
    assert message.content == "最终建议:重启服务"


async def test_non_chat_attempt_keeps_legacy_frames(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    attempt_id = await _claim(session_factory, runtime)

    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=valid_result_v1(summary="done"),
        signing_secret=TEST_JWT_SECRET,
        storage=object_storage,
        redis=redis_client,
    )

    # Issue executions keep their per-execution completed frame.
    completed_frames = await _realtime_rows(session_factory, "execution.completed")
    assert len(completed_frames) == 1
    assert completed_frames[0].payload["channel"] == f"execution:{execution.id}"
    # No chat buffer involvement.
    keys = []
    async for key in redis_client.scan_iter("chat:gen:*"):
        keys.append(key)
    assert keys == []


async def test_queued_chat_cancel_finalizes_via_finished_event(
    session_factory, redis_client
):
    """Safety net: terminal paths that bypass the daemon PATCH (a queued run
    cancelled before any claim) still finalize the generation via the §3.6
    execution.finished event."""
    from mesh.chat.finalize import finalize_chat_from_finished_event

    world = await seed_world(session_factory)
    chat = await _chat_world(session_factory, world)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=chat["task_spec"],
    )
    await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
    )
    async with session_factory() as session:
        finished = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "execution.finished"
                )
            )
        ).scalars().all()
    assert len(finished) == 1

    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, finished[0], redis_client)

    message = await _load_message(session_factory, chat["message_id"])
    assert message.generation_status == "interrupted"
    assert message.content == ""
    frames = await _frames(redis_client, chat["generation_id"])
    assert frames[-1]["event"] == "message.interrupted"

    # Idempotent redelivery: the second pass is a no-op (no duplicate frame).
    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, finished[0], redis_client)
    frames_again = await _frames(redis_client, chat["generation_id"])
    assert len(frames_again) == len(frames)

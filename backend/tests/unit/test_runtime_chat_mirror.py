"""Chat stdout → SSE buffer mirror (MES-191, chat-session.md §4.4).

Chat generations ride the real runtime chain: the daemon's provider emits
TextDelta chunks, the LogUploader posts them as stdout lines, and the log
endpoint mirrors those lines onto the generation's private SSE frame buffer
(message.created exactly once, then one message.delta per line). stderr and
non-chat executions are never mirrored.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.runtime.claim import claim_execution
from mesh.runtime.logs import append_log_lines
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
)

pytestmark = pytest.mark.unit


async def _claim(session_factory, runtime):
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return uuid.UUID(result.attempt["id"])


def _chat_task_spec() -> dict:
    return {
        "kind": "chat_generation",
        "session_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "generation_id": str(uuid.uuid4()),
        "untrusted_context": "你好",
    }


async def _frames(redis_client, generation_id: uuid.UUID) -> list[dict]:
    raw = await redis_client.xrange(f"chat:gen:{generation_id}:events", min="-", max="+")
    return [json.loads(fields["frame"]) for _entry_id, fields in raw]


async def test_chat_stdout_lines_mirror_to_sse_buffer(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    task_spec = _chat_task_spec()
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=task_spec,
    )
    attempt_id = await _claim(session_factory, runtime)
    generation_id = uuid.UUID(task_spec["generation_id"])

    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["第一段回复", "第二段回复"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )

    frames = await _frames(redis_client, generation_id)
    assert [f["event"] for f in frames] == [
        "message.created",
        "message.delta",
        "message.delta",
    ]
    assert [f["seq"] for f in frames] == [1, 2, 3]
    assert frames[0]["data"]["message_id"] == task_spec["message_id"]
    assert frames[0]["data"]["generation_status"] == "streaming"
    assert frames[1]["data"]["delta"] == "第一段回复"
    assert frames[2]["data"]["delta"] == "第二段回复"


async def test_chat_stderr_lines_not_mirrored(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    task_spec = _chat_task_spec()
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=task_spec,
    )
    attempt_id = await _claim(session_factory, runtime)

    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stderr",
        start_offset=0,
        lines=["debug noise"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )

    frames = await _frames(redis_client, uuid.UUID(task_spec["generation_id"]))
    assert frames == []


async def test_non_chat_execution_stdout_not_mirrored(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    attempt_id = await _claim(session_factory, runtime)

    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["build output"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )

    # No generation buffer exists for a non-chat execution; also the normal
    # execution log channel frames still flow (regression guard).
    keys = []
    async for key in redis_client.scan_iter("chat:gen:*"):
        keys.append(key)
    assert keys == []
    async with session_factory() as session:
        log_frames = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "realtime.publish",
                    OutboxEvent.payload["event"].astext == "execution.log",
                )
            )
        ).scalars().all()
    assert len(log_frames) == 1
    assert log_frames[0].payload["channel"] == f"execution:{execution.id}:logs"


async def test_chat_created_frame_once_across_batches(
    session_factory, object_storage, redis_client
):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    task_spec = _chat_task_spec()
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=task_spec,
    )
    attempt_id = await _claim(session_factory, runtime)
    generation_id = uuid.UUID(task_spec["generation_id"])

    first = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["chunk-1"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )
    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=first["accepted_end_offset"],
        lines=["chunk-2"],
        signing_secret=TEST_JWT_SECRET,
        redis=redis_client,
    )

    frames = await _frames(redis_client, generation_id)
    events = [f["event"] for f in frames]
    assert events == ["message.created", "message.delta", "message.delta"]
    assert [f["data"].get("delta") for f in frames[1:]] == ["chunk-1", "chunk-2"]
    assert [f["seq"] for f in frames] == [1, 2, 3]


async def test_append_without_redis_keeps_legacy_behavior(
    session_factory, object_storage
):
    """Existing daemon deployments / tests pass no redis client: persistence
    and the execution log channel work exactly as before (no mirror)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    task_spec = _chat_task_spec()
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        task_spec=task_spec,
    )
    attempt_id = await _claim(session_factory, runtime)

    result = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["no mirror"],
        signing_secret=TEST_JWT_SECRET,
    )
    assert result["accepted_end_offset"] > 0

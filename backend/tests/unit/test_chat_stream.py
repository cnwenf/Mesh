"""Direct coverage of the SSE stream generator (chat-session.md §3.3).

Exercises ``generation_event_stream`` against the real Redis: replay +
live follow, ``Last-Event-ID`` resume mid-generation, heartbeat pings, and
the buffer-eviction degradation path.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from mesh.chat.stream import (
    format_sse_frame,
    generation_event_stream,
    parse_last_event_id,
)

pytestmark = pytest.mark.unit


def _parse_frames(raw_frames: list[str]) -> list[dict]:
    parsed = []
    current: dict = {}
    for chunk in raw_frames:
        for line in chunk.split("\n"):
            if line.startswith("id: "):
                current["id"] = line[4:]
            elif line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
        if current.get("event"):
            parsed.append(current)
            current = {}
    return parsed


async def _append(redis, generation_id: str, seq: int, event: str, data: dict) -> None:
    key = f"chat:gen:{generation_id}:events"
    frame = json.dumps({"seq": seq, "event": event, "data": data})
    await redis.xadd(key, {"frame": frame}, id=f"{seq}-0")
    await redis.publish(f"chat:gen:{generation_id}:pubsub", "frame")


def test_format_sse_frame_shape():
    frame = format_sse_frame(7, "message.delta", {"delta": "x"})
    assert frame == 'id: 7\nevent: message.delta\ndata: {"delta": "x"}\n\n'


def test_parse_last_event_id_variants():
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("") == 0
    assert parse_last_event_id("42") == 42
    assert parse_last_event_id("bogus") == 0
    assert parse_last_event_id("-3") == 0


async def test_replay_and_follow_until_terminal(redis_client):
    gid = uuid.uuid4().hex
    await _append(redis_client, gid, 1, "message.created", {"message_id": "m1"})

    async def load_state():
        return None  # still streaming

    collected: list[str] = []

    async def consume():
        gen = generation_event_stream(
            redis_client,
            generation_id=gid,
            last_event_id=0,
            ping_seconds=5.0,
            max_seconds=10.0,
            load_message_state=load_state,
        )
        async for chunk in gen:
            collected.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.3)  # generator parked in follow mode
    await _append(redis_client, gid, 2, "message.delta", {"message_id": "m1", "delta": "he"})
    await _append(redis_client, gid, 3, "message.delta", {"message_id": "m1", "delta": "llo"})
    await _append(redis_client, gid, 4, "message.done", {"message_id": "m1"})
    await asyncio.wait_for(task, timeout=5)
    frames = _parse_frames(collected)
    assert [f["event"] for f in frames] == [
        "message.created",
        "message.delta",
        "message.delta",
        "message.done",
    ]
    assert [f["id"] for f in frames] == ["1", "2", "3", "4"]


async def test_resume_after_last_event_id(redis_client):
    gid = uuid.uuid4().hex
    for seq in range(1, 4):
        await _append(redis_client, gid, seq, "message.delta", {"delta": f"c{seq}"})
    await _append(redis_client, gid, 4, "message.done", {"message_id": "m1"})

    async def load_state():
        return None

    collected = []
    gen = generation_event_stream(
        redis_client,
        generation_id=gid,
        last_event_id=2,  # client already saw 1..2
        ping_seconds=5.0,
        max_seconds=5.0,
        load_message_state=load_state,
    )
    async for chunk in gen:
        collected.append(chunk)
    frames = _parse_frames(collected)
    assert [f["id"] for f in frames] == ["3", "4"]  # only frames after the cursor
    assert frames[-1]["event"] == "message.done"


async def test_degraded_late_subscriber(redis_client):
    gid = uuid.uuid4().hex  # buffer never existed (evicted)

    async def load_state():
        return {
            "message_id": "m9",
            "generation_status": "done",
            "content": "完整正文",
            "error_message": None,
        }

    collected = []
    gen = generation_event_stream(
        redis_client,
        generation_id=gid,
        last_event_id=0,
        ping_seconds=5.0,
        max_seconds=5.0,
        load_message_state=load_state,
    )
    async for chunk in gen:
        collected.append(chunk)
    frames = _parse_frames(collected)
    assert [f["event"] for f in frames] == ["message.delta", "message.done"]
    assert frames[0]["data"]["delta"] == "完整正文"


async def test_degraded_failed_state_maps_to_error_frame(redis_client):
    gid = uuid.uuid4().hex

    async def load_state():
        return {
            "message_id": "m9",
            "generation_status": "failed",
            "content": "",
            "error_message": "generation failed",
        }

    collected = []
    gen = generation_event_stream(
        redis_client,
        generation_id=gid,
        last_event_id=0,
        ping_seconds=5.0,
        max_seconds=5.0,
        load_message_state=load_state,
    )
    async for chunk in gen:
        collected.append(chunk)
    frames = _parse_frames(collected)
    assert frames[-1]["event"] == "error"
    assert frames[-1]["data"]["code"] == "generation_failed"


async def test_heartbeat_ping_while_idle(redis_client):
    gid = uuid.uuid4().hex
    await _append(redis_client, gid, 1, "message.created", {"message_id": "m1"})

    async def load_state():
        return None

    collected: list[str] = []

    async def consume():
        gen = generation_event_stream(
            redis_client,
            generation_id=gid,
            last_event_id=0,
            ping_seconds=0.2,  # fast heartbeat for the test
            max_seconds=5.0,
            load_message_state=load_state,
        )
        async for chunk in gen:
            collected.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.7)  # ~3 ping windows with no traffic
    await _append(redis_client, gid, 2, "message.done", {"message_id": "m1"})
    await asyncio.wait_for(task, timeout=5)
    frames = _parse_frames(collected)
    events = [f["event"] for f in frames]
    assert "ping" in events  # heartbeat kept the connection alive
    assert events[0] == "message.created"
    assert events[-1] == "message.done"

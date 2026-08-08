"""Chat finalization defensive edge cases (MES-191, chat-session.md §4.4).

Completes the guard branches of ``mesh.chat.finalize`` with lightweight
stubs (no HTTP, no daemon): UUID-parse validation, malformed SSE buffer
frames, result-summary shape checks, the SETNX terminal-frame race, and the
early returns for non-chat / incomplete / non-terminal executions. The happy
paths live in test_runtime_chat_finalize.py.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from mesh.chat.engine import AUTO_TITLE_MAX_CHARS
from mesh.chat.finalize import (
    ChatFinalization,
    _parse_uuid,
    _result_summary,
    append_terminal_frame,
    buffered_content_from_redis,
    finalize_chat_from_finished_event,
    finalize_chat_generation,
    terminal_frame_data,
)
from mesh.db.models.chat import ChatMessage, ChatSession
from mesh.db.tenant import set_tenant_context
from tests.unit.runtime_support import make_execution, seed_chat_world, seed_world

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Minimal in-memory stand-in for the Redis commands finalize uses."""

    def __init__(self, *, xrange_entries=None, set_result=True, raise_on=()):
        self._xrange_entries = list(xrange_entries or [])
        self._set_result = set_result
        self._raise_on = set(raise_on)
        self.added_frames: list[tuple[str, dict]] = []
        self.published: list[tuple[str, str]] = []
        self._seq = 0

    async def xrange(self, key, min=None, max=None):
        if "xrange" in self._raise_on:
            raise RuntimeError("redis unavailable")
        return self._xrange_entries

    async def set(self, key, value, nx=False, ex=None):
        if "set" in self._raise_on:
            raise RuntimeError("redis unavailable")
        return self._set_result

    async def incr(self, key):
        self._seq += 1
        return self._seq

    async def xadd(self, key, fields, id=None, maxlen=None):
        if "xadd" in self._raise_on:
            raise RuntimeError("redis unavailable")
        self.added_frames.append((key, fields))
        return id

    async def expire(self, key, ttl):
        return True

    async def publish(self, channel, message):
        self.published.append((channel, message))


def _finalization(**overrides) -> ChatFinalization:
    base = {
        "wrote": True,
        "generation_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "session_id": None,
        "generation_status": "done",
        "content": "正文",
        "completion_tokens": 2,
        "error_message": None,
        "terminal_event": "message.done",
    }
    base.update(overrides)
    return ChatFinalization(**base)


def _stub_execution(**overrides) -> SimpleNamespace:
    base = {
        "trigger": "chat",
        "task_spec": {
            "kind": "chat_generation",
            "generation_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
        },
        "status": "completed",
        "result": {"outcome": {"summary": "摘要"}},
        "failure_reason": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# pure guards
# ---------------------------------------------------------------------------


def test_parse_uuid_rejects_falsy_and_invalid_input():
    assert _parse_uuid(None) is None
    assert _parse_uuid("") is None
    assert _parse_uuid("not-a-uuid") is None
    parsed = _parse_uuid("123e4567-e89b-12d3-a456-426614174000")
    assert parsed == uuid.UUID("123e4567-e89b-12d3-a456-426614174000")


def test_result_summary_requires_schema_v1_shape():
    assert _result_summary("not-a-dict") == ""
    assert _result_summary(None) == ""
    assert _result_summary({"outcome": "not-a-dict"}) == ""
    assert _result_summary({"outcome": {"summary": 42}}) == ""
    assert _result_summary({"outcome": {"summary": "ok"}}) == "ok"


async def test_buffered_content_skips_malformed_frames():
    good = json.dumps({"seq": 1, "event": "message.delta", "data": {"delta": "你好"}})
    done = json.dumps({"seq": 2, "event": "message.done", "data": {}})
    entries = [
        ("1-0", {"frame": good}),
        ("2-0", {"other": "missing frame key"}),
        ("3-0", {"frame": "{not valid json"}),
        ("4-0", {"frame": done}),
        ("5-0", {"frame": json.dumps({"seq": 3, "event": "message.delta", "data": {}})}),
    ]
    content = await buffered_content_from_redis(
        _FakeRedis(xrange_entries=entries), uuid.uuid4()
    )
    assert content == "你好"


def test_terminal_frame_data_variants():
    done = terminal_frame_data(_finalization())
    assert done["generation_status"] == "done"
    assert done["completion_tokens"] == 2
    interrupted = terminal_frame_data(
        _finalization(
            terminal_event="message.interrupted", generation_status="interrupted"
        )
    )
    assert interrupted["partial_content"] == "正文"
    failed = terminal_frame_data(
        _finalization(terminal_event="error", generation_status="failed")
    )
    assert failed["code"] == "generation_failed"
    assert failed["message"] == "generation failed"
    failed_named = terminal_frame_data(
        _finalization(
            terminal_event="error", generation_status="failed", error_message="boom"
        )
    )
    assert failed_named["message"] == "boom"


async def test_append_terminal_frame_noop_without_redis_or_write():
    await append_terminal_frame(None, _finalization())
    redis = _FakeRedis()
    await append_terminal_frame(redis, _finalization(wrote=False))
    assert redis.added_frames == []


async def test_append_terminal_frame_setnx_dedups_second_writer():
    redis = _FakeRedis(set_result=None)  # loses the SETNX race
    await append_terminal_frame(redis, _finalization())
    assert redis.added_frames == []


async def test_append_terminal_frame_is_best_effort_on_redis_error():
    redis = _FakeRedis(raise_on={"set"})
    await append_terminal_frame(redis, _finalization())  # swallowed
    assert redis.added_frames == []


async def test_append_terminal_frame_happy_path():
    redis = _FakeRedis()
    finalization = _finalization()
    await append_terminal_frame(redis, finalization)
    assert len(redis.added_frames) == 1
    key, fields = redis.added_frames[0]
    assert key == f"chat:gen:{finalization.generation_id}:events"
    frame = json.loads(fields["frame"])
    assert frame["event"] == "message.done"
    assert frame["data"]["generation_status"] == "done"


async def test_finalize_generation_guards_reject_non_applicable_executions():
    session = SimpleNamespace()  # never touched on the guard paths
    ws_id = uuid.uuid4()
    not_chat = _stub_execution(trigger="assign")
    assert (
        await finalize_chat_generation(session, workspace_id=ws_id, execution=not_chat)
        is None
    )
    missing_ids = _stub_execution(task_spec={"kind": "chat_generation"})
    assert (
        await finalize_chat_generation(
            session, workspace_id=ws_id, execution=missing_ids
        )
        is None
    )
    non_terminal = _stub_execution(status="running")
    assert (
        await finalize_chat_generation(
            session, workspace_id=ws_id, execution=non_terminal
        )
        is None
    )


# ---------------------------------------------------------------------------
# real PostgreSQL paths
# ---------------------------------------------------------------------------


async def test_finalize_generation_falls_back_when_buffer_read_fails(
    session_factory,
):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    execution = _stub_execution(
        task_spec=chat["task_spec"],
        result={"outcome": {"summary": "兜底摘要"}},
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        finalization = await finalize_chat_generation(
            session,
            workspace_id=world["ws_id"],
            execution=execution,
            redis=_FakeRedis(raise_on={"xrange"}),
        )
    assert finalization is not None and finalization.wrote
    assert finalization.content == "兜底摘要"
    async with session_factory() as session:
        message = await session.get(ChatMessage, chat["message_id"])
    assert message.generation_status == "done"
    assert message.content == "兜底摘要"


async def test_auto_title_respects_manual_titles(session_factory):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        await session.execute(
            update(ChatSession)
            .where(ChatSession.id == chat["session_id"])
            .values(title="手工标题", title_is_auto=False)
        )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        await finalize_chat_generation(
            session,
            workspace_id=world["ws_id"],
            execution=_stub_execution(task_spec=chat["task_spec"]),
        )
    async with session_factory() as session:
        row = await session.get(ChatSession, chat["session_id"])
    assert row.title == "手工标题"


async def test_auto_title_truncates_long_first_question(session_factory):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    long_question = "这是一个非常长的问题文本" * 12
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        user_message_id = await session.scalar(
            select(ChatMessage.id).where(
                ChatMessage.session_id == chat["session_id"],
                ChatMessage.role == "user",
            )
        )
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.id == user_message_id)
            .values(content=long_question)
        )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        await finalize_chat_generation(
            session,
            workspace_id=world["ws_id"],
            execution=_stub_execution(task_spec=chat["task_spec"]),
        )
    async with session_factory() as session:
        row = await session.get(ChatSession, chat["session_id"])
    assert row.title == long_question.replace("\n", " ")[:AUTO_TITLE_MAX_CHARS] + "…"


async def test_auto_title_skips_session_without_user_message(session_factory):
    world = await seed_world(session_factory)
    chat = await seed_chat_world(session_factory, world)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        await session.execute(
            update(ChatMessage)
            .where(
                ChatMessage.session_id == chat["session_id"],
                ChatMessage.role == "user",
            )
            .values(content="")
        )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        await finalize_chat_generation(
            session,
            workspace_id=world["ws_id"],
            execution=_stub_execution(task_spec=chat["task_spec"]),
        )
    async with session_factory() as session:
        row = await session.get(ChatSession, chat["session_id"])
    assert row.title == "新对话"


async def test_finished_event_guards_reject_unusable_payloads(session_factory):
    world = await seed_world(session_factory)
    # No execution_id → no-op before any DB access.
    missing = SimpleNamespace(payload={}, workspace_id=world["ws_id"])
    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, missing, None)
    # Unknown execution id → no-op.
    unknown = SimpleNamespace(
        payload={
            "execution_id": str(uuid.uuid4()),
            "workspace_id": str(world["ws_id"]),
        },
        workspace_id=world["ws_id"],
    )
    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, unknown, None)
    # Non-chat execution → no-op (issue terminals keep their own path).
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"]
    )
    non_chat = SimpleNamespace(
        payload={
            "execution_id": str(execution.id),
            "workspace_id": str(world["ws_id"]),
        },
        workspace_id=world["ws_id"],
    )
    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, non_chat, None)


async def test_finished_event_skips_terminal_frame_without_generation(
    session_factory,
):
    """Chat execution whose spec lost its ids → finalize returns None, and
    no SSE terminal frame is appended (the 363->exit guard)."""
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        status="cancelled",
        task_spec={"kind": "chat_generation"},
    )
    event = SimpleNamespace(
        payload={
            "execution_id": str(execution.id),
            "workspace_id": str(world["ws_id"]),
        },
        workspace_id=world["ws_id"],
    )
    redis = _FakeRedis()
    async with session_factory() as session, session.begin():
        await finalize_chat_from_finished_event(session, event, redis)
    assert redis.added_frames == []

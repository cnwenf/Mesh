"""Stale-generation reclaim + degraded stop (MES-191, §3.5 / §3.3).

A streaming row older than ``streaming_stale_seconds`` is a stuck generation:
the next ``send_message`` reclaims the slot (conditional flip to failed,
cancel of any live execution, owner-only error frames) instead of 409ing
forever. ``stop_generation`` must also work when Redis is unreachable — it
degrades to the persisted body instead of erroring.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.chat.service import ChatService
from mesh.db.models.chat import ChatMessage
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from tests.unit.runtime_support import make_execution, seed_world

pytestmark = pytest.mark.unit


class _BrokenRedis:
    """Stand-in for an unreachable Redis (every command raises)."""

    async def xrange(self, key, min=None, max=None):
        raise RuntimeError("redis unavailable")


@pytest_asyncio.fixture
async def world(session_factory):
    seeded = await seed_world(session_factory)
    async with session_factory() as session:
        member = await session.get(Member, seeded["member_id"])
        session.expunge(member)
    return {**seeded, "member": member}


@pytest_asyncio.fixture
async def stale_service(session_factory):
    """ChatService whose streaming rows are immediately considered stale."""
    return ChatService(session_factory, streaming_stale_seconds=0)


async def _backdate_streaming_row(session_factory, *, ws_id, session_id, age_seconds):
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws_id)
        message_id = await session.scalar(
            select(ChatMessage.id).where(
                ChatMessage.session_id == session_id,
                ChatMessage.generation_status == "streaming",
            )
        )
        from sqlalchemy import update

        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.id == message_id)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=age_seconds))
        )
    return message_id


async def test_send_message_reclaims_stale_generation_and_cancels_live_execution(
    session_factory, stale_service, world
):
    service = stale_service
    row = await service.create_session(
        actor=world["member"],
        workspace_id=world["ws_id"],
        agent_id=world["agent_id"],
    )
    session_id = uuid.UUID(row["id"])
    first = await service.send_message(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        content="第一条",
    )
    stale_generation_id = uuid.UUID(first["generation_id"])
    # A live execution bound to the stuck generation (relay already
    # materialized it before the daemon went dark).
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        trigger="chat",
        status="queued",
        task_spec={"kind": "chat_generation", "generation_id": str(stale_generation_id)},
    )
    await _backdate_streaming_row(
        session_factory,
        ws_id=world["ws_id"],
        session_id=session_id,
        age_seconds=3600,
    )

    # The second send reclaims the slot instead of 409ing.
    second = await service.send_message(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        content="第二条",
    )
    assert second["generation_id"] != first["generation_id"]

    # Stale row flipped to failed with the timeout reason.
    async with session_factory() as session:
        await set_tenant_context(session, world["ws_id"])
        stale_message = await session.scalar(
            select(ChatMessage).where(
                ChatMessage.generation_id == stale_generation_id
            )
        )
    assert stale_message.generation_status == "failed"
    assert stale_message.error_message == "generation timed out"

    # The live execution was cancelled through the runtime chain.
    async with session_factory() as session:
        refreshed = await session.get(TaskExecution, execution.id)
    assert refreshed.status == "cancelled"

    # Owner-only error frames: session channel + owner's private list channel.
    async with session_factory() as session:
        error_frames = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "realtime.publish",
                        OutboxEvent.payload["event"].astext == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
    channels = {f.payload["channel"] for f in error_frames}
    assert f"chat_session:{session_id}" in channels
    assert f"chat_list:{world['member_id']}" in channels
    session_frame = next(
        f for f in error_frames if f.payload["channel"] == f"chat_session:{session_id}"
    )
    assert session_frame.payload["data"]["code"] == "generation_failed"
    list_frame = next(
        f for f in error_frames if f.payload["channel"] == f"chat_list:{world['member_id']}"
    )
    assert list_frame.payload["data"]["generation_status"] == "failed"


async def test_send_message_reclaim_without_live_execution(session_factory, world):
    """Reclaim also works when no execution row exists (lost before the relay
    materialized it) — the slot frees with no cancel attempt."""
    service = ChatService(session_factory, streaming_stale_seconds=0)
    row = await service.create_session(
        actor=world["member"],
        workspace_id=world["ws_id"],
        agent_id=world["agent_id"],
    )
    session_id = uuid.UUID(row["id"])
    await service.send_message(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        content="第一条",
    )
    await _backdate_streaming_row(
        session_factory,
        ws_id=world["ws_id"],
        session_id=session_id,
        age_seconds=3600,
    )
    second = await service.send_message(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        content="第二条",
    )
    assert second["message_id"]


async def test_stop_generation_degrades_when_redis_unavailable(session_factory, world):
    service = ChatService(session_factory)
    row = await service.create_session(
        actor=world["member"],
        workspace_id=world["ws_id"],
        agent_id=world["agent_id"],
    )
    session_id = uuid.UUID(row["id"])
    sent = await service.send_message(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        content="问题",
    )
    # No relay materialized an execution → stop flips the stale row directly;
    # the buffer read fails (Redis down) and degrades to the persisted body.
    stopped = await service.stop_generation(
        actor=world["member"],
        workspace_id=world["ws_id"],
        session_id=session_id,
        generation_id=uuid.UUID(sent["generation_id"]),
        redis=_BrokenRedis(),
    )
    assert stopped["generation_status"] == "interrupted"
    async with session_factory() as session:
        message = await session.get(ChatMessage, uuid.UUID(sent["message_id"]))
    assert message.generation_status == "interrupted"
    assert message.content == ""  # nothing persisted yet; nothing lost

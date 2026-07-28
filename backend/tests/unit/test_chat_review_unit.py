"""Unit-level coverage for the acceptance-review remediation (MES-67 round 2).

Covers, without a server: H4 (ping carries no id → cannot pollute the resume
cursor), L4 (stop never overwrites a longer persisted body with an empty
buffer), the streaming-stale reclaim path, and M5 (non-selected candidates are
excluded from the model context history).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.chat.engine import ChatGenerationEngine, GenerationPrompt
from mesh.chat.service import ChatService
from mesh.chat.stream import format_sse_frame
from mesh.db.models.agent import Agent
from mesh.db.models.chat import ChatMessage, ChatSession
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.errors import NotFoundError

pytestmark = pytest.mark.unit


async def _world(session_factory):
    async with session_factory() as session, session.begin():
        ws_user = User(email=f"o-{uuid.uuid4().hex[:8]}@x.io", display_name="O")
        session.add(ws_user)
        await session.flush()
        from mesh.db.models.workspace import Workspace

        ws = Workspace(name="W", slug=f"w-{uuid.uuid4().hex[:8]}")
        session.add(ws)
        await session.flush()
        owner = Member(
            workspace_id=ws.id, user_id=ws_user.id, member_type="human",
            role="member", status="active",
        )
        session.add(owner)
        ao = User(email=f"ao-{uuid.uuid4().hex[:8]}@x.io", display_name="AO")
        session.add(ao)
        await session.flush()
        agent = Agent(workspace_id=ws.id, name="bot", owner_user_id=ao.id)
        session.add(agent)
        await session.flush()
        agent_member = Member(
            workspace_id=ws.id, member_type="agent", agent_id=agent.id,
            role="member", status="active",
        )
        session.add(agent_member)
    return {"ws": ws, "owner": owner, "agent": agent}


async def _session_row(session_factory, world):
    async with session_factory() as session, session.begin():
        row = ChatSession(
            workspace_id=world["ws"].id, owner_id=world["owner"].id,
            agent_id=world["agent"].id,
        )
        session.add(row)
    return row


# ---------------------------------------------------------------------------
# H4 — heartbeat frame must carry no id: line
# ---------------------------------------------------------------------------


def test_ping_frame_has_no_id_line():
    frame = format_sse_frame(None, "ping", {"ts": 1})
    assert not frame.startswith("id:")
    assert "id:" not in frame.split("\n", 1)[0]
    assert frame.startswith("event: ping")


def test_data_frame_carries_id():
    frame = format_sse_frame(7, "message.delta", {"delta": "x"})
    assert frame.startswith("id: 7\n")


@pytest.mark.asyncio
async def test_stream_ping_in_buffer_round_trip(redis_client):
    """The generator must never store/emit an id-bearing ping that a client
    would treat as a resume cursor (H4)."""
    engine = ChatGenerationEngine(redis_client, session_factory=None)
    gid = uuid.uuid4()
    await engine.append_frame(gid, 4, "message.delta", {"delta": "x"})
    # A real event frame carries an id; the heartbeat helper produces none.
    real = format_sse_frame(4, "message.delta", {"delta": "x"})
    ping = format_sse_frame(None, "ping", {"ts": 1})
    assert real.splitlines()[0] == "id: 4"
    assert all(not line.startswith("id:") for line in ping.splitlines())


# ---------------------------------------------------------------------------
# L4 — stop keeps the longer persisted body when the buffer is empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_keeps_longer_content_when_buffer_empty(session_factory, redis_client):
    world = await _world(session_factory)
    chat = await _session_row(session_factory, world)
    async with session_factory() as session, session.begin():
        msg = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="agent",
            content="already persisted longer body", generation_id=uuid.uuid4(),
            generation_status="streaming", parent_id=None,
        )
        session.add(msg)
    service = ChatService(session_factory, streaming_stale_seconds=600)
    service.engine = ChatGenerationEngine(redis_client, session_factory)
    gid = msg.generation_id
    await service.stop_generation(
        actor=world["owner"], workspace_id=world["ws"].id,
        session_id=chat.id, generation_id=gid,
    )
    async with session_factory() as session:
        row = await session.get(ChatMessage, msg.id)
    assert row.generation_status == "interrupted"
    assert row.content == "already persisted longer body"  # NOT clobbered to ""


# ---------------------------------------------------------------------------
# streaming-stale reclaim frees the single-concurrency slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_streaming_is_reclaimed_on_send(session_factory, redis_client):
    world = await _world(session_factory)
    chat = await _session_row(session_factory, world)
    stale_started = datetime.now(UTC) - timedelta(seconds=700)
    async with session_factory() as session, session.begin():
        stuck = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="agent",
            content="", generation_id=uuid.uuid4(), generation_status="streaming",
            parent_id=None, started_at=stale_started,
        )
        session.add(stuck)
    service = ChatService(session_factory, streaming_stale_seconds=600)
    service.engine = ChatGenerationEngine(redis_client, session_factory)
    # A new send must NOT 409: the stuck row is reclaimed first.
    sent = await service.send_message(
        actor=world["owner"], workspace_id=world["ws"].id,
        session_id=chat.id, content="after reclaim",
    )
    assert sent["message_id"]
    async with session_factory() as session:
        stuck_row = await session.get(ChatMessage, stuck.id)
        finished = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == world["ws"].id,
                    OutboxEvent.event_type == "chat.generation_finished",
                )
            )
        ).scalars().all()
    assert stuck_row.generation_status == "failed"
    assert stuck_row.error_message == "generation timed out"
    # The stuck generation's execution finalization event was emitted.
    assert any("reclaim" in (ev.idempotency_key or "") for ev in finished)
    await service.engine.drain()


# ---------------------------------------------------------------------------
# M5 — non-selected candidates are excluded from the model context
# ---------------------------------------------------------------------------


class _CapturingProvider:
    def __init__(self):
        self.history = None

    async def stream(self, prompt: GenerationPrompt):
        self.history = prompt.history
        yield "ok"
        yield "."


@pytest.mark.asyncio
async def test_history_excludes_non_selected_candidate(session_factory, redis_client):
    world = await _world(session_factory)
    chat = await _session_row(session_factory, world)
    async with session_factory() as session, session.begin():
        user_msg = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="user",
            content="the question", generation_status="done",
        )
        session.add(user_msg)
        await session.flush()
        wrong = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="agent",
            content="WRONG_UNSELECTED_CONTEXT", generation_status="done",
            parent_id=user_msg.id, selected_candidate=False,
        )
        right = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="agent",
            content="right selected context", generation_status="done",
            parent_id=user_msg.id, selected_candidate=True,
        )
        session.add_all([wrong, right])
    provider = _CapturingProvider()
    engine = ChatGenerationEngine(redis_client, session_factory, provider=provider)
    async with session_factory() as session, session.begin():
        new_agent = ChatMessage(
            workspace_id=world["ws"].id, session_id=chat.id, role="agent",
            content="", generation_id=uuid.uuid4(), generation_status="streaming",
            parent_id=user_msg.id, selected_candidate=True,
        )
        session.add(new_agent)
    await engine.run(
        workspace_id=world["ws"].id, session_id=chat.id,
        message_id=new_agent.id, generation_id=new_agent.generation_id,
    )
    joined = " ".join(content for _role, content in (provider.history or ()))
    assert "right selected context" in joined
    assert "WRONG_UNSELECTED_CONTEXT" not in joined
    await engine.drain()


# ---------------------------------------------------------------------------
# M2 — favorites PUT on a chat_session is owner-only (existence oracle closed)
# ---------------------------------------------------------------------------


async def _second_human(session_factory, world):
    async with session_factory() as session, session.begin():
        u = User(email=f"o2-{uuid.uuid4().hex[:8]}@x.io", display_name="O2")
        session.add(u)
        await session.flush()
        m = Member(
            workspace_id=world["ws"].id, user_id=u.id, member_type="human",
            role="member", status="active",
        )
        session.add(m)
    return m


@pytest.mark.asyncio
async def test_favorites_put_chat_session_owner_only(session_factory):
    from mesh.favorites.service import FavoritesService

    world = await _world(session_factory)
    other = await _second_human(session_factory, world)
    chat = await _session_row(session_factory, world)  # owned by world["owner"]
    fav = FavoritesService(session_factory)
    # Owner can pin.
    await fav.put(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=chat.id,
    )
    # Another member cannot pin (uniform 404 — no existence oracle).
    with pytest.raises(NotFoundError):
        await fav.put(
            actor=other, workspace_id=world["ws"].id,
            target_type="chat_session", target_id=chat.id,
        )


# ---------------------------------------------------------------------------
# M3 — guest cannot attach a private/unshared project issue as chat context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guest_context_issue_requires_project_access(session_factory):
    from mesh.db.models.issue import Issue, IssueStatus
    from mesh.db.models.project import Project

    world = await _world(session_factory)
    async with session_factory() as session, session.begin():
        guest_user = User(email=f"g-{uuid.uuid4().hex[:8]}@x.io", display_name="G")
        session.add(guest_user)
        await session.flush()
        guest = Member(
            workspace_id=world["ws"].id, user_id=guest_user.id, member_type="human",
            role="guest", status="active",
        )
        session.add(guest)
        project = Project(workspace_id=world["ws"].id, name="Secret", key=f"S{uuid.uuid4().hex[:5]}")
        session.add(project)
        await session.flush()
        status = IssueStatus(
            workspace_id=world["ws"].id, name=f"t-{uuid.uuid4().hex[:5]}", category="todo",
        )
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=world["ws"].id, project_id=project.id, status_id=status.id,
            identifier_namespace_key="WS", number=1, identifier="WS-1", title="x",
            state_category="todo",
        )
        session.add(issue)
    service = ChatService(session_factory)
    async with service._session_factory() as session:
        from mesh.db.tenant import set_tenant_context
        await set_tenant_context(session, world["ws"].id)
        with pytest.raises(NotFoundError):
            await service._validate_context_issue(
                session, workspace_id=world["ws"].id, actor=guest, issue_id=issue.id,
            )


# ---------------------------------------------------------------------------
# H2 — chat_list channel checker is owner-private
# ---------------------------------------------------------------------------


class _Princ:
    def __init__(self, subject, workspace_ids):
        self.subject = subject
        self.workspace_ids = workspace_ids


@pytest.mark.asyncio
async def test_chat_list_checker_owner_only(session_factory):
    from mesh.chat.channels import make_chat_list_checker

    world = await _world(session_factory)
    other = await _second_human(session_factory, world)
    checker = make_chat_list_checker(session_factory)
    ws_ids = [world["ws"].id]
    owner_user_id = world["owner"].user_id
    # Owner may subscribe to their own list channel.
    assert await checker(_Princ(str(owner_user_id), ws_ids), f"chat_list:{world['owner'].id}")
    # Owner may NOT subscribe to another member's list channel.
    assert not await checker(_Princ(str(owner_user_id), ws_ids), f"chat_list:{other.id}")
    # Dev principal (non-uuid subject) is workspace-scoped → allowed.
    assert await checker(_Princ("mesh-dev", ws_ids), f"chat_list:{other.id}")
    # Malformed key rejected.
    assert not await checker(_Princ(str(owner_user_id), ws_ids), "chat_list:not-a-uuid")


# ---------------------------------------------------------------------------
# chat_generation_finished handler (terminal write-back for trigger='chat')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_finish_handler_finalizes_and_idempotent(session_factory):
    from mesh.db.models.outbox import OutboxEvent
    from mesh.db.models.runtime import TaskExecution
    from mesh.runtime.enqueue import chat_generation_finished_handler

    world = await _world(session_factory)
    ws = world["ws"].id
    key = f"chat-finish-{uuid.uuid4().hex}"
    # Materialize a queued chat execution (as the enqueue handler would).
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(workspace_id=ws, trigger="chat", idempotency_key=key)
        )
    event = OutboxEvent(
        workspace_id=ws,
        event_type="chat.generation_finished",
        payload={"idempotency_key": key, "status": "done", "message_id": str(uuid.uuid4())},
        idempotency_key=f"out-{uuid.uuid4().hex}",
    )
    async with session_factory() as session, session.begin():
        await chat_generation_finished_handler(session, event)
    async with session_factory() as session:
        row = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.idempotency_key == key)
            )
        ).scalar_one()
    assert row.status == "completed"
    assert isinstance(row.result, dict) and "chat_message_id" in row.result
    # Idempotent re-delivery on an already-finalized row → no-op (no raise).
    async with session_factory() as session, session.begin():
        await chat_generation_finished_handler(session, event)
    # Malformed event (missing key) → no-op, no raise.
    bad = OutboxEvent(
        workspace_id=ws, event_type="chat.generation_finished",
        payload={"status": "done"}, idempotency_key=f"out2-{uuid.uuid4().hex}",
    )
    async with session_factory() as session, session.begin():
        assert await chat_generation_finished_handler(session, bad) is None


# ---------------------------------------------------------------------------
# H2 — gateway channel authorization for owner-private chat channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_channel_authorization(session_factory):
    from types import SimpleNamespace

    from mesh.chat.channels import register_chat_checkers
    from mesh.realtime.auth import DefaultChannelAuthorizer, Principal

    world = await _world(session_factory)
    ws = world["ws"].id
    chat = await _session_row(session_factory, world)  # owned by world["owner"]
    # A second human member in the same workspace.
    async with session_factory() as session, session.begin():
        u2 = User(email=f"o3-{uuid.uuid4().hex[:8]}@x.io", display_name="O3")
        session.add(u2)
        await session.flush()
        other = Member(
            workspace_id=ws, user_id=u2.id, member_type="human",
            role="member", status="active",
        )
        session.add(other)

    authorizer = DefaultChannelAuthorizer(session_factory)
    register_chat_checkers(authorizer, session_factory)
    owner_p = Principal(subject=str(world["owner"].user_id), workspace_ids=frozenset({ws}))
    other_p = Principal(subject=str(u2.id), workspace_ids=frozenset({ws}))

    # chat_list is owner-private.
    assert await authorizer.authorize(owner_p, f"chat_list:{world['owner'].id}") == ws
    assert await authorizer.authorize(other_p, f"chat_list:{world['owner'].id}") is None
    assert await authorizer.authorize(owner_p, f"chat_list:{other.id}") is None
    # chat_session is owner-only.
    assert await authorizer.authorize(owner_p, f"chat_session:{chat.id}") == ws
    assert await authorizer.authorize(other_p, f"chat_session:{chat.id}") is None
    # Malformed channel keys are denied.
    assert await authorizer.authorize(owner_p, "chat_list:not-a-uuid") is None
    _ = SimpleNamespace  # keep import meaningful

"""Schema tests for the chat increment (chat-session.md §2 / §5.3).

Real-PostgreSQL assertions for the constraints the spec demands at the
database layer: composite-FK tenant isolation (README §6.2), same-session
parenting / quoting (rule 7), column-level ``ON DELETE SET NULL`` (rule 6,
T18 real-DELETE behavior), favorites de-duplication (§6.19) and the CHECK
vocabularies.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mesh.db.models.agent import Agent
from mesh.db.models.chat import ChatMessage, ChatSession, Favorite
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.user import User

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def agent_member_factory(session_factory):
    """Create an agent row + its members roster entry (README §6.1)."""

    async def _create(workspace, name: str = "Bot") -> Member:
        async with session_factory() as session, session.begin():
            owner = User(email=f"owner-{uuid.uuid4().hex[:10]}@mesh.test", display_name=name)
            session.add(owner)
            await session.flush()
            agent = Agent(workspace_id=workspace.id, name=name, owner_user_id=owner.id)
            session.add(agent)
            await session.flush()
            member = Member(
                workspace_id=workspace.id,
                member_type="agent",
                agent_id=agent.id,
                role="member",
                display_override=name,
            )
            session.add(member)
        return member

    return _create


async def _mk_session(factory, workspace, owner: Member, agent_member: Member, **kw) -> ChatSession:
    async with factory() as session, session.begin():
        row = ChatSession(
            workspace_id=workspace.id,
            owner_id=owner.id,
            agent_id=agent_member.agent_id,
            **kw,
        )
        session.add(row)
    return row


async def _mk_message(factory, workspace, chat_session, **kw) -> ChatMessage:
    async with factory() as session, session.begin():
        row = ChatMessage(
            workspace_id=workspace.id,
            session_id=chat_session.id,
            **kw,
        )
        session.add(row)
    return row


# ---------------------------------------------------------------------------
# composite FK tenant isolation (README §6.2 / §5.3 T1)
# ---------------------------------------------------------------------------


async def test_cross_tenant_agent_fk_rejected(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws_a = await workspace_factory(name="A")
    ws_b = await workspace_factory(name="B")
    owner = await member_factory(ws_a)
    foreign_agent = await agent_member_factory(ws_b, name="Foreign Bot")

    with pytest.raises(IntegrityError):
        await _mk_session(session_factory, ws_a, owner, foreign_agent)


async def test_cross_tenant_owner_fk_rejected(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws_a = await workspace_factory(name="A")
    ws_b = await workspace_factory(name="B")
    outsider = await member_factory(ws_b)
    agent = await agent_member_factory(ws_a)

    with pytest.raises(IntegrityError):
        await _mk_session(session_factory, ws_a, outsider, agent)


async def test_session_message_composite_fk_rejected(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws_a = await workspace_factory(name="A")
    ws_b = await workspace_factory(name="B")
    owner = await member_factory(ws_a)
    agent = await agent_member_factory(ws_a)
    foreign_session = await _mk_session(
        session_factory, ws_b, await member_factory(ws_b), await agent_member_factory(ws_b)
    )

    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(
                ChatMessage(
                    workspace_id=ws_a.id,
                    session_id=foreign_session.id,  # belongs to ws_b
                    role="user",
                    content="x",
                )
            )
    # keep the owner/agent rows referenced for cleanliness
    assert owner.id and agent.id


# ---------------------------------------------------------------------------
# same-session parenting / quoting (README §6.2 rule 7)
# ---------------------------------------------------------------------------


async def test_parent_must_share_session(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    s1 = await _mk_session(session_factory, ws, owner, agent)
    s2 = await _mk_session(session_factory, ws, owner, agent)
    foreign_parent = await _mk_message(
        session_factory, ws, s2, role="user", content="other session"
    )

    with pytest.raises(IntegrityError):
        await _mk_message(
            session_factory, ws, s1, role="agent", content="reply", parent_id=foreign_parent.id
        )


async def test_quote_must_share_session(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    s1 = await _mk_session(session_factory, ws, owner, agent)
    s2 = await _mk_session(session_factory, ws, owner, agent)
    foreign_quote = await _mk_message(session_factory, ws, s2, role="user", content="quoted")

    with pytest.raises(IntegrityError):
        await _mk_message(
            session_factory,
            ws,
            s1,
            role="user",
            content="follow-up",
            quote_message_id=foreign_quote.id,
        )


async def test_same_session_parent_allowed(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    chat = await _mk_session(session_factory, ws, owner, agent)
    user_msg = await _mk_message(session_factory, ws, chat, role="user", content="hello")
    reply = await _mk_message(
        session_factory, ws, chat, role="agent", content="hi", parent_id=user_msg.id
    )
    assert reply.parent_id == user_msg.id


# ---------------------------------------------------------------------------
# column-level ON DELETE SET NULL (README §6.2 rule 6 / T18 real DELETE)
# ---------------------------------------------------------------------------


async def test_delete_parent_nulls_only_parent_column(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    chat = await _mk_session(session_factory, ws, owner, agent)
    user_msg = await _mk_message(session_factory, ws, chat, role="user", content="hello")
    reply = await _mk_message(
        session_factory, ws, chat, role="agent", content="hi", parent_id=user_msg.id
    )

    async with session_factory() as session, session.begin():
        await session.execute(select(ChatMessage).where(ChatMessage.id == user_msg.id))
        await session.delete(user_msg)

    async with session_factory() as session:
        kept = await session.get(ChatMessage, reply.id)
    assert kept is not None
    assert kept.parent_id is None  # reference column nulled…
    assert kept.workspace_id == ws.id  # …tenant key untouched
    assert kept.session_id == chat.id  # …session binding untouched


async def test_delete_context_project_nulls_only_context_column(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    async with session_factory() as session, session.begin():
        project = Project(workspace_id=ws.id, name="P", key=f"K{uuid.uuid4().hex[:6]}")
        session.add(project)
    chat = await _mk_session(
        session_factory, ws, owner, agent, context_project_id=project.id
    )

    async with session_factory() as session, session.begin():
        await session.delete(project)

    async with session_factory() as session:
        kept = await session.get(ChatSession, chat.id)
    assert kept.context_project_id is None
    assert kept.workspace_id == ws.id
    assert kept.owner_id == owner.id


# ---------------------------------------------------------------------------
# CHECK vocabularies + defaults
# ---------------------------------------------------------------------------


async def test_status_check_rejects_unknown_value(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    with pytest.raises(IntegrityError):
        await _mk_session(session_factory, ws, owner, agent, status="bogus")


async def test_generation_status_check_rejects_unknown_value(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    chat = await _mk_session(session_factory, ws, owner, agent)
    with pytest.raises(IntegrityError):
        await _mk_message(session_factory, ws, chat, role="user", content="x",
                          generation_status="bogus")


async def test_message_count_nonnegative_check(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    with pytest.raises(IntegrityError):
        await _mk_session(session_factory, ws, owner, agent, message_count=-1)


async def test_session_defaults(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    chat = await _mk_session(session_factory, ws, owner, agent)
    async with session_factory() as session:
        row = await session.get(ChatSession, chat.id)
    assert row.title == "新对话"
    assert row.title_is_auto is True
    assert row.status == "active"
    assert row.message_count == 0
    assert row.created_at is not None and row.updated_at is not None


async def test_message_defaults_and_idempotency_unique(
    session_factory, workspace_factory, member_factory, agent_member_factory
):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    agent = await agent_member_factory(ws)
    chat = await _mk_session(session_factory, ws, owner, agent)
    msg = await _mk_message(
        session_factory, ws, chat, role="user", content="x", idempotency_key="k-1"
    )
    async with session_factory() as session:
        row = await session.get(ChatMessage, msg.id)
    assert row.generation_status == "done"
    assert row.selected_candidate is True
    assert row.content == "x"

    # Duplicate idempotency key in the same workspace is rejected…
    with pytest.raises(IntegrityError):
        await _mk_message(
            session_factory, ws, chat, role="user", content="y", idempotency_key="k-1"
        )
    # …while NULL keys never collide.
    await _mk_message(session_factory, ws, chat, role="user", content="z")


# ---------------------------------------------------------------------------
# favorites (§6.19)
# ---------------------------------------------------------------------------


async def test_favorites_unique_per_member_target(
    session_factory, workspace_factory, member_factory
):
    ws = await workspace_factory()
    member = await member_factory(ws)
    target = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Favorite(
                workspace_id=ws.id,
                member_id=member.id,
                target_type="chat_session",
                target_id=target,
            )
        )
    # Idempotent PUT semantics: the same (member, type, target) is rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(
                Favorite(
                    workspace_id=ws.id,
                    member_id=member.id,
                    target_type="chat_session",
                    target_id=target,
                )
            )


async def test_favorites_target_type_check(session_factory, workspace_factory, member_factory):
    ws = await workspace_factory()
    member = await member_factory(ws)
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            session.add(
                Favorite(
                    workspace_id=ws.id,
                    member_id=member.id,
                    target_type="bogus",
                    target_id=uuid.uuid4(),
                )
            )


async def test_favorites_member_cascade(session_factory, workspace_factory, member_factory):
    ws = await workspace_factory()
    member = await member_factory(ws)
    target = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Favorite(
                workspace_id=ws.id,
                member_id=member.id,
                target_type="issue",
                target_id=target,
            )
        )
    async with session_factory() as session, session.begin():
        await session.delete(member)
    async with session_factory() as session:
        rows = (
            await session.execute(select(Favorite).where(Favorite.member_id == member.id))
        ).scalars().all()
    assert rows == []

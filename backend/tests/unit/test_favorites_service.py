"""Direct service-level coverage for the favorites module (README §6.19)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.chat.service import ChatService
from mesh.db.models.chat import Favorite
from mesh.errors import NotFoundError, ValidationError
from mesh.favorites.service import FavoritesService

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def world(session_factory, workspace_factory, member_factory):
    ws = await workspace_factory()
    owner = await member_factory(ws)
    from mesh.db.models.agent import Agent
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        agent_owner = User(email=f"fo-{uuid.uuid4().hex[:8]}@x.io", display_name="FO")
        session.add(agent_owner)
        await session.flush()
        agent = Agent(workspace_id=ws.id, name="fav-bot", owner_user_id=agent_owner.id)
        session.add(agent)
        await session.flush()
        session.add(
            Member(workspace_id=ws.id, member_type="agent", agent_id=agent.id, role="member")
        )
    chat_service = ChatService(session_factory)
    session_ids = []
    for _ in range(3):
        row = await chat_service.create_session(
            actor=owner, workspace_id=ws.id, agent_id=agent.id
        )
        session_ids.append(uuid.UUID(row["id"]))
    return {"ws": ws, "owner": owner, "session_ids": session_ids, "chat": chat_service}


@pytest_asyncio.fixture
async def service(session_factory):
    return FavoritesService(session_factory)


async def test_put_idempotent_and_rendered(service, world):
    target = world["session_ids"][0]
    first = await service.put(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    assert first["target_type"] == "chat_session"
    assert first["target_id"] == str(target)
    second = await service.put(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    assert second["id"] == first["id"]  # same row — PUT is idempotent


async def test_put_validates_type_and_target(service, world):
    with pytest.raises(ValidationError):
        await service.put(
            actor=world["owner"], workspace_id=world["ws"].id,
            target_type="bogus", target_id=uuid.uuid4(),
        )
    with pytest.raises(NotFoundError):
        await service.put(
            actor=world["owner"], workspace_id=world["ws"].id,
            target_type="chat_session", target_id=uuid.uuid4(),
        )
    # soft-deleted target is dead → 404
    dead = world["session_ids"][2]
    await world["chat"].delete_session(
        actor=world["owner"], workspace_id=world["ws"].id, session_id=dead
    )
    with pytest.raises(NotFoundError):
        await service.put(
            actor=world["owner"], workspace_id=world["ws"].id,
            target_type="chat_session", target_id=dead,
        )


async def test_remove_idempotent(service, world):
    target = world["session_ids"][0]
    await service.put(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    await service.remove(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    # second removal is a silent no-op
    await service.remove(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    with pytest.raises(ValidationError):
        await service.remove(
            actor=world["owner"], workspace_id=world["ws"].id,
            target_type="bogus", target_id=target,
        )


async def test_list_filter_cursor_and_pruning(service, world, session_factory):
    ids = world["session_ids"]
    for target in ids:
        await service.put(
            actor=world["owner"], workspace_id=world["ws"].id,
            target_type="chat_session", target_id=target,
        )
    page = await service.list(actor=world["owner"], workspace_id=world["ws"].id, limit=2)
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    page2 = await service.list(
        actor=world["owner"], workspace_id=world["ws"].id, limit=2,
        cursor=page["next_cursor"],
    )
    assert len(page2["items"]) == 1
    seen = {item["target_id"] for item in page["items"] + page2["items"]}
    assert seen == {str(target) for target in ids}
    # target_type filter + invalid filter + invalid cursor
    filtered = await service.list(
        actor=world["owner"], workspace_id=world["ws"].id, target_type="issue"
    )
    assert filtered["items"] == []
    with pytest.raises(ValidationError):
        await service.list(
            actor=world["owner"], workspace_id=world["ws"].id, target_type="bogus"
        )
    with pytest.raises(ValidationError):
        await service.list(actor=world["owner"], workspace_id=world["ws"].id, cursor="bogus")
    # dead targets are pruned from listings (§6.19)
    await world["chat"].delete_session(
        actor=world["owner"], workspace_id=world["ws"].id, session_id=ids[0]
    )
    pruned = await service.list(actor=world["owner"], workspace_id=world["ws"].id)
    assert str(ids[0]) not in {item["target_id"] for item in pruned["items"]}
    assert len(pruned["items"]) == 2
    # other workspace members' rows never leak in
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(email=f"other-{uuid.uuid4().hex[:6]}@x.io", display_name="O")
        session.add(user)
        await session.flush()
        other = Member(
            workspace_id=world["ws"].id, user_id=user.id, member_type="human",
            role="member", status="active",
        )
        session.add(other)
    theirs = await service.list(actor=other, workspace_id=world["ws"].id)
    assert theirs["items"] == []


async def test_cleanup_for_target(service, world):
    target = world["session_ids"][0]
    await service.put(
        actor=world["owner"], workspace_id=world["ws"].id,
        target_type="chat_session", target_id=target,
    )
    async with service._session_factory() as session, session.begin():
        await service.cleanup_for_target(
            session, workspace_id=world["ws"].id,
            target_type="chat_session", target_id=target,
        )
    async with service._session_factory() as session:
        rows = (
            await session.execute(
                select(Favorite).where(Favorite.target_id == target)
            )
        ).scalars().all()
    assert rows == []

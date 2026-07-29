"""members.search_name sync contract (search-command-palette.md §2.2).

Writes go through the single ``sync_member_search_name`` function; renames
recompute every member row of the identity (across workspaces); the daily
reconcile repairs drift.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.search.projection import (
    reconcile_search_names,
    recompute_for_agent,
    recompute_for_user,
    sync_member_search_name,
)

pytestmark = pytest.mark.unit


async def _workspace(session: AsyncSession, slug: str) -> Workspace:
    ws = Workspace(name=f"ws-{slug}", slug=slug)
    session.add(ws)
    await session.flush()
    return ws


async def _human_member(
    session: AsyncSession, ws: Workspace, *, name: str, email: str
) -> tuple[Member, User]:
    user = User(email=email, display_name=name)
    session.add(user)
    await session.flush()
    member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role="member")
    session.add(member)
    await session.flush()
    return member, user


async def _agent_member(
    session: AsyncSession, ws: Workspace, *, name: str, owner: User
) -> tuple[Member, Agent]:
    agent = Agent(workspace_id=ws.id, name=name, owner_user_id=owner.id)
    session.add(agent)
    await session.flush()
    member = Member(workspace_id=ws.id, member_type="agent", agent_id=agent.id, role="member")
    session.add(member)
    await session.flush()
    return member, agent


async def test_sync_member_search_name_human_chain(db_session):
    ws = await _workspace(db_session, "proj-sync-1")
    member, user = await _human_member(
        db_session, ws, name="José Àncône", email="jose@corp.example"
    )
    assert member.search_name == ""  # nothing synced yet

    await sync_member_search_name(db_session, member.id)
    await db_session.flush()
    await db_session.refresh(member)
    assert member.search_name == "jose ancone"

    # display_override wins over the user chain. The resync reads row state,
    # so the override must be flushed first (service paths do this).
    member.display_override = "ZHANG Wei"
    await db_session.flush()
    await sync_member_search_name(db_session, member.id)
    await db_session.flush()
    await db_session.refresh(member)
    assert member.search_name == "zhang wei"
    assert user.display_name == "José Àncône"  # display truth untouched


async def test_sync_member_search_name_agent(db_session):
    ws = await _workspace(db_session, "proj-sync-2")
    _, owner = await _human_member(db_session, ws, name="Owner", email="o@corp.example")
    member, _ = await _agent_member(db_session, ws, name="代码助手", owner=owner)

    await sync_member_search_name(db_session, member.id)
    await db_session.flush()
    await db_session.refresh(member)
    assert member.search_name == "代码助手"


async def test_recompute_for_user_updates_all_workspaces(db_session):
    ws_a = await _workspace(db_session, "proj-sync-3a")
    ws_b = await _workspace(db_session, "proj-sync-3b")
    user = User(email="renamed@corp.example", display_name="Before Name")
    db_session.add(user)
    await db_session.flush()
    rows = [
        Member(workspace_id=ws_a.id, member_type="human", user_id=user.id, role="member"),
        Member(workspace_id=ws_b.id, member_type="human", user_id=user.id, role="admin"),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    user.display_name = "After Rename"
    await db_session.flush()
    fixed = await recompute_for_user(db_session, user.id)
    await db_session.flush()

    assert fixed == 2
    synced = (
        (
            await db_session.execute(
                select(Member.search_name).where(Member.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert synced == ["after rename", "after rename"]


async def test_recompute_for_agent(db_session):
    ws = await _workspace(db_session, "proj-sync-4")
    _, owner = await _human_member(db_session, ws, name="Owner", email="o4@corp.example")
    member, agent = await _agent_member(db_session, ws, name="Old Bot", owner=owner)

    agent.name = "New Bot"
    await db_session.flush()
    fixed = await recompute_for_agent(db_session, agent.id)
    await db_session.flush()
    await db_session.refresh(member)
    assert fixed == 1
    assert member.search_name == "new bot"


async def test_reconcile_repairs_drift(db_session):
    ws = await _workspace(db_session, "proj-sync-5")
    member, _ = await _human_member(db_session, ws, name="Drift Case", email="d@corp.example")
    await sync_member_search_name(db_session, member.id)
    await db_session.flush()

    # Corrupt the projection behind the service's back.
    await db_session.execute(
        text("UPDATE members SET search_name = 'stale' WHERE id = :mid"),
        {"mid": member.id},
    )
    await db_session.commit()

    fixed = await reconcile_search_names(db_session)
    assert fixed >= 1
    fresh = (
        await db_session.execute(select(Member.search_name).where(Member.id == member.id))
    ).scalar_one()
    assert fresh == "drift case"


async def test_resync_unknown_kind_rejected(db_session):
    with pytest.raises(Exception):
        await db_session.execute(
            text("SELECT public.mesh_resync_search_name('bogus', NULL)")
        )


async def test_unknown_member_sync_is_noop(db_session):
    fixed = await sync_member_search_name(db_session, uuid.uuid4())
    assert fixed == 0

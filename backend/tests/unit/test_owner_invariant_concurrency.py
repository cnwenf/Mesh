"""Concurrency regression for the owner invariant (MES-35 MB-M2).

Pre-fix, the active-owner count was a plain READ COMMITTED SELECT: two
concurrent "leave one owner behind" operations could both read count=2 and
both commit, leaving zero active owners. These tests fire real concurrent
transactions at real PostgreSQL and assert exactly one operation is rejected
and the invariant (>= 1 active owner) holds afterwards — for every path that
reduces the active-owner count (demote / remove / disable) and a mixed pair.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text

from mesh.db.models.member import Member
from mesh.errors import ConflictError
from mesh.member.service import MemberPatch, MemberService
from mesh.workspace.members import change_member_role

pytestmark = pytest.mark.unit

BARRIER_PARTIES = 2


async def _two_owner_workspace(session_factory):
    """Workspace with exactly two active human owners; returns ids + actor."""
    async with session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Race WS', :s) RETURNING id"
                ),
                {"s": f"race-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
        owner_ids = []
        for i in range(BARRIER_PARTIES):
            user_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (email, display_name) "
                        "VALUES (:e, :n) RETURNING id"
                    ),
                    {"e": f"race-owner{i}-{uuid.uuid4().hex[:8]}@corp.com", "n": f"O{i}"},
                )
            ).scalar_one()
            owner_ids.append(
                (
                    await session.execute(
                        text(
                            "INSERT INTO members (workspace_id, member_type, user_id, role) "
                            "VALUES (:ws, 'human', :u, 'owner') RETURNING id"
                        ),
                        {"ws": workspace_id, "u": user_id},
                    )
                ).scalar_one()
            )
    async with session_factory() as session:
        actor = await session.scalar(select(Member).where(Member.id == owner_ids[0]))
    return workspace_id, owner_ids, actor


async def _active_owner_count(session_factory, workspace_id) -> int:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count(Member.id)).where(
                Member.workspace_id == workspace_id,
                Member.role == "owner",
                Member.status == "active",
            )
        )


async def _race(op_a, op_b):
    """Run two coroutines concurrently behind a barrier; classify outcomes."""
    barrier = asyncio.Barrier(BARRIER_PARTIES)

    async def guarded(op):
        await barrier.wait()
        try:
            await op()
            return "ok"
        except ConflictError as exc:
            assert exc.code == "last_owner"
            return "conflict"

    return await asyncio.gather(guarded(op_a), guarded(op_b))


async def test_concurrent_removals_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.remove_member(actor=actor, workspace_id=workspace_id, member_id=o1),
        lambda: service.remove_member(actor=actor, workspace_id=workspace_id, member_id=o2),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_demotions_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    results = await _race(
        lambda: change_member_role(
            session_factory, actor=actor, workspace_id=workspace_id,
            member_id=o1, new_role="member",
        ),
        lambda: change_member_role(
            session_factory, actor=actor, workspace_id=workspace_id,
            member_id=o2, new_role="admin",
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_disables_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o1,
            patch=MemberPatch(status="disabled"),
        ),
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o2,
            patch=MemberPatch(status="disabled"),
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_mixed_remove_and_disable_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.remove_member(actor=actor, workspace_id=workspace_id, member_id=o1),
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o2,
            patch=MemberPatch(status="disabled"),
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_owner_ops_across_workspaces_do_not_interfere(session_factory):
    """Locking is per-workspace: removals in separate workspaces both succeed."""
    ws_a, (a1, _a2), actor_a = await _two_owner_workspace(session_factory)
    ws_b, (b1, _b2), actor_b = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await asyncio.gather(
        service.remove_member(actor=actor_a, workspace_id=ws_a, member_id=a1),
        service.remove_member(actor=actor_b, workspace_id=ws_b, member_id=b1),
    )
    assert all(r["removed"] for r in results)
    assert await _active_owner_count(session_factory, ws_a) == 1
    assert await _active_owner_count(session_factory, ws_b) == 1

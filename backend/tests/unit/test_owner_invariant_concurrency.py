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
from mesh.errors import ConflictError, NotFoundError
from mesh.member.service import MemberPatch, MemberService
from mesh.workspace.members import change_member_role

pytestmark = pytest.mark.unit

BARRIER_PARTIES = 2
RACE_ITERATIONS = 10
LOCK_BLOCK_PROBE_SECONDS = 0.3


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


async def _owner_plus_member_workspace(session_factory):
    """Workspace with one active owner + one plain member; returns ids + actor."""
    async with session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Promo WS', :s) RETURNING id"
                ),
                {"s": f"promo-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
        ids = {}
        for name, role in (("owner", "owner"), ("member", "member")):
            user_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (email, display_name) "
                        "VALUES (:e, :n) RETURNING id"
                    ),
                    {"e": f"promo-{name}-{uuid.uuid4().hex[:8]}@corp.com", "n": name},
                )
            ).scalar_one()
            ids[name] = (
                await session.execute(
                    text(
                        "INSERT INTO members (workspace_id, member_type, user_id, role) "
                        "VALUES (:ws, 'human', :u, :r) RETURNING id"
                    ),
                    {"ws": workspace_id, "u": user_id, "r": role},
                )
            ).scalar_one()
    async with session_factory() as session:
        actor = await session.scalar(select(Member).where(Member.id == ids["owner"]))
    return workspace_id, ids["owner"], ids["member"], actor


async def test_lock_waits_for_in_flight_promotion_and_counts_new_owner(session_factory):
    """Deterministic proof the gate decision is made AFTER the lock: while an
    uncommitted promotion holds the target's row lock, the owner-set sweep
    blocks; once it commits, the sweep sees the fresh owner role and counts it
    — a reducer can never read a stale 'member' and skip the guard."""
    from mesh.member.owner_guard import lock_active_owner_set

    workspace_id, _owner_id, member_id, _actor = await _owner_plus_member_workspace(
        session_factory
    )

    # S1: promote the member, keep the transaction open (row lock held).
    s1 = session_factory()
    await s1.begin()
    await s1.execute(
        text("UPDATE members SET role = 'owner' WHERE id = :id"), {"id": member_id}
    )

    async def sweep():
        async with session_factory() as s2, s2.begin():
            return await lock_active_owner_set(
                s2, workspace_id=workspace_id, target_id=member_id
            )

    task = asyncio.create_task(sweep())
    await asyncio.sleep(LOCK_BLOCK_PROBE_SECONDS)
    assert not task.done(), "sweep must block on the in-flight promotion's row lock"

    await s1.commit()
    await s1.close()
    count, target = await task
    assert count == 2  # the original owner + the just-committed promoted member
    assert target is not None
    assert target.role == "owner"


async def test_remove_waits_for_in_flight_removal_then_404(session_factory):
    """Post-lock refresh: when a concurrent transaction removes our target and
    commits, our operation re-reads 'removed' under the lock and 404s instead
    of double-writing audit/events over the removed row."""
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    s1 = session_factory()
    await s1.begin()
    await s1.execute(
        text("UPDATE members SET status = 'removed' WHERE id = :id"), {"id": o2}
    )

    service = MemberService(session_factory)

    async def remove_o2():
        return await service.remove_member(
            actor=actor, workspace_id=workspace_id, member_id=o2
        )

    task = asyncio.create_task(remove_o2())
    await asyncio.sleep(LOCK_BLOCK_PROBE_SECONDS)
    assert not task.done(), "remove must block on the in-flight removal's row lock"

    await s1.commit()
    await s1.close()
    with pytest.raises(NotFoundError):
        await task
    assert await _active_owner_count(session_factory, workspace_id) == 1
    assert o1 != o2


async def test_disable_waits_for_in_flight_removal_then_404(session_factory):
    """Same post-lock 404 on the status-change path: a disable racing a
    committed removal must not resurrect the removed row."""
    workspace_id, owner_id, member_id, actor = await _owner_plus_member_workspace(
        session_factory
    )
    s1 = session_factory()
    await s1.begin()
    await s1.execute(
        text("UPDATE members SET status = 'removed' WHERE id = :id"), {"id": member_id}
    )

    service = MemberService(session_factory)

    async def disable_member():
        return await service.update_member(
            actor=actor,
            workspace_id=workspace_id,
            member_id=member_id,
            patch=MemberPatch(status="disabled"),
        )

    task = asyncio.create_task(disable_member())
    await asyncio.sleep(LOCK_BLOCK_PROBE_SECONDS)
    assert not task.done(), "disable must block on the in-flight removal's row lock"

    await s1.commit()
    await s1.close()
    with pytest.raises(NotFoundError):
        await task
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == member_id))
    assert fresh.status == "removed"  # not resurrected to 'disabled'
    assert owner_id != member_id


async def _reduce_vs_promote_iteration(session_factory, service) -> int:
    """One barrier-synchronized promote + disable + remove race; returns the
    active-owner count left behind (the invariant: always >= 1)."""
    workspace_id, owner_id, member_id, actor = await _owner_plus_member_workspace(
        session_factory
    )
    barrier = asyncio.Barrier(3)

    async def promote():
        await barrier.wait()
        return await change_member_role(
            session_factory,
            actor=actor,
            workspace_id=workspace_id,
            member_id=member_id,
            new_role="owner",
        )

    async def disable():
        await barrier.wait()
        return await service.update_member(
            actor=actor,
            workspace_id=workspace_id,
            member_id=member_id,
            patch=MemberPatch(status="disabled"),
        )

    async def remove_owner():
        await barrier.wait()
        return await service.remove_member(
            actor=actor, workspace_id=workspace_id, member_id=owner_id
        )

    results = await asyncio.gather(
        promote(), disable(), remove_owner(), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            assert isinstance(result, (ConflictError, NotFoundError)), (
                f"unexpected exception in race: {result!r}"
            )
    return await _active_owner_count(session_factory, workspace_id)


async def test_reduce_vs_promote_race_never_zeroes_active_owners(session_factory):
    """Reduce-vs-promote interleaving regression (code-review finding): a
    disable/remove racing a concurrent promotion of the SAME member must never
    leave zero active owners, over many barrier-synchronized iterations."""
    service = MemberService(session_factory)
    for _ in range(RACE_ITERATIONS):
        remaining = await _reduce_vs_promote_iteration(session_factory, service)
        assert remaining >= 1, f"owner invariant violated: {remaining} active owners"

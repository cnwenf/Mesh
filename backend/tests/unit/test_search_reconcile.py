"""Daily search_name reconcile loop (search-command-palette.md §2.2 周期对账).

The loop is the drift backstop: corrupt the projection behind the service's
back, run the loop, assert the repair; a failing session factory must not
kill the loop. M12 — dedicated coverage for search_reconcile.py.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.workers.search_reconcile import DEFAULT_INTERVAL_SECONDS, search_reconcile_loop

pytestmark = pytest.mark.unit


async def _seed_drift(session_factory):
    """Create a member whose projection is corrupted; return (member_id, fresh)."""
    async with session_factory() as session, session.begin():
        ws = Workspace(name="reconcile-ws", slug="reconcile-ws")
        session.add(ws)
        await session.flush()
        user = User(email="drift@reconcile.example", display_name="漂移前名字")
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=ws.id, member_type="human", user_id=user.id, role="member"
        )
        session.add(member)
        await session.flush()
        member_id = member.id
        # Sync correctly, then corrupt behind the service's back.
        await session.execute(
            text("SELECT public.mesh_resync_search_name('member', :mid)"),
            {"mid": member_id},
        )
        await session.execute(
            text("UPDATE members SET search_name = 'stale-garbage' WHERE id = :mid"),
            {"mid": member_id},
        )
    async with session_factory() as session:
        fresh = (
            await session.execute(text("SELECT public.mesh_search_norm('漂移前名字')"))
        ).scalar_one()
        stale = (
            await session.execute(select(Member.search_name).where(Member.id == member_id))
        ).scalar_one()
    assert stale == "stale-garbage"
    return member_id, fresh


async def test_reconcile_loop_repairs_drift(db_url):
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    member_id, fresh = await _seed_drift(session_factory)

    stop = asyncio.Event()
    task = asyncio.create_task(
        search_reconcile_loop(session_factory, interval=0.05, stop=stop)
    )
    current = None
    for _ in range(200):
        await asyncio.sleep(0.02)
        async with session_factory() as session:
            current = (
                await session.execute(select(Member.search_name).where(Member.id == member_id))
            ).scalar_one()
        if current == fresh:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert current == fresh, "reconcile loop did not repair the corrupted projection"
    await engine.dispose()


async def test_reconcile_loop_swallows_iteration_errors(db_url):
    """A failing iteration must not kill the loop (worker resilience)."""
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    calls = {"n": 0}

    def flaky_factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated transient failure")
        return session_factory()

    stop = asyncio.Event()
    task = asyncio.create_task(search_reconcile_loop(flaky_factory, interval=0.05, stop=stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert calls["n"] >= 2, "loop died after the first failing iteration"
    await engine.dispose()


def test_reconcile_default_interval_is_daily():
    assert DEFAULT_INTERVAL_SECONDS == 86400.0

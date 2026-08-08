"""Notification auto-archive sweep tests (README §6.13 分组与归档).

Real DB. The sweep archives groups that are already read AND stale
(both created_at and updated_at predate the cutoff). Unread rows, fresh
rows, and already-archived rows are untouched. Workspace-agnostic (owner
role), bounded batch, idempotent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.workers.notification_archive import (
    archive_read_expired_notifications,
    notification_archive_loop,
)

pytestmark = pytest.mark.unit

NOW = datetime.now(UTC).replace(microsecond=0)
RETENTION = timedelta(days=7)


async def _workspace(factory) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _human(factory, workspace) -> Member:
    async with factory() as session, session.begin():
        user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.io", display_name="U")
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="member"
        )
        session.add(member)
    return member


async def _notify(factory, workspace, member, *, read_at, created_at, updated_at=None,
                  archived_at=None) -> Notification:
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        row = Notification(
            workspace_id=workspace.id,
            recipient_id=member.id,
            type="comment_created",
            priority="normal",
            payload={"title": "t"},
            read_at=read_at,
            created_at=created_at,
            updated_at=updated_at or created_at,
            archived_at=archived_at,
        )
        session.add(row)
        await session.flush()
        return row


async def _archived_ids(factory) -> set:
    async with factory() as session:
        rows = (await session.execute(select(Notification))).scalars().all()
    return {row.id for row in rows if row.archived_at is not None}


@pytest.fixture
async def env(session_factory):
    workspace = await _workspace(session_factory)
    member = await _human(session_factory, workspace)
    return {"factory": session_factory, "workspace": workspace, "member": member}


async def test_read_and_stale_is_archived(env):
    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    stale_read = await _notify(
        factory, workspace, member,
        read_at=NOW - timedelta(days=10), created_at=NOW - timedelta(days=12),
    )
    archived = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW
    )
    assert archived == 1
    assert await _archived_ids(factory) == {stale_read.id}


async def test_unread_stale_is_not_archived(env):
    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    await _notify(
        factory, workspace, member,
        read_at=None, created_at=NOW - timedelta(days=30),
    )
    archived = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW
    )
    assert archived == 0
    assert await _archived_ids(factory) == set()


async def test_read_but_fresh_is_not_archived(env):
    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    # read long ago but the group got fresh activity inside the window
    await _notify(
        factory, workspace, member,
        read_at=NOW - timedelta(days=10),
        created_at=NOW - timedelta(days=12),
        updated_at=NOW - timedelta(days=1),
    )
    archived = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW
    )
    assert archived == 0
    assert await _archived_ids(factory) == set()


async def test_already_archived_is_idempotent(env):
    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    await _notify(
        factory, workspace, member,
        read_at=NOW - timedelta(days=10), created_at=NOW - timedelta(days=12),
        archived_at=NOW - timedelta(days=1),
    )
    archived = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW
    )
    assert archived == 0


async def test_batch_limit_bounds_single_pass(env):
    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    for _ in range(3):
        await _notify(
            factory, workspace, member,
            read_at=NOW - timedelta(days=10), created_at=NOW - timedelta(days=12),
        )
    first = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW, batch_limit=2
    )
    assert first == 2
    second = await archive_read_expired_notifications(
        factory, retention=RETENTION, now=NOW, batch_limit=2
    )
    assert second == 1


async def test_loop_archives_then_stops(env):
    import asyncio

    factory, workspace, member = env["factory"], env["workspace"], env["member"]
    await _notify(
        factory, workspace, member,
        read_at=NOW - timedelta(days=10), created_at=NOW - timedelta(days=12),
    )

    class _Settings:
        notification_archive_retention = RETENTION
        notification_archive_interval = 0.05

    stop = asyncio.Event()
    task = asyncio.create_task(
        notification_archive_loop(factory, settings=_Settings(), stop=stop)
    )
    await asyncio.sleep(0.2)  # let at least one pass run
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(await _archived_ids(factory)) == 1

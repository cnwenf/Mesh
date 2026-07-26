"""Owner invariant guard tests (member.md §3.3/§5.3, MES-35 MB-M1/MB-M2).

The workspace must always retain at least one active owner (role='owner' AND
status='active'). ``lock_active_owner_set`` is the single locking primitive
shared by the demote / remove / disable paths: it locks the target row plus
every active owner in one ascending-id FOR UPDATE sweep and refreshes
session-cached entities, so callers decide on post-lock state. Concurrency
behavior lives in test_owner_invariant_concurrency.py (real PostgreSQL).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.member import Member
from mesh.member.owner_guard import LAST_OWNER_CODE, lock_active_owner_set

pytestmark = pytest.mark.unit


async def _workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Guard WS', :s) RETURNING id"
                ),
                {"s": f"guard-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
    return workspace_id


async def _add_member(session_factory, workspace_id, *, role="owner", status="active"):
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'M') RETURNING id"
                ),
                {"e": f"m-{uuid.uuid4().hex[:10]}@corp.com"},
            )
        ).scalar_one()
        member_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, :r, :st) RETURNING id"
                ),
                {"ws": workspace_id, "u": user_id, "r": role, "st": status},
            )
        ).scalar_one()
    return member_id


async def test_lock_counts_single_active_owner(session_factory):
    workspace_id = await _workspace(session_factory)
    owner_id = await _add_member(session_factory, workspace_id)
    async with session_factory() as session, session.begin():
        count, target = await lock_active_owner_set(
            session, workspace_id=workspace_id, target_id=owner_id
        )
    assert count == 1  # caller raises 409 last_owner on <= 1
    assert target is not None and target.id == owner_id


async def test_lock_counts_two_active_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    owner_id = await _add_member(session_factory, workspace_id)
    await _add_member(session_factory, workspace_id)
    async with session_factory() as session, session.begin():
        count, _target = await lock_active_owner_set(
            session, workspace_id=workspace_id, target_id=owner_id
        )
    assert count == 2


async def test_lock_ignores_disabled_and_removed_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    owner_id = await _add_member(session_factory, workspace_id)  # only ACTIVE owner
    await _add_member(session_factory, workspace_id, status="disabled")
    await _add_member(session_factory, workspace_id, status="removed")
    async with session_factory() as session, session.begin():
        count, _target = await lock_active_owner_set(
            session, workspace_id=workspace_id, target_id=owner_id
        )
    assert count == 1


async def test_lock_returns_none_for_foreign_workspace_target(session_factory):
    workspace_id = await _workspace(session_factory)
    other_id = await _workspace(session_factory)
    foreign_member = await _add_member(session_factory, other_id)
    async with session_factory() as session, session.begin():
        count, target = await lock_active_owner_set(
            session, workspace_id=workspace_id, target_id=foreign_member
        )
    assert target is None
    assert count == 0  # no active owners in THIS workspace


async def test_lock_refreshes_stale_session_entity(session_factory):
    """populate_existing: an entity loaded before the lock is refreshed with
    the locked (committed) values — the gate-skip hole's structural fix."""
    workspace_id = await _workspace(session_factory)
    member_id = await _add_member(session_factory, workspace_id, role="member")

    # Stale unlocked read sees a plain member...
    async with session_factory() as session, session.begin():
        stale = await session.scalar(select(Member).where(Member.id == member_id))
        assert stale.role == "member"

        # ...a concurrent transaction promotes and commits...
        async with session_factory() as other, other.begin():
            await other.execute(
                text("UPDATE members SET role = 'owner' WHERE id = :id"),
                {"id": member_id},
            )

        # ...and the lock sweep refreshes the cached entity under lock.
        count, target = await lock_active_owner_set(
            session, workspace_id=workspace_id, target_id=member_id
        )
        assert count == 1
        assert target is stale  # same identity-map instance...
        assert stale.role == "owner"  # ...with post-lock state


async def test_last_owner_code_constant(session_factory):
    assert LAST_OWNER_CODE == "last_owner"

"""Owner invariant guard tests (member.md §3.3/§5.3, MES-35 MB-M1/MB-M2).

The workspace must always retain at least one active owner (role='owner' AND
status='active'); the guard is the single enforcement point shared by the
demote / remove / disable paths. Real PostgreSQL: the guard's row locking is
exercised here against a live database, and its concurrency behavior in
test_owner_invariant_concurrency.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mesh.errors import ConflictError
from mesh.member.owner_guard import LAST_OWNER_CODE, ensure_not_last_active_owner

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


async def _add_owner(session_factory, workspace_id, *, status="active") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Owner') RETURNING id"
                ),
                {"e": f"owner-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        member_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, 'owner', :st) RETURNING id"
                ),
                {"ws": workspace_id, "u": user_id, "st": status},
            )
        ).scalar_one()
    return member_id


async def test_guard_rejects_when_single_active_owner(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    with pytest.raises(ConflictError) as excinfo:
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot demote the last owner of the workspace",
            )
    assert excinfo.value.code == LAST_OWNER_CODE == "last_owner"
    assert "demote" in str(excinfo.value)


async def test_guard_rejects_when_zero_active_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id, status="disabled")
    with pytest.raises(ConflictError) as excinfo:
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot remove the last owner of the workspace",
            )
    assert excinfo.value.code == "last_owner"


async def test_guard_passes_with_two_active_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    await _add_owner(session_factory, workspace_id)
    async with session_factory() as session, session.begin():
        count = await ensure_not_last_active_owner(
            session,
            workspace_id=workspace_id,
            error_message="cannot disable the last owner of the workspace",
        )
    assert count == 2


async def test_guard_ignores_disabled_and_removed_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)  # the only ACTIVE owner
    await _add_owner(session_factory, workspace_id, status="disabled")
    async with session_factory() as session, session.begin():
        removed_user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Gone') RETURNING id"
                ),
                {"e": f"gone-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                "VALUES (:ws, 'human', :u, 'owner', 'removed')"
            ),
            {"ws": workspace_id, "u": removed_user},
        )
    with pytest.raises(ConflictError):
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot demote the last owner of the workspace",
            )


async def test_guard_scopes_to_workspace(session_factory):
    workspace_id = await _workspace(session_factory)
    other_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    await _add_owner(session_factory, other_id)
    await _add_owner(session_factory, other_id)
    # other workspace has two active owners; this one still only one.
    with pytest.raises(ConflictError):
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot remove the last owner of the workspace",
            )

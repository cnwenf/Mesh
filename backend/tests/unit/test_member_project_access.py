"""Guest project-level visibility tests (member.md §2.3 / M12).

Project sharing only applies to ``role='guest'`` members; other roles' visibility
is decided by their role. Revoking a grant makes the project invisible immediately
(the project module consults ``assert_guest_project_visible``). The projects
table lands with the project.md increment, so here we exercise the access table
+ the visibility hook with synthetic project ids.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.auth.rbac import assert_guest_project_visible
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.member.service import MemberService
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit


def _user_stub(user_id):
    from mesh.db.models.user import User

    return User(id=user_id, email="stub@corp.com", display_name="Stub")


async def _add_user(session_factory, name="U"):
    from sqlalchemy import text

    async with session_factory() as session, session.begin():
        return (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e,:d) RETURNING id"),
                {"e": f"pa-{uuid.uuid4().hex[:10]}@corp.com", "d": name},
            )
        ).scalar_one()


async def _setup(session_factory):
    """Workspace + owner + one guest member. Returns (service, ws, owner, guest)."""
    service = MemberService(session_factory)
    ws_service = WorkspaceService(session_factory)
    owner_uid = await _add_user(session_factory, "Owner")
    guest_uid = await _add_user(session_factory, "Guest")
    created = await ws_service.create_workspace(
        user=_user_stub(owner_uid), name="PA WS", slug=f"pa-{uuid.uuid4().hex[:10]}"
    )
    ws = created["id"]
    async with session_factory() as session, session.begin():
        guest = Member(
            workspace_id=ws, member_type="human", user_id=guest_uid, role="guest"
        )
        session.add(guest)
        await session.flush()
        guest_id = guest.id
    async with session_factory() as session:
        owner = await session.scalar(
            select(Member).where(Member.workspace_id == ws, Member.user_id == owner_uid)
        )
        guest = await session.scalar(select(Member).where(Member.id == guest_id))
    return service, ws, owner, guest


async def test_grant_and_list_project_access(session_factory):
    service, ws, owner, guest = await _setup(session_factory)
    project_id = uuid.uuid4()
    created = await service.grant_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id,
        project_id=project_id, permission="read",
    )
    assert created["permission"] == "read"
    assert created["project_id"] == project_id

    items = await service.list_project_access(actor=owner, workspace_id=ws, member_id=guest.id)
    assert [i["project_id"] for i in items] == [project_id]


async def test_grant_to_non_guest_rejected(session_factory):
    service, ws, owner, _guest = await _setup(session_factory)
    # owner is not a guest → 422
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.grant_project_access(
            actor=owner, workspace_id=ws, member_id=owner.id,
            project_id=uuid.uuid4(), permission="read",
        )
    assert excinfo.value.code == "not_guest_member"


async def test_grant_invalid_permission(session_factory):
    service, ws, owner, guest = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.grant_project_access(
            actor=owner, workspace_id=ws, member_id=guest.id,
            project_id=uuid.uuid4(), permission="admin",
        )


async def test_grant_upsert_updates_permission(session_factory):
    service, ws, owner, guest = await _setup(session_factory)
    project_id = uuid.uuid4()
    await service.grant_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id,
        project_id=project_id, permission="read",
    )
    updated = await service.grant_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id,
        project_id=project_id, permission="write",
    )
    assert updated["permission"] == "write"
    items = await service.list_project_access(actor=owner, workspace_id=ws, member_id=guest.id)
    assert len(items) == 1  # single row, permission updated (not duplicated)
    assert items[0]["permission"] == "write"


async def test_revoke_is_immediate_and_idempotent(session_factory):
    service, ws, owner, guest = await _setup(session_factory)
    project_id = uuid.uuid4()
    await service.grant_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id,
        project_id=project_id, permission="read",
    )
    # Visible right after the grant.
    async with session_factory() as session:
        await set_tenant_context(session, ws)
        await assert_guest_project_visible(session, member=guest, project_id=project_id)

    revoked = await service.revoke_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id, project_id=project_id
    )
    assert revoked == {"revoked": True}

    # Invisible immediately after revoke.
    async with session_factory() as session:
        await set_tenant_context(session, ws)
        with pytest.raises(NotFoundError):
            await assert_guest_project_visible(
                session, member=guest, project_id=project_id
            )

    # Second revoke is a no-op.
    again = await service.revoke_project_access(
        actor=owner, workspace_id=ws, member_id=guest.id, project_id=project_id
    )
    assert again == {"revoked": False}


async def test_grant_requires_admin(session_factory):
    service, ws, _owner, guest = await _setup(session_factory)
    # guest is not admin → cannot grant
    with pytest.raises(ForbiddenError):
        await service.grant_project_access(
            actor=guest, workspace_id=ws, member_id=guest.id,
            project_id=uuid.uuid4(), permission="read",
        )


async def test_cross_workspace_member_not_found(session_factory):
    service, ws, owner, _guest = await _setup(session_factory)
    _s2, _ws_b, owner_b, _g2 = await _setup(session_factory)
    # owner_b belongs to another workspace, invisible from ws
    with pytest.raises(NotFoundError):
        await service.grant_project_access(
            actor=owner, workspace_id=ws, member_id=owner_b.id,
            project_id=uuid.uuid4(), permission="read",
        )

"""Member role-change service tests (member.md §3.3/§4.4, workspace.md scope 4).

Role changes are audited (append-only audit_logs) and event-sourced
(member.role_changed through the outbox). Last-owner and agent-owner
protections are server-enforced — UI disabling is not trusted.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from mesh.workspace.members import change_member_role
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit


async def _setup(session_factory, slug="role-ws"):
    """Workspace + owner + plain member; return (workspace_id, owner, member)."""
    service = WorkspaceService(session_factory)
    async with session_factory() as session, session.begin():
        owner_user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Owner') RETURNING id"
                ),
                {"e": f"owner-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        plain_user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Plain') RETURNING id"
                ),
                {"e": f"plain-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
    created = await service.create_workspace(user=_user_stub(owner_user), name="Role WS", slug=slug)
    workspace_id = created["id"]
    async with session_factory() as session:
        owner = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.user_id == owner_user
            )
        )
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace_id,
            member_type="human",
            user_id=plain_user,
            role="member",
        )
        session.add(member)
        await session.flush()
        member_id = member.id
    async with session_factory() as session:
        plain = await session.scalar(select(Member).where(Member.id == member_id))
    return workspace_id, owner, plain


def _user_stub(user_id):
    from mesh.db.models.user import User

    return User(id=user_id, email="stub@corp.com", display_name="Stub")


async def _audit_rows(session_factory):
    async with session_factory() as session:
        return (await session.execute(select(AuditLog))).scalars().all()


async def _role_change_events(session_factory):
    async with session_factory() as session:
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e
        for e in events
        if e.event_type == "realtime.publish"
        and e.payload["event"] == "member.role_changed"
    ]


async def test_role_change_happy_path_audited_and_evented(session_factory):
    workspace_id, owner, member = await _setup(session_factory, "role-happy")
    result = await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="admin",
    )
    assert result["role"] == "admin"

    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == member.id))
    assert fresh.role == "admin"

    audits = await _audit_rows(session_factory)
    role_audits = [a for a in audits if a.action == "member.role_changed"]
    assert len(role_audits) == 1
    assert role_audits[0].actor_member_id == owner.id
    assert role_audits[0].actor_kind == "member"
    assert role_audits[0].metadata_ == {
        "target_member_id": str(member.id),
        "old_role": "member",
        "new_role": "admin",
    }

    events = await _role_change_events(session_factory)
    assert len(events) == 1
    assert events[0].payload["data"] == {
        "member_id": str(member.id),
        "old_role": "member",
        "new_role": "admin",
    }


async def test_role_change_no_change_is_noop(session_factory):
    workspace_id, owner, member = await _setup(session_factory, "role-noop")
    result = await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="member",
    )
    assert result["role"] == "member"
    assert [a for a in await _audit_rows(session_factory)
            if a.action == "member.role_changed"] == []
    assert await _role_change_events(session_factory) == []


async def test_last_owner_cannot_be_demoted(session_factory):
    workspace_id, owner, member = await _setup(session_factory, "role-last-owner")
    with pytest.raises(ConflictError) as excinfo:
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=owner.id,
            new_role="member",
        )
    assert excinfo.value.code == "last_owner"

    # With two owners, demoting one is fine.
    await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="owner",
    )
    result = await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="admin",
    )
    assert result["role"] == "admin"


async def test_demote_disabled_co_owner_is_allowed(session_factory):
    """Demoting a DISABLED co-owner cannot reduce the ACTIVE owner count, so
    the guard must not fire (invariant is about active owners, review MB-M2)."""
    workspace_id, owner, member = await _setup(session_factory, "role-disabled-co")
    await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="owner",
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET status = 'disabled' WHERE id = :id"),
            {"id": member.id},
        )
    result = await change_member_role(
        session_factory,
        actor=owner,
        workspace_id=workspace_id,
        member_id=member.id,
        new_role="member",
    )
    assert result["role"] == "member"


async def test_agent_cannot_become_owner(session_factory):
    """Server-side guard + DB CHECK backstop (member.md §2.2)."""
    workspace_id, owner, _member = await _setup(session_factory, "role-agent")
    # No agents table yet (agent.md increment) — the deferred-FK model allows
    # inserting an agent roster row directly for this test.
    async with session_factory() as session, session.begin():
        agent_member = Member(
            workspace_id=workspace_id,
            member_type="agent",
            agent_id=uuid.uuid4(),
            role="member",
        )
        session.add(agent_member)
        await session.flush()
        agent_id = agent_member.id
    async with session_factory() as session:
        agent = await session.scalar(select(Member).where(Member.id == agent_id))

    with pytest.raises(ConflictError) as excinfo:
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=agent.id,
            new_role="owner",
        )
    assert excinfo.value.code == "agent_owner_not_allowed"

    # DB-level backstop: the CHECK rejects agent owners even bypassing the service.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, agent_id, role) "
                    "VALUES (:ws, 'agent', :a, 'owner')"
                ),
                {"ws": workspace_id, "a": uuid.uuid4()},
            )


async def test_role_change_requires_manage_permission(session_factory):
    workspace_id, _owner, member = await _setup(session_factory, "role-forbidden")
    # The plain member is not admin — cannot change roles.
    with pytest.raises(ForbiddenError):
        await change_member_role(
            session_factory,
            actor=member,
            workspace_id=workspace_id,
            member_id=member.id,
            new_role="admin",
        )


async def test_role_change_unknown_target_or_workspace_404(session_factory):
    workspace_id, owner, _member = await _setup(session_factory, "role-404")
    with pytest.raises(NotFoundError):
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=uuid.uuid4(),
            new_role="admin",
        )
    # A member id from another workspace is also 404 (composite scope).
    other_ws, other_owner, _ = await _setup(session_factory, "role-other")
    with pytest.raises(NotFoundError):
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=other_owner.id,
            new_role="admin",
        )
    assert other_ws != workspace_id


async def test_role_change_invalid_role_400(session_factory):
    workspace_id, owner, member = await _setup(session_factory, "role-invalid")
    with pytest.raises(ValidationError):
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=member.id,
            new_role="superadmin",
        )


async def test_removed_member_is_not_changeable(session_factory):
    workspace_id, owner, member = await _setup(session_factory, "role-removed")
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET status = 'removed' WHERE id = :id"),
            {"id": member.id},
        )
    with pytest.raises(NotFoundError):
        await change_member_role(
            session_factory,
            actor=owner,
            workspace_id=workspace_id,
            member_id=member.id,
            new_role="admin",
        )

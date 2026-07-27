"""MemberService tests (member.md §3 — roster feature layer).

Service-level coverage for list/detail/add/update/remove/reassign and the
display-name + profile rendering. Role-change protection is covered in
test_member_role_service.py; here we verify update_member wires role/status/
display_override correctly (audit + member.updated / member.role_changed
events) and that removal is a soft ``status='removed'``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.member.service import UNSET, MemberPatch, MemberService
from mesh.workspace.members import change_member_role
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit


def _user_stub(user_id) -> User:
    return User(id=user_id, email="stub@corp.com", display_name="Stub")


async def _add_user(session_factory, display_name="Some User", status="active") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        return (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name, status) "
                    "VALUES (:e, :d, :s) RETURNING id"
                ),
                {
                    "e": f"u-{uuid.uuid4().hex[:10]}@corp.com",
                    "d": display_name,
                    "s": status,
                },
            )
        ).scalar_one()


async def _setup(session_factory, slug=None):
    """Workspace + owner + one plain human member.

    Returns (service, workspace_id, owner, plain, plain_user_id).
    """
    service = MemberService(session_factory)
    ws_service = WorkspaceService(session_factory)
    owner_uid = await _add_user(session_factory, "Owner")
    plain_uid = await _add_user(session_factory, "Plain Person")
    created = await ws_service.create_workspace(
        user=_user_stub(owner_uid), name="Member WS", slug=slug or f"m-{uuid.uuid4().hex[:10]}"
    )
    workspace_id = created["id"]
    async with session_factory() as session, session.begin():
        plain = Member(
            workspace_id=workspace_id,
            member_type="human",
            user_id=plain_uid,
            role="member",
            joined_at=None,
        )
        session.add(plain)
        await session.flush()
        plain_id = plain.id
    async with session_factory() as session:
        owner = await session.scalar(
            select(Member).where(Member.workspace_id == workspace_id, Member.user_id == owner_uid)
        )
        plain = await session.scalar(select(Member).where(Member.id == plain_id))
    return service, workspace_id, owner, plain, plain_uid


async def _add_agent_member(session_factory, workspace_id) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        from mesh.db.models.agent import Agent
        from mesh.db.models.user import User

        # Agent roster rows reference a real agents row (composite FK); the
        # agent needs a human owner (agents.owner_user_id NOT NULL).
        owner = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            password_hash="x",
            display_name="Agent Owner",
        )
        session.add(owner)
        await session.flush()
        agent_row = Agent(workspace_id=workspace_id, name="Roster Agent", owner_user_id=owner.id)
        session.add(agent_row)
        await session.flush()
        agent = Member(
            workspace_id=workspace_id,
            member_type="agent",
            agent_id=agent_row.id,
            role="member",
            joined_at=None,
        )
        session.add(agent)
        await session.flush()
        return agent.id


async def _events(session_factory, name):
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e
        for e in rows
        if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


async def _audits(session_factory, action):
    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return [a for a in rows if a.action == action]


# --- list / detail -------------------------------------------------------------


async def test_list_returns_humans_and_agents_same_roster(session_factory):
    service, ws, _owner, plain, _uid = await _setup(session_factory)
    await _add_agent_member(session_factory, ws)
    items, cursor = await service.list_members(workspace_id=ws)
    assert cursor is None
    types = {item["member_type"] for item in items}
    assert types == {"human", "agent"}
    assert len(items) == 3  # owner + plain + agent


async def test_list_agent_filter_is_projection_of_same_roster(session_factory):
    service, ws, _owner, _plain, _uid = await _setup(session_factory)
    agent_id = await _add_agent_member(session_factory, ws)
    all_items, _ = await service.list_members(workspace_id=ws)
    agent_items, _ = await service.list_members(workspace_id=ws, member_type="agent")
    assert [i["id"] for i in agent_items] == [
        i["id"] for i in all_items if i["member_type"] == "agent"
    ]
    assert [str(i["id"]) for i in agent_items] == [str(agent_id)]


async def test_list_default_status_hides_removed(session_factory):
    service, ws, _owner, plain, _uid = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET status='removed' WHERE id=:id"), {"id": plain.id}
        )
    default_items, _ = await service.list_members(workspace_id=ws)
    assert all(i["status"] != "removed" for i in default_items)
    removed_items, _ = await service.list_members(workspace_id=ws, status="removed")
    assert [str(i["id"]) for i in removed_items] == [str(plain.id)]
    all_items, _ = await service.list_members(workspace_id=ws, status="all")
    assert len(all_items) == 2


async def test_list_role_filter_and_q_search(session_factory):
    service, ws, owner, plain, _uid = await _setup(session_factory)
    # role filter
    owners, _ = await service.list_members(workspace_id=ws, role="owner")
    assert [str(i["id"]) for i in owners] == [str(owner.id)]
    # q search hits users.display_name ("Plain Person")
    hits, _ = await service.list_members(workspace_id=ws, q="Plain Person")
    assert [str(i["id"]) for i in hits] == [str(plain.id)]
    # q search hits display_override
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET display_override='小李' WHERE id=:id"), {"id": plain.id}
        )
    hits2, _ = await service.list_members(workspace_id=ws, q="小李")
    assert [str(i["id"]) for i in hits2] == [str(plain.id)]


async def test_list_q_search_escapes_like_wildcards(session_factory):
    """L5 parity (member.md §3.4): ``%`` / ``_`` in ``q`` match literally.

    Before the escape the roster search widened with user-supplied
    wildcards — ``q=%`` enumerated the whole roster. The query stays
    parameterised; only the match set must not widen.
    """
    service, ws, _owner, _plain, _uid = await _setup(session_factory)
    named = {}
    for display in ("100% Club", "100X Club", "a_b Lead", "axb Lead"):
        uid = await _add_user(session_factory, display)
        async with session_factory() as session, session.begin():
            row = Member(
                workspace_id=ws,
                member_type="human",
                user_id=uid,
                role="member",
                joined_at=None,
            )
            session.add(row)
            await session.flush()
            named[display] = row.id

    def hit_ids(items):
        return {str(i["id"]) for i in items}

    # literal "%" only matches the display containing a real percent sign
    percent, _ = await service.list_members(workspace_id=ws, q="100%")
    assert hit_ids(percent) == {str(named["100% Club"])}

    # literal "_" must not behave as a single-char wildcard
    underscore, _ = await service.list_members(workspace_id=ws, q="a_b")
    assert hit_ids(underscore) == {str(named["a_b Lead"])}

    # the old `_` wildcard would have hit "100% Club" ("1_0" ~ "100")
    wildcard, _ = await service.list_members(workspace_id=ws, q="1_0")
    assert wildcard == []

    # q=% no longer enumerates the roster — only literal-% rows match
    bare, _ = await service.list_members(workspace_id=ws, q="%")
    assert hit_ids(bare) == {str(named["100% Club"])}


async def test_list_pagination_walks_all_rows_once(session_factory):
    service, ws, _owner, _plain, _uid = await _setup(session_factory)
    # add 5 more humans with distinct joined_at
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        for i in range(5):
            uid = await _add_user(session_factory, f"P{i}")
            session.add(
                Member(
                    workspace_id=ws,
                    member_type="human",
                    user_id=uid,
                    role="member",
                    joined_at=base + timedelta(hours=i),
                )
            )
    seen = []
    cursor = None
    for _ in range(10):
        items, cursor = await service.list_members(workspace_id=ws, limit=2, cursor=cursor)
        seen.extend(str(i["id"]) for i in items)
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 7  # owner + plain + 5 (no dupes, none dropped)


async def test_list_invalid_filters_raise(session_factory):
    service, ws, *_ = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.list_members(workspace_id=ws, member_type="robot")
    with pytest.raises(ValidationError):
        await service.list_members(workspace_id=ws, status="sleeping")
    with pytest.raises(ValidationError):
        await service.list_members(workspace_id=ws, role="superadmin")


async def test_list_is_scoped_to_workspace(session_factory):
    service, ws_a, *_ = await _setup(session_factory, slug="scope-a")
    _sb, ws_b, *_b = await _setup(session_factory, slug="scope-b")
    items_a, _ = await service.list_members(workspace_id=ws_a)
    async with session_factory() as session:
        ids_b = {
            str(m.id)
            for m in (
                await session.execute(select(Member).where(Member.workspace_id == ws_b))
            ).scalars().all()
        }
    assert ids_b
    assert all(str(i["id"]) not in ids_b for i in items_a)


async def test_get_member_detail_shape(session_factory):
    service, ws, _owner, plain, _uid = await _setup(session_factory)
    detail = await service.get_member(workspace_id=ws, member_id=plain.id)
    assert detail["member_type"] == "human"
    assert detail["display_name"] == "Plain Person"
    assert detail["profile"]["full_name"] == "Plain Person"
    assert detail["counts"] == {"open_issues_assigned": 0}
    assert "display_override" in detail and "disabled_at" in detail


async def test_get_member_not_found_and_cross_workspace(session_factory):
    service, ws, *_ = await _setup(session_factory, slug="nf-a")
    _sb, ws_b, owner_b, *_b = await _setup(session_factory, slug="nf-b")
    with pytest.raises(NotFoundError):
        await service.get_member(workspace_id=ws, member_id=uuid.uuid4())
    # A member of workspace B is invisible from workspace A (composite scope).
    with pytest.raises(NotFoundError):
        await service.get_member(workspace_id=ws, member_id=owner_b.id)


async def test_render_agent_profile_and_display_fallback(session_factory):
    service, ws, *_ = await _setup(session_factory)
    agent_id = await _add_agent_member(session_factory, ws)
    detail = await service.get_member(workspace_id=ws, member_id=agent_id)
    assert detail["member_type"] == "agent"
    # The agents table (agent.md) backs the profile; display name resolves
    # from agents.name (README §6.1 order).
    assert detail["profile"]["name"] == "Roster Agent"
    assert detail["profile"]["is_active"] is True
    assert detail["display_name"] == "Roster Agent"


# --- add -----------------------------------------------------------------------


async def test_add_human_member_emits_event_and_audit(session_factory):
    service, ws, owner, *_ = await _setup(session_factory)
    new_uid = await _add_user(session_factory, "New Hire")
    result = await service.add_member(
        actor=owner, workspace_id=ws, member_type="human", user_id=new_uid, role="member"
    )
    assert result["role"] == "member"
    assert result["display_name"] == "New Hire"
    # workspace creation already emits member.added for the owner — assert the
    # event for THIS member specifically.
    added = await _events(session_factory, "member.added")
    assert [e for e in added if e.payload["data"]["member_id"] == str(result["id"])]
    assert len(await _audits(session_factory, "member.added")) == 1


async def test_add_duplicate_active_member_conflicts(session_factory):
    service, ws, owner, plain, _uid = await _setup(session_factory)
    with pytest.raises(ConflictError) as excinfo:
        await service.add_member(
            actor=owner, workspace_id=ws, member_type="human", user_id=_uid
        )
    assert excinfo.value.code == "already_member"


async def test_add_reactivates_disabled_row(session_factory):
    service, ws, owner, plain, uid = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET status='disabled' WHERE id=:id"), {"id": plain.id}
        )
    result = await service.add_member(
        actor=owner, workspace_id=ws, member_type="human", user_id=uid, role="admin"
    )
    assert result["status"] == "active"
    assert result["role"] == "admin"


async def test_add_unknown_user_404(session_factory):
    service, ws, owner, *_ = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await service.add_member(
            actor=owner, workspace_id=ws, member_type="human", user_id=uuid.uuid4()
        )


async def test_add_inactive_user_rejected(session_factory):
    service, ws, owner, *_ = await _setup(session_factory)
    disabled_uid = await _add_user(session_factory, "Gone", status="disabled")
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.add_member(
            actor=owner, workspace_id=ws, member_type="human", user_id=disabled_uid
        )
    assert excinfo.value.code == "user_not_active"


async def test_add_requires_admin(session_factory):
    service, ws, _owner, plain, *_ = await _setup(session_factory)
    uid = await _add_user(session_factory)
    with pytest.raises(ForbiddenError):
        await service.add_member(
            actor=plain, workspace_id=ws, member_type="human", user_id=uid
        )


async def test_add_agent_not_available(session_factory):
    service, ws, owner, *_ = await _setup(session_factory)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.add_member(
            actor=owner, workspace_id=ws, member_type="agent", agent_id=uuid.uuid4()
        )
    assert excinfo.value.code == "agents_not_available"


async def test_add_human_without_user_id_validation(session_factory):
    service, ws, owner, *_ = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.add_member(actor=owner, workspace_id=ws, member_type="human")
    with pytest.raises(ValidationError):
        await service.add_member(
            actor=owner, workspace_id=ws, member_type="human", user_id=await _add_user(session_factory),
            role="bogus",
        )


# --- update --------------------------------------------------------------------


async def test_update_status_disable_enable(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    disabled = await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id, patch=MemberPatch(status="disabled")
    )
    assert disabled["status"] == "disabled"
    assert len(await _events(session_factory, "member.updated")) == 1
    assert len(await _audits(session_factory, "member.status_changed")) == 1

    enabled = await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id, patch=MemberPatch(status="active")
    )
    assert enabled["status"] == "active"


async def test_update_status_removed_rejected(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.update_member(
            actor=owner, workspace_id=ws, member_id=plain.id, patch=MemberPatch(status="removed")
        )


async def test_update_status_requires_admin(session_factory):
    service, ws, _owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(ForbiddenError):
        await service.update_member(
            actor=plain, workspace_id=ws, member_id=plain.id, patch=MemberPatch(status="disabled")
        )


async def test_update_status_disable_last_owner_conflicts(session_factory):
    """MB-M1: disabling the only active owner is 409 last_owner (member.md §5.3).

    Workspace entry is gated on status='active', so disabling the last active
    owner would orphan the workspace — the status branch must enforce the same
    invariant as the demote/remove paths.
    """
    service, ws, owner, _plain, *_ = await _setup(session_factory)
    with pytest.raises(ConflictError) as excinfo:
        await service.update_member(
            actor=owner, workspace_id=ws, member_id=owner.id, patch=MemberPatch(status="disabled")
        )
    assert excinfo.value.code == "last_owner"

    # 落库未变:仍是 active owner,无 disabled_at,无 status_changed 审计/事件。
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == owner.id))
    assert fresh.status == "active"
    assert fresh.role == "owner"
    assert fresh.disabled_at is None
    assert await _audits(session_factory, "member.status_changed") == []
    assert await _events(session_factory, "member.updated") == []


async def test_update_status_disable_owner_allowed_with_second_owner(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await change_member_role(
        session_factory, actor=owner, workspace_id=ws, member_id=plain.id, new_role="owner"
    )
    result = await service.update_member(
        actor=owner, workspace_id=ws, member_id=owner.id, patch=MemberPatch(status="disabled")
    )
    assert result["status"] == "disabled"
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == owner.id))
    assert fresh.status == "disabled"
    assert fresh.disabled_at is not None


async def test_update_status_reenable_owner_not_blocked_by_guard(session_factory):
    """The guard fires only on active→disabled; re-enabling increases the count."""
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await change_member_role(
        session_factory, actor=owner, workspace_id=ws, member_id=plain.id, new_role="owner"
    )
    await service.update_member(
        actor=owner, workspace_id=ws, member_id=owner.id, patch=MemberPatch(status="disabled")
    )
    result = await service.update_member(
        actor=owner, workspace_id=ws, member_id=owner.id, patch=MemberPatch(status="active")
    )
    assert result["status"] == "active"


async def test_update_display_override_self_service(session_factory):
    service, ws, _owner, plain, *_ = await _setup(session_factory)
    result = await service.update_member(
        actor=plain,
        workspace_id=ws,
        member_id=plain.id,
        patch=MemberPatch(display_override="小李"),
    )
    assert result["display_name"] == "小李"
    assert len(await _audits(session_factory, "member.profile_updated")) == 1


async def test_update_display_override_other_requires_admin(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    # a plain member cannot change someone else's display name
    with pytest.raises(ForbiddenError):
        await service.update_member(
            actor=plain,
            workspace_id=ws,
            member_id=owner.id,
            patch=MemberPatch(display_override="hacked"),
        )
    # but an admin can
    result = await service.update_member(
        actor=owner,
        workspace_id=ws,
        member_id=plain.id,
        patch=MemberPatch(display_override="昵称"),
    )
    assert result["display_name"] == "昵称"


async def test_update_display_override_clear_with_none(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id,
        patch=MemberPatch(display_override="临时"),
    )
    cleared = await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id,
        patch=MemberPatch(display_override=None),
    )
    assert cleared["display_name"] == "Plain Person"  # falls back to user name


async def test_update_display_override_too_long(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.update_member(
            actor=owner, workspace_id=ws, member_id=plain.id,
            patch=MemberPatch(display_override="x" * 81),
        )


async def test_update_no_change_is_noop(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id,
        patch=MemberPatch(status="active"),  # already active
    )
    assert await _events(session_factory, "member.updated") == []
    assert await _audits(session_factory, "member.status_changed") == []


async def test_update_unset_display_override_keeps_value(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id,
        patch=MemberPatch(display_override="保留"),
    )
    # UNSET display_override + status change must not touch the override
    result = await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id,
        patch=MemberPatch(status="disabled", display_override=UNSET),
    )
    assert result["display_name"] == "保留"


async def test_update_role_delegates(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    result = await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id, patch=MemberPatch(role="admin")
    )
    assert result["role"] == "admin"
    assert len(await _events(session_factory, "member.role_changed")) == 1


# --- remove --------------------------------------------------------------------


class _FakeReassigner:
    def __init__(self, count=3):
        self.calls = []
        self.count = count

    async def reassign(self, session, *, workspace_id, from_member_id, to_member_id, statuses):
        self.calls.append(
            {"from": from_member_id, "to": to_member_id, "statuses": statuses}
        )
        return self.count

    async def open_issues_assigned(self, session, *, workspace_id, member_id):
        return self.count


async def test_remove_member_soft_and_evented(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    result = await service.remove_member(actor=owner, workspace_id=ws, member_id=plain.id)
    assert result == {"removed": True, "reassigned_issues": 0}
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == plain.id))
    assert fresh.status == "removed"  # soft — row still present
    assert len(await _events(session_factory, "member.removed")) == 1
    assert len(await _audits(session_factory, "member.removed")) == 1


async def test_remove_with_reassign_calls_reassigner(session_factory):
    reassigner = _FakeReassigner(count=7)
    service = MemberService(session_factory, reassigner=reassigner)
    _s2, ws, owner, plain, _ = await _setup(session_factory)
    target_uid = await _add_user(session_factory, "Target")
    async with session_factory() as session, session.begin():
        target = Member(
            workspace_id=ws, member_type="human", user_id=target_uid, role="member"
        )
        session.add(target)
        await session.flush()
        target_id = target.id
    result = await service.remove_member(
        actor=owner, workspace_id=ws, member_id=plain.id, reassign_to=target_id
    )
    assert result["reassigned_issues"] == 7
    assert reassigner.calls[0]["to"] == target_id


async def test_remove_invalid_reassign_target(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory, slug="rt-a")
    # self
    with pytest.raises(BusinessRuleError) as e1:
        await service.remove_member(
            actor=owner, workspace_id=ws, member_id=plain.id, reassign_to=plain.id
        )
    assert e1.value.code == "reassign_target_invalid"
    # unknown
    with pytest.raises(BusinessRuleError) as e2:
        await service.remove_member(
            actor=owner, workspace_id=ws, member_id=plain.id, reassign_to=uuid.uuid4()
        )
    assert e2.value.code == "reassign_target_invalid"
    # cross-workspace member
    _sb, ws_b, owner_b, *_b = await _setup(session_factory, slug="rt-b")
    with pytest.raises(BusinessRuleError) as e3:
        await service.remove_member(
            actor=owner, workspace_id=ws, member_id=plain.id, reassign_to=owner_b.id
        )
    assert e3.value.code == "reassign_target_invalid"


async def test_remove_last_owner_conflicts(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(ConflictError) as excinfo:
        await service.remove_member(actor=owner, workspace_id=ws, member_id=owner.id)
    assert excinfo.value.code == "last_owner"


async def test_remove_disabled_co_owner_is_allowed(session_factory):
    """Removing a DISABLED owner cannot reduce the active-owner count, so the
    guard must not fire (invariant is about ACTIVE owners, review MB-M2)."""
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await change_member_role(
        session_factory, actor=owner, workspace_id=ws, member_id=plain.id, new_role="owner"
    )
    await service.update_member(
        actor=owner, workspace_id=ws, member_id=plain.id, patch=MemberPatch(status="disabled")
    )
    result = await service.remove_member(actor=owner, workspace_id=ws, member_id=plain.id)
    assert result["removed"] is True
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == plain.id))
    assert fresh.status == "removed"


async def test_remove_already_removed_404(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    await service.remove_member(actor=owner, workspace_id=ws, member_id=plain.id)
    with pytest.raises(NotFoundError):
        await service.remove_member(actor=owner, workspace_id=ws, member_id=plain.id)


async def test_remove_requires_admin(session_factory):
    service, ws, _owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(ForbiddenError):
        await service.remove_member(actor=plain, workspace_id=ws, member_id=plain.id)


# --- reassign endpoint logic ---------------------------------------------------


async def test_reassign_issues_happy_and_audit(session_factory):
    reassigner = _FakeReassigner(count=4)
    service = MemberService(session_factory, reassigner=reassigner)
    _s2, ws, owner, plain, _ = await _setup(session_factory)
    target_uid = await _add_user(session_factory, "T2")
    async with session_factory() as session, session.begin():
        target = Member(workspace_id=ws, member_type="human", user_id=target_uid, role="member")
        session.add(target)
        await session.flush()
        target_id = target.id
    result = await service.reassign_issues(
        actor=owner, workspace_id=ws, from_member_id=plain.id, to_member_id=target_id
    )
    assert result == {"reassigned_issues": 4}
    assert len(await _audits(session_factory, "member.reassigned")) == 1


async def test_reassign_issues_validations(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await service.reassign_issues(
            actor=owner, workspace_id=ws, from_member_id=uuid.uuid4(), to_member_id=plain.id
        )
    with pytest.raises(BusinessRuleError):
        await service.reassign_issues(
            actor=owner, workspace_id=ws, from_member_id=plain.id, to_member_id=uuid.uuid4()
        )
    with pytest.raises(ValidationError):
        await service.reassign_issues(
            actor=owner, workspace_id=ws, from_member_id=plain.id, to_member_id=plain.id,
            statuses=[],
        )


# --- available agents + memberships -------------------------------------------


async def test_list_available_agents_empty(session_factory):
    service, ws, owner, plain, *_ = await _setup(session_factory)
    items, cursor = await service.list_available_agents(actor=owner, workspace_id=ws)
    assert items == [] and cursor is None
    with pytest.raises(ForbiddenError):
        await service.list_available_agents(actor=plain, workspace_id=ws)


async def test_list_user_memberships_across_workspaces(session_factory):
    service, ws_a, *_ = await _setup(session_factory, slug="ms-a")
    _sb, ws_b, *_b = await _setup(session_factory, slug="ms-b")
    uid = await _add_user(session_factory, "Multi")
    # add the same user to both workspaces
    for ws in (ws_a, ws_b):
        async with session_factory() as session, session.begin():
            session.add(
                Member(workspace_id=ws, member_type="human", user_id=uid, role="member")
            )
    user = User(id=uid, email="multi@corp.com", display_name="Multi")
    memberships = await service.list_user_memberships(user=user)
    assert {str(m["workspace_id"]) for m in memberships} == {str(ws_a), str(ws_b)}
    assert all(m["workspace_name"] for m in memberships)

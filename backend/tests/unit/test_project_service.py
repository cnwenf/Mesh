"""Project service unit tests — direct service calls against real PostgreSQL.

Covers project.md §2/§3/§5: CRUD, prefix permanent reservation (§6.3),
health/status trail, milestones (incl. derived overdue), cycles (incl.
auto-roll), project members, templates (incl. §3.2b instantiation with
graceful degradation) and the authorization matrix (§3.4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import (
    Cycle,
    Project,
    ProjectMember,
    ProjectUpdate,
)
from mesh.db.models.user import User
from mesh.db.models.workspace import IdentifierPrefixRegistry, Workspace
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.project.schemas import (
    AddProjectMemberRequest,
    AddProjectUpdateRequest,
    CreateCycleRequest,
    CreateMilestoneRequest,
    CreateProjectRequest,
    CreateProjectTemplateRequest,
    InstantiateProjectTemplateRequest,
    UpdateProjectMemberRequest,
    UpdateProjectTemplateRequest,
)
from mesh.project.service import (
    CyclePatch,
    MilestonePatch,
    ProjectPatch,
    ProjectService,
)

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _add_user(session_factory, display_name: str = "User") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name=display_name,
            password_hash="x",
            status="active",
        )
        session.add(user)
    return user.id


async def _setup(session_factory, *, role: str = "member", ws_name: str = "WS"):
    """Workspace + one human member; returns (workspace, member, service)."""
    async with session_factory() as session, session.begin():
        workspace = Workspace(name=ws_name, slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role=role,
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(member)
    service = ProjectService(session_factory, clock=_clock)
    return workspace, member, service


async def _second_member(session_factory, workspace, *, role: str = "member") -> Member:
    user_id = await _add_user(session_factory, display_name="Other")
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role=role,
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(member)
    return member


def _body(**overrides) -> CreateProjectRequest:
    base = {"name": "Site Revamp", "key": "WEB"}
    base.update(overrides)
    return CreateProjectRequest(**base)


async def _events(session_factory, event_name: str | None = None) -> list[OutboxEvent]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    if event_name is None:
        return rows
    return [row for row in rows if row.payload.get("event") == event_name]


# --- create -----------------------------------------------------------------


async def test_create_project_defaults_and_prefix_registration(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    assert created["name"] == "Site Revamp"
    assert created["key"] == "WEB"
    assert created["status"] == "planning"
    assert created["visibility"] == "public"
    assert created["issue_seq"] == 0
    assert created["progress"] == 0.0
    assert created["archived"] is False
    # Prefix registered in the same transaction (README §6.3).
    async with session_factory() as session:
        registry = await session.scalar(
            select(IdentifierPrefixRegistry).where(
                IdentifierPrefixRegistry.workspace_id == workspace.id,
                IdentifierPrefixRegistry.key == "WEB",
            )
        )
    assert registry is not None
    assert registry.kind == "project"
    assert registry.project_id == uuid.UUID(created["id"])
    events = await _events(session_factory, "project.created")
    assert len(events) == 2  # public → project channel + workspace channel
    channels = {event.payload["channel"] for event in events}
    assert channels == {
        f"project:{created['id']}",
        f"workspace:{workspace.id}:projects",
    }


async def test_create_project_with_lead_adds_lead_membership(session_factory):
    workspace, member, service = await _setup(session_factory)
    lead = await _second_member(session_factory, workspace)
    created = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(lead_member_id=str(lead.id)),
    )
    assert created["lead_member_id"] == str(lead.id)
    assert created["lead"]["id"] == str(lead.id)
    assert created["my_role"] == "lead"  # creator is a lead member too
    async with session_factory() as session:
        membership = await session.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == uuid.UUID(created["id"]),
                ProjectMember.member_id == lead.id,
            )
        )
    assert membership is not None
    assert membership.role == "lead"


async def test_create_project_key_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    for bad_key in ("web", "1ABC", "A" * 13, "", "AB-CD"):
        with pytest.raises(ValidationError):
            await service.create_project(
                actor=member, workspace_id=workspace.id, body=_body(key=bad_key)
            )


async def test_create_project_name_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(name="")
        )
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(name="x" * 121)
        )


async def test_create_project_invalid_enums_and_dates(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(status="bogus")
        )
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(visibility="bogus")
        )
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member,
            workspace_id=workspace.id,
            body=_body(start_date=date(2026, 8, 1), target_date=date(2026, 7, 1)),
        )


async def test_create_project_duplicate_key_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    await service.create_project(actor=member, workspace_id=workspace.id, body=_body())
    with pytest.raises(ConflictError) as excinfo:
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(name="Other")
        )
    assert excinfo.value.code == "project_key_taken"


async def test_project_key_availability_uses_permanent_prefix_registry(session_factory):
    workspace, member, service = await _setup(session_factory)
    assert await service.project_key_available(
        actor=member, workspace_id=workspace.id, key="FREE"
    ) is True
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body(key="USED")
    )
    assert await service.project_key_available(
        actor=member, workspace_id=workspace.id, key="USED"
    ) is False
    await service.delete_project(
        actor=member,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
    )
    assert await service.project_key_available(
        actor=member, workspace_id=workspace.id, key="USED"
    ) is False


@pytest.mark.parametrize("key", ["used", " USED", "USED ", ""])
async def test_project_key_availability_uses_create_key_validation(session_factory, key):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.project_key_available(
            actor=member, workspace_id=workspace.id, key=key
        )


async def test_project_key_availability_guest_is_forbidden(session_factory):
    workspace, _, service = await _setup(session_factory)
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await service.project_key_available(
            actor=guest, workspace_id=workspace.id, key="FREE"
        )


async def test_create_project_key_conflicts_with_inbox_prefix(session_factory):
    workspace, member, service = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            IdentifierPrefixRegistry(workspace_id=workspace.id, key="WS", kind="inbox")
        )
    with pytest.raises(ConflictError) as excinfo:
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(key="WS")
        )
    assert excinfo.value.code == "project_key_taken"


async def test_create_project_key_conflicts_with_retired_prefix(session_factory):
    workspace, member, service = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            IdentifierPrefixRegistry(workspace_id=workspace.id, key="OLD", kind="retired")
        )
    with pytest.raises(ConflictError) as excinfo:
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(key="OLD")
        )
    assert excinfo.value.code == "project_key_taken"


async def test_create_project_duplicate_name_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    await service.create_project(actor=member, workspace_id=workspace.id, body=_body())
    with pytest.raises(ConflictError) as excinfo:
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(key="WEB2")
        )
    assert excinfo.value.code == "project_name_taken"


async def test_create_project_lead_must_be_active_member(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.create_project(
            actor=member,
            workspace_id=workspace.id,
            body=_body(lead_member_id=str(uuid.uuid4())),
        )


async def test_create_project_guest_forbidden(session_factory):
    workspace, _member, service = await _setup(session_factory)
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await service.create_project(
            actor=guest, workspace_id=workspace.id, body=_body()
        )


# --- list / get -------------------------------------------------------------


async def test_list_projects_visibility_and_filters(session_factory):
    workspace, member, service = await _setup(session_factory)
    outsider = await _second_member(session_factory, workspace)
    public_project = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body(key="PUB")
    )
    private_project = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(name="Secret", key="PRV", visibility="private"),
    )
    # Outsider sees only the public project.
    items, next_cursor = await service.list_projects(
        viewer=outsider, workspace_id=workspace.id
    )
    assert next_cursor is None
    assert [item["id"] for item in items] == [public_project["id"]]
    # Admin sees both.
    admin = await _second_member(session_factory, workspace, role="admin")
    items, _ = await service.list_projects(viewer=admin, workspace_id=workspace.id)
    assert len(items) == 2
    # Visibility filter.
    items, _ = await service.list_projects(
        viewer=admin, workspace_id=workspace.id, visibility="private"
    )
    assert [item["id"] for item in items] == [private_project["id"]]
    # Status filter: both planning → active filter empty.
    items, _ = await service.list_projects(
        viewer=admin, workspace_id=workspace.id, status="active"
    )
    assert items == []
    # Invalid filters → 400.
    with pytest.raises(ValidationError):
        await service.list_projects(viewer=admin, workspace_id=workspace.id, status="bogus")
    with pytest.raises(ValidationError):
        await service.list_projects(
            viewer=admin, workspace_id=workspace.id, visibility="bogus"
        )


async def test_list_projects_mine_and_pagination(session_factory):
    workspace, member, service = await _setup(session_factory)
    for index in range(3):
        await service.create_project(
            actor=member,
            workspace_id=workspace.id,
            body=_body(name=f"P{index}", key=f"PG{index}"),
        )
    items, next_cursor = await service.list_projects(
        viewer=member, workspace_id=workspace.id, limit=2
    )
    assert len(items) == 2
    assert next_cursor is not None
    items2, next_cursor2 = await service.list_projects(
        viewer=member, workspace_id=workspace.id, limit=2, cursor=next_cursor
    )
    assert len(items2) == 1
    assert next_cursor2 is None
    # mine filter — creator is a lead member of every project they created.
    items, _ = await service.list_projects(
        viewer=member, workspace_id=workspace.id, mine=True
    )
    assert len(items) == 3


async def test_list_projects_guest_only_granted(session_factory):
    workspace, member, service = await _setup(session_factory)
    guest = await _second_member(session_factory, workspace, role="guest")
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    items, _ = await service.list_projects(viewer=guest, workspace_id=workspace.id)
    assert items == []
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=workspace.id,
                member_id=guest.id,
                project_id=uuid.UUID(created["id"]),
            )
        )
    items, _ = await service.list_projects(viewer=guest, workspace_id=workspace.id)
    assert [item["id"] for item in items] == [created["id"]]


async def test_get_project_detail_and_private_access(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(visibility="private"),
    )
    await service.create_milestone(
        actor=member,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
        body=CreateMilestoneRequest(title="v1", target_date=date(2026, 1, 1)),
    )
    # Lead/creator is not a project member here (no lead set) → admin yes.
    admin = await _second_member(session_factory, workspace, role="admin")
    detail = await service.get_project(
        viewer=admin, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
    )
    assert detail["visibility"] == "private"
    assert len(detail["milestones"]) == 1
    assert detail["milestones"][0]["overdue"] is True  # 2026-01-01 < fixed now
    # Regular member → 403 for private.
    regular = await _second_member(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.get_project(
            viewer=regular, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
        )
    # Guest without grant → 404.
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(NotFoundError):
        await service.get_project(
            viewer=guest, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
        )
    # Unknown project → 404.
    with pytest.raises(NotFoundError):
        await service.get_project(
            viewer=admin, workspace_id=workspace.id, project_id=uuid.uuid4()
        )


async def test_assert_can_write_guest_matrix(session_factory):
    """L8:guest 写门三分支,与视图门(assert_can_view)口径一致——
    无授权 → 404(项目不可见,写门不成存在性 oracle);
    read 授权 → 403(可见但不可写);write 授权 → 放行。"""
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body(visibility="private")
    )
    project_id = uuid.UUID(created["id"])
    guest = await _second_member(session_factory, workspace, role="guest")

    async def _load_project():
        async with session_factory() as session:
            return await session.get(Project, project_id)

    # ① 无授权 → 404(不是 403)
    with pytest.raises(NotFoundError) as not_found:
        async with session_factory() as session:
            await service.assert_can_write(
                session, viewer=guest, project=await _load_project()
            )
    assert not_found.value.code == "not_found"

    # ② read 授权 → 403
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=workspace.id,
                member_id=guest.id,
                project_id=project_id,
                permission="read",
            )
        )
    with pytest.raises(ForbiddenError):
        async with session_factory() as session:
            await service.assert_can_write(
                session, viewer=guest, project=await _load_project()
            )

    # ③ write 授权 → 放行
    async with session_factory() as session, session.begin():
        access = await session.scalar(
            select(MemberProjectAccess).where(
                MemberProjectAccess.member_id == guest.id,
                MemberProjectAccess.project_id == project_id,
            )
        )
        access.permission = "write"
    async with session_factory() as session:
        await service.assert_can_write(
            session, viewer=guest, project=await _load_project()
        )  # no raise


async def test_get_project_progress_cache_fallback(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    async with session_factory() as session, session.begin():
        project = await session.get(Project, uuid.UUID(created["id"]))
        project.progress_cache = 0.62
    detail = await service.get_project(
        viewer=member, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
    )
    assert detail["progress"] == pytest.approx(0.62)
    assert detail["open_issues"] == 0  # issue aggregation lands with issue.md


# --- update -----------------------------------------------------------------


async def test_update_project_changes_emit_event(session_factory):
    workspace, member, service = await _setup(session_factory)
    admin = await _second_member(session_factory, workspace, role="admin")
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    updated = await service.update_project(
        actor=admin,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
        patch=ProjectPatch(status="active", health="at_risk", description="desc"),
    )
    assert updated["status"] == "active"
    assert updated["health"] == "at_risk"
    events = await _events(session_factory, "project.updated")
    assert len(events) >= 1
    assert events[-1].payload["data"]["changes"]["status"] == "active"


async def test_update_project_no_change_is_noop(session_factory):
    workspace, member, service = await _setup(session_factory)
    admin = await _second_member(session_factory, workspace, role="admin")
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    updated = await service.update_project(
        actor=admin,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
        patch=ProjectPatch(name="Site Revamp"),  # same value
    )
    assert updated["id"] == created["id"]
    assert await _events(session_factory, "project.updated") == []


async def test_update_project_if_match_optimistic_concurrency(session_factory):
    workspace, member, service = await _setup(session_factory)
    admin = await _second_member(session_factory, workspace, role="admin")
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    stale = "2020-01-01T00:00:00Z"
    with pytest.raises(ConflictError) as excinfo:
        await service.update_project(
            actor=admin,
            workspace_id=workspace.id,
            project_id=uuid.UUID(created["id"]),
            patch=ProjectPatch(name="Renamed"),
            if_match=stale,
        )
    assert excinfo.value.code == "conflict"
    # Correct version (from the create response) succeeds.
    current = created["updated_at"].isoformat().replace("+00:00", "Z")
    updated = await service.update_project(
        actor=admin,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
        patch=ProjectPatch(name="Renamed"),
        if_match=current,
    )
    assert updated["name"] == "Renamed"
    # Garbage If-Match → conflict.
    with pytest.raises(ConflictError):
        await service.update_project(
            actor=admin,
            workspace_id=workspace.id,
            project_id=uuid.UUID(created["id"]),
            patch=ProjectPatch(name="Again"),
            if_match="not-a-timestamp",
        )


async def test_update_project_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    admin = await _second_member(session_factory, workspace, role="admin")
    created = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(start_date=date(2026, 8, 1)),
    )
    project_id = uuid.UUID(created["id"])
    with pytest.raises(ValidationError):
        await service.update_project(
            actor=admin, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(status="bogus"),
        )
    with pytest.raises(ValidationError):
        await service.update_project(
            actor=admin, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(health="bogus"),
        )
    with pytest.raises(ValidationError):
        await service.update_project(
            actor=admin, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(target_date=date(2026, 7, 1)),  # before start_date
        )
    with pytest.raises(ValidationError):
        await service.update_project(
            actor=admin, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(lead_member_id=uuid.uuid4()),
        )
    # Nulling health is allowed (valid enum check skipped for None).
    updated = await service.update_project(
        actor=admin, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(health=None),
    )
    assert updated["health"] is None


async def test_update_project_auth_matrix(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    regular = await _second_member(session_factory, workspace)
    # Regular workspace member without project membership → 403.
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=regular, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(name="X"),
        )
    # Add as project member → write allowed.
    await service.add_project_member(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=AddProjectMemberRequest(member_id=str(regular.id), role="member"),
    )
    updated = await service.update_project(
        actor=regular, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(name="By Member"),
    )
    assert updated["name"] == "By Member"


async def test_update_project_lead_change(session_factory):
    workspace, member, service = await _setup(session_factory)
    admin = await _second_member(session_factory, workspace, role="admin")
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    updated = await service.update_project(
        actor=admin, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(lead_member_id=admin.id),
    )
    assert updated["lead_member_id"] == str(admin.id)
    # Clear the lead (explicit null).
    updated = await service.update_project(
        actor=admin, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(lead_member_id=None),
    )
    assert updated["lead_member_id"] is None


async def test_update_project_lead_reassignment_requires_lead_or_admin(session_factory):
    """PJ-H1: changing lead_member_id (incl. nulling) is a lead/admin-only act.

    A plain project member must NOT escalate by self-assigning the lead
    (project.md §3.4 authorization matrix): reassignment and clearing the
    lead require the current lead or a workspace admin, while ordinary
    field edits stay at the member write gate.
    """
    # Arrange
    workspace, creator, service = await _setup(session_factory)
    created = await service.create_project(
        actor=creator,
        workspace_id=workspace.id,
        body=_body(lead_member_id=str(creator.id)),
    )
    project_id = uuid.UUID(created["id"])
    member = await _second_member(session_factory, workspace)
    other = await _second_member(session_factory, workspace)
    await service.add_project_member(
        actor=creator,
        workspace_id=workspace.id,
        project_id=project_id,
        body=AddProjectMemberRequest(member_id=str(member.id), role="member"),
    )

    # Act / Assert — member self-assignment → 403.
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(lead_member_id=member.id),
        )
    # Member reassigning the lead to someone else → 403.
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(lead_member_id=other.id),
        )
    # Member clearing the lead → 403.
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(lead_member_id=None),
        )
    # A gate failure aborts the whole patch — the in-flight name edit in the
    # same request must not persist either (single transaction rollback).
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(name="Hacked", lead_member_id=member.id),
        )

    # State is intact: lead unchanged, and the escalation loop stays closed —
    # the failed self-assignment grants no delete/archive powers.
    detail = await service.get_project(
        viewer=creator, workspace_id=workspace.id, project_id=project_id
    )
    assert detail["lead_member_id"] == str(creator.id)
    assert detail["name"] == "Site Revamp"
    with pytest.raises(ForbiddenError):
        await service.delete_project(
            actor=member, workspace_id=workspace.id, project_id=project_id
        )

    # Current lead may reassign → 200.
    reassigned = await service.update_project(
        actor=creator, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(lead_member_id=member.id),
    )
    assert reassigned["lead_member_id"] == str(member.id)
    # The new lead (member role, lead via lead_member_id) may clear it → 200.
    nulled = await service.update_project(
        actor=member, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(lead_member_id=None),
    )
    assert nulled["lead_member_id"] is None
    # Workspace admin may reassign → 200.
    admin = await _second_member(session_factory, workspace, role="admin")
    by_admin = await service.update_project(
        actor=admin, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(lead_member_id=creator.id),
    )
    assert by_admin["lead_member_id"] == str(creator.id)


async def test_get_project_reports_lead_role_when_also_a_member(session_factory):
    """A member reassigned to lead via lead_member_id reports my_role=lead.

    The frontend lead-reassignment gate (§4.2) keys off my_role, so a viewer
    who is the lead — even while holding a project_members member row — must
    see my_role=lead, else the UI wrongly locks them out of reassigning.
    """
    workspace, creator, service = await _setup(session_factory)
    created = await service.create_project(
        actor=creator,
        workspace_id=workspace.id,
        body=_body(lead_member_id=str(creator.id)),
    )
    project_id = uuid.UUID(created["id"])
    member = await _second_member(session_factory, workspace)
    await service.add_project_member(
        actor=creator,
        workspace_id=workspace.id,
        project_id=project_id,
        body=AddProjectMemberRequest(member_id=str(member.id), role="member"),
    )
    await service.update_project(
        actor=creator,
        workspace_id=workspace.id,
        project_id=project_id,
        patch=ProjectPatch(lead_member_id=member.id),
    )
    detail = await service.get_project(
        viewer=member, workspace_id=workspace.id, project_id=project_id
    )
    assert detail["my_role"] == "lead"


async def test_update_project_guest_write_cannot_self_assign_lead(session_factory):
    """PJ-H1 guest path: a write grant is project editing, not lead powers."""
    # Arrange
    workspace, creator, service = await _setup(session_factory)
    created = await service.create_project(
        actor=creator,
        workspace_id=workspace.id,
        body=_body(lead_member_id=str(creator.id)),
    )
    project_id = uuid.UUID(created["id"])
    guest = await _second_member(session_factory, workspace, role="guest")
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=workspace.id,
                member_id=guest.id,
                project_id=project_id,
                permission="write",
            )
        )

    # Sanity: the write grant covers ordinary field edits.
    edited = await service.update_project(
        actor=guest, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(name="Guest Edit"),
    )
    assert edited["name"] == "Guest Edit"

    # Act / Assert — guest self-assigning the lead → 403.
    with pytest.raises(ForbiddenError):
        await service.update_project(
            actor=guest, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(lead_member_id=guest.id),
        )
    # Lead unchanged; no escalation to delete.
    detail = await service.get_project(
        viewer=guest, workspace_id=workspace.id, project_id=project_id
    )
    assert detail["lead_member_id"] == str(creator.id)
    with pytest.raises(ForbiddenError):
        await service.delete_project(
            actor=guest, workspace_id=workspace.id, project_id=project_id
        )


# --- archive / delete -------------------------------------------------------


async def test_archive_makes_project_readonly(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    archived = await service.archive_project(
        actor=member, workspace_id=workspace.id, project_id=project_id
    )
    assert archived["archived"] is True
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.update_project(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            patch=ProjectPatch(name="Nope"),
        )
    assert excinfo.value.code == "project_archived"
    # Idempotent re-archive.
    again = await service.archive_project(
        actor=member, workspace_id=workspace.id, project_id=project_id
    )
    assert again["archived"] is True
    # Unarchive restores writability.
    restored = await service.unarchive_project(
        actor=member, workspace_id=workspace.id, project_id=project_id
    )
    assert restored["archived"] is False
    ok = await service.update_project(
        actor=member, workspace_id=workspace.id, project_id=project_id,
        patch=ProjectPatch(name="Writable"),
    )
    assert ok["name"] == "Writable"
    # Archive events emitted.
    assert await _events(session_factory, "project.archived")
    assert await _events(session_factory, "project.unarchived")


async def test_archive_requires_lead_or_admin(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    regular = await _second_member(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.archive_project(
            actor=regular, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
        )
    # Lead via projects.lead_member_id qualifies.
    admin = await _second_member(session_factory, workspace, role="admin")
    await service.update_project(
        actor=admin,
        workspace_id=workspace.id,
        project_id=uuid.UUID(created["id"]),
        patch=ProjectPatch(lead_member_id=regular.id),
    )
    archived = await service.archive_project(
        actor=regular, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
    )
    assert archived["archived"] is True


async def test_delete_project_soft_and_prefix_stays_reserved(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    result = await service.delete_project(
        actor=member, workspace_id=workspace.id, project_id=project_id
    )
    assert result == {"id": str(project_id), "deleted": True}
    # Soft-deleted: invisible via list/get.
    items, _ = await service.list_projects(viewer=member, workspace_id=workspace.id)
    assert items == []
    with pytest.raises(NotFoundError):
        await service.get_project(
            viewer=member, workspace_id=workspace.id, project_id=project_id
        )
    # Prefix permanently reserved (README §6.3) — reuse rejected.
    with pytest.raises(ConflictError) as excinfo:
        await service.create_project(
            actor=member, workspace_id=workspace.id, body=_body(name="Again")
        )
    assert excinfo.value.code == "project_key_taken"
    assert await _events(session_factory, "project.deleted")


async def test_delete_requires_lead(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    regular = await _second_member(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.delete_project(
            actor=regular, workspace_id=workspace.id, project_id=uuid.UUID(created["id"])
        )


# --- updates trail ----------------------------------------------------------


async def test_add_update_writes_trail_and_backfills(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    update = await service.add_update(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=AddProjectUpdateRequest(health="at_risk", message="risk note"),
    )
    assert update["health"] == "at_risk"
    assert update["author"]["id"] == str(member.id)
    detail = await service.get_project(
        viewer=member, workspace_id=workspace.id, project_id=project_id
    )
    assert detail["health"] == "at_risk"
    # Events: project_update.added (detail channel only) + project.updated.
    added = await _events(session_factory, "project_update.added")
    assert len(added) == 1
    assert added[0].payload["channel"] == f"project:{project_id}"
    assert await _events(session_factory, "project.updated")
    async with session_factory() as session:
        trail = (
            (
                await session.execute(
                    select(ProjectUpdate).where(ProjectUpdate.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(trail) == 1
    assert trail[0].message == "risk note"


async def test_add_update_requires_content_and_validates(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    with pytest.raises(ValidationError):
        await service.add_update(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectUpdateRequest(),
        )
    with pytest.raises(ValidationError):
        await service.add_update(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectUpdateRequest(health="bogus"),
        )
    with pytest.raises(ValidationError):
        await service.add_update(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectUpdateRequest(status="bogus"),
        )


async def test_list_updates_pagination(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    for index in range(3):
        await service.add_update(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectUpdateRequest(message=f"note {index}"),
        )
    items, next_cursor = await service.list_updates(
        viewer=member, workspace_id=workspace.id, project_id=project_id, limit=2
    )
    assert len(items) == 2
    rest, tail = await service.list_updates(
        viewer=member, workspace_id=workspace.id, project_id=project_id, limit=2,
        cursor=next_cursor,
    )
    assert len(rest) == 1
    assert tail is None


# --- milestones -------------------------------------------------------------


async def test_milestone_crud_and_overdue(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    milestone = await service.create_milestone(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=CreateMilestoneRequest(title="v1.0", target_date=date(2026, 8, 31)),
    )
    assert milestone["state"] == "open"
    assert milestone["overdue"] is False
    with pytest.raises(ValidationError):
        await service.create_milestone(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=CreateMilestoneRequest(title=""),
        )
    # Update: close it + overdue derivation for past open milestones.
    updated = await service.update_milestone(
        actor=member,
        workspace_id=workspace.id,
        milestone_id=uuid.UUID(milestone["id"]),
        patch=MilestonePatch(state="closed", title="v1.0 shipped"),
    )
    assert updated["state"] == "closed"
    assert updated["title"] == "v1.0 shipped"
    past = await service.create_milestone(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=CreateMilestoneRequest(title="late", target_date=date(2026, 1, 1)),
    )
    assert past["overdue"] is True
    with pytest.raises(ValidationError):
        await service.update_milestone(
            actor=member,
            workspace_id=workspace.id,
            milestone_id=uuid.UUID(milestone["id"]),
            patch=MilestonePatch(state="bogus"),
        )
    # No-change patch is a no-op.
    same = await service.update_milestone(
        actor=member,
        workspace_id=workspace.id,
        milestone_id=uuid.UUID(milestone["id"]),
        patch=MilestonePatch(state="closed"),
    )
    assert same["id"] == milestone["id"]
    # List + filter + delete.
    items, _ = await service.list_milestones(
        viewer=member, workspace_id=workspace.id, project_id=project_id
    )
    assert len(items) == 2
    items, _ = await service.list_milestones(
        viewer=member, workspace_id=workspace.id, project_id=project_id, state="open"
    )
    assert [item["id"] for item in items] == [past["id"]]
    with pytest.raises(ValidationError):
        await service.list_milestones(
            viewer=member, workspace_id=workspace.id, project_id=project_id, state="bogus"
        )
    deleted = await service.delete_milestone(
        actor=member, workspace_id=workspace.id, milestone_id=uuid.UUID(milestone["id"])
    )
    assert deleted["deleted"] is True
    assert await _events(session_factory, "milestone.created")
    assert await _events(session_factory, "milestone.updated")
    assert await _events(session_factory, "milestone.deleted")


async def test_milestone_write_requires_membership(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    regular = await _second_member(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.create_milestone(
            actor=regular,
            workspace_id=workspace.id,
            project_id=uuid.UUID(created["id"]),
            body=CreateMilestoneRequest(title="x"),
        )
    with pytest.raises(NotFoundError):
        await service.update_milestone(
            actor=member,
            workspace_id=workspace.id,
            milestone_id=uuid.uuid4(),
            patch=MilestonePatch(title="x"),
        )
    with pytest.raises(NotFoundError):
        await service.delete_milestone(
            actor=member, workspace_id=workspace.id, milestone_id=uuid.uuid4()
        )


# --- cycles -----------------------------------------------------------------


def _cycle_body(**overrides) -> CreateCycleRequest:
    base = {
        "name": "Sprint 12",
        "starts_at": date(2026, 8, 1),
        "ends_at": date(2026, 8, 14),
    }
    base.update(overrides)
    return CreateCycleRequest(**base)


async def test_cycle_create_validation_and_events(session_factory):
    workspace, member, service = await _setup(session_factory)
    cycle = await service.create_cycle(
        actor=member, workspace_id=workspace.id, body=_cycle_body()
    )
    assert cycle["state"] == "planned"
    assert cycle["auto_roll"] is False
    assert await _events(session_factory, "cycle.updated")  # creation also emits cycle.updated
    with pytest.raises(ValidationError):
        await service.create_cycle(
            actor=member,
            workspace_id=workspace.id,
            body=_cycle_body(starts_at=date(2026, 8, 14), ends_at=date(2026, 8, 1)),
        )
    with pytest.raises(ValidationError):
        await service.create_cycle(
            actor=member, workspace_id=workspace.id, body=_cycle_body(state="bogus")
        )
    with pytest.raises(ValidationError):
        await service.create_cycle(
            actor=member, workspace_id=workspace.id, body=_cycle_body(name="")
        )


async def test_cycle_bound_to_project_and_auth(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    cycle = await service.create_cycle(
        actor=member,
        workspace_id=workspace.id,
        body=_cycle_body(project_id=created["id"]),
    )
    assert cycle["project_id"] == created["id"]
    regular = await _second_member(session_factory, workspace)
    # Project-bound cycle: write requires project membership.
    with pytest.raises(ForbiddenError):
        await service.update_cycle(
            actor=regular,
            workspace_id=workspace.id,
            cycle_id=uuid.UUID(cycle["id"]),
            patch=CyclePatch(state="active"),
        )
    # Unknown project id → 404.
    with pytest.raises(NotFoundError):
        await service.create_cycle(
            actor=member,
            workspace_id=workspace.id,
            body=_cycle_body(project_id=str(uuid.uuid4())),
        )
    # Guests cannot manage workspace-level cycles.
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await service.create_cycle(
            actor=guest, workspace_id=workspace.id, body=_cycle_body()
        )


async def test_cycle_update_noop_and_auto_roll(session_factory):
    workspace, member, service = await _setup(session_factory)
    cycle = await service.create_cycle(
        actor=member, workspace_id=workspace.id, body=_cycle_body(auto_roll=True)
    )
    cycle_id = uuid.UUID(cycle["id"])
    # No-change patch → no event.
    same = await service.update_cycle(
        actor=member, workspace_id=workspace.id, cycle_id=cycle_id,
        patch=CyclePatch(name="Sprint 12"),
    )
    assert same["id"] == cycle["id"]
    updated_events = await _events(session_factory, "cycle.updated")
    assert len(updated_events) == 1  # only the create event so far
    # Complete with auto_roll → next cycle created in the same transaction.
    completed = await service.update_cycle(
        actor=member, workspace_id=workspace.id, cycle_id=cycle_id,
        patch=CyclePatch(state="completed"),
    )
    assert completed["state"] == "completed"
    next_cycle = completed["next_cycle"]
    assert next_cycle["name"] == "Sprint 12+1"
    assert next_cycle["starts_at"] == "2026-08-15"
    assert next_cycle["ends_at"] == "2026-08-28"
    assert next_cycle["auto_roll"] is True
    async with session_factory() as session:
        count = len(
            (await session.execute(select(Cycle).where(Cycle.workspace_id == workspace.id)))
            .scalars()
            .all()
        )
    assert count == 2
    # Invalid transition range check on update.
    with pytest.raises(ValidationError):
        await service.update_cycle(
            actor=member, workspace_id=workspace.id, cycle_id=cycle_id,
            patch=CyclePatch(starts_at=date(2026, 9, 1)),  # after ends_at
        )
    with pytest.raises(ValidationError):
        await service.update_cycle(
            actor=member, workspace_id=workspace.id, cycle_id=cycle_id,
            patch=CyclePatch(state="bogus"),
        )


async def test_cycle_list_filters_and_visibility(session_factory):
    workspace, member, service = await _setup(session_factory)
    private = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(visibility="private"),
    )
    await service.create_cycle(
        actor=member, workspace_id=workspace.id, body=_cycle_body(name="WS Cycle")
    )
    await service.create_cycle(
        actor=member,
        workspace_id=workspace.id,
        body=_cycle_body(name="Private Cycle", project_id=private["id"],
                       starts_at=date(2026, 9, 1), ends_at=date(2026, 9, 14)),
    )
    regular = await _second_member(session_factory, workspace)
    items, _ = await service.list_cycles(viewer=regular, workspace_id=workspace.id)
    assert [item["name"] for item in items] == ["WS Cycle"]  # private project cycle hidden
    items, _ = await service.list_cycles(viewer=member, workspace_id=workspace.id)
    assert len(items) == 2
    items, _ = await service.list_cycles(
        viewer=member, workspace_id=workspace.id, project_id=uuid.UUID(private["id"])
    )
    assert [item["name"] for item in items] == ["Private Cycle"]
    with pytest.raises(ValidationError):
        await service.list_cycles(viewer=member, workspace_id=workspace.id, state="bogus")
    # Guests see no workspace-level cycles.
    guest = await _second_member(session_factory, workspace, role="guest")
    items, _ = await service.list_cycles(viewer=guest, workspace_id=workspace.id)
    assert items == []


async def test_cycle_not_found(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await service.update_cycle(
            actor=member, workspace_id=workspace.id, cycle_id=uuid.uuid4(),
            patch=CyclePatch(state="active"),
        )


# --- project members --------------------------------------------------------


async def test_project_member_lifecycle(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    regular = await _second_member(session_factory, workspace)
    added = await service.add_project_member(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=AddProjectMemberRequest(member_id=str(regular.id), role="member"),
    )
    assert added["role"] == "member"
    with pytest.raises(ConflictError) as excinfo:
        await service.add_project_member(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id=str(regular.id)),
        )
    assert excinfo.value.code == "project_member_exists"
    with pytest.raises(ValidationError):
        await service.add_project_member(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id=str(regular.id), role="bogus"),
        )
    with pytest.raises(ValidationError):
        await service.add_project_member(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id=str(uuid.uuid4())),
        )
    with pytest.raises(ValidationError):
        await service.add_project_member(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id="not-a-uuid"),
        )
    updated = await service.update_project_member(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        member_id=regular.id,
        body=UpdateProjectMemberRequest(role="lead"),
    )
    assert updated["role"] == "lead"
    # No-change role update is silent.
    again = await service.update_project_member(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        member_id=regular.id,
        body=UpdateProjectMemberRequest(role="lead"),
    )
    assert again["role"] == "lead"
    items, _ = await service.list_project_members(
        viewer=member, workspace_id=workspace.id, project_id=project_id
    )
    assert len(items) == 2  # creator (lead) + regular
    assert {item["member"]["id"] for item in items} == {str(regular.id), str(member.id)}
    removed = await service.remove_project_member(
        actor=member, workspace_id=workspace.id, project_id=project_id,
        member_id=regular.id,
    )
    assert removed["removed"] is True
    with pytest.raises(NotFoundError):
        await service.remove_project_member(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            member_id=regular.id,
        )
    with pytest.raises(NotFoundError):
        await service.update_project_member(
            actor=member, workspace_id=workspace.id, project_id=project_id,
            member_id=regular.id, body=UpdateProjectMemberRequest(role="member"),
        )


async def test_project_member_management_requires_lead(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    regular = await _second_member(session_factory, workspace)
    other = await _second_member(session_factory, workspace)
    # Creator has no project role and is not lead → forbidden.
    with pytest.raises(ForbiddenError):
        await service.add_project_member(
            actor=regular,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id=str(other.id)),
        )


# --- templates --------------------------------------------------------------


async def test_template_crud(session_factory):
    workspace, member, service = await _setup(session_factory)
    template = await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(name="Standard", template_body={"description": "d"}),
    )
    assert template["creator"]["id"] == str(member.id)
    with pytest.raises(ConflictError) as excinfo:
        await service.create_template(
            actor=member,
            workspace_id=workspace.id,
            body=CreateProjectTemplateRequest(name="Standard", template_body={}),
        )
    assert excinfo.value.code == "template_name_taken"
    with pytest.raises(ValidationError):
        await service.create_template(
            actor=member,
            workspace_id=workspace.id,
            body=CreateProjectTemplateRequest(name="", template_body={}),
        )
    items, _ = await service.list_templates(viewer=member, workspace_id=workspace.id)
    assert len(items) == 1
    template_id = uuid.UUID(template["id"])
    updated = await service.update_template(
        actor=member,
        workspace_id=workspace.id,
        template_id=template_id,
        body=CreateProjectTemplateRequest(name="Renamed", template_body={"a": 1}),
    )
    assert updated["name"] == "Renamed"
    # Non-creator non-admin → forbidden.
    regular = await _second_member(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.update_template(
            actor=regular,
            workspace_id=workspace.id,
            template_id=template_id,
            body=UpdateProjectTemplateRequest(name="X", template_body={}),
        )
    with pytest.raises(ForbiddenError):
        await service.delete_template(
            actor=regular, workspace_id=workspace.id, template_id=template_id
        )
    # Admin can.
    admin = await _second_member(session_factory, workspace, role="admin")
    deleted = await service.delete_template(
        actor=admin, workspace_id=workspace.id, template_id=template_id
    )
    assert deleted["deleted"] is True
    with pytest.raises(NotFoundError):
        await service.delete_template(
            actor=admin, workspace_id=workspace.id, template_id=template_id
        )


async def test_template_guest_forbidden(session_factory):
    workspace, _member, service = await _setup(session_factory)
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await service.create_template(
            actor=guest,
            workspace_id=workspace.id,
            body=CreateProjectTemplateRequest(name="T", template_body={}),
        )
    with pytest.raises(ForbiddenError):
        await service.list_templates(viewer=guest, workspace_id=workspace.id)


async def test_instantiate_template_full(session_factory):
    workspace, member, service = await _setup(session_factory)
    template = await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(
            name="Launch",
            template_body={
                "description": "from template",
                "default_visibility": "private",
                "initial_milestones": [{"title": "GA", "target_date": "2026-08-31"}, {}],
                "initial_cycles": [
                    {
                        "name": "S1",
                        "starts_at": "2026-08-01",
                        "ends_at": "2026-08-14",
                        "auto_roll": True,
                    },
                    {"name": "", "starts_at": "2026-08-01", "ends_at": "2026-08-14"},
                ],
                "status_set_seed": ["todo", "done"],
                "default_view_config": {"columns": []},
            },
        ),
    )
    result = await service.instantiate_template(
        actor=member,
        workspace_id=workspace.id,
        template_id=uuid.UUID(template["id"]),
        body=InstantiateProjectTemplateRequest(name="Q3 Launch", key="Q3L"),
    )
    assert result["name"] == "Q3 Launch"
    assert result["visibility"] == "private"
    assert result["description"] == "from template"
    assert len(result["milestone_ids"]) == 1  # invalid item skipped
    assert len(result["cycle_ids"]) == 1
    assert "initial_milestones:invalid_item" in result["skipped"]
    assert "initial_cycles:invalid_item" in result["skipped"]
    assert "status_set_seed:issue_module_pending" in result["skipped"]
    assert "default_view_config:kanban_module_pending" in result["skipped"]
    # Key conflicts on instantiation are rejected through the registry.
    with pytest.raises(ConflictError):
        await service.instantiate_template(
            actor=member,
            workspace_id=workspace.id,
            template_id=uuid.UUID(template["id"]),
            body=InstantiateProjectTemplateRequest(name="Dup", key="Q3L"),
        )


async def test_instantiate_template_overrides(session_factory):
    workspace, member, service = await _setup(session_factory)
    template = await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(
            name="Base",
            template_body={
                "initial_cycles": [
                    {"name": "bad", "starts_at": "2026-08-14", "ends_at": "2026-08-01"}
                ]
            },
        ),
    )
    result = await service.instantiate_template(
        actor=member,
        workspace_id=workspace.id,
        template_id=uuid.UUID(template["id"]),
        body=InstantiateProjectTemplateRequest(
            name="Override", key="OVR", overrides={"description": "override"}
        ),
    )
    assert result["description"] == "override"
    assert "initial_cycles:invalid_item" in result["skipped"]
    with pytest.raises(NotFoundError):
        await service.instantiate_template(
            actor=member,
            workspace_id=workspace.id,
            template_id=uuid.uuid4(),
            body=InstantiateProjectTemplateRequest(name="X", key="ZZZ"),
        )


# --- numbering + resolvers --------------------------------------------------


async def test_next_issue_number_monotonic(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    async with session_factory() as session, session.begin():
        first = await service.next_issue_number(session, project_id=project_id)
        second = await service.next_issue_number(session, project_id=project_id)
    assert (first, second) == (1, 2)
    async with session_factory() as session:
        seq = await session.scalar(select(Project.issue_seq).where(Project.id == project_id))
    assert seq == 2


async def test_resolvers_return_workspace(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    assert await service.resolve_project_workspace(project_id) == workspace.id
    assert await service.resolve_project_workspace(uuid.uuid4()) is None
    milestone = await service.create_milestone(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        body=CreateMilestoneRequest(title="M"),
    )
    assert await service.resolve_milestone_workspace(uuid.UUID(milestone["id"])) == workspace.id
    assert await service.resolve_milestone_workspace(uuid.uuid4()) is None
    cycle = await service.create_cycle(
        actor=member, workspace_id=workspace.id, body=_cycle_body()
    )
    assert await service.resolve_cycle_workspace(uuid.UUID(cycle["id"])) == workspace.id
    assert await service.resolve_cycle_workspace(uuid.uuid4()) is None
    template = await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(name="T", template_body={}),
    )
    assert await service.resolve_template_workspace(uuid.UUID(template["id"])) == workspace.id
    assert await service.resolve_template_workspace(uuid.uuid4()) is None


async def test_archived_project_blocks_all_writes(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=_body()
    )
    project_id = uuid.UUID(created["id"])
    await service.archive_project(
        actor=member, workspace_id=workspace.id, project_id=project_id
    )
    with pytest.raises(BusinessRuleError):
        await service.add_update(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectUpdateRequest(message="x"),
        )
    with pytest.raises(BusinessRuleError):
        await service.create_milestone(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=CreateMilestoneRequest(title="x"),
        )
    with pytest.raises(BusinessRuleError):
        await service.delete_project(
            actor=member, workspace_id=workspace.id, project_id=project_id
        )
    with pytest.raises(BusinessRuleError):
        await service.add_project_member(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            body=AddProjectMemberRequest(member_id=str(member.id)),
        )


async def test_private_project_events_only_detail_channel(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=_body(visibility="private"),
    )
    events = await _events(session_factory, "project.created")
    assert len(events) == 1
    assert events[0].payload["channel"] == f"project:{created['id']}"


async def test_limit_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.list_projects(viewer=member, workspace_id=workspace.id, limit=0)
    # Huge limits clamp to MAX_PAGE_LIMIT without error.
    items, _ = await service.list_projects(
        viewer=member, workspace_id=workspace.id, limit=10_000
    )
    assert items == []


async def test_update_template_conflict_and_noop(session_factory):
    workspace, member, service = await _setup(session_factory)
    first = await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(name="A", template_body={}),
    )
    await service.create_template(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectTemplateRequest(name="B", template_body={}),
    )
    # Rename A → B conflicts.
    with pytest.raises(ConflictError):
        await service.update_template(
            actor=member,
            workspace_id=workspace.id,
            template_id=uuid.UUID(first["id"]),
            body=UpdateProjectTemplateRequest(name="B", template_body={}),
        )


# --- P2: If-Match lost-update guard (CWE-362) + public→private removal frame ---


async def _raw_bump_updated_at(session_factory, project_id: uuid.UUID) -> None:
    """Simulate a concurrent writer advancing updated_at outside our transaction."""
    from sqlalchemy import text

    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE projects SET updated_at = updated_at + interval '1 second' "
                 "WHERE id = :pid"),
            {"pid": project_id},
        )


async def test_update_project_if_match_detects_concurrent_write(session_factory):
    """A concurrent write that advances updated_at between reading the version and
    the locked PATCH must be rejected 409 (row lock + version re-check)."""
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=CreateProjectRequest(name="A", key="IFM")
    )
    project_id = uuid.UUID(created["id"])
    stale_version = created["updated_at"].isoformat().replace("+00:00", "Z")
    # A concurrent writer advances the row version before our locked PATCH runs.
    await _raw_bump_updated_at(session_factory, project_id)
    with pytest.raises(ConflictError) as excinfo:
        await service.update_project(
            actor=member,
            workspace_id=workspace.id,
            project_id=project_id,
            patch=ProjectPatch(name="A2"),
            if_match=stale_version,
        )
    assert excinfo.value.code == "conflict"


async def test_update_project_if_match_succeeds_without_concurrent_write(session_factory):
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member, workspace_id=workspace.id, body=CreateProjectRequest(name="B", key="IFK")
    )
    project_id = uuid.UUID(created["id"])
    version = created["updated_at"].isoformat().replace("+00:00", "Z")
    updated = await service.update_project(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        patch=ProjectPatch(name="B2"),
        if_match=version,
    )
    assert updated["name"] == "B2"


async def test_public_to_private_emits_workspace_list_removal(session_factory):
    """Flipping public→private must drop the card from non-members' lists now,
    not wait for a reload: emit a removal frame on the workspace list channel."""
    workspace, member, service = await _setup(session_factory)
    created = await service.create_project(
        actor=member,
        workspace_id=workspace.id,
        body=CreateProjectRequest(name="C", key="VIS", visibility="public"),
    )
    project_id = uuid.UUID(created["id"])
    await service.update_project(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        patch=ProjectPatch(visibility="private"),
    )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    removals = [
        r for r in rows
        if r.payload.get("event") == "project.deleted"
        and r.payload.get("channel") == f"workspace:{workspace.id}:projects"
        and r.payload.get("data", {}).get("id") == str(project_id)
    ]
    assert len(removals) == 1
    # private → public must NOT emit a removal (the project re-appears via updated).
    await service.update_project(
        actor=member,
        workspace_id=workspace.id,
        project_id=project_id,
        patch=ProjectPatch(visibility="public"),
    )
    async with session_factory() as session:
        rows2 = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    removals2 = [
        r for r in rows2
        if r.payload.get("event") == "project.deleted"
        and r.payload.get("channel") == f"workspace:{workspace.id}:projects"
    ]
    assert len(removals2) == 1  # unchanged: no new removal on private→public

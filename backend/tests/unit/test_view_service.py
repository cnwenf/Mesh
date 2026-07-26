"""View service tests — direct service calls against real PostgreSQL.

Covers kanban.md §3/§5.1 (definition-layer slice): CRUD, duplicate, WIP
config, sidebar reorder, the §3.4 authorization matrix, scope uniqueness
(README §6.3), optimistic concurrency (README §6.14 If-Match), no-op PATCH
semantics (§6.9), shallow JSONB merge and the view.updated outbox emission
through the unique write path (§6.6/§6.7).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.view import View
from mesh.db.models.workspace import Workspace
from mesh.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from mesh.views.schemas import CreateViewRequest, WipRequest
from mesh.views.service import ViewService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


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


async def _add_member(
    session_factory, workspace: Workspace, *, role: str = "member"
) -> Member:
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
    return member


async def _setup(session_factory, *, role: str = "member"):
    """Workspace + one human member; returns (workspace, member, service)."""
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    member = await _add_member(session_factory, workspace, role=role)
    service = ViewService(session_factory, clock=_clock)
    return workspace, member, service


async def _create_project(
    session_factory, workspace: Workspace, *, visibility: str = "public", key: str | None = None
) -> Project:
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=workspace.id,
            name=f"Project {uuid.uuid4().hex[:6]}",
            key=key or f"K{uuid.uuid4().hex[:5].upper()}",
            visibility=visibility,
        )
        session.add(project)
    return project


async def _grant_project_access(
    session_factory, workspace: Workspace, project: Project, member: Member, role: str = "member"
) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=project.id, member_id=member.id, role=role
            )
        )


def _create_body(**overrides) -> CreateViewRequest:
    fields = {"name": "Sprint Board"}
    fields.update(overrides)
    return CreateViewRequest(**fields)


async def _outbox_realtime(session_factory) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "realtime.publish"
                    )
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_view_minimal_defaults(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    assert created["name"] == "Sprint Board"
    assert created["layout"] == "board"
    assert created["visibility"] == "private"
    assert created["filters"] == {}
    assert created["sort"] == []
    assert created["display_fields"] == []
    assert created["board_settings"] == {}
    assert created["position"] == 1.0
    assert created["is_default"] is False
    assert created["can_write"] is True

    async with session_factory() as session:
        stored = await session.scalar(select(View).where(View.id == uuid.UUID(created["id"])))
    assert stored is not None
    assert stored.owner_member_id == member.id
    assert stored.workspace_id == workspace.id


async def test_create_view_full_config_project_scoped(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    created = await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(
            layout="list",
            visibility="shared",
            project_id=str(project.id),
            filters={
                "operator": "AND",
                "conditions": [{"field": "priority", "op": "in", "value": ["high"]}],
            },
            group_by="priority",
            sort=[{"field": "position", "order": "asc"}],
            display_fields=["status", "assignee"],
            board_settings={"card_fields": ["labels"]},
        ),
    )
    assert created["project_id"] == str(project.id)
    assert created["layout"] == "list"
    assert created["group_by"] == "priority"
    assert created["sort"] == [{"field": "position", "order": "asc"}]
    assert created["display_fields"] == ["status", "assignee"]


async def test_create_view_position_increments(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    first = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="One")
    )
    second = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="Two")
    )
    assert first["position"] == 1.0
    assert second["position"] == 2.0


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"layout": "gantt"}, "invalid_layout"),
        ({"visibility": "public"}, "invalid_visibility"),
        ({"group_by": "severity"}, "invalid_group_by"),
        ({"sub_group_by": "severity"}, "invalid_group_by"),
        (
            {
                "filters": {
                    "operator": "AND",
                    "conditions": [{"field": "evil", "op": "eq", "value": "x"}],
                }
            },
            "invalid_filters",
        ),
        ({"sort": [{"field": "evil", "order": "asc"}]}, "invalid_sort"),
        ({"display_fields": [1]}, "invalid_display_fields"),
        ({"board_settings": {"wip": {"todo": {"limit": 0}}}}, "invalid_board_settings"),
    ],
)
async def test_create_view_invalid_config_named_codes(
    session_factory, overrides, code
) -> None:
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError) as excinfo:
        await service.create_view(
            actor=member, workspace_id=workspace.id, body=_create_body(**overrides)
        )
    assert excinfo.value.code == code


async def test_create_view_guest_forbidden(session_factory) -> None:
    workspace, guest, service = await _setup(session_factory, role="guest")
    with pytest.raises(ForbiddenError):
        await service.create_view(
            actor=guest, workspace_id=workspace.id, body=_create_body()
        )


async def test_create_view_unknown_project(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await service.create_view(
            actor=member,
            workspace_id=workspace.id,
            body=_create_body(project_id=str(uuid.uuid4())),
        )
    with pytest.raises(NotFoundError):
        await service.create_view(
            actor=member,
            workspace_id=workspace.id,
            body=_create_body(project_id="not-a-uuid"),
        )


async def test_create_view_foreign_project_forbidden(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    other_ws, _, _ = await _setup(session_factory)
    foreign = await _create_project(session_factory, other_ws)
    with pytest.raises(NotFoundError):
        await service.create_view(
            actor=member,
            workspace_id=workspace.id,
            body=_create_body(project_id=str(foreign.id)),
        )


async def test_create_view_private_project_not_visible(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    private = await _create_project(session_factory, workspace, visibility="private")
    with pytest.raises(ForbiddenError):
        await service.create_view(
            actor=member,
            workspace_id=workspace.id,
            body=_create_body(project_id=str(private.id)),
        )


# ---------------------------------------------------------------------------
# name + default scope uniqueness (README §6.3)
# ---------------------------------------------------------------------------


async def test_create_view_duplicate_name_same_scope_conflict(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    await service.create_view(actor=member, workspace_id=workspace.id, body=_create_body())
    with pytest.raises(ConflictError) as excinfo:
        await service.create_view(
            actor=member, workspace_id=workspace.id, body=_create_body()
        )
    assert excinfo.value.code == "view_name_taken"


async def test_create_view_same_name_different_scopes_allowed(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    project_a = await _create_project(session_factory, workspace)
    project_b = await _create_project(session_factory, workspace)
    await service.create_view(actor=member, workspace_id=workspace.id, body=_create_body())
    await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(project_id=str(project_a.id)),
    )
    await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(project_id=str(project_b.id)),
    )
    async with session_factory() as session:
        count = len(
            (await session.execute(select(View).where(View.name == "Sprint Board"))).scalars().all()
        )
    assert count == 3


async def test_create_default_view_clears_scope_default(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    first = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="A", is_default=True)
    )
    second = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="B", is_default=True)
    )
    assert second["is_default"] is True
    async with session_factory() as session:
        first_row = await session.scalar(select(View).where(View.id == uuid.UUID(first["id"])))
    assert first_row.is_default is False


async def test_default_views_in_different_scopes_coexist(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    ws_default = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="WS", is_default=True)
    )
    project_default = await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(name="PRJ", project_id=str(project.id), is_default=True),
    )
    assert ws_default["is_default"] is True
    assert project_default["is_default"] is True


# ---------------------------------------------------------------------------
# get + read authorization (kanban §3.4)
# ---------------------------------------------------------------------------


async def test_get_view_owner(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    fetched = await service.get_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
    )
    assert fetched["id"] == created["id"]
    assert fetched["can_write"] is True


async def test_get_view_foreign_private_is_404(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    other = await _add_member(session_factory, workspace)
    created = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(NotFoundError):
        await service.get_view(
            viewer=other, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
        )


async def test_get_view_manager_cannot_read_foreign_private(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    admin = await _add_member(session_factory, workspace, role="admin")
    created = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(NotFoundError):
        await service.get_view(
            viewer=admin, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
        )


async def test_get_view_shared_readable_by_member(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    other = await _add_member(session_factory, workspace)
    created = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body(visibility="shared")
    )
    fetched = await service.get_view(
        viewer=other, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
    )
    assert fetched["can_write"] is False


async def test_get_view_private_project_shared_hidden_from_outsider(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory, role="owner")
    outsider = await _add_member(session_factory, workspace)
    project = await _create_project(session_factory, workspace, visibility="private")
    await _grant_project_access(session_factory, workspace, project, owner, role="lead")
    created = await service.create_view(
        actor=owner,
        workspace_id=workspace.id,
        body=_create_body(visibility="shared", project_id=str(project.id)),
    )
    with pytest.raises(NotFoundError):
        await service.get_view(
            viewer=outsider, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
        )
    # A project member can read it.
    insider = await _add_member(session_factory, workspace)
    await _grant_project_access(session_factory, workspace, project, insider)
    fetched = await service.get_view(
        viewer=insider, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
    )
    assert fetched["id"] == created["id"]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_views_visibility(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    other = await _add_member(session_factory, workspace)
    mine = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body(name="Mine")
    )
    other_private = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body(name="OtherPrivate")
    )
    shared = await service.create_view(
        actor=owner,
        workspace_id=workspace.id,
        body=_create_body(name="Shared", visibility="shared"),
    )
    items, cursor = await service.list_views(viewer=other, workspace_id=workspace.id)
    ids = {item["id"] for item in items}
    assert ids == {shared["id"]}
    assert cursor is None
    own_items, _ = await service.list_views(viewer=owner, workspace_id=workspace.id)
    assert {item["id"] for item in own_items} == {
        mine["id"],
        other_private["id"],
        shared["id"],
    }


async def test_list_views_pagination_cursor_roundtrip(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created_ids = []
    for index in range(3):
        created = await service.create_view(
            actor=member, workspace_id=workspace.id, body=_create_body(name=f"V{index}")
        )
        created_ids.append(created["id"])
    page_one, cursor = await service.list_views(viewer=member, workspace_id=workspace.id, limit=2)
    assert [item["id"] for item in page_one] == created_ids[:2]
    assert cursor is not None
    page_two, next_cursor = await service.list_views(
        viewer=member, workspace_id=workspace.id, limit=2, cursor=cursor
    )
    assert [item["id"] for item in page_two] == created_ids[2:]
    assert next_cursor is None


async def test_list_views_project_filter(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    await service.create_view(actor=member, workspace_id=workspace.id, body=_create_body(name="WS"))
    scoped = await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(name="Scoped", project_id=str(project.id)),
    )
    items, _ = await service.list_views(
        viewer=member, workspace_id=workspace.id, project_id=project.id
    )
    assert [item["id"] for item in items] == [scoped["id"]]


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_view_changes_and_event(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    updated = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"name": "Renamed", "group_by": "priority"},
    )
    assert updated["name"] == "Renamed"
    assert updated["group_by"] == "priority"
    events = await _outbox_realtime(session_factory)
    view_events = [row for row in events if row["event"] == "view.updated"]
    # Each operation fans out to two channels: view:{id} + workspace:{ws}:views.
    assert len(view_events) == 4
    create_frames = [frame for frame in view_events if "changes" not in frame["data"]]
    update_frames = [frame for frame in view_events if "changes" in frame["data"]]
    assert len(create_frames) == 2
    assert len(update_frames) == 2
    for frame in update_frames:
        assert frame["data"]["changes"] == ["group_by", "name"]
    assert {frame["channel"] for frame in update_frames} == {
        f"view:{created['id']}",
        f"workspace:{workspace.id}:views",
    }


async def test_update_view_noop_emits_nothing(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(group_by="priority")
    )
    baseline = len(await _outbox_realtime(session_factory))
    result = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"name": "Sprint Board", "group_by": "priority", "filters": {}},
    )
    assert result["updated_at"] == created["updated_at"]
    assert len(await _outbox_realtime(session_factory)) == baseline


async def test_update_view_board_settings_shallow_merge(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member,
        workspace_id=workspace.id,
        body=_create_body(board_settings={"card_fields": ["labels"], "columns": ["todo"]}),
    )
    updated = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"board_settings": {"wip": {"in_progress": {"limit": 5}}}},
    )
    assert updated["board_settings"] == {
        "card_fields": ["labels"],
        "columns": ["todo"],
        "wip": {"in_progress": {"limit": 5, "enforcement": "warn"}},
    }


async def test_update_view_if_match_conflict_and_success(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    stale = "2020-01-01T00:00:00Z"
    with pytest.raises(ConflictError) as excinfo:
        await service.update_view(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(created["id"]),
            fields={"name": "X"},
            if_match=stale,
        )
    assert excinfo.value.code == "conflict"
    ok = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"name": "Matched"},
        if_match=f'"{created["updated_at"]}"',
    )
    assert ok["name"] == "Matched"


async def test_update_view_invalid_config_rejected(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(ValidationError) as excinfo:
        await service.update_view(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(created["id"]),
            fields={"filters": {"operator": "AND", "conditions": [{"field": "nope", "op": "eq", "value": 1}]}},
        )
    assert excinfo.value.code == "invalid_filters"


async def test_update_view_write_authorization(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    stranger = await _add_member(session_factory, workspace)
    admin = await _add_member(session_factory, workspace, role="admin")

    private = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body(name="Private")
    )
    with pytest.raises(ForbiddenError):
        await service.update_view(
            actor=stranger,
            workspace_id=workspace.id,
            view_id=uuid.UUID(private["id"]),
            fields={"name": "Hacked"},
        )

    shared = await service.create_view(
        actor=owner,
        workspace_id=workspace.id,
        body=_create_body(name="Shared", visibility="shared"),
    )
    with pytest.raises(ForbiddenError):
        await service.update_view(
            actor=stranger,
            workspace_id=workspace.id,
            view_id=uuid.UUID(shared["id"]),
            fields={"name": "Hacked"},
        )
    by_admin = await service.update_view(
        actor=admin,
        workspace_id=workspace.id,
        view_id=uuid.UUID(shared["id"]),
        fields={"name": "AdminEdit"},
    )
    assert by_admin["name"] == "AdminEdit"


async def test_update_view_project_lead_can_write_project_scoped(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory, role="owner")
    lead = await _add_member(session_factory, workspace)
    project = await _create_project(session_factory, workspace)
    await _grant_project_access(session_factory, workspace, project, lead, role="lead")
    created = await service.create_view(
        actor=owner,
        workspace_id=workspace.id,
        body=_create_body(visibility="shared", project_id=str(project.id)),
    )
    updated = await service.update_view(
        actor=lead,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"name": "LeadEdit"},
    )
    assert updated["name"] == "LeadEdit"


async def test_update_view_set_default_clears_others(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    first = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="A", is_default=True)
    )
    second = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body(name="B")
    )
    updated = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(second["id"]),
        fields={"is_default": True},
    )
    assert updated["is_default"] is True
    async with session_factory() as session:
        first_row = await session.scalar(select(View).where(View.id == uuid.UUID(first["id"])))
    assert first_row.is_default is False


async def test_update_view_move_to_project_scope(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    updated = await service.update_view(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        fields={"project_id": str(project.id)},
    )
    assert updated["project_id"] == str(project.id)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_view_removes_row_and_emits_marker(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    await service.delete_view(
        actor=member, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
    )
    async with session_factory() as session:
        assert (
            await session.scalar(select(View).where(View.id == uuid.UUID(created["id"])))
        ) is None
    events = await _outbox_realtime(session_factory)
    deleted_frames = [
        row
        for row in events
        if row["event"] == "view.updated" and row["data"].get("deleted") is True
    ]
    assert len(deleted_frames) == 1
    assert deleted_frames[0]["channel"] == f"workspace:{workspace.id}:views"
    assert deleted_frames[0]["data"]["id"] == created["id"]


async def test_delete_view_foreign_forbidden(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    stranger = await _add_member(session_factory, workspace)
    created = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(ForbiddenError):
        await service.delete_view(
            actor=stranger, workspace_id=workspace.id, view_id=uuid.UUID(created["id"])
        )


# ---------------------------------------------------------------------------
# duplicate
# ---------------------------------------------------------------------------


async def test_duplicate_view(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    copier = await _add_member(session_factory, workspace)
    source = await service.create_view(
        actor=owner,
        workspace_id=workspace.id,
        body=_create_body(
            visibility="shared",
            group_by="priority",
            board_settings={"card_fields": ["labels"]},
            is_default=True,
        ),
    )
    copy = await service.duplicate_view(
        actor=copier, workspace_id=workspace.id, view_id=uuid.UUID(source["id"])
    )
    assert copy["name"] == "Sprint Board (copy)"
    assert copy["owner_member_id"] == str(copier.id)
    assert copy["is_default"] is False
    assert copy["group_by"] == "priority"
    assert copy["board_settings"] == {"card_fields": ["labels"]}
    copy_two = await service.duplicate_view(
        actor=copier, workspace_id=workspace.id, view_id=uuid.UUID(source["id"])
    )
    assert copy_two["name"] == "Sprint Board (copy 2)"


async def test_duplicate_private_view_forbidden_for_stranger(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    stranger = await _add_member(session_factory, workspace)
    source = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(NotFoundError):
        await service.duplicate_view(
            actor=stranger, workspace_id=workspace.id, view_id=uuid.UUID(source["id"])
        )


# ---------------------------------------------------------------------------
# wip config
# ---------------------------------------------------------------------------


async def test_patch_wip_set_and_remove(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    view_id = uuid.UUID(created["id"])
    updated = await service.patch_wip(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        body=WipRequest(group_key="in_progress", limit=5, enforcement="block"),
    )
    assert updated["board_settings"]["wip"] == {
        "in_progress": {"limit": 5, "enforcement": "block"}
    }
    # Sibling keys survive the wip write (shallow merge semantics).
    again = await service.patch_wip(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        body=WipRequest(group_key="todo", limit=2),
    )
    assert again["board_settings"]["wip"] == {
        "in_progress": {"limit": 5, "enforcement": "block"},
        "todo": {"limit": 2, "enforcement": "warn"},
    }
    removed = await service.patch_wip(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        body=WipRequest(group_key="in_progress", limit=None),
    )
    assert removed["board_settings"]["wip"] == {"todo": {"limit": 2, "enforcement": "warn"}}


async def test_patch_wip_noop_remove_absent(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    baseline = len(await _outbox_realtime(session_factory))
    result = await service.patch_wip(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(created["id"]),
        body=WipRequest(group_key="nope", limit=None),
    )
    assert result["updated_at"] == created["updated_at"]
    assert len(await _outbox_realtime(session_factory)) == baseline


async def test_patch_wip_invalid_enforcement(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(ValidationError) as excinfo:
        await service.patch_wip(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(created["id"]),
            body=WipRequest(group_key="todo", limit=1, enforcement="hard"),
        )
    assert excinfo.value.code == "invalid_board_settings"


# ---------------------------------------------------------------------------
# reorder
# ---------------------------------------------------------------------------


async def test_reorder_views_assigns_positions(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    ids = []
    for index in range(3):
        created = await service.create_view(
            actor=member, workspace_id=workspace.id, body=_create_body(name=f"V{index}")
        )
        ids.append(uuid.UUID(created["id"]))
    result = await service.reorder_views(
        actor=member, workspace_id=workspace.id, view_ids=[ids[2], ids[0], ids[1]]
    )
    assert [item["id"] for item in result] == [str(ids[2]), str(ids[0]), str(ids[1])]
    assert [item["position"] for item in result] == [1.0, 2.0, 3.0]


async def test_reorder_views_foreign_private_forbidden(session_factory) -> None:
    workspace, owner, service = await _setup(session_factory)
    stranger = await _add_member(session_factory, workspace)
    created = await service.create_view(
        actor=owner, workspace_id=workspace.id, body=_create_body()
    )
    with pytest.raises(ForbiddenError):
        await service.reorder_views(
            actor=stranger,
            workspace_id=workspace.id,
            view_ids=[uuid.UUID(created["id"])],
        )


# ---------------------------------------------------------------------------
# workspace resolution + version matching
# ---------------------------------------------------------------------------


async def test_resolve_view_workspace(session_factory) -> None:
    workspace, member, service = await _setup(session_factory)
    created = await service.create_view(
        actor=member, workspace_id=workspace.id, body=_create_body()
    )
    resolved = await service.resolve_view_workspace(uuid.UUID(created["id"]))
    assert resolved == workspace.id
    assert await service.resolve_view_workspace(uuid.uuid4()) is None


def test_matches_version_formats() -> None:
    view = View(
        workspace_id=uuid.uuid4(),
        owner_member_id=uuid.uuid4(),
        name="x",
        updated_at=FIXED_NOW,
    )
    assert ViewService._matches_version(view, "2026-07-26T12:00:00Z")
    assert ViewService._matches_version(view, '"2026-07-26T12:00:00Z"')
    assert ViewService._matches_version(view, "2026-07-26T12:00:00+00:00")
    assert ViewService._matches_version(view, "2026-07-26T12:00:00")
    assert not ViewService._matches_version(view, "2020-01-01T00:00:00Z")
    assert not ViewService._matches_version(view, "garbage")

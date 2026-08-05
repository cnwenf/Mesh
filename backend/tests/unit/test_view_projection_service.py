"""ProjectionService tests — grouped projection query against real PostgreSQL.

Covers kanban.md §3.2/§5.1 + README §6.14: group_by mapping (state_category /
status / priority / assignee / project), the OVERALL-cursor contract (one
next_cursor, count = group total, data = page slice, no per-group cursor),
column_target_status (category → default status), view filters applied to the
projection, per-view manual ordering + view isolation (§2.7), WIP surfaced per
group, member-visibility trimming, label group_by gating (MES-32), 501 for
timeline/table layouts, and filter_too_complex on over-limit stored config.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.label import (
    CustomFieldDef,
    CustomFieldOption,
    IssueCustomFieldValue,
    IssueLabel,
    Label,
)
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.models.workspace import Workspace
from mesh.errors import ConflictError, ValidationError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import seed_default_statuses
from mesh.views.projection import ProjectionService, _encode_custom_text_key
from mesh.views.schemas import CreateViewRequest
from mesh.views.service import ViewService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _setup(session_factory):
    """workspace + human member + the three services under test."""
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Board WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Boarder",
            password_hash="x",
            status="active",
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user.id,
            role="member",
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(member)
    # Seed the workspace default status set (7 categories).
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=workspace.id, project_id=None)
    issue_service = IssueService(session_factory, clock=_clock)
    view_service = ViewService(session_factory, clock=_clock)
    projection = ProjectionService(session_factory, issue_service, view_service, clock=_clock)
    return workspace, member, issue_service, view_service, projection


async def _statuses(session_factory, workspace) -> dict[str, uuid.UUID]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IssueStatus.category, IssueStatus.id).where(
                    IssueStatus.workspace_id == workspace.id,
                    IssueStatus.project_id.is_(None),
                )
            )
        ).all()
    return {category: sid for category, sid in rows}


async def _mk_issue(issue_service, *, actor, workspace, category=None, status_map=None, **kw) -> dict:
    status_id = None
    if category is not None:
        status_id = str(status_map[category])
    body = CreateIssueRequest(
        title=kw.pop("title", f"Issue {uuid.uuid4().hex[:6]}"),
        status_id=status_id,
        **kw,
    )
    return await issue_service.create_issue(actor=actor, workspace_id=workspace.id, body=body)


async def _mk_view(view_service, *, actor, workspace, **overrides) -> dict:
    fields = {"name": f"View {uuid.uuid4().hex[:6]}"}
    fields.update(overrides)
    return await view_service.create_view(
        actor=actor, workspace_id=workspace.id, body=CreateViewRequest(**fields)
    )


# ---------------------------------------------------------------------------
# group_by = state_category (default)
# ---------------------------------------------------------------------------


async def test_execute_view_groups_by_state_category(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="in_progress", status_map=status_map
    )
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="done", status_map=status_map)

    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )

    assert result["group_by"] == "state_category"
    groups = {group["key"]: group for group in result["groups"]}
    # All seven fixed category columns are present (empty ones too).
    for key in ("backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"):
        assert key in groups, key
    assert groups["todo"]["count"] == 2
    assert len(groups["todo"]["data"]) == 2
    assert groups["in_progress"]["count"] == 1
    assert groups["done"]["count"] == 1
    assert groups["backlog"]["count"] == 0
    assert groups["backlog"]["data"] == []
    # Overall cursor: single top-level cursor, null when everything fits.
    assert result["next_cursor"] is None
    # No per-group cursor anywhere.
    for group in result["groups"]:
        assert "cursor" not in group


async def test_execute_view_column_target_status_maps_category_default(session_factory) -> None:
    from tests.unit.test_view_service import _create_project, _grant_project_access

    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    project = await _create_project(session_factory, workspace, visibility="public")
    await _grant_project_access(session_factory, workspace, project, member)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        project_id=str(project.id),
        group_by="state_category",
    )
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    mapping = result["column_target_status"]
    assert mapping["todo"] == str(status_map["todo"])
    assert mapping["in_progress"] == str(status_map["in_progress"])
    assert mapping["done"] == str(status_map["done"])


async def test_workspace_wide_category_view_omits_ambiguous_target_status(
    session_factory,
) -> None:
    workspace, member, _issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    assert "column_target_status" not in result


# ---------------------------------------------------------------------------
# overall-cursor pagination
# ---------------------------------------------------------------------------


async def test_execute_view_overall_cursor_pagination(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    created_ids = set()
    for _ in range(5):
        issue = await _mk_issue(
            issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
        )
        created_ids.add(issue["id"])

    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_id = uuid.UUID(view["id"])

    page1 = await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=view_id, limit=2)
    todo1 = next(g for g in page1["groups"] if g["key"] == "todo")
    assert todo1["count"] == 5  # full group total, not the page slice
    assert len(todo1["data"]) == 2
    assert page1["next_cursor"] is not None

    page2 = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=view_id, limit=2, cursor=page1["next_cursor"]
    )
    todo2 = next(g for g in page2["groups"] if g["key"] == "todo")
    assert todo2["count"] == 5
    page3 = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=view_id, limit=2, cursor=page2["next_cursor"]
    )
    todo3 = next(g for g in page3["groups"] if g["key"] == "todo")
    assert page3["next_cursor"] is None

    seen = (
        {c["id"] for c in todo1["data"]} | {c["id"] for c in todo2["data"]} | {c["id"] for c in todo3["data"]}
    )
    assert seen == created_ids


# ---------------------------------------------------------------------------
# filters + visibility
# ---------------------------------------------------------------------------


async def test_execute_view_applies_view_filters(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        priority="high",
    )
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        priority="low",
    )

    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        filters={
            "operator": "AND",
            "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
        },
    )
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    todo = next(g for g in result["groups"] if g["key"] == "todo")
    assert todo["count"] == 1
    assert todo["data"][0]["priority"] == "high"


async def test_execute_view_visibility_trims_private_project(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from tests.unit.test_view_service import _add_member, _create_project, _grant_project_access

    status_map = await _statuses(session_factory, workspace)
    # A private project; `member` belongs, `outsider` does not.
    private = await _create_project(session_factory, workspace, visibility="private")
    await _grant_project_access(session_factory, workspace, private, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(private.id),
    )
    # Shared workspace-level view so both members can execute it.
    view = await _mk_view(
        view_service, actor=member, workspace=workspace, group_by="state_category", visibility="shared"
    )
    view_id = uuid.UUID(view["id"])
    outsider = await _add_member(session_factory, workspace)

    # Outsider (not a project member): the private-project issue is trimmed.
    result = await projection.execute_view(viewer=outsider, workspace_id=workspace.id, view_id=view_id)
    todo = next(g for g in result["groups"] if g["key"] == "todo")
    assert all(card["id"] != issue["id"] for card in todo["data"])

    # Member (project access): it appears.
    result2 = await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=view_id)
    todo2 = next(g for g in result2["groups"] if g["key"] == "todo")
    assert any(card["id"] == issue["id"] for card in todo2["data"])


# ---------------------------------------------------------------------------
# other group_by dimensions
# ---------------------------------------------------------------------------


async def test_execute_view_group_by_priority(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        priority="high",
    )
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        priority="urgent",
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="priority")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    keys = {g["key"] for g in result["groups"]}
    assert {"urgent", "high", "medium", "low", "none"} <= keys
    by_key = {g["key"]: g for g in result["groups"]}
    assert by_key["high"]["count"] == 1
    assert by_key["urgent"]["count"] == 1


async def test_execute_view_group_by_status(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="status")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    keys = {g["key"] for g in result["groups"]}
    assert str(status_map["todo"]) in keys
    assert "column_target_status" not in result


async def test_execute_view_group_by_assignee_uses_none_key(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        assignee_id=str(member.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="assignee")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    keys = {g["key"] for g in result["groups"]}
    assert "__none__" in keys
    assert str(member.id) in keys


# ---------------------------------------------------------------------------
# per-view manual ordering + isolation (§2.7)
# ---------------------------------------------------------------------------


async def test_execute_view_per_view_position_ordering_and_isolation(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    a = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        position=1.0,
    )
    b = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        position=2.0,
    )

    view_a = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_b = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")

    # In view A, manually put B before A.
    async with session_factory() as session, session.begin():
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id,
                view_id=uuid.UUID(view_a["id"]),
                issue_id=uuid.UUID(b["id"]),
                group_key="todo",
                position=0.5,
            )
        )
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id,
                view_id=uuid.UUID(view_a["id"]),
                issue_id=uuid.UUID(a["id"]),
                group_key="todo",
                position=1.5,
            )
        )

    result_a = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view_a["id"])
    )
    order_a = [c["id"] for c in next(g for g in result_a["groups"] if g["key"] == "todo")["data"]]
    assert order_a == [b["id"], a["id"]]

    # View B has no manual rows → canonical issues.position order (A before B).
    result_b = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view_b["id"])
    )
    order_b = [c["id"] for c in next(g for g in result_b["groups"] if g["key"] == "todo")["data"]]
    assert order_b == [a["id"], b["id"]]


# ---------------------------------------------------------------------------
# WIP surfaced + gating + errors
# ---------------------------------------------------------------------------


async def test_execute_view_surfaces_wip_per_group(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 2, "enforcement": "block"}}},
    )
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    by_key = {g["key"]: g for g in result["groups"]}
    assert by_key["in_progress"]["wip"] == {"limit": 2, "enforcement": "block"}
    assert by_key["todo"]["wip"] is None


async def test_execute_view_group_by_label_projects_each_value_and_empty_group(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    first = await _mk_issue(issue_service, actor=member, workspace=workspace)
    second = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        bug = Label(workspace_id=workspace.id, name="Bug", color="#e5484d")
        api = Label(workspace_id=workspace.id, name="API", color="#30a46c")
        session.add_all([bug, api])
        await session.flush()
        session.add_all(
            [
                IssueLabel(
                    workspace_id=workspace.id,
                    issue_id=uuid.UUID(first["id"]),
                    label_id=bug.id,
                ),
                IssueLabel(
                    workspace_id=workspace.id,
                    issue_id=uuid.UUID(first["id"]),
                    label_id=api.id,
                ),
            ]
        )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="label")
    result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
    )

    groups = {group["label"]: group for group in result["groups"]}
    assert [group["label"] for group in result["groups"][:2]] == ["API", "Bug"]
    assert {card["id"] for card in groups["API"]["data"]} == {first["id"]}
    assert {card["id"] for card in groups["Bug"]["data"]} == {first["id"]}
    assert {card["id"] for card in groups["No label"]["data"]} == {second["id"]}
    assert groups["API"]["data"][0]["labels"] == [
        {"id": str(api.id), "name": "API", "color": "#30a46c"},
        {"id": str(bug.id), "name": "Bug", "color": "#e5484d"},
    ]


async def test_label_axis_skeleton_hides_private_project_definitions_from_workspace_view(
    session_factory,
) -> None:
    from tests.unit.test_view_service import _create_project, _grant_project_access

    workspace, member, _issue_service, view_service, projection = await _setup(session_factory)
    public_project = await _create_project(session_factory, workspace, visibility="public")
    private_project = await _create_project(session_factory, workspace, visibility="private")
    async with session_factory() as session, session.begin():
        workspace_label = Label(
            workspace_id=workspace.id,
            name="Workspace label",
            color="#336699",
        )
        public_label = Label(
            workspace_id=workspace.id,
            project_id=public_project.id,
            name="Public project label",
            color="#30a46c",
        )
        hidden_label = Label(
            workspace_id=workspace.id,
            project_id=private_project.id,
            name="Private secret label",
            color="#e5484d",
        )
        session.add_all([workspace_label, public_label, hidden_label])
        await session.flush()
        ids = {
            "workspace": str(workspace_label.id),
            "public": str(public_label.id),
            "hidden": str(hidden_label.id),
        }

    workspace_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="label",
    )
    workspace_result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(workspace_view["id"]),
    )
    workspace_keys = {group["key"] for group in workspace_result["groups"]}
    assert ids["workspace"] in workspace_keys
    assert ids["public"] in workspace_keys
    assert ids["hidden"] not in workspace_keys
    assert "Private secret label" not in {group["label"] for group in workspace_result["groups"]}

    await _grant_project_access(session_factory, workspace, private_project, member)
    project_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        project_id=str(private_project.id),
        group_by="label",
    )
    project_result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(project_view["id"]),
    )
    project_keys = {group["key"] for group in project_result["groups"]}
    assert ids["workspace"] in project_keys
    assert ids["hidden"] in project_keys
    assert ids["public"] not in project_keys


async def test_label_axis_skeleton_does_not_leak_ungranted_public_project_labels_to_guest(
    session_factory,
) -> None:
    from tests.unit.test_view_service import _create_project

    workspace, owner, _issue_service, view_service, projection = await _setup(
        session_factory
    )
    public_project = await _create_project(
        session_factory, workspace, visibility="public"
    )
    async with session_factory() as session, session.begin():
        guest_user = User(
            email=f"{uuid.uuid4().hex[:12]}@guest.test",
            display_name="Guest",
            password_hash="x",
            status="active",
        )
        session.add(guest_user)
        await session.flush()
        guest = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=guest_user.id,
            role="guest",
            status="active",
            joined_at=FIXED_NOW,
        )
        workspace_label = Label(
            workspace_id=workspace.id,
            name="Workspace label",
            color="#336699",
        )
        project_label = Label(
            workspace_id=workspace.id,
            project_id=public_project.id,
            name="Ungranted public-project label",
            color="#30a46c",
        )
        session.add_all([guest, workspace_label, project_label])
        await session.flush()
        workspace_label_id = str(workspace_label.id)
        project_label_id = str(project_label.id)

    view = await _mk_view(
        view_service,
        actor=owner,
        workspace=workspace,
        group_by="label",
        visibility="shared",
    )
    result = await projection.execute_view(
        viewer=guest,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
    )

    keys = {group["key"] for group in result["groups"]}
    assert workspace_label_id in keys
    assert project_label_id not in keys


async def test_custom_axis_and_sort_enforce_view_project_scope(session_factory) -> None:
    from tests.unit.test_view_service import _create_project, _grant_project_access

    workspace, member, _issue_service, view_service, projection = await _setup(session_factory)
    project = await _create_project(session_factory, workspace, visibility="private")
    await _grant_project_access(session_factory, workspace, project, member)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            project_id=project.id,
            name="Private severity",
            field_key=f"private_severity_{uuid.uuid4().hex[:8]}",
            type="single_select",
        )
        session.add(definition)
        await session.flush()
        option = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Secret option",
        )
        session.add(option)
        await session.flush()
        field_id = str(definition.id)
        option_id = str(option.id)

    workspace_axis = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=field_id,
    )
    with pytest.raises(ValidationError) as axis_error:
        await projection.execute_view(
            viewer=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(workspace_axis["id"]),
        )
    assert axis_error.value.code == "invalid_group_by"

    custom_sort = [
        {
            "field_kind": "custom_field",
            "field_def_id": field_id,
            "order": "asc",
        }
    ]
    workspace_sort = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sort=custom_sort,
    )
    with pytest.raises(ValidationError) as sort_error:
        await projection.execute_view(
            viewer=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(workspace_sort["id"]),
        )
    assert sort_error.value.code == "invalid_group_by"

    project_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        project_id=str(project.id),
        group_by=field_id,
        sort=custom_sort,
    )
    result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(project_view["id"]),
    )
    groups = {group["key"]: group for group in result["groups"]}
    assert groups[option_id]["label"] == "Secret option"


async def test_execute_view_custom_select_filter_and_group_skeleton(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    major_issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name="Severity",
            field_key=f"severity_{uuid.uuid4().hex[:8]}",
            type="single_select",
        )
        session.add(definition)
        await session.flush()
        major = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Major",
            color="#e5484d",
            position=1,
        )
        minor = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Minor",
            color="#30a46c",
            position=2,
        )
        session.add_all([major, minor])
        await session.flush()
        session.add(
            IssueCustomFieldValue(
                workspace_id=workspace.id,
                issue_id=uuid.UUID(major_issue["id"]),
                field_def_id=definition.id,
                value_json=str(major.id),
            )
        )
        field_id = str(definition.id)
        major_id = str(major.id)
        minor_id = str(minor.id)

    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=field_id,
        filters={
            "operator": "OR",
            "conditions": [
                {
                    "field_kind": "custom_field",
                    "field_def_id": field_id,
                    "op": "eq",
                    "value": major_id,
                },
                {
                    "field_kind": "custom_field",
                    "field_def_id": field_id,
                    "op": "is_null",
                },
            ],
        },
    )
    result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
    )

    assert [group["key"] for group in result["groups"]] == [major_id, minor_id, "__none__"]
    groups = {group["key"]: group for group in result["groups"]}
    assert groups[major_id]["count"] == 1
    assert groups[minor_id]["count"] == 0
    assert groups["__none__"]["count"] == 1
    assert groups[major_id]["label"] == "Major"
    assert groups[minor_id]["label"] == "Minor"


async def test_custom_select_projection_and_sort_fail_closed_for_inactive_options(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(
        session_factory
    )
    first = await _mk_issue(issue_service, actor=member, workspace=workspace)
    second = await _mk_issue(issue_service, actor=member, workspace=workspace)
    retired = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name="Severity",
            field_key=f"severity_{uuid.uuid4().hex[:8]}",
            type="single_select",
        )
        session.add(definition)
        await session.flush()
        inactive = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Retired",
            position=0,
            is_active=False,
        )
        first_option = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="First",
            position=1,
        )
        second_option = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Second",
            position=2,
        )
        session.add_all([inactive, first_option, second_option])
        await session.flush()
        session.add_all(
            [
                IssueCustomFieldValue(
                    workspace_id=workspace.id,
                    issue_id=uuid.UUID(first["id"]),
                    field_def_id=definition.id,
                    value_json=str(first_option.id),
                ),
                IssueCustomFieldValue(
                    workspace_id=workspace.id,
                    issue_id=uuid.UUID(second["id"]),
                    field_def_id=definition.id,
                    value_json=str(second_option.id),
                ),
                IssueCustomFieldValue(
                    workspace_id=workspace.id,
                    issue_id=uuid.UUID(retired["id"]),
                    field_def_id=definition.id,
                    value_json=str(inactive.id),
                ),
            ]
        )
        field_id = str(definition.id)
        inactive_id = str(inactive.id)
        first_option_id = str(first_option.id)
        second_option_id = str(second_option.id)

    axis_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=field_id,
    )
    axis_result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(axis_view["id"]),
    )

    assert [group["key"] for group in axis_result["groups"]] == [
        first_option_id,
        second_option_id,
        "__none__",
    ]
    axis_groups = {group["key"]: group for group in axis_result["groups"]}
    assert inactive_id not in axis_groups
    assert {item["id"] for item in axis_groups["__none__"]["data"]} == {
        retired["id"]
    }

    sorted_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sort=[
            {
                "field_kind": "custom_field",
                "field_def_id": field_id,
                "order": "asc",
            }
        ],
    )
    sorted_result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(sorted_view["id"]),
    )
    state_group = next(
        group
        for group in sorted_result["groups"]
        if group["key"] == first["state_category"]
    )
    assert [item["id"] for item in state_group["data"]] == [
        first["id"],
        second["id"],
        retired["id"],
    ]


async def test_execute_view_two_multi_value_axes_form_cartesian_cells(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        red = Label(workspace_id=workspace.id, name="Red", color="#e5484d")
        blue = Label(workspace_id=workspace.id, name="Blue", color="#3e63dd")
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name="Components",
            field_key=f"components_{uuid.uuid4().hex[:8]}",
            type="multi_select",
        )
        session.add_all([red, blue, definition])
        await session.flush()
        web = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Web",
            position=1,
        )
        api = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="API",
            position=2,
        )
        session.add_all([web, api])
        await session.flush()
        issue_id = uuid.UUID(issue["id"])
        session.add_all(
            [
                IssueLabel(workspace_id=workspace.id, issue_id=issue_id, label_id=red.id),
                IssueLabel(workspace_id=workspace.id, issue_id=issue_id, label_id=blue.id),
                IssueCustomFieldValue(
                    workspace_id=workspace.id,
                    issue_id=issue_id,
                    field_def_id=definition.id,
                    value_json=[str(web.id), str(api.id)],
                ),
            ]
        )
        field_id = str(definition.id)
        option_ids = {str(web.id), str(api.id)}
        label_ids = {str(red.id), str(blue.id)}

    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="label",
        sub_group_by=field_id,
    )
    result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
    )

    populated = {
        (lane["key"], group["key"]) for lane in result["lanes"] for group in lane["groups"] if group["count"]
    }
    assert populated == {(option_id, label_id) for option_id in option_ids for label_id in label_ids}
    assert all(column["count"] == 1 for column in result["columns"] if column["key"] in label_ids)
    assert all(lane["count"] == 1 for lane in result["lanes"] if lane["key"] in option_ids)


async def test_execute_view_timeline_layout_is_501(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, layout="timeline")
    from mesh.views.projection import NotImplementedLayout

    with pytest.raises(NotImplementedLayout):
        await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"]))


async def test_execute_view_not_found(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=uuid.uuid4())


async def test_execute_view_private_view_hidden_from_others(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from mesh.errors import NotFoundError
    from tests.unit.test_view_service import _add_member

    view = await _mk_view(view_service, actor=member, workspace=workspace, visibility="private")
    other = await _add_member(session_factory, workspace)
    with pytest.raises(NotFoundError):
        await projection.execute_view(viewer=other, workspace_id=workspace.id, view_id=uuid.UUID(view["id"]))


async def test_execute_view_filter_too_complex_on_stored_config(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    # Bypass write-time validation: store an over-limit filter directly.
    deep = {
        "operator": "AND",
        "conditions": [
            {
                "operator": "OR",
                "conditions": [
                    {
                        "operator": "AND",
                        "conditions": [
                            {
                                "operator": "OR",
                                "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    async with session_factory() as session, session.begin():
        view = View(
            workspace_id=workspace.id,
            owner_member_id=member.id,
            name="Over Limit",
            layout="board",
            visibility="private",
            filters=deep,
            group_by="state_category",
            sort=[],
            display_fields=[],
            board_settings={},
            position=1.0,
        )
        session.add(view)
    from mesh.issue.filters import FilterTooComplexError

    with pytest.raises(FilterTooComplexError):
        await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=view.id)


async def test_execute_view_invalid_limit(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    with pytest.raises(ValidationError) as exc:
        await projection.execute_view(
            viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"]), limit=0
        )
    assert exc.value.code == "invalid_limit"


async def test_execute_view_project_scoped_limits_to_project(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from tests.unit.test_view_service import _create_project, _grant_project_access

    status_map = await _statuses(session_factory, workspace)
    project = await _create_project(session_factory, workspace, visibility="public")
    await _grant_project_access(session_factory, workspace, project, member)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(project.id),
    )
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    # Project-scoped view sees only that project's issue.
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        project_id=str(project.id),
    )
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    todo = next(g for g in result["groups"] if g["key"] == "todo")
    assert todo["count"] == 1


async def test_execute_view_group_by_project_labels(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from tests.unit.test_view_service import _create_project, _grant_project_access

    status_map = await _statuses(session_factory, workspace)
    project = await _create_project(session_factory, workspace, visibility="public")
    await _grant_project_access(session_factory, workspace, project, member)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(project.id),
    )
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    labels = {g["key"]: g["label"] for g in result["groups"]}
    assert labels[str(project.id)] == project.name
    assert labels["__none__"] == "No project"
    assert "column_target_status" not in result


async def test_execute_view_group_by_assignee_labels(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        assignee_id=str(member.id),
    )
    # An unassigned issue produces the "__none__" column.
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="assignee")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    labels = {g["key"]: g["label"] for g in result["groups"]}
    assert labels[str(member.id)] == "Boarder"
    assert labels["__none__"] == "No assignee"


# ---------------------------------------------------------------------------
# two-dimensional swimlane projection (kanban §2.4/§3.2)
# ---------------------------------------------------------------------------


async def test_execute_view_projects_two_dimensional_swimlanes(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    created = [
        await _mk_issue(
            issue_service,
            actor=member,
            workspace=workspace,
            category="todo",
            status_map=status_map,
            priority="high",
        ),
        await _mk_issue(
            issue_service,
            actor=member,
            workspace=workspace,
            category="todo",
            status_map=status_map,
            priority="low",
        ),
        await _mk_issue(
            issue_service,
            actor=member,
            workspace=workspace,
            category="in_progress",
            status_map=status_map,
            priority="high",
        ),
    ]
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
        board_settings={"wip": {"todo": {"limit": 4, "enforcement": "warn"}}},
    )

    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )

    assert result["group_by"] == "state_category"
    assert result["sub_group_by"] == "priority"
    assert "groups" not in result
    columns = {column["key"]: column for column in result["columns"]}
    assert columns["todo"] == {
        "key": "todo",
        "label": "Todo",
        "count": 2,
        "wip": {"limit": 4, "enforcement": "warn"},
    }
    assert columns["in_progress"]["count"] == 1
    lanes = {lane["key"]: lane for lane in result["lanes"]}
    assert list(lanes) == ["urgent", "high", "medium", "low", "none"]
    assert lanes["high"]["count"] == 2
    high_cells = {cell["key"]: cell for cell in lanes["high"]["groups"]}
    assert high_cells["todo"]["count"] == 1
    assert high_cells["in_progress"]["count"] == 1
    assert {card["id"] for card in high_cells["todo"]["data"]} == {created[0]["id"]}
    assert {card["id"] for card in high_cells["in_progress"]["data"]} == {created[2]["id"]}
    low_cells = {cell["key"]: cell for cell in lanes["low"]["groups"]}
    assert {card["id"] for card in low_cells["todo"]["data"]} == {created[1]["id"]}
    assert all("cursor" not in lane for lane in result["lanes"])
    assert all("cursor" not in cell for lane in result["lanes"] for cell in lane["groups"])


async def test_execute_swimlane_uses_one_cursor_across_cell_boundaries(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    created_ids: set[str] = set()
    for priority, category in (("high", "todo"), ("high", "in_progress"), ("low", "todo")):
        issue = await _mk_issue(
            issue_service,
            actor=member,
            workspace=workspace,
            category=category,
            status_map=status_map,
            priority=priority,
        )
        created_ids.add(issue["id"])
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    view_id = uuid.UUID(view["id"])

    page1 = await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=view_id, limit=2)
    page2 = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=view_id,
        limit=2,
        cursor=page1["next_cursor"],
    )

    assert page1["next_cursor"] is not None
    assert page2["next_cursor"] is None
    assert [column["count"] for column in page1["columns"]] == [
        column["count"] for column in page2["columns"]
    ]
    seen = {
        card["id"]
        for page in (page1, page2)
        for lane in page["lanes"]
        for cell in lane["groups"]
        for card in cell["data"]
    }
    assert seen == created_ids


async def test_project_swimlanes_omit_ambiguous_column_target_status(session_factory) -> None:
    workspace, member, _issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="project",
    )
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    assert "column_target_status" not in result


async def test_one_dimensional_stale_position_does_not_leak_into_new_group(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    moved = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        position=50.0,
    )
    first = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="in_progress",
        status_map=status_map,
        position=1.0,
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_id = uuid.UUID(view["id"])
    async with session_factory() as session, session.begin():
        issue = await session.get(Issue, uuid.UUID(moved["id"]))
        issue.status_id = status_map["in_progress"]
        issue.state_category = "in_progress"
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id,
                view_id=view_id,
                issue_id=issue.id,
                group_key="todo",
                sub_group_key="",
                position=-100.0,
            )
        )

    result = await projection.execute_view(viewer=member, workspace_id=workspace.id, view_id=view_id)
    in_progress = next(group for group in result["groups"] if group["key"] == "in_progress")
    assert [card["id"] for card in in_progress["data"]] == [first["id"], moved["id"]]


async def test_swimlane_cursor_is_bound_to_snapshot_and_view(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    created = []
    for _ in range(3):
        created.append(
            await _mk_issue(
                issue_service,
                actor=member,
                workspace=workspace,
                category="todo",
                status_map=status_map,
                priority="high",
            )
        )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    other_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    page = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        limit=1,
    )
    cursor = page["next_cursor"]
    assert cursor is not None

    with pytest.raises(ConflictError) as cross_view:
        await projection.execute_view(
            viewer=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(other_view["id"]),
            limit=1,
            cursor=cursor,
        )
    assert cross_view.value.code == "cursor_invalidated"

    async with session_factory() as session, session.begin():
        issue = await session.get(Issue, uuid.UUID(created[-1]["id"]))
        issue.updated_at = FIXED_NOW + timedelta(seconds=1)
    with pytest.raises(ConflictError) as changed_snapshot:
        await projection.execute_view(
            viewer=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            limit=1,
            cursor=cursor,
        )
    assert changed_snapshot.value.code == "cursor_invalidated"


async def test_swimlane_cursor_expires_fail_closed(session_factory) -> None:
    workspace, member, issue_service, view_service, _projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    for _ in range(2):
        await _mk_issue(
            issue_service,
            actor=member,
            workspace=workspace,
            category="todo",
            status_map=status_map,
            priority="high",
        )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    current = [FIXED_NOW]
    projection = ProjectionService(
        session_factory,
        issue_service,
        view_service,
        clock=lambda: current[0],
        cursor_secret="expiry-test-secret",
    )
    first = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        limit=1,
    )
    assert first["next_cursor"] is not None
    current[0] = FIXED_NOW + timedelta(minutes=16)
    with pytest.raises(ConflictError) as expired:
        await projection.execute_view(
            viewer=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            limit=1,
            cursor=first["next_cursor"],
        )
    assert expired.value.code == "cursor_invalidated"


async def test_text_custom_literal_none_has_distinct_projection_key(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(
        session_factory
    )
    literal = await _mk_issue(issue_service, actor=member, workspace=workspace)
    empty = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name="Literal sentinel",
            field_key=f"literal_{uuid.uuid4().hex[:8]}",
            type="text",
        )
        session.add(definition)
        await session.flush()
        session.add(
            IssueCustomFieldValue(
                workspace_id=workspace.id,
                issue_id=uuid.UUID(literal["id"]),
                field_def_id=definition.id,
                value_text="__none__",
            )
        )
        field_id = str(definition.id)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=field_id,
    )

    result = await projection.execute_view(
        viewer=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
    )

    literal_key = _encode_custom_text_key("__none__")
    groups = {group["key"]: group for group in result["groups"]}
    assert literal_key != "__none__"
    assert groups[literal_key]["label"] == "__none__"
    assert {card["id"] for card in groups[literal_key]["data"]} == {literal["id"]}
    assert {card["id"] for card in groups["__none__"]["data"]} == {empty["id"]}

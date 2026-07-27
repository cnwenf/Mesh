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
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.issue import IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.models.workspace import Workspace
from mesh.errors import ValidationError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import seed_default_statuses
from mesh.views.projection import PROJECTION_FIELD_PENDING, ProjectionService
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
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="done", status_map=status_map
    )

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
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    mapping = result["column_target_status"]
    assert mapping["todo"] == str(status_map["todo"])
    assert mapping["in_progress"] == str(status_map["in_progress"])
    assert mapping["done"] == str(status_map["done"])


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

    seen = {c["id"] for c in todo1["data"]} | {c["id"] for c in todo2["data"]} | {
        c["id"] for c in todo3["data"]
    }
    assert seen == created_ids


# ---------------------------------------------------------------------------
# filters + visibility
# ---------------------------------------------------------------------------


async def test_execute_view_applies_view_filters(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
        priority="high",
    )
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
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
    result = await projection.execute_view(
        viewer=outsider, workspace_id=workspace.id, view_id=view_id
    )
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
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
        priority="high",
    )
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
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
    # column_target_status is the identity map for status grouping.
    assert result["column_target_status"][str(status_map["todo"])] == str(status_map["todo"])


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
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
        position=1.0,
    )
    b = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map,
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


async def test_execute_view_group_by_label_is_gated(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="label")
    with pytest.raises(ValidationError) as exc:
        await projection.execute_view(
            viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
        )
    assert exc.value.code == PROJECTION_FIELD_PENDING


async def test_execute_view_timeline_layout_is_501(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, layout="timeline")
    from mesh.views.projection import NotImplementedLayout

    with pytest.raises(NotImplementedLayout):
        await projection.execute_view(
            viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
        )


async def test_execute_view_not_found(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await projection.execute_view(
            viewer=member, workspace_id=workspace.id, view_id=uuid.uuid4()
        )


async def test_execute_view_private_view_hidden_from_others(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    from mesh.errors import NotFoundError
    from tests.unit.test_view_service import _add_member

    view = await _mk_view(view_service, actor=member, workspace=workspace, visibility="private")
    other = await _add_member(session_factory, workspace)
    with pytest.raises(NotFoundError):
        await projection.execute_view(
            viewer=other, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
        )


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
                                "conditions": [
                                    {"field": "priority", "op": "eq", "value": "high"}
                                ],
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
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, project_id=str(project.id),
    )
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    # Project-scoped view sees only that project's issue.
    view = await _mk_view(
        view_service, actor=member, workspace=workspace, group_by="state_category",
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
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, project_id=str(project.id),
    )
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    labels = {g["key"]: g["label"] for g in result["groups"]}
    assert labels[str(project.id)] == project.name
    assert labels["__none__"] == "No project"
    # No column_target_status for project grouping.
    assert result["column_target_status"] == {}


async def test_execute_view_group_by_assignee_labels(session_factory) -> None:
    workspace, member, issue_service, view_service, projection = await _setup(session_factory)
    status_map = await _statuses(session_factory, workspace)
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, assignee_id=str(member.id),
    )
    # An unassigned issue produces the "__none__" column.
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="assignee")
    result = await projection.execute_view(
        viewer=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"])
    )
    labels = {g["key"]: g["label"] for g in result["groups"]}
    assert labels[str(member.id)] == "Boarder"
    assert labels["__none__"] == "No assignee"

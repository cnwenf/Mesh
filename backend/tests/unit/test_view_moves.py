"""BoardMoveService tests — atomic move + WIP against real PostgreSQL.

Covers kanban.md §3.2/§4.3/§4.4 and README §9 T9/T22: the move command's single
transaction (optimistic lock → advisory lock → WIP count → field change →
per-view position upsert), WIP block/warn enforcement, cross-column status
change via column_target_status, group_by=priority/assignee moves, and the
group_by=project two-step cross-project contract (preview → confirm).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mesh.db.models.issue import Issue
from mesh.db.models.label import (
    CustomFieldDef,
    CustomFieldOption,
    IssueCustomFieldValue,
    IssueLabel,
    Label,
)
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.view_position import ViewIssuePosition, ViewQuickCreateRequest
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError, ValidationError
from mesh.issue.move import MoveService
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import seed_default_statuses
from mesh.views.moves import BoardMoveService
from mesh.views.projection import _encode_custom_text_key
from mesh.views.schemas import CreateViewRequest
from mesh.views.service import ViewService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _setup(session_factory):
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Move WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Mover",
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
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=workspace.id, project_id=None)
    issue_service = IssueService(session_factory, clock=_clock)
    view_service = ViewService(session_factory, clock=_clock)
    move_service = MoveService(issue_service)
    board = BoardMoveService(session_factory, issue_service, move_service, view_service, clock=_clock)
    return workspace, member, issue_service, view_service, board


async def _status_map(session_factory, workspace):
    from mesh.db.models.issue import IssueStatus

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


async def _mk_issue(issue_service, *, actor, workspace, category=None, status_map=None, **kw):
    status_id = str(status_map[category]) if category is not None else None
    body = CreateIssueRequest(
        title=kw.pop("title", f"Issue {uuid.uuid4().hex[:6]}"), status_id=status_id, **kw
    )
    return await issue_service.create_issue(actor=actor, workspace_id=workspace.id, body=body)


async def _mk_view(view_service, *, actor, workspace, **overrides):
    fields = {"name": f"View {uuid.uuid4().hex[:6]}"}
    fields.update(overrides)
    return await view_service.create_view(
        actor=actor, workspace_id=workspace.id, body=CreateViewRequest(**fields)
    )


async def _position_row(session_factory, view_id, issue_id):
    async with session_factory() as session:
        return await session.scalar(
            select(ViewIssuePosition).where(
                ViewIssuePosition.view_id == view_id, ViewIssuePosition.issue_id == issue_id
            )
        )


async def _outbox_events(session_factory, event_name):
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent.payload).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    return [row for row in rows if row.get("event") == event_name]


async def _multi_value_fixture(session_factory, workspace):
    async with session_factory() as session, session.begin():
        label = Label(
            workspace_id=workspace.id,
            name=f"Label {uuid.uuid4().hex[:6]}",
            color="#336699",
        )
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name=f"Areas {uuid.uuid4().hex[:6]}",
            field_key=f"areas_{uuid.uuid4().hex[:8]}",
            type="multi_select",
            config={},
        )
        session.add_all([label, definition])
        await session.flush()
        option = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Payments",
            color="#8844cc",
        )
        session.add(option)
        await session.flush()
        return label, definition, option


async def _single_select_fixture(session_factory, workspace):
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name=f"Severity {uuid.uuid4().hex[:6]}",
            field_key=f"severity_{uuid.uuid4().hex[:8]}",
            type="single_select",
            config={},
        )
        session.add(definition)
        await session.flush()
        minor = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Minor",
            color="#669944",
            position=1,
        )
        major = CustomFieldOption(
            workspace_id=workspace.id,
            field_def_id=definition.id,
            name="Major",
            color="#cc5533",
            position=2,
        )
        session.add_all([minor, major])
        await session.flush()
        return definition, minor, major


# ---------------------------------------------------------------------------
# cross-column move (status change) + position upsert
# ---------------------------------------------------------------------------


async def test_move_cross_category_sets_default_status(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_id = uuid.UUID(view["id"])

    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="in_progress",
        position=2.5,
        version=issue["version"],
    )
    assert result["state_category"] == "in_progress"
    assert result["status"]["category"] == "in_progress"
    assert result["status"]["id"] == str(status_map["in_progress"])
    assert result["version"] == issue["version"] + 1

    row = await _position_row(session_factory, view_id, uuid.UUID(issue["id"]))
    assert row is not None
    assert row.group_key == "in_progress"
    assert row.position == 2.5

    moved = await _outbox_events(session_factory, "issue.moved")
    assert any(frame["data"]["id"] == issue["id"] for frame in moved)


async def test_move_stale_version_conflicts(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    with pytest.raises(ConflictError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="in_progress",
            position=1.0,
            version=issue["version"] + 99,
        )
    assert exc.value.code == "conflict"


# ---------------------------------------------------------------------------
# WIP enforcement
# ---------------------------------------------------------------------------


async def test_move_wip_block_full_rejected(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    # One issue already occupies in_progress (limit 1, block).
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="in_progress", status_map=status_map
    )
    mover = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 1, "enforcement": "block"}}},
    )
    view_id = uuid.UUID(view["id"])

    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=view_id,
            issue_id=uuid.UUID(mover["id"]),
            to_group_key="in_progress",
            position=1.0,
            version=mover["version"],
        )
    assert exc.value.code == "wip_limit_exceeded"
    assert exc.value.details["group_key"] == "in_progress"
    assert exc.value.details["limit"] == 1
    assert exc.value.details["count"] == 1

    # DB unchanged: still todo, no position row.
    assert await _position_row(session_factory, view_id, uuid.UUID(mover["id"])) is None


async def test_move_wip_warn_over_succeeds_and_emits(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="in_progress", status_map=status_map
    )
    mover = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 1, "enforcement": "warn"}}},
    )
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(mover["id"]),
        to_group_key="in_progress",
        position=1.0,
        version=mover["version"],
    )
    assert result["state_category"] == "in_progress"
    exceeded = await _outbox_events(session_factory, "view.wip_exceeded")
    assert any(frame["data"]["group_key"] == "in_progress" for frame in exceeded)


# ---------------------------------------------------------------------------
# other group_by dimensions
# ---------------------------------------------------------------------------


async def test_move_group_by_priority(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="priority")
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="high",
        position=1.0,
        version=issue["version"],
    )
    assert result["priority"] == "high"


async def test_move_group_by_assignee(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="assignee")
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(member.id),
        position=1.0,
        version=issue["version"],
    )
    assert result["assignee_id"] == str(member.id)

    # Drag back to the "__none__" column clears the assignee.
    result2 = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="__none__",
        position=2.0,
        version=result["version"],
    )
    assert result2["assignee_id"] is None


# ---------------------------------------------------------------------------
# two-dimensional cell moves + quick-create (kanban §3.2/§4.5)
# ---------------------------------------------------------------------------


async def test_move_updates_primary_and_swimlane_axes_atomically(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
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
        sub_group_by="priority",
    )
    view_id = uuid.UUID(view["id"])

    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="in_progress",
        to_sub_group_key="high",
        position=2.5,
        version=issue["version"],
    )

    assert result["state_category"] == "in_progress"
    assert result["priority"] == "high"
    row = await _position_row(session_factory, view_id, uuid.UUID(issue["id"]))
    assert row is not None
    assert row.group_key == "in_progress"
    assert row.sub_group_key == "high"
    moved = await _outbox_events(session_factory, "issue.moved")
    frame = next(frame for frame in moved if frame["data"]["id"] == issue["id"])
    assert frame["data"]["from_sub_group"] == "low"
    assert frame["data"]["to_sub_group"] == "high"


async def test_pure_swimlane_move_does_not_reapply_full_primary_wip(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    mover = await _mk_issue(
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
        sub_group_by="priority",
        board_settings={"wip": {"todo": {"limit": 1, "enforcement": "block"}}},
    )
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(mover["id"]),
        to_group_key="todo",
        to_sub_group_key="high",
        position=1.0,
        version=mover["version"],
    )
    assert result["priority"] == "high"


async def test_move_sub_group_shape_is_fail_closed(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    one_dimensional = await _mk_view(
        view_service, actor=member, workspace=workspace, group_by="state_category"
    )
    with pytest.raises(ValidationError):
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(one_dimensional["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="todo",
            to_sub_group_key="high",
            position=1.0,
        )


async def test_quick_create_inherits_both_cell_axes(session_factory) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title="Created in cell",
        group_key="in_progress",
        sub_group_key="urgent",
    )
    assert created["title"] == "Created in cell"
    assert created["state_category"] == "in_progress"
    assert created["priority"] == "urgent"
    row = await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(created["id"]))
    assert row is not None
    assert (row.group_key, row.sub_group_key) == ("in_progress", "urgent")


async def test_quick_create_filter_mismatch_rolls_back(session_factory) -> None:
    from mesh.db.models.issue import Issue

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
        filters={
            "operator": "AND",
            "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
        },
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Must roll back",
            group_key="todo",
            sub_group_key="low",
        )
    assert excinfo.value.code == "quick_create_filter_mismatch"
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(Issue).where(Issue.workspace_id == workspace.id)
        )
        stored_workspace = await session.get(Workspace, workspace.id)
        position_count = await session.scalar(
            select(func.count())
            .select_from(ViewIssuePosition)
            .where(ViewIssuePosition.view_id == uuid.UUID(view["id"]))
        )
    assert count == 0
    assert stored_workspace is not None and stored_workspace.inbox_issue_seq == 0
    assert position_count == 0
    assert await _outbox_events(session_factory, "issue.created") == []


async def test_quick_create_idempotency_key_replays_first_result(session_factory) -> None:
    from mesh.db.models.issue import Issue

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
    )
    kwargs = {
        "actor": member,
        "workspace_id": workspace.id,
        "view_id": uuid.UUID(view["id"]),
        "group_key": "todo",
        "sub_group_key": "high",
        "idempotency_key": "quick-create-retry-1",
    }
    first = await board.quick_create(title="First title", **kwargs)
    replay = await board.quick_create(title="A changed retry body", **kwargs)

    assert replay["id"] == first["id"]
    assert replay["title"] == "First title"
    async with session_factory() as session:
        issue_count = await session.scalar(
            select(func.count()).select_from(Issue).where(Issue.workspace_id == workspace.id)
        )
        position_count = await session.scalar(
            select(func.count())
            .select_from(ViewIssuePosition)
            .where(ViewIssuePosition.view_id == uuid.UUID(view["id"]))
        )
        ledger_count = await session.scalar(
            select(func.count())
            .select_from(ViewQuickCreateRequest)
            .where(ViewQuickCreateRequest.view_id == uuid.UUID(view["id"]))
        )
    assert (issue_count, position_count, ledger_count) == (1, 1, 1)
    created_events = await _outbox_events(session_factory, "issue.created")
    matching_events = [frame for frame in created_events if frame["data"]["issue"]["id"] == first["id"]]
    # One detail-channel and one workspace-list event, both from the first
    # transaction only. A replay must not emit either channel again.
    assert len(matching_events) == 2
    assert len({frame["channel"] for frame in matching_events}) == 2


async def test_quick_create_rejects_empty_idempotency_key(session_factory) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    with pytest.raises(ValidationError) as excinfo:
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Not created",
            group_key="todo",
            idempotency_key="   ",
        )
    assert excinfo.value.details == {"field": "Idempotency-Key"}


async def test_quick_create_guest_is_rejected_before_writes(session_factory) -> None:
    from mesh.db.models.issue import Issue

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        visibility="shared",
        group_by="state_category",
    )
    async with session_factory() as session, session.begin():
        stored = await session.get(Member, member.id)
        assert stored is not None
        stored.role = "guest"
    member.role = "guest"

    with pytest.raises(ForbiddenError):
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Guest must not create",
            group_key="todo",
        )
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Issue).where(Issue.workspace_id == workspace.id)
            )
            == 0
        )


async def test_quick_create_project_axes_are_symmetric(session_factory) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    project, _unused = await _two_projects(session_factory, workspace, member)

    project_columns = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="project",
        sub_group_by="state_category",
    )
    first = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(project_columns["id"]),
        title="Project first",
        group_key=str(project.id),
        sub_group_key="in_progress",
    )

    project_lanes = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="project",
    )
    second = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(project_lanes["id"]),
        title="Project lane",
        group_key="in_review",
        sub_group_key=str(project.id),
    )

    assert (first["project_id"], first["state_category"]) == (
        str(project.id),
        "in_progress",
    )
    assert (second["project_id"], second["state_category"]) == (
        str(project.id),
        "in_review",
    )


async def test_quick_create_requires_target_project_write(session_factory) -> None:
    from tests.unit.test_view_service import _create_project

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    project = await _create_project(session_factory, workspace, visibility="public", key="NOACCESS")
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="project",
    )
    with pytest.raises(ForbiddenError):
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="No project permission",
            group_key="todo",
            sub_group_key=str(project.id),
        )


async def test_quick_create_category_status_incompatibility_is_zero_write(
    session_factory,
) -> None:
    from mesh.db.models.issue import Issue

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    statuses = await _status_map(session_factory, workspace)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="status",
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Incompatible cell",
            group_key="todo",
            sub_group_key=str(statuses["in_progress"]),
        )
    assert excinfo.value.code == "incompatible_projection_cell"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Issue)) == 0


async def test_quick_create_wip_is_shared_across_swimlanes(session_factory) -> None:
    from mesh.db.models.issue import Issue

    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by="priority",
        board_settings={"wip": {"todo": {"limit": 1, "enforcement": "block"}}},
    )

    async def create_in_lane(priority: str):
        return await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title=f"Lane {priority}",
            group_key="todo",
            sub_group_key=priority,
        )

    outcomes = await asyncio.gather(create_in_lane("low"), create_in_lane("high"), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    rejected = next(item for item in outcomes if isinstance(item, BusinessRuleError))
    assert rejected.code == "wip_limit_exceeded"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Issue)) == 1


# ---------------------------------------------------------------------------
# group_by = project → two-step cross-project contract (README §9 T22)
# ---------------------------------------------------------------------------


async def _two_projects(session_factory, workspace, member):
    from tests.unit.test_view_service import _create_project, _grant_project_access

    src = await _create_project(session_factory, workspace, visibility="public", key="SRC")
    dst = await _create_project(session_factory, workspace, visibility="public", key="DST")
    await _grant_project_access(session_factory, workspace, src, member)
    await _grant_project_access(session_factory, workspace, dst, member)
    return src, dst


async def test_move_project_unconfirmed_requires_confirmation(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    src, dst = await _two_projects(session_factory, workspace, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(src.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key=str(dst.id),
            position=1.0,
            version=issue["version"],
        )
    assert exc.value.code == "move_confirmation_required"
    assert "preview" in exc.value.details


async def test_move_project_dry_run_returns_preview(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    src, dst = await _two_projects(session_factory, workspace, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(src.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    preview = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(dst.id),
        position=1.0,
        version=issue["version"],
        dry_run=True,
    )
    assert preview["target_project_id"] == str(dst.id)
    assert "mapped_fields" in preview and "cleared_fields" in preview


async def test_move_project_confirmed(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    src, dst = await _two_projects(session_factory, workspace, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(src.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    view_id = uuid.UUID(view["id"])
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(dst.id),
        position=1.5,
        version=issue["version"],
        confirm=True,
    )
    assert result["project_id"] == str(dst.id)
    assert "move_result" in result
    # Position upserted for the project group key.
    row = await _position_row(session_factory, view_id, uuid.UUID(issue["id"]))
    assert row is not None
    assert row.group_key == str(dst.id)
    changed = await _outbox_events(session_factory, "issue.project_changed")
    assert any(frame["data"]["id"] == issue["id"] for frame in changed)


@pytest.mark.parametrize(
    ("group_by", "sub_group_by"),
    [
        ("project", "state_category"),
        ("state_category", "project"),
    ],
)
async def test_move_project_axis_is_symmetric_in_two_dimensions(
    session_factory, group_by: str, sub_group_by: str
) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    src, dst = await _two_projects(session_factory, workspace, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(src.id),
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=group_by,
        sub_group_by=sub_group_by,
    )
    if group_by == "project":
        group_key, sub_group_key = str(dst.id), "in_progress"
    else:
        group_key, sub_group_key = "in_progress", str(dst.id)

    with pytest.raises(BusinessRuleError) as excinfo:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key=group_key,
            to_sub_group_key=sub_group_key,
            position=3.0,
            version=issue["version"],
        )
    assert excinfo.value.code == "move_confirmation_required"

    moved = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=group_key,
        to_sub_group_key=sub_group_key,
        position=3.0,
        version=issue["version"],
        confirm=True,
    )
    assert (moved["project_id"], moved["state_category"]) == (
        str(dst.id),
        "in_progress",
    )
    row = await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(issue["id"]))
    assert row is not None
    assert (row.group_key, row.sub_group_key) == (group_key, sub_group_key)


# ---------------------------------------------------------------------------
# reorder (no status change)
# ---------------------------------------------------------------------------


async def test_reorder_writes_position_not_issue_position(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        position=7.0,
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_id = uuid.UUID(view["id"])
    await board.reorder(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="todo",
        position=3.0,
    )
    row = await _position_row(session_factory, view_id, uuid.UUID(issue["id"]))
    assert row is not None and row.position == 3.0
    # issues.position untouched (no cross-view pollution, kanban §2.7).
    from mesh.db.models.issue import Issue

    async with session_factory() as session:
        stored = await session.scalar(select(Issue).where(Issue.id == uuid.UUID(issue["id"])))
    assert stored.position == 7.0


async def test_reorder_does_not_touch_other_view(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view_a = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_b = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    await board.reorder(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view_a["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="todo",
        position=1.0,
    )
    # Only view A got a position row; view B is unaffected (kanban §2.7 isolation).
    assert await _position_row(session_factory, uuid.UUID(view_a["id"]), uuid.UUID(issue["id"]))
    assert await _position_row(session_factory, uuid.UUID(view_b["id"]), uuid.UUID(issue["id"])) is None


async def test_reorder_rejects_a_forged_swimlane_cell(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
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
        sub_group_by="priority",
    )
    view_id = uuid.UUID(view["id"])
    with pytest.raises(BusinessRuleError) as excinfo:
        await board.reorder(
            actor=member,
            workspace_id=workspace.id,
            view_id=view_id,
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="todo",
            sub_group_key="high",
            position=1.0,
        )
    assert excinfo.value.code == "incompatible_projection_cell"
    assert await _position_row(session_factory, view_id, uuid.UUID(issue["id"])) is None


async def test_reorder_precision_exhaustion_reranks_column(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    a = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    b = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    c = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="state_category")
    view_id = uuid.UUID(view["id"])

    # Two adjacent rows whose gap is below the float-midpoint floor.
    async with session_factory() as session, session.begin():
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id,
                view_id=view_id,
                issue_id=uuid.UUID(a["id"]),
                group_key="todo",
                position=1.0,
            )
        )
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id,
                view_id=view_id,
                issue_id=uuid.UUID(b["id"]),
                group_key="todo",
                position=1.0 + 1e-9,
            )
        )

    # Dropping C onto 1.0 collides → the whole column re-spaces to integers.
    await board.reorder(
        actor=member,
        workspace_id=workspace.id,
        view_id=view_id,
        issue_id=uuid.UUID(c["id"]),
        to_group_key="todo",
        position=1.0,
    )
    row_a = await _position_row(session_factory, view_id, uuid.UUID(a["id"]))
    row_b = await _position_row(session_factory, view_id, uuid.UUID(b["id"]))
    row_c = await _position_row(session_factory, view_id, uuid.UUID(c["id"]))
    # The exhausted column re-spaces EVERY card (incl. the moved one) to distinct
    # integer positions; the exact card→slot mapping follows id order (§4.3).
    positions = {row_a.position, row_b.position, row_c.position}
    assert positions == {1.0, 2.0, 3.0}
    # Whole-column convergence broadcast (the reranked cards carry view_id).
    moved = await _outbox_events(session_factory, "issue.moved")
    moved_ids = {frame["data"]["id"] for frame in moved}
    assert {a["id"], b["id"], c["id"]} <= moved_ids


# ---------------------------------------------------------------------------
# edge paths: missing view, label gating, status/priority/assignee WIP
# ---------------------------------------------------------------------------


async def test_move_missing_view_not_found(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.uuid4(),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="in_progress",
            position=1.0,
        )


async def test_label_axis_move_and_reorder_are_read_only(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="label")
    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="lbl_x",
            position=1.0,
        )
    assert exc.value.code == "multi_value_axis_move_unsupported"

    with pytest.raises(BusinessRuleError) as reorder_exc:
        await board.reorder(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="lbl_x",
            position=1.0,
        )
    assert reorder_exc.value.code == "multi_value_axis_move_unsupported"


async def test_multi_value_axes_quick_create_writes_both_associations_atomically(
    session_factory,
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    label, definition, option = await _multi_value_fixture(session_factory, workspace)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="label",
        sub_group_by=str(definition.id),
    )

    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title="Created in two dynamic axes",
        group_key=str(label.id),
        sub_group_key=str(option.id),
    )

    assert created["labels"] == [{"id": str(label.id), "name": label.name, "color": label.color}]
    assert created["custom_field_values"] == [
        {
            "field_def_id": str(definition.id),
            "value_text": None,
            "value_number": None,
            "value_date": None,
            "value_member_id": None,
            "value_boolean": None,
            "value_json": [str(option.id)],
        }
    ]
    async with session_factory() as session:
        label_row = await session.scalar(
            select(IssueLabel).where(IssueLabel.issue_id == uuid.UUID(created["id"]))
        )
        field_row = await session.scalar(
            select(IssueCustomFieldValue).where(IssueCustomFieldValue.issue_id == uuid.UUID(created["id"]))
        )
    assert label_row is not None and label_row.label_id == label.id
    assert field_row is not None and field_row.value_json == [str(option.id)]
    created_events = await _outbox_events(session_factory, "issue.created")
    snapshots = [
        frame["data"]["issue"] for frame in created_events if frame["data"]["issue"]["id"] == created["id"]
    ]
    assert snapshots
    assert all(snapshot["labels"] == created["labels"] for snapshot in snapshots)
    assert all(snapshot["custom_field_values"] == created["custom_field_values"] for snapshot in snapshots)


async def test_multi_value_axes_quick_create_none_keeps_empty_sets(session_factory) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    _label, definition, option = await _multi_value_fixture(session_factory, workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldDef, definition.id)
        assert stored is not None
        stored.default_value = [str(option.id)]
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="label",
        sub_group_by=str(definition.id),
    )

    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title="Empty dynamic values",
        group_key="__none__",
        sub_group_key="__none__",
    )

    assert created["labels"] == []
    assert created["custom_field_values"] == []


async def test_quick_create_applies_non_axis_custom_default_before_filter_check(
    session_factory,
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    definition, _minor, major = await _single_select_fixture(session_factory, workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldDef, definition.id)
        assert stored is not None
        stored.default_value = str(major.id)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="priority",
        filters={
            "operator": "AND",
            "conditions": [
                {
                    "field_kind": "custom_field",
                    "field_def_id": str(definition.id),
                    "op": "eq",
                    "value": str(major.id),
                }
            ],
        },
    )

    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title="Defaulted severity",
        group_key="high",
    )

    async with session_factory() as session:
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(created["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert value is not None and value.value_json == str(major.id)
    assert created["custom_field_values"][0]["value_json"] == str(major.id)


async def test_multi_select_quick_create_rejects_inactive_option_before_writes(
    session_factory,
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    _label, definition, option = await _multi_value_fixture(session_factory, workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldOption, option.id)
        assert stored is not None
        stored.is_active = False
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    with pytest.raises(BusinessRuleError) as exc:
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Must not be created",
            group_key=str(option.id),
        )
    assert exc.value.code == "invalid_field_value"
    async with session_factory() as session:
        issue_count = await session.scalar(
            select(func.count()).select_from(Issue).where(Issue.workspace_id == workspace.id)
        )
        stored_workspace = await session.get(Workspace, workspace.id)
    assert issue_count == 0
    assert stored_workspace is not None and stored_workspace.inbox_issue_seq == 0
    assert await _outbox_events(session_factory, "issue.created") == []


async def test_single_select_primary_axis_move_writes_eav_position_and_realtime(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    definition, _minor, major = await _single_select_fixture(session_factory, workspace)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    moved = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(major.id),
        position=7.5,
        version=issue["version"],
    )

    async with session_factory() as session:
        stored_issue = await session.get(Issue, uuid.UUID(issue["id"]))
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    position = await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(issue["id"]))
    assert moved["version"] == issue["version"] + 1
    assert stored_issue is not None and stored_issue.version == moved["version"]
    assert stored_issue.updated_at == FIXED_NOW
    assert value is not None and value.value_json == str(major.id)
    assert position is not None
    assert (position.group_key, position.sub_group_key, float(position.position)) == (
        str(major.id),
        "",
        7.5,
    )
    events = await _outbox_events(session_factory, "issue.custom_field_changed")
    assert any(
        event["data"]["field_def_id"] == str(definition.id)
        and event["data"]["value"]["value_json"] == str(major.id)
        for event in events
    )


async def test_single_select_swimlane_move_writes_secondary_axis(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    definition, minor, _major = await _single_select_fixture(session_factory, workspace)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace, priority="high")
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="priority",
        sub_group_by=str(definition.id),
    )

    await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="high",
        to_sub_group_key=str(minor.id),
        position=3.0,
        version=issue["version"],
    )

    async with session_factory() as session:
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    position = await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(issue["id"]))
    assert value is not None and value.value_json == str(minor.id)
    assert position is not None
    assert (position.group_key, position.sub_group_key) == ("high", str(minor.id))


async def test_single_select_move_to_none_clears_eav_row(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    definition, minor, _major = await _single_select_fixture(session_factory, workspace)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        session.add(
            IssueCustomFieldValue(
                workspace_id=workspace.id,
                issue_id=uuid.UUID(issue["id"]),
                field_def_id=definition.id,
                value_json=str(minor.id),
            )
        )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    moved = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key="__none__",
        position=2.0,
        version=issue["version"],
    )

    async with session_factory() as session:
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert value is None
    assert moved["version"] == issue["version"] + 1
    events = await _outbox_events(session_factory, "issue.custom_field_changed")
    assert any(
        event["data"]["field_def_id"] == str(definition.id) and event["data"]["value"] is None
        for event in events
    )


async def test_single_select_quick_create_supports_primary_secondary_and_none(
    session_factory,
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    definition, minor, major = await _single_select_fixture(session_factory, workspace)
    primary_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )
    primary = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(primary_view["id"]),
        title="Primary scalar custom",
        group_key=str(major.id),
    )
    secondary_view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="priority",
        sub_group_by=str(definition.id),
    )
    secondary = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(secondary_view["id"]),
        title="Secondary scalar custom",
        group_key="low",
        sub_group_key=str(minor.id),
    )
    empty = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(primary_view["id"]),
        title="Empty scalar custom",
        group_key="__none__",
    )

    assert primary["custom_field_values"][0]["value_json"] == str(major.id)
    assert secondary["custom_field_values"][0]["value_json"] == str(minor.id)
    assert empty["custom_field_values"] == []
    primary_position = await _position_row(
        session_factory, uuid.UUID(primary_view["id"]), uuid.UUID(primary["id"])
    )
    secondary_position = await _position_row(
        session_factory, uuid.UUID(secondary_view["id"]), uuid.UUID(secondary["id"])
    )
    empty_position = await _position_row(
        session_factory, uuid.UUID(primary_view["id"]), uuid.UUID(empty["id"])
    )
    assert primary_position is not None and primary_position.group_key == str(major.id)
    assert secondary_position is not None
    assert (secondary_position.group_key, secondary_position.sub_group_key) == (
        "low",
        str(minor.id),
    )
    assert empty_position is not None and empty_position.group_key == "__none__"


async def test_single_select_move_filter_mismatch_rolls_back_eav_and_position(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    definition, minor, major = await _single_select_fixture(session_factory, workspace)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        session.add(
            IssueCustomFieldValue(
                workspace_id=workspace.id,
                issue_id=uuid.UUID(issue["id"]),
                field_def_id=definition.id,
                value_json=str(minor.id),
            )
        )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
        filters={
            "operator": "AND",
            "conditions": [
                {
                    "field_kind": "custom_field",
                    "field_def_id": str(definition.id),
                    "op": "eq",
                    "value": str(minor.id),
                }
            ],
        },
    )

    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key=str(major.id),
            position=9.0,
            version=issue["version"],
        )
    assert exc.value.code == "incompatible_projection_cell"
    async with session_factory() as session:
        stored_issue = await session.get(Issue, uuid.UUID(issue["id"]))
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert stored_issue is not None and stored_issue.version == issue["version"]
    assert value is not None and value.value_json == str(minor.id)
    assert await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(issue["id"])) is None


async def test_single_select_inactive_option_is_rejected_without_writes(
    session_factory,
) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    definition, _minor, major = await _single_select_fixture(session_factory, workspace)
    issue = await _mk_issue(issue_service, actor=member, workspace=workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldOption, major.id)
        assert stored is not None
        stored.is_active = False
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key=str(major.id),
            position=1.0,
            version=issue["version"],
        )
    assert exc.value.code == "invalid_field_value"
    async with session_factory() as session:
        stored_issue = await session.get(Issue, uuid.UUID(issue["id"]))
        value_count = await session.scalar(
            select(func.count())
            .select_from(IssueCustomFieldValue)
            .where(IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]))
        )
    assert stored_issue is not None and stored_issue.version == issue["version"]
    assert value_count == 0
    assert await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(issue["id"])) is None


@pytest.mark.parametrize("custom_axis_first", [False, True])
async def test_diagonal_move_validates_required_field_after_custom_axis_write(
    session_factory,
    custom_axis_first,
) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    statuses = await _status_map(session_factory, workspace)
    definition, _minor, major = await _single_select_fixture(session_factory, workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldDef, definition.id)
        assert stored is not None
        stored.is_required = True
        stored.required_on = ["status:done"]
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=statuses,
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id) if custom_axis_first else "state_category",
        sub_group_by="state_category" if custom_axis_first else str(definition.id),
    )

    moved = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(major.id) if custom_axis_first else "done",
        to_sub_group_key="done" if custom_axis_first else str(major.id),
        position=4.0,
        version=issue["version"],
    )

    async with session_factory() as session:
        stored_issue = await session.get(Issue, uuid.UUID(issue["id"]))
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert moved["version"] == issue["version"] + 1
    assert stored_issue is not None and stored_issue.state_category == "done"
    assert value is not None and value.value_json == str(major.id)


async def test_diagonal_move_missing_required_custom_axis_rolls_back(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    statuses = await _status_map(session_factory, workspace)
    definition, _minor, _major = await _single_select_fixture(session_factory, workspace)
    async with session_factory() as session, session.begin():
        stored = await session.get(CustomFieldDef, definition.id)
        assert stored is not None
        stored.is_required = True
        stored.required_on = ["status:done"]
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=statuses,
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="state_category",
        sub_group_by=str(definition.id),
    )

    with pytest.raises(BusinessRuleError) as excinfo:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="done",
            to_sub_group_key="__none__",
            position=4.0,
            version=issue["version"],
        )
    assert excinfo.value.code == "required_field_missing"
    async with session_factory() as session:
        stored_issue = await session.get(Issue, uuid.UUID(issue["id"]))
        value_count = await session.scalar(
            select(func.count())
            .select_from(IssueCustomFieldValue)
            .where(IssueCustomFieldValue.issue_id == uuid.UUID(issue["id"]))
        )
    assert stored_issue is not None and stored_issue.state_category == "todo"
    assert stored_issue.version == issue["version"]
    assert value_count == 0


@pytest.mark.parametrize(
    ("field_type", "group_key", "value_column", "expected"),
    [
        ("text", "none", "value_text", "none"),
        ("textarea", "Longer board value", "value_text", "Longer board value"),
        ("url", "https://mesh.example/board", "value_text", "https://mesh.example/board"),
        ("number", "12.5", "value_number", 12.5),
        ("date", "2026-08-05T00:00:00Z", "value_date", "2026-08-05"),
        ("datetime", "2026-08-05T12:34:56Z", "value_date", "2026-08-05T12:34:56Z"),
        ("boolean", "true", "value_boolean", True),
        ("member", "__actor__", "value_member_id", "__actor__"),
    ],
)
async def test_scalar_custom_quick_create_uses_typed_eav_column(
    session_factory,
    field_type,
    group_key,
    value_column,
    expected,
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    actual_key = str(member.id) if group_key == "__actor__" else group_key
    if field_type in {"text", "textarea", "url"}:
        actual_key = _encode_custom_text_key(actual_key)
    actual_expected = str(member.id) if expected == "__actor__" else expected
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name=f"{field_type} {uuid.uuid4().hex[:6]}",
            field_key=f"scalar_{uuid.uuid4().hex[:8]}",
            type=field_type,
            config={},
        )
        session.add(definition)
        await session.flush()
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title=f"Typed {field_type}",
        group_key=actual_key,
    )

    async with session_factory() as session:
        row = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(created["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert row is not None
    stored = getattr(row, value_column)
    if field_type == "number":
        assert float(stored) == actual_expected
    elif field_type == "date":
        assert stored.date().isoformat() == actual_expected
    elif field_type == "datetime":
        assert stored.isoformat().replace("+00:00", "Z") == actual_expected
    elif field_type == "member":
        assert str(stored) == actual_expected
    else:
        assert stored == actual_expected
    position = await _position_row(session_factory, uuid.UUID(view["id"]), uuid.UUID(created["id"]))
    assert position is not None and position.group_key == actual_key


@pytest.mark.parametrize(
    ("field_type", "group_key"),
    [
        ("boolean", "yes"),
        ("date", "notTdate"),
        ("member", "00000000-0000-0000-0000-000000000001"),
    ],
)
async def test_scalar_custom_quick_create_rejects_invalid_typed_key(
    session_factory, field_type, group_key
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name=f"Invalid {field_type}",
            field_key=f"invalid_{uuid.uuid4().hex[:8]}",
            type=field_type,
            config={},
        )
        session.add(definition)
        await session.flush()
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )

    with pytest.raises(BusinessRuleError):
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Invalid scalar value",
            group_key=group_key,
        )


async def test_text_custom_literal_none_key_is_not_treated_as_empty(session_factory) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    async with session_factory() as session, session.begin():
        definition = CustomFieldDef(
            workspace_id=workspace.id,
            name="Literal sentinel",
            field_key=f"literal_{uuid.uuid4().hex[:8]}",
            type="text",
            config={},
        )
        session.add(definition)
        await session.flush()
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by=str(definition.id),
    )
    literal_key = _encode_custom_text_key("__none__")

    created = await board.quick_create(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        title="Literal none",
        group_key=literal_key,
    )

    async with session_factory() as session:
        value = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == uuid.UUID(created["id"]),
                IssueCustomFieldValue.field_def_id == definition.id,
            )
        )
    assert value is not None and value.value_text == "__none__"
    position = await _position_row(
        session_factory, uuid.UUID(view["id"]), uuid.UUID(created["id"])
    )
    assert position is not None and position.group_key == literal_key


async def test_label_quick_create_constraint_failure_rolls_back_entire_transaction(
    session_factory, monkeypatch
) -> None:
    workspace, member, _issue_service, view_service, board = await _setup(session_factory)
    label, _definition, _option = await _multi_value_fixture(session_factory, workspace)
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="label")
    real_write = board._write_multi_value_targets

    async def fail_after_valid_association(session, **kwargs):
        await real_write(session, **kwargs)
        issue = kwargs["issue"]
        session.add(
            IssueLabel(
                workspace_id=workspace.id,
                issue_id=issue.id,
                label_id=uuid.uuid4(),
            )
        )
        await session.flush()

    monkeypatch.setattr(board, "_write_multi_value_targets", fail_after_valid_association)
    with pytest.raises(IntegrityError):
        await board.quick_create(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            title="Everything rolls back",
            group_key=str(label.id),
        )

    async with session_factory() as session:
        issue_count = await session.scalar(
            select(func.count()).select_from(Issue).where(Issue.workspace_id == workspace.id)
        )
        label_count = await session.scalar(
            select(func.count()).select_from(IssueLabel).where(IssueLabel.workspace_id == workspace.id)
        )
        stored_workspace = await session.get(Workspace, workspace.id)
    assert (issue_count, label_count) == (0, 0)
    assert stored_workspace is not None and stored_workspace.inbox_issue_seq == 0
    assert await _outbox_events(session_factory, "issue.created") == []


async def test_move_group_by_status(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="status")
    target_status = status_map["in_progress"]
    result = await board.move(
        actor=member,
        workspace_id=workspace.id,
        view_id=uuid.UUID(view["id"]),
        issue_id=uuid.UUID(issue["id"]),
        to_group_key=str(target_status),
        position=1.0,
        version=issue["version"],
    )
    assert result["status"]["id"] == str(target_status)


async def test_move_wip_priority_group(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        priority="high",
    )
    mover = await _mk_issue(
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
        group_by="priority",
        board_settings={"wip": {"high": {"limit": 1, "enforcement": "block"}}},
    )
    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(mover["id"]),
            to_group_key="high",
            position=1.0,
            version=mover["version"],
        )
    assert exc.value.code == "wip_limit_exceeded"
    assert exc.value.details["group_key"] == "high"


async def test_move_wip_assignee_group_none(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    # One unassigned issue already occupies the __none__ column (limit 1, block).
    await _mk_issue(issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map)
    mover = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        assignee_id=str(member.id),
    )
    view = await _mk_view(
        view_service,
        actor=member,
        workspace=workspace,
        group_by="assignee",
        board_settings={"wip": {"__none__": {"limit": 1, "enforcement": "block"}}},
    )
    with pytest.raises(BusinessRuleError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(mover["id"]),
            to_group_key="__none__",
            position=1.0,
            version=mover["version"],
        )
    assert exc.value.code == "wip_limit_exceeded"


# ---------------------------------------------------------------------------
# 必修-1: group_by=project confirmed move shares ONE transaction with the
# per-view position upsert (kanban.md §3.2 single-txn contract).
# ---------------------------------------------------------------------------


async def _make_project(session_factory, workspace, *, key: str):
    from mesh.db.models.project import Project

    async with session_factory() as session, session.begin():
        project = Project(workspace_id=workspace.id, name=f"Proj {key}", key=key, visibility="public")
        session.add(project)
    return project


async def _grant(session_factory, workspace, project, member):
    from mesh.db.models.project import ProjectMember

    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=project.id, member_id=member.id, role="member"
            )
        )


async def test_move_project_confirmed_is_single_transaction(session_factory) -> None:
    """If the position upsert fails, the migration must roll back too — proof the
    two share one transaction (§3.2)."""
    from mesh.db.models.issue import Issue
    from mesh.db.tenant import set_tenant_context

    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    src = await _make_project(session_factory, workspace, key="SRC")
    dst = await _make_project(session_factory, workspace, key="DST")
    await _grant(session_factory, workspace, src, member)
    await _grant(session_factory, workspace, dst, member)
    issue = await _mk_issue(
        issue_service,
        actor=member,
        workspace=workspace,
        category="todo",
        status_map=status_map,
        project_id=str(src.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    issue_id = uuid.UUID(issue["id"])

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated position-upsert failure")

    board._upsert_position_tx = _boom  # type: ignore[method-assign]

    import pytest as _pt

    with _pt.raises(RuntimeError):
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=issue_id,
            to_group_key=str(dst.id),
            position=1.5,
            version=issue["version"],
            confirm=True,
        )

    # Migration rolled back alongside the upsert → project_id unchanged.
    async with session_factory() as session:
        await set_tenant_context(session, workspace.id)
        row = await session.get(Issue, issue_id)
    assert row is not None
    assert row.project_id == src.id  # NOT dst → the migration did not commit

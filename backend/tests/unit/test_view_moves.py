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


async def test_move_label_group_by_gated(session_factory) -> None:
    workspace, member, issue_service, view_service, board = await _setup(session_factory)
    status_map = await _status_map(session_factory, workspace)
    issue = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="label")
    from mesh.errors import ValidationError
    from mesh.views.projection import PROJECTION_FIELD_PENDING

    with pytest.raises(ValidationError) as exc:
        await board.move(
            actor=member,
            workspace_id=workspace.id,
            view_id=uuid.UUID(view["id"]),
            issue_id=uuid.UUID(issue["id"]),
            to_group_key="lbl_x",
            position=1.0,
        )
    assert exc.value.code == PROJECTION_FIELD_PENDING


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

"""BoardMoveService tests — atomic move + WIP against real PostgreSQL.

Covers kanban.md §3.2/§4.3/§4.4 and README §9 T9/T22: the move command's single
transaction (optimistic lock → advisory lock → WIP count → field change →
per-view position upsert), WIP block/warn enforcement, cross-column status
change via column_target_status, group_by=priority/assignee moves, and the
group_by=project two-step cross-project contract (preview → confirm).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, ConflictError
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
    board = BoardMoveService(
        session_factory, issue_service, move_service, view_service, clock=_clock
    )
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
    assert (
        await _position_row(session_factory, uuid.UUID(view_b["id"]), uuid.UUID(issue["id"]))
        is None
    )


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
                workspace_id=workspace.id, view_id=view_id, issue_id=uuid.UUID(a["id"]),
                group_key="todo", position=1.0,
            )
        )
        session.add(
            ViewIssuePosition(
                workspace_id=workspace.id, view_id=view_id, issue_id=uuid.UUID(b["id"]),
                group_key="todo", position=1.0 + 1e-9,
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
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, priority="high",
    )
    mover = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, priority="low",
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
    await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo", status_map=status_map
    )
    mover = await _mk_issue(
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, assignee_id=str(member.id),
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
        project = Project(
            workspace_id=workspace.id, name=f"Proj {key}", key=key, visibility="public"
        )
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
        issue_service, actor=member, workspace=workspace, category="todo",
        status_map=status_map, project_id=str(src.id),
    )
    view = await _mk_view(view_service, actor=member, workspace=workspace, group_by="project")
    issue_id = uuid.UUID(issue["id"])

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated position-upsert failure")

    board._upsert_position_tx = _boom  # type: ignore[method-assign]

    import pytest as _pt

    with _pt.raises(RuntimeError):
        await board.move(
            actor=member, workspace_id=workspace.id, view_id=uuid.UUID(view["id"]),
            issue_id=issue_id, to_group_key=str(dst.id), position=1.5,
            version=issue["version"], confirm=True,
        )

    # Migration rolled back alongside the upsert → project_id unchanged.
    async with session_factory() as session:
        await set_tenant_context(session, workspace.id)
        row = await session.get(Issue, issue_id)
    assert row is not None
    assert row.project_id == src.id  # NOT dst → the migration did not commit

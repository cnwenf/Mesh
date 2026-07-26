"""Final branch coverage: bulk per-item paths (service level), status
service update/delete paths, and a few route-layer edges (issue.md §5.5/§5.2).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError, NotFoundError, ValidationError
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService
from mesh.issue.schemas import BulkChanges, BulkRequest, CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import StatusPatch, StatusService
from mesh.project.schemas import CreateProjectRequest
from mesh.project.service import ProjectService


def _mgr(m: Member) -> bool:
    return m.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory):
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory):
    return ProjectService(session_factory)


@pytest.fixture
def status_service(session_factory):
    return StatusService(session_factory, is_workspace_manager=_mgr)


@pytest.fixture
def move_service(issue_service):
    return MoveService(issue_service)


@pytest.fixture
def bulk_service(issue_service, move_service):
    return BulkService(issue_service, move_service)


async def _ws(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws = Workspace(name="Final", slug=f"fin-{uuid.uuid4().hex[:10]}")
        session.add(ws)
    return ws


async def _member(session_factory, ws, *, role="member"):
    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x", display_name="F")
        session.add(user)
        await session.flush()
        member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _issue(issue_service, *, actor, ws, **fields):
    fields.setdefault("title", "t")
    return await issue_service.create_issue(
        actor=actor, workspace_id=ws.id, body=CreateIssueRequest(**fields)
    )


def _unset(v):
    return v is None


@pytest.mark.unit
async def test_bulk_delete_and_invalid_ids_service_level(session_factory, issue_service, bulk_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    i1 = await _issue(issue_service, actor=owner, ws=ws)
    i2 = await _issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=ws.id,
            body=BulkRequest(issue_ids=[i1["id"], i2["id"], "not-a-uuid"], delete=True),
        )
    # invalid id → per-item failure; real deletes still succeeded
    details = exc_info.value.details
    assert details["succeeded"] == 2 and details["failed"] == 1
    assert details["errors"][0]["code"] == "not_found"
    from sqlalchemy import select

    from mesh.db.models.issue import Issue

    async with session_factory() as session:
        deleted_rows = (
            (
                await session.execute(
                    select(Issue).where(
                        Issue.id.in_([uuid.UUID(i1["id"]), uuid.UUID(i2["id"])])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(row.deleted_at is not None for row in deleted_rows)


@pytest.mark.unit
async def test_bulk_unconfirmed_previews_include_not_found(
    session_factory, issue_service, bulk_service, project_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    project = await project_service.create_project(
        actor=owner,
        workspace_id=ws.id,
        body=CreateProjectRequest(name="FP", key=f"F{uuid.uuid4().hex[:4].upper()}"),
    )
    issue = await _issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=ws.id,
            body=BulkRequest(
                issue_ids=[issue["id"], str(uuid.uuid4())],
                changes=BulkChanges(project_id=project["id"]),
            ),
        )
    assert exc_info.value.code == "move_confirmation_required"
    previews = exc_info.value.details["previews"]
    assert any(p.get("error") == "not_found" for p in previews)
    assert any(p.get("issue_id") == issue["id"] for p in previews)


@pytest.mark.unit
async def test_bulk_status_and_cycle_changes(session_factory, issue_service, bulk_service, project_service):
    from mesh.project.schemas import CreateCycleRequest

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    cycle = await project_service.create_cycle(
        actor=owner,
        workspace_id=ws.id,
        body=CreateCycleRequest(name="FC", starts_at=date(2026, 8, 1), ends_at=date(2026, 8, 7)),
    )
    issue = await _issue(issue_service, actor=owner, ws=ws)
    from sqlalchemy import select

    from mesh.db.models.issue import IssueStatus

    async with session_factory() as session:
        done = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == ws.id, IssueStatus.category == "done"
            )
        )
    result = await bulk_service.execute(
        actor=owner,
        workspace_id=ws.id,
        body=BulkRequest(
            issue_ids=[issue["id"]],
            changes=BulkChanges(status_id=str(done.id), cycle_id=cycle["id"]),
        ),
    )
    assert result["succeeded"] == 1
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"])
    )
    assert got["state_category"] == "done" and got["cycle_id"] == cycle["id"]


@pytest.mark.unit
async def test_status_service_update_paths(session_factory, status_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    created = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Path", category="todo"
    )
    sid = uuid.UUID(created["id"])
    updated = await status_service.update_status(
        actor=owner,
        workspace_id=ws.id,
        status_id=sid,
        patch=StatusPatch(name="Path 2", color="#abcdef", position=5.0, category="in_progress"),
        is_unset=_unset,
    )
    assert updated["name"] == "Path 2"
    assert updated["category"] == "in_progress"
    # handoff to default, then bare-unset refusal
    to_default = await status_service.update_status(
        actor=owner, workspace_id=ws.id, status_id=sid,
        patch=StatusPatch(is_default=True), is_unset=_unset,
    )
    assert to_default["is_default"] is True
    with pytest.raises(BusinessRuleError) as exc_info:
        await status_service.update_status(
            actor=owner, workspace_id=ws.id, status_id=sid,
            patch=StatusPatch(is_default=False), is_unset=_unset,
        )
    assert exc_info.value.code == "default_status_required"
    # no-op update (nothing present) renders current
    same = await status_service.update_status(
        actor=owner, workspace_id=ws.id, status_id=sid,
        patch=StatusPatch(), is_unset=_unset,
    )
    assert same["id"] == str(sid)
    # create with is_default=True hands off the existing default
    new_default = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Path Default", category="todo", is_default=True
    )
    assert new_default["is_default"] is True
    # delete the now-non-default, unreferenced status
    deleted = await status_service.delete_status(
        actor=owner, workspace_id=ws.id, status_id=sid
    )
    assert deleted["deleted"] is True
    with pytest.raises(NotFoundError):
        await status_service.delete_status(actor=owner, workspace_id=ws.id, status_id=sid)


@pytest.mark.unit
async def test_create_status_default_taken_defensive_branch(session_factory, status_service):
    """Two concurrent is_default inserts: the partial unique index resolves
    the race; the second creation still succeeds via the handoff unsets."""
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    a = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Race A", category="todo", is_default=True
    )
    b = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Race B", category="todo", is_default=True
    )
    statuses = await status_service.list_statuses(workspace_id=ws.id)
    defaults = [s for s in statuses if s["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == b["id"] and a["id"] != b["id"]


@pytest.mark.unit
async def test_bulk_move_maps_private_status_and_clears_milestone(
    session_factory, issue_service, bulk_service, project_service, status_service
):
    from sqlalchemy import select

    from mesh.db.models.outbox import OutboxEvent
    from mesh.project.schemas import CreateMilestoneRequest

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    async def _mk(key, **fields):
        return await project_service.create_project(
            actor=owner, workspace_id=ws.id,
            body=CreateProjectRequest(name=f"P{key}", key=key, **fields),
        )

    source = await _mk(f"S{uuid.uuid4().hex[:4].upper()}")
    target = await _mk(f"T{uuid.uuid4().hex[:4].upper()}")
    private = await _mk(f"P{uuid.uuid4().hex[:4].upper()}", visibility="private")
    pstatus = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="BulkPriv", category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    milestone = await project_service.create_milestone(
        actor=owner, workspace_id=ws.id, project_id=uuid.UUID(source["id"]),
        body=CreateMilestoneRequest(title="BM"),
    )
    issue = await _issue(
        issue_service, actor=owner, ws=ws, project_id=source["id"],
        status_id=pstatus["id"], milestone_id=milestone["id"],
    )
    result = await bulk_service.execute(
        actor=owner, workspace_id=ws.id,
        body=BulkRequest(issue_ids=[issue["id"]],
                         changes=BulkChanges(project_id=target["id"]), confirm=True),
    )
    assert result["succeeded"] == 1
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"])
    )
    assert got["milestone_id"] is None            # cleared
    assert got["status_id"] != pstatus["id"]      # mapped off the private status
    assert got["state_category"] == "in_progress"

    # public → private move emits the list-removal frame (issue.deleted)
    issue2 = await _issue(issue_service, actor=owner, ws=ws, project_id=target["id"])
    await bulk_service.execute(
        actor=owner, workspace_id=ws.id,
        body=BulkRequest(issue_ids=[issue2["id"]],
                         changes=BulkChanges(project_id=private["id"]), confirm=True),
    )
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.workspace_id == ws.id)
                )
            )
            .scalars()
            .all()
        )
        frames = [
            e.payload for e in events
            if e.event_type == "realtime.publish" and e.payload.get("event") == "issue.deleted"
        ]
        assert any(f["data"]["id"] == issue2["id"] for f in frames)


@pytest.mark.unit
async def test_status_update_rename_conflict_and_invalid_name(session_factory, status_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    a = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Taken", category="todo"
    )
    b = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Free", category="todo"
    )
    from mesh.errors import ConflictError

    with pytest.raises(ConflictError) as exc_info:
        await status_service.update_status(
            actor=owner, workspace_id=ws.id, status_id=uuid.UUID(b["id"]),
            patch=StatusPatch(name="Taken"), is_unset=_unset,
        )
    assert exc_info.value.code == "status_name_taken"
    with pytest.raises(ValidationError):
        await status_service.update_status(
            actor=owner, workspace_id=ws.id, status_id=uuid.UUID(b["id"]),
            patch=StatusPatch(name="x" * 51), is_unset=_unset,
        )
    assert a["id"] != b["id"]


@pytest.mark.unit
async def test_project_scope_seeding(session_factory, status_service):
    from mesh.issue.statuses import scope_status_count, seed_default_statuses

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    project = await ProjectService(session_factory).create_project(
        actor=owner, workspace_id=ws.id,
        body=CreateProjectRequest(name="PS", key=f"PS{uuid.uuid4().hex[:3].upper()}"),
    )
    pid = uuid.UUID(project["id"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        assert await scope_status_count(session, workspace_id=ws.id, project_id=pid) == 0
        created = await seed_default_statuses(session, workspace_id=ws.id, project_id=pid)
        assert len(created) == 7
        # idempotent: second call seeds nothing
        assert await seed_default_statuses(session, workspace_id=ws.id, project_id=pid) == []
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        assert await scope_status_count(session, workspace_id=ws.id, project_id=pid) == 7
    # project-private statuses are only visible when listing that scope
    listing = await status_service.list_statuses(workspace_id=ws.id, project_id=pid)
    assert len(listing) == 14  # 7 workspace-level + 7 project-private

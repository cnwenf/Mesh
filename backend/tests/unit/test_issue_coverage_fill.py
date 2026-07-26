"""Targeted coverage for issue-module branches: list filters/sort/group,
PATCH field branches, guest visibility matrix, query-cost backstop, move
clearing rules, bulk edges, status/template edges and the channel checker's
involvement/deleted-project paths (issue.md §3.2/§3.5/§3.8, README §6.14).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from mesh.db.models.issue import Issue
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError, ValidationError
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService
from mesh.issue.schemas import (
    BulkChanges,
    BulkRequest,
    CreateIssueRequest,
    CreateIssueTemplateRequest,
    InstantiateIssueTemplateRequest,
    UpdateIssueTemplateRequest,
)
from mesh.issue.service import IssuePatch, IssueService
from mesh.issue.statuses import StatusService
from mesh.issue.templates import TemplateService
from mesh.project.schemas import (
    CreateCycleRequest,
    CreateMilestoneRequest,
    CreateProjectRequest,
)
from mesh.project.service import ProjectService


def _mgr(member: Member) -> bool:
    return member.role in ("owner", "admin")


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


@pytest.fixture
def template_service(issue_service):
    return TemplateService(issue_service)


async def _ws(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws = Workspace(name="Cov", slug=f"cov-{uuid.uuid4().hex[:10]}")
        session.add(ws)
    return ws


async def _member(session_factory, ws, *, role="member"):
    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x", display_name="C")
        session.add(user)
        await session.flush()
        member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _project(project_service, *, actor, ws, key=None, **fields):
    return await project_service.create_project(
        actor=actor,
        workspace_id=ws.id,
        body=CreateProjectRequest(
            name=f"P{uuid.uuid4().hex[:5]}", key=key or f"C{uuid.uuid4().hex[:4].upper()}", **fields
        ),
    )


async def _issue(issue_service, *, actor, ws, **fields):
    fields.setdefault("title", "t")
    return await issue_service.create_issue(
        actor=actor, workspace_id=ws.id, body=CreateIssueRequest(**fields)
    )


# ---------------------------------------------------------------------------
# list filters / sort / group branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_list_all_flat_filters(session_factory, issue_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    assignee = await _member(session_factory, ws)
    project = await _project(project_service, actor=owner, ws=ws)
    cycle = await project_service.create_cycle(
        actor=owner,
        workspace_id=ws.id,
        body=CreateCycleRequest(name="Sprint 1", starts_at=date(2026, 8, 1), ends_at=date(2026, 8, 14)),
    )
    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=ws.id,
        project_id=uuid.UUID(project["id"]),
        body=CreateMilestoneRequest(title="M"),
    )
    target = await _issue(
        issue_service,
        actor=owner,
        ws=ws,
        project_id=project["id"],
        assignee_id=str(assignee.id),
        cycle_id=cycle["id"],
        milestone_id=milestone["id"],
        due_date="2026-08-10",
        start_date="2026-08-01",
    )
    await _issue(issue_service, actor=owner, ws=ws)  # noise

    result = await issue_service.list_issues(
        viewer=owner,
        workspace_id=ws.id,
        status_id=uuid.UUID(target["status_id"]),
        state_category=target["state_category"],
        assignee_id=assignee.id,
        reporter_id=owner.id,
        project_id=uuid.UUID(project["id"]),
        cycle_id=uuid.UUID(cycle["id"]),
        milestone_id=uuid.UUID(milestone["id"]),
        parent_id=None,
        due_before=date(2026, 8, 31),
        due_after=date(2026, 8, 1),
        q="t",
    )
    assert [item["id"] for item in result["data"]] == [target["id"]]
    # invalid enum params
    with pytest.raises(ValidationError):
        await issue_service.list_issues(viewer=owner, workspace_id=ws.id, priority="bogus")
    with pytest.raises(ValidationError):
        await issue_service.list_issues(viewer=owner, workspace_id=ws.id, state_category="bogus")
    with pytest.raises(ValidationError):
        await issue_service.list_issues(viewer=owner, workspace_id=ws.id, group_by="bogus")


@pytest.mark.unit
async def test_priority_sort_both_orders_and_cursor(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    for p in ("none", "low", "medium", "high", "urgent"):
        await _issue(issue_service, actor=owner, ws=ws, priority=p)

    desc = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="priority", order="desc", limit=2
    )
    assert [i["priority"] for i in desc["data"]] == ["urgent", "high"]
    assert desc["next_cursor"]
    desc2 = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="priority", order="desc", limit=2,
        cursor=desc["next_cursor"],
    )
    assert [i["priority"] for i in desc2["data"]] == ["medium", "low"]

    asc = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="priority", order="asc", limit=3
    )
    assert [i["priority"] for i in asc["data"]] == ["none", "low", "medium"]
    asc2 = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="priority", order="asc", limit=3,
        cursor=asc["next_cursor"],
    )
    assert [i["priority"] for i in asc2["data"]] == ["high", "urgent"]

    # position sort with cursor
    pos = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="position", order="asc", limit=2
    )
    assert pos["next_cursor"]


@pytest.mark.unit
async def test_group_by_assignee_project_cycle_labels(session_factory, issue_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    assignee = await _member(session_factory, ws)
    project = await _project(project_service, actor=owner, ws=ws)
    cycle = await project_service.create_cycle(
        actor=owner,
        workspace_id=ws.id,
        body=CreateCycleRequest(name="S1", starts_at=date(2026, 8, 1), ends_at=date(2026, 8, 7)),
    )
    await _issue(issue_service, actor=owner, ws=ws, assignee_id=str(assignee.id),
                 project_id=project["id"], cycle_id=cycle["id"])
    await _issue(issue_service, actor=owner, ws=ws)  # unassigned / no project / no cycle

    for group_by, expected_keys in (
        ("assignee", {"unassigned"}),
        ("project", {"no_project"}),
        ("cycle", {"no_cycle"}),
    ):
        result = await issue_service.list_issues(
            viewer=owner, workspace_id=ws.id, group_by=group_by
        )
        keys = {g["key"] for g in result["groups"]}
        assert expected_keys <= keys
        assert all(g["label"] for g in result["groups"])
        assert sum(g["count"] for g in result["groups"]) == 2


@pytest.mark.unit
async def test_query_cost_exceeded_backstop(session_factory, issue_service, monkeypatch):
    from sqlalchemy import insert

    import mesh.issue.service as svc

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    issue = await _issue(issue_service, actor=owner, ws=ws)
    # bulk-insert rows so the leading-wildcard ILIKE scan reliably overruns
    async with session_factory() as session, session.begin():
        rows = [
            {
                "workspace_id": ws.id,
                "identifier_namespace_key": "WS",
                "number": 1000 + i,
                "identifier": f"WS-{1000 + i}",
                "title": f"bulk row {i}",
                "status_id": uuid.UUID(issue["status_id"]),
                "state_category": issue["state_category"],
            }
            for i in range(20000)
        ]
        await session.execute(insert(Issue), rows)
    monkeypatch.setattr(svc, "LIST_STATEMENT_TIMEOUT_MS", 1)
    with pytest.raises(BusinessRuleError) as exc_info:
        await issue_service.list_issues(viewer=owner, workspace_id=ws.id, q="%a%b%c%")
    assert exc_info.value.code == "query_cost_exceeded"


@pytest.mark.unit
async def test_children_and_activity_pagination(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    parent = await _issue(issue_service, actor=owner, ws=ws)
    for i in range(3):
        await _issue(issue_service, actor=owner, ws=ws, parent_id=parent["id"], title=f"c{i}")
    page1, cursor = await issue_service.list_children(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(parent["id"]), limit=2
    )
    assert len(page1) == 2 and cursor
    page2, cursor2 = await issue_service.list_children(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(parent["id"]), limit=2, cursor=cursor
    )
    assert len(page2) == 1 and cursor2 is None

    # generate some activity
    issue_id = uuid.UUID(page1[0]["id"])
    for priority in ("low", "high"):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=issue_id,
            patch=IssuePatch(priority=priority),
        )
    act1, act_cursor = await issue_service.list_activity(
        viewer=owner, workspace_id=ws.id, issue_id=issue_id, limit=1
    )
    assert len(act1) == 1 and act_cursor
    act2, _ = await issue_service.list_activity(
        viewer=owner, workspace_id=ws.id, issue_id=issue_id, limit=1, cursor=act_cursor
    )
    assert len(act2) == 1


# ---------------------------------------------------------------------------
# guest visibility matrix (§3.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_guest_and_member_visibility_matrix(session_factory, issue_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    member = await _member(session_factory, ws)
    guest = await _member(session_factory, ws, role="guest")
    private = await _project(project_service, actor=owner, ws=ws, visibility="private")
    assignee_guest_issue = await _issue(
        issue_service, actor=owner, ws=ws, project_id=private["id"],
        assignee_id=str(guest.id),
    )
    plain_private_issue = await _issue(
        issue_service, actor=owner, ws=ws, project_id=private["id"]
    )
    # guest: involved issue visible, unrelated private issue 404
    got = await issue_service.get_issue(
        viewer=guest, workspace_id=ws.id, issue_id=uuid.UUID(assignee_guest_issue["id"])
    )
    assert got["id"] == assignee_guest_issue["id"]
    with pytest.raises(NotFoundError):
        await issue_service.get_issue(
            viewer=guest, workspace_id=ws.id, issue_id=uuid.UUID(plain_private_issue["id"])
        )
    # member without project membership: 403 on private
    with pytest.raises(ForbiddenError):
        await issue_service.get_issue(
            viewer=member, workspace_id=ws.id, issue_id=uuid.UUID(plain_private_issue["id"])
        )
    # explicit guest grant unlocks the project's issues
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=ws.id,
                project_id=uuid.UUID(private["id"]),
                member_id=guest.id,
                permission="read",
            )
        )
    got2 = await issue_service.get_issue(
        viewer=guest, workspace_id=ws.id, issue_id=uuid.UUID(plain_private_issue["id"])
    )
    assert got2["id"] == plain_private_issue["id"]
    # guest write with only a read grant → forbidden; guest list sees granted
    with pytest.raises(ForbiddenError):
        await issue_service.update_issue(
            actor=guest, workspace_id=ws.id, issue_id=uuid.UUID(plain_private_issue["id"]),
            patch=IssuePatch(title="nope"),
        )
    listing = await issue_service.list_issues(viewer=guest, workspace_id=ws.id)
    assert {item["id"] for item in listing["data"]} == {
        assignee_guest_issue["id"], plain_private_issue["id"]
    }


@pytest.mark.unit
async def test_guest_write_grant_allows_patch(session_factory, issue_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    guest = await _member(session_factory, ws, role="guest")
    project = await _project(project_service, actor=owner, ws=ws, visibility="private")
    issue = await _issue(issue_service, actor=owner, ws=ws, project_id=project["id"])
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=ws.id, project_id=uuid.UUID(project["id"]),
                member_id=guest.id, permission="write",
            )
        )
    patched = await issue_service.update_issue(
        actor=guest, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(priority="high"),
    )
    assert patched["priority"] == "high"


# ---------------------------------------------------------------------------
# PATCH field branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_patch_milestone_cycle_reporter_dates(session_factory, issue_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    other = await _member(session_factory, ws)
    project = await _project(project_service, actor=owner, ws=ws)
    other_project = await _project(project_service, actor=owner, ws=ws)
    milestone = await project_service.create_milestone(
        actor=owner, workspace_id=ws.id, project_id=uuid.UUID(project["id"]),
        body=CreateMilestoneRequest(title="M1"),
    )
    other_milestone = await project_service.create_milestone(
        actor=owner, workspace_id=ws.id, project_id=uuid.UUID(other_project["id"]),
        body=CreateMilestoneRequest(title="M2"),
    )
    cycle = await project_service.create_cycle(
        actor=owner, workspace_id=ws.id,
        body=CreateCycleRequest(name="S", starts_at=date(2026, 9, 1), ends_at=date(2026, 9, 7)),
    )
    issue = await _issue(issue_service, actor=owner, ws=ws, project_id=project["id"])
    iid = uuid.UUID(issue["id"])

    updated = await issue_service.update_issue(
        actor=owner, workspace_id=ws.id, issue_id=iid,
        patch=IssuePatch(
            milestone_id=uuid.UUID(milestone["id"]),
            cycle_id=uuid.UUID(cycle["id"]),
            reporter_id=other.id,
            due_date=date(2026, 9, 10),
            start_date=date(2026, 9, 1),
            estimate=8,
            estimate_unit="points",
            position=7.5,
        ),
    )
    assert updated["milestone_id"] == milestone["id"]
    assert updated["cycle_id"] == cycle["id"]
    assert updated["reporter_id"] == str(other.id)

    # milestone from another project → 400; unknown cycle → 400
    with pytest.raises(ValidationError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=iid,
            patch=IssuePatch(milestone_id=uuid.UUID(other_milestone["id"])),
        )
    with pytest.raises(ValidationError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=iid,
            patch=IssuePatch(cycle_id=uuid.uuid4()),
        )
    with pytest.raises(ValidationError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=iid,
            patch=IssuePatch(milestone_id=uuid.uuid4()),
        )
    # due < start combinations both directions
    with pytest.raises(ValidationError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=iid,
            patch=IssuePatch(due_date=date(2026, 8, 1)),
        )
    with pytest.raises(ValidationError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=iid,
            patch=IssuePatch(start_date=date(2026, 10, 1)),
        )
    # clearing milestone/cycle/reporter to None
    cleared = await issue_service.update_issue(
        actor=owner, workspace_id=ws.id, issue_id=iid,
        patch=IssuePatch(milestone_id=None, cycle_id=None, reporter_id=None,
                         due_date=None, start_date=None, estimate=None, estimate_unit=None),
    )
    assert cleared["milestone_id"] is None and cleared["cycle_id"] is None
    assert cleared["reporter_id"] is None and cleared["estimate"] is None


@pytest.mark.unit
async def test_patch_parent_reparent_and_status_validation(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    a = await _issue(issue_service, actor=owner, ws=ws, title="a")
    b = await _issue(issue_service, actor=owner, ws=ws, title="b")
    reparented = await issue_service.update_issue(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(b["id"]),
        patch=IssuePatch(parent_id=uuid.UUID(a["id"])),
    )
    assert reparented["parent_id"] == a["id"]
    detached = await issue_service.update_issue(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(b["id"]),
        patch=IssuePatch(parent_id=None),
    )
    assert detached["parent_id"] is None
    # status out of scope → 404
    with pytest.raises(NotFoundError):
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(a["id"]),
            patch=IssuePatch(status_id=uuid.uuid4()),
        )


@pytest.mark.unit
async def test_if_match_garbage_rejected(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    issue = await _issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(Exception) as exc_info:
        await issue_service.update_issue(
            actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"]),
            patch=IssuePatch(title="x"), if_match="garbage-etag",
        )
    assert exc_info.value.code == "conflict"


# ---------------------------------------------------------------------------
# move: clearing rules + mapping to done
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_move_clears_project_cycle_keeps_workspace_cycle(
    session_factory, issue_service, move_service, project_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    source = await _project(project_service, actor=owner, ws=ws)
    target = await _project(project_service, actor=owner, ws=ws)
    project_cycle = await project_service.create_cycle(
        actor=owner, workspace_id=ws.id,
        body=CreateCycleRequest(name="bound", starts_at=date(2026, 8, 1),
                                ends_at=date(2026, 8, 7), project_id=source["id"]),
    )
    workspace_cycle = await project_service.create_cycle(
        actor=owner, workspace_id=ws.id,
        body=CreateCycleRequest(name="free", starts_at=date(2026, 8, 1),
                                ends_at=date(2026, 8, 7)),
    )
    # project-bound cycle is cleared
    i1 = await _issue(issue_service, actor=owner, ws=ws, project_id=source["id"],
                      cycle_id=project_cycle["id"])
    moved1 = await move_service.move(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(i1["id"]),
        target_project_id=uuid.UUID(target["id"]), confirm=True,
        expected_version=i1["version"],
    )
    assert moved1["cycle_id"] is None
    # workspace-level cycle is KEPT
    i2 = await _issue(issue_service, actor=owner, ws=ws, project_id=source["id"],
                      cycle_id=workspace_cycle["id"])
    preview2 = await move_service.preview(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(i2["id"]),
        target_project_id=uuid.UUID(target["id"]),
    )
    assert all(c["field"] != "cycle_id" for c in preview2["cleared_fields"])
    moved2 = await move_service.move(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(i2["id"]),
        target_project_id=uuid.UUID(target["id"]), confirm=True,
        expected_version=i2["version"],
    )
    assert moved2["cycle_id"] == workspace_cycle["id"]


@pytest.mark.unit
async def test_move_status_mapping_into_done_sets_completed_at(
    session_factory, issue_service, move_service, project_service, status_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    source = await _project(project_service, actor=owner, ws=ws)
    target = await _project(project_service, actor=owner, ws=ws)
    private_status = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="SrcOnly", category="done",
        project_id=uuid.UUID(source["id"]),
    )
    issue = await _issue(issue_service, actor=owner, ws=ws, project_id=source["id"],
                         status_id=private_status["id"])
    assert issue["completed_at"] is not None
    moved = await move_service.move(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]), confirm=True,
        expected_version=issue["version"],
    )
    # mapped to target-scope done default; completed_at stays set
    assert moved["state_category"] == "done"
    assert moved["completed_at"] is not None
    assert moved["status_id"] != private_status["id"]


@pytest.mark.unit
async def test_move_from_deleted_project_to_inbox(
    session_factory, issue_service, move_service, project_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    source = await _project(project_service, actor=owner, ws=ws)
    issue = await _issue(issue_service, actor=owner, ws=ws, project_id=source["id"])
    moved = await move_service.move(
        actor=owner, workspace_id=ws.id, issue_id=uuid.UUID(issue["id"]),
        target_project_id=None, confirm=True,
        expected_version=issue["version"],
    )
    assert moved["project_id"] is None and moved["identifier"] == issue["identifier"]


# ---------------------------------------------------------------------------
# bulk edges
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bulk_no_changes_no_delete_per_item_error(session_factory, issue_service, bulk_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    issue = await _issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner, workspace_id=ws.id,
            body=BulkRequest(issue_ids=[issue["id"]]),
        )
    assert exc_info.value.code == "bulk_partial_failure"
    assert exc_info.value.details["errors"][0]["code"] == "validation_error"


@pytest.mark.unit
async def test_bulk_assignee_and_project_confirm(
    session_factory, issue_service, bulk_service, project_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    member = await _member(session_factory, ws)
    target = await _project(project_service, actor=owner, ws=ws)
    i1 = await _issue(issue_service, actor=owner, ws=ws)
    i2 = await _issue(issue_service, actor=owner, ws=ws)
    # assignee change via bulk
    result = await bulk_service.execute(
        actor=owner, workspace_id=ws.id,
        body=BulkRequest(issue_ids=[i1["id"], i2["id"]],
                         changes=BulkChanges(assignee_id=str(member.id))),
    )
    assert result["succeeded"] == 2
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(i1["id"])
    )
    assert got["assignee_id"] == str(member.id)
    # invalid assignee → per-item 422 failures
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner, workspace_id=ws.id,
            body=BulkRequest(issue_ids=[i1["id"]],
                             changes=BulkChanges(assignee_id=str(uuid.uuid4()))),
        )
    assert exc_info.value.details["errors"][0]["code"] == "assignee_not_member"
    # project change without confirm → move_confirmation_required with previews
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner, workspace_id=ws.id,
            body=BulkRequest(issue_ids=[i1["id"]], changes=BulkChanges(project_id=target["id"])),
        )
    assert exc_info.value.code == "move_confirmation_required"
    assert exc_info.value.details["previews"]
    # with confirm it moves
    moved = await bulk_service.execute(
        actor=owner, workspace_id=ws.id,
        body=BulkRequest(issue_ids=[i1["id"]],
                         changes=BulkChanges(project_id=target["id"]), confirm=True),
    )
    assert moved["succeeded"] == 1


# ---------------------------------------------------------------------------
# status + template edges
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_status_validation_and_guest_gates(session_factory, status_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    guest = await _member(session_factory, ws, role="guest")
    with pytest.raises(ValidationError):
        await status_service.create_status(
            actor=owner, workspace_id=ws.id, name="X", category="bogus"
        )
    with pytest.raises(ValidationError):
        await status_service.create_status(
            actor=owner, workspace_id=ws.id, name="y" * 51, category="todo"
        )
    with pytest.raises(ValidationError):
        await status_service.create_status(
            actor=guest, workspace_id=ws.id, name="G", category="todo"
        )
    created = await status_service.create_status(
        actor=owner, workspace_id=ws.id, name="Editable", category="todo"
    )
    with pytest.raises(ValidationError):
        await status_service.update_status(
            actor=owner, workspace_id=ws.id, status_id=uuid.UUID(created["id"]),
            patch=_spatch(category="bogus"), is_unset=lambda v: v is None,
        )
    with pytest.raises(ValidationError):
        await status_service.update_status(
            actor=guest, workspace_id=ws.id, status_id=uuid.UUID(created["id"]),
            patch=_spatch(name="Hack"), is_unset=lambda v: v is None,
        )
    with pytest.raises(ValidationError):
        await status_service.delete_status(
            actor=guest, workspace_id=ws.id, status_id=uuid.UUID(created["id"])
        )
    # unknown status → 404
    with pytest.raises(NotFoundError):
        await status_service.update_status(
            actor=owner, workspace_id=ws.id, status_id=uuid.uuid4(),
            patch=_spatch(name="Nope"), is_unset=lambda v: v is None,
        )
    with pytest.raises(NotFoundError):
        await status_service.delete_status(
            actor=owner, workspace_id=ws.id, status_id=uuid.uuid4()
        )


def _spatch(**kwargs):
    from mesh.issue.statuses import StatusPatch

    base = {"name": None, "color": None, "position": None, "category": None, "is_default": None}
    base.update(kwargs)
    return StatusPatch(**base)


@pytest.mark.unit
async def test_template_edges(session_factory, issue_service, template_service, project_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    other = await _member(session_factory, ws)
    project = await _project(project_service, actor=owner, ws=ws)
    t1 = await template_service.create_template(
        actor=owner, workspace_id=ws.id,
        body=CreateIssueTemplateRequest(name="T1", template_body={}),
    )
    t2 = await template_service.create_template(
        actor=owner, workspace_id=ws.id,
        body=CreateIssueTemplateRequest(name="T2", template_body={}, project_id=project["id"]),
    )
    # guest forbidden
    guest = await _member(session_factory, ws, role="guest")
    with pytest.raises(ForbiddenError):
        await template_service.create_template(
            actor=guest, workspace_id=ws.id,
            body=CreateIssueTemplateRequest(name="G", template_body={}),
        )
    # name conflict
    from mesh.errors import ConflictError

    with pytest.raises(ConflictError) as exc_info:
        await template_service.create_template(
            actor=owner, workspace_id=ws.id,
            body=CreateIssueTemplateRequest(name="T1", template_body={}),
        )
    assert exc_info.value.code == "template_name_taken"
    # update rename into an occupied name IN THE SAME scope → conflict
    t3_name = await template_service.create_template(
        actor=owner, workspace_id=ws.id,
        body=CreateIssueTemplateRequest(name="T3", template_body={}, project_id=project["id"]),
    )
    with pytest.raises(ConflictError) as rename_exc:
        await template_service.update_template(
            actor=owner, workspace_id=ws.id, template_id=uuid.UUID(t3_name["id"]),
            body=UpdateIssueTemplateRequest(name="T2"),
        )
    assert rename_exc.value.code == "template_name_taken"
    # list with project filter
    items, cursor = await template_service.list_templates(
        viewer=owner, workspace_id=ws.id, project_id=uuid.UUID(project["id"]), limit=10
    )
    assert {t["id"] for t in items} == {t1["id"], t2["id"], t3_name["id"]}  # ws-level + project
    # admin (manager) can manage another's template
    admin = await _member(session_factory, ws, role="admin")
    updated = await template_service.update_template(
        actor=admin, workspace_id=ws.id, template_id=uuid.UUID(t1["id"]),
        body=UpdateIssueTemplateRequest(description="by admin"),
    )
    assert updated["description"] == "by admin"
    # unknown templates → 404
    with pytest.raises(NotFoundError):
        await template_service.update_template(
            actor=owner, workspace_id=ws.id, template_id=uuid.uuid4(),
            body=UpdateIssueTemplateRequest(name="X"),
        )
    with pytest.raises(NotFoundError):
        await template_service.delete_template(
            actor=owner, workspace_id=ws.id, template_id=uuid.uuid4()
        )
    with pytest.raises(NotFoundError):
        await template_service.instantiate(
            actor=owner, workspace_id=ws.id, template_id=uuid.uuid4(),
            body=InstantiateIssueTemplateRequest(title="x"),
        )
    # instantiate with stale status + project scope + custom field degradation
    body = {
        "project_id": project["id"],
        "status_id": str(uuid.uuid4()),
        "custom_field_values": {"severity": "major"},
        "assignee_id": str(owner.id),
        "estimate": 3,
        "estimate_unit": "points",
        "description": "d",
        "due_date": "2026-09-01",
        "start_date": "2026-08-01",
        "priority": "high",
    }
    t5 = await template_service.create_template(
        actor=owner, workspace_id=ws.id,
        body=CreateIssueTemplateRequest(name="T5", template_body=body),
    )
    created = await template_service.instantiate(
        actor=owner, workspace_id=ws.id, template_id=uuid.UUID(t5["id"]),
        body=InstantiateIssueTemplateRequest(title="from t3"),
    )
    reasons = {s["reason"] for s in created["skipped_fields"]}
    assert "reference_stale" in reasons and "custom_field_module_pending" in reasons
    assert created["project_id"] == project["id"]
    # delete by non-creator non-admin → forbidden
    with pytest.raises(ForbiddenError):
        await template_service.delete_template(
            actor=other, workspace_id=ws.id, template_id=uuid.UUID(t5["id"])
        )
    deleted = await template_service.delete_template(
        actor=owner, workspace_id=ws.id, template_id=uuid.UUID(t5["id"])
    )
    assert deleted["deleted"] is True


# ---------------------------------------------------------------------------
# channel checker: involvement + deleted project
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_channel_checker_involvement_and_deleted_project(
    session_factory, issue_service, project_service
):
    from mesh.issue.channels import make_issue_channel_checker
    from mesh.realtime.auth import Principal

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws, role="owner")
    member = await _member(session_factory, ws)
    private = await _project(project_service, actor=owner, ws=ws, visibility="private")
    involved = await _issue(issue_service, actor=owner, ws=ws, project_id=private["id"],
                            assignee_id=str(member.id))
    reporter_issue = await _issue(issue_service, actor=owner, ws=ws, project_id=private["id"],
                                  reporter_id=str(member.id))
    other_issue = await _issue(issue_service, actor=owner, ws=ws, project_id=private["id"])
    # issue whose project is soft-deleted → checker treats it as inbox-level
    orphan_project = await _project(project_service, actor=owner, ws=ws, visibility="private")
    orphan = await _issue(issue_service, actor=owner, ws=ws, project_id=orphan_project["id"])
    await project_service.delete_project(
        actor=owner, workspace_id=ws.id, project_id=uuid.UUID(orphan_project["id"])
    )

    checker = make_issue_channel_checker(session_factory)
    principal = Principal(subject=str(member.user_id), workspace_ids=frozenset({ws.id}))
    assert await checker(principal, f"issue:{involved['id']}") is True
    assert await checker(principal, f"issue:{reporter_issue['id']}") is True
    assert await checker(principal, f"issue:{other_issue['id']}") is False
    assert await checker(principal, f"issue:{orphan['id']}") is True
    # malformed channel / non-uuid key
    assert await checker(principal, "issue:nonsense") is False
    assert await checker(principal, "bad channel") is False
    # principal with no workspaces
    empty = Principal(subject=str(member.user_id), workspace_ids=frozenset())
    assert await checker(empty, f"issue:{involved['id']}") is False
    # unknown user subject in a workspace → False
    ghost = Principal(subject=str(uuid.uuid4()), workspace_ids=frozenset({ws.id}))
    assert await checker(ghost, f"issue:{involved['id']}") is False

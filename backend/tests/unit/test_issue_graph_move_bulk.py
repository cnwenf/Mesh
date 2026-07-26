"""Graph / move / bulk / DELETE-behavior tests (issue.md §2.5 / §3.8 / §5.3–§5.5,
README §9 T12/T18/T19/T22). Real PostgreSQL; concurrency cases use asyncio
tasks against the shared test database.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.issue import Issue, IssueDependency
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, ConflictError
from mesh.issue.bulk import BulkService
from mesh.issue.dependencies import DependencyService
from mesh.issue.move import MoveService
from mesh.issue.schemas import (
    BulkChanges,
    BulkRequest,
    CreateIssueRequest,
)
from mesh.issue.service import IssueService
from mesh.project.schemas import CreateProjectRequest
from mesh.project.service import ProjectService


def _is_manager(member: Member) -> bool:
    return member.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory) -> ProjectService:
    return ProjectService(session_factory)


@pytest.fixture
def dependency_service(issue_service) -> DependencyService:
    return DependencyService(issue_service)


@pytest.fixture
def move_service(issue_service) -> MoveService:
    return MoveService(issue_service)


@pytest.fixture
def bulk_service(issue_service, move_service) -> BulkService:
    return BulkService(issue_service, move_service)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Graph WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="member") -> Member:
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Tester")
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role=role
        )
        session.add(member)
    return member


async def _make_project(project_service, *, actor, workspace, key=None) -> dict:
    return await project_service.create_project(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateProjectRequest(
            name=f"Project {uuid.uuid4().hex[:6]}", key=key or f"K{uuid.uuid4().hex[:4].upper()}"
        ),
    )


async def _make_issue(issue_service, *, actor, workspace, title="t", **kwargs) -> dict:
    return await issue_service.create_issue(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title=title, **kwargs),
    )


# ---------------------------------------------------------------------------
# parent tree (§2.5 rule 3 / §5.3 / T12)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_self_parent_rejected_by_check(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace)
    from mesh.errors import MeshError

    with pytest.raises(MeshError):
        await issue_service.update_issue(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            patch=_patch(parent_id=uuid.UUID(issue["id"])),
        )


@pytest.mark.unit
async def test_deep_parent_cycle_rejected(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a = await _make_issue(issue_service, actor=owner, workspace=workspace, title="a")
    b = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="b", parent_id=a["id"]
    )
    c = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="c", parent_id=b["id"]
    )
    with pytest.raises(ConflictError) as exc_info:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(a["id"]),
            patch=_patch(parent_id=uuid.UUID(c["id"])),  # a under its grandchild
        )
    assert exc_info.value.code == "circular_parent"
    assert exc_info.value.details["path"]


@pytest.mark.unit
async def test_children_list_and_progress(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    parent = await _make_issue(issue_service, actor=owner, workspace=workspace, title="epic")
    child1 = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="s1", parent_id=parent["id"]
    )
    await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="s2", parent_id=parent["id"]
    )
    # complete one child
    async with session_factory() as session:
        from mesh.db.models.issue import IssueStatus

        done = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id, IssueStatus.category == "done"
            )
        )
    await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(child1["id"]),
        patch=_patch(status_id=done.id),
    )
    detail = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(parent["id"])
    )
    assert detail["children_progress"] == {"total": 2, "done": 1}
    children, _ = await issue_service.list_children(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(parent["id"])
    )
    assert len(children) == 2


# ---------------------------------------------------------------------------
# dependency graph (§2.5 rule 4 / §5.3 / T12)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_dependency_cycle_rejected_with_path(session_factory, dependency_service, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a = await _make_issue(issue_service, actor=owner, workspace=workspace, title="a")
    b = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b")
    await dependency_service.add_dependency(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(a["id"]),
        depends_on_id=uuid.UUID(b["id"]),
        dep_type="blocks",  # a blocks b
    )
    with pytest.raises(ConflictError) as exc_info:
        await dependency_service.add_dependency(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(b["id"]),
            depends_on_id=uuid.UUID(a["id"]),
            dep_type="blocks",  # b blocks a → cycle
        )
    assert exc_info.value.code == "circular_dependency"
    assert exc_info.value.details["path"]


@pytest.mark.unit
async def test_blocked_by_normalized_and_bidirectional(session_factory, dependency_service, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a = await _make_issue(issue_service, actor=owner, workspace=workspace, title="a")
    b = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b")
    created = await dependency_service.add_dependency(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(a["id"]),
        depends_on_id=uuid.UUID(b["id"]),
        dep_type="blocked_by",  # a blocked_by b
    )
    assert created["type"] == "blocked_by"
    assert created["issue_id"] == a["id"]
    # stored as a single blocks edge b → a
    async with session_factory() as session:
        edges = (
            (await session.execute(select(IssueDependency))).scalars().all()
        )
        assert len(edges) == 1
        assert edges[0].type == "blocks"
        assert str(edges[0].issue_id) == b["id"]
    # b's perspective reads blocked... wait, b blocks a: from b it's "blocks"
    from_b = await dependency_service.list_dependencies(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(b["id"])
    )
    assert from_b[0]["type"] == "blocks"
    assert from_b[0]["depends_on_id"] == a["id"]
    # duplicate edge (same relation via blocks direction) rejected
    with pytest.raises(ConflictError) as exc_info:
        await dependency_service.add_dependency(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(b["id"]),
            depends_on_id=uuid.UUID(a["id"]),
            dep_type="blocks",
        )
    assert exc_info.value.code == "dependency_exists"


@pytest.mark.unit
async def test_concurrent_opposite_dependencies_exactly_one_rejected(
    session_factory, dependency_service, issue_service
):
    """T12: advisory lock serialization — concurrent A→B / B→A cannot both pass."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a = await _make_issue(issue_service, actor=owner, workspace=workspace, title="a")
    b = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b")

    async def add(first: dict, second: dict):
        try:
            await dependency_service.add_dependency(
                actor=owner,
                workspace_id=workspace.id,
                issue_id=uuid.UUID(first["id"]),
                depends_on_id=uuid.UUID(second["id"]),
                dep_type="blocks",
            )
            return "ok"
        except ConflictError as exc:
            assert exc.code == "circular_dependency"
            return "cycle"

    results = await asyncio.gather(add(a, b), add(b, a))
    assert sorted(results) == ["cycle", "ok"]


@pytest.mark.unit
async def test_dependency_removal(session_factory, dependency_service, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    a = await _make_issue(issue_service, actor=owner, workspace=workspace, title="a")
    b = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b")
    created = await dependency_service.add_dependency(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(a["id"]),
        depends_on_id=uuid.UUID(b["id"]),
        dep_type="relates_to",
    )
    removed = await dependency_service.remove_dependency(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(a["id"]),
        dependency_id=uuid.UUID(created["id"]),
    )
    assert removed["deleted"] is True
    listing = await dependency_service.list_dependencies(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(a["id"])
    )
    assert listing == []


# ---------------------------------------------------------------------------
# cross-project move (§3.8 / §5.7 / T19 / T22)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_move_preview_maps_private_status_and_clears_project_fields(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="SRC")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="DST")
    # project-private status on the source project
    from mesh.issue.statuses import StatusService

    status_service = StatusService(session_factory, is_workspace_manager=_is_manager)
    private_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="源私有",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    # project-private milestone on source
    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=workspace.id,
        project_id=uuid.UUID(source["id"]),
        body=_milestone_body("M1"),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        title="mover",
        project_id=source["id"],
        status_id=private_status["id"],
        milestone_id=milestone["id"],
    )
    preview = await move_service.preview(
        viewer=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
    )
    mapped = {m["field"]: m for m in preview["mapped_fields"]}
    assert "status" in mapped
    assert mapped["status"]["from"]["id"] == private_status["id"]
    assert mapped["status"]["to"]["category"] == "in_progress"
    cleared_fields = {c["field"] for c in preview["cleared_fields"]}
    assert "milestone_id" in cleared_fields
    assert "identifier" in preview["kept_fields"]


def _milestone_body(title: str):
    from mesh.project.schemas import CreateMilestoneRequest

    return CreateMilestoneRequest(title=title)


@pytest.mark.unit
async def test_move_requires_confirmation_with_preview(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="S2")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="D2")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="m", project_id=source["id"]
    )
    with pytest.raises(BusinessRuleError) as exc_info:
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(target["id"]),
            confirm=False,
        )
    assert exc_info.value.code == "move_confirmation_required"
    assert exc_info.value.details["preview"]["issue_id"] == issue["id"]


@pytest.mark.unit
async def test_move_single_transaction_identifier_immutable_T19_T22(
    session_factory, issue_service, move_service, project_service
):
    from mesh.db.models.outbox import OutboxEvent

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="WEB")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="APP")
    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=workspace.id,
        project_id=uuid.UUID(source["id"]),
        body=_milestone_body("rel"),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        title="immutable",
        project_id=source["id"],
        milestone_id=milestone["id"],
    )
    # target project already has its own APP-1 (a different namespace) — the
    # moved WEB-1 must NOT collide (T19).
    await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="app one", project_id=target["id"]
    )
    moved = await move_service.move(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
        confirm=True,
        expected_version=issue["version"],
    )
    assert moved["identifier"] == "WEB-1"  # unchanged
    assert moved["identifier_namespace_key"] == "WEB"
    assert moved["number"] == 1
    assert moved["project_id"] == target["id"]
    assert moved["milestone_id"] is None  # project-private milestone cleared
    assert moved["version"] == issue["version"] + 1
    # project_changed event with mapped/cleared manifest
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
        moved_events = [
            e for e in events if e.payload.get("event") == "issue.project_changed"
        ]
        assert moved_events
        payload = moved_events[0].payload["data"]
        assert payload["from_project_id"] == source["id"]
        assert payload["to_project_id"] == target["id"]
        assert payload["cleared_fields"]
    # counters: the target's issue_seq was NOT consumed by the move
    async with session_factory() as session:
        from mesh.db.models.project import Project

        target_row = await session.get(Project, uuid.UUID(target["id"]))
        assert target_row.issue_seq == 1  # only "app one"


@pytest.mark.unit
async def test_move_version_conflict(session_factory, issue_service, move_service, project_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="S3")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="D3")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="v", project_id=source["id"]
    )
    with pytest.raises(ConflictError) as exc_info:
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(target["id"]),
            confirm=True,
            expected_version=99,
        )
    assert exc_info.value.code == "conflict"


# ---------------------------------------------------------------------------
# bulk (§5.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bulk_partial_failure_counts(session_factory, issue_service, bulk_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    i1 = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b1")
    i2 = await _make_issue(issue_service, actor=owner, workspace=workspace, title="b2")
    # guest cannot write project-less issues → per-item failure
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=guest,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[i1["id"], i2["id"]],
                changes=BulkChanges(priority="urgent"),
            ),
        )
    assert exc_info.value.code == "bulk_partial_failure"
    details = exc_info.value.details
    assert details["succeeded"] == 0
    assert details["failed"] == 2
    assert all(e["code"] == "forbidden" for e in details["errors"])


@pytest.mark.unit
async def test_bulk_success_and_delete(session_factory, issue_service, bulk_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    i1 = await _make_issue(issue_service, actor=owner, workspace=workspace, title="c1")
    i2 = await _make_issue(issue_service, actor=owner, workspace=workspace, title="c2")
    result = await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(issue_ids=[i1["id"], i2["id"]], changes=BulkChanges(priority="urgent")),
    )
    assert result == {"succeeded": 2, "failed": 0, "errors": []}
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(i1["id"])
    )
    assert got["priority"] == "urgent"
    deleted = await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(issue_ids=[i1["id"], i2["id"]], delete=True),
    )
    assert deleted["succeeded"] == 2
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Issue).where(Issue.id.in_([uuid.UUID(i1["id"]), uuid.UUID(i2["id"])]))
                )
            )
            .scalars()
            .all()
        )
        assert all(row.deleted_at is not None for row in rows)


@pytest.mark.unit
async def test_bulk_project_change_requires_confirm(
    session_factory, issue_service, bulk_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="BULK")
    i1 = await _make_issue(issue_service, actor=owner, workspace=workspace, title="m1")
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=workspace.id,
            body=BulkRequest(issue_ids=[i1["id"]], changes=BulkChanges(project_id=target["id"])),
        )
    assert exc_info.value.code == "move_confirmation_required"
    confirmed = await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(
            issue_ids=[i1["id"]], changes=BulkChanges(project_id=target["id"]), confirm=True
        ),
    )
    assert confirmed["succeeded"] == 1
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(i1["id"])
    )
    assert got["project_id"] == target["id"]
    assert got["identifier"] == "WS-1"  # numbering immutable across move


@pytest.mark.unit
async def test_bulk_move_preview_covers_every_item(
    session_factory, issue_service, bulk_service, project_service
):
    """L7(issue.md §3.8):未确认聚合预览覆盖全部 issue_ids(schema 上限
    100),确认前映射/清除清单对每一项可见,不截断。"""
    total = 25  # 旧实现截断前 20,第 21–25 项确认前不可见
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="BULKP")
    ids = [
        (await _make_issue(issue_service, actor=owner, workspace=workspace, title=f"m{n}"))["id"]
        for n in range(total)
    ]
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=workspace.id,
            body=BulkRequest(issue_ids=ids, changes=BulkChanges(project_id=target["id"])),
        )
    assert exc_info.value.code == "move_confirmation_required"
    previews = exc_info.value.details["previews"]
    assert len(previews) == total
    assert {preview["issue_id"] for preview in previews} == set(ids)
    # 全部为 plan(无截断、无 error marker):owner 对每项可读
    assert all("mapped_fields" in preview for preview in previews)


# ---------------------------------------------------------------------------
# real DELETE behaviors (README §9 T18)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_t18_member_delete_sets_null_columns_only(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    assignee = await _make_member(session_factory, workspace)
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        title="owned",
        assignee_id=str(assignee.id),
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM members WHERE id = :id"), {"id": assignee.id}
        )
    async with session_factory() as session:
        row = await session.get(Issue, uuid.UUID(issue["id"]))
        assert row.assignee_id is None  # column-level SET NULL
        assert row.workspace_id == workspace.id  # NOT nulled


@pytest.mark.unit
async def test_t18_project_delete_nulls_project_id_identifier_kept(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="GONE")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="x", project_id=project["id"]
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM project_members WHERE project_id = :pid"),
            {"pid": uuid.UUID(project["id"])},
        )
        await session.execute(
            text("DELETE FROM projects WHERE id = :pid"), {"pid": uuid.UUID(project["id"])}
        )
    async with session_factory() as session:
        row = await session.get(Issue, uuid.UUID(issue["id"]))
        assert row.project_id is None
        assert row.identifier == "GONE-1"


@pytest.mark.unit
async def test_t18_parent_delete_cascades_children(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    parent = await _make_issue(issue_service, actor=owner, workspace=workspace, title="p")
    child = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="c", parent_id=parent["id"]
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM issues WHERE id = :id"), {"id": uuid.UUID(parent["id"])}
        )
    async with session_factory() as session:
        assert await session.get(Issue, uuid.UUID(child["id"])) is None


def _patch(**kwargs):
    from mesh.issue.service import IssuePatch

    return IssuePatch(**kwargs)

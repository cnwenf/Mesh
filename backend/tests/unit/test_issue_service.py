"""Issue service unit tests — real PostgreSQL, nothing mocked (issue.md §2–§5).

Covers numbering (§2.4/T15), two-layer state (§1.2.3/§5.2), status seeding
and unique-default semantics (README §6.3), no-op diff (§6.9), optimistic
concurrency (§3.4/T9), filter limits (§6.14), activity trail (§5.6),
soft delete (§5.1), children (§5.3), templates (§3.9) and the §6.9 assign
trigger emission.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, ConflictError
from mesh.issue.filters import MAX_FILTER_CONDITIONS, MAX_FILTER_DEPTH
from mesh.issue.schemas import (
    CreateIssueRequest,
    CreateIssueTemplateRequest,
    InstantiateIssueTemplateRequest,
)
from mesh.issue.service import IssuePatch, IssueService
from mesh.issue.statuses import StatusService
from mesh.issue.templates import TemplateService
from mesh.outbox.service import emit_event  # noqa: F401  (import guard)
from mesh.project.schemas import CreateProjectRequest
from mesh.project.service import ProjectService


def _is_manager(member: Member) -> bool:
    return member.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def status_service(session_factory) -> StatusService:
    return StatusService(session_factory, is_workspace_manager=_is_manager)


@pytest.fixture
def project_service(session_factory) -> ProjectService:
    return ProjectService(session_factory)


@pytest.fixture
def template_service(issue_service) -> TemplateService:
    return TemplateService(issue_service)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Issue WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="member", agent=False) -> Member:
    async with session_factory() as session, session.begin():
        if agent:
            member = Member(
                workspace_id=workspace.id,
                member_type="agent",
                agent_id=uuid.uuid4(),
                role=role if role != "owner" else "member",
            )
        else:
            from mesh.db.models.user import User

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


async def _default_status(session_factory, workspace) -> IssueStatus:
    async with session_factory() as session:
        return await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.project_id.is_(None),
                IssueStatus.is_default.is_(True),
            )
        )


async def _make_status(
    session_factory,
    workspace,
    *,
    project_id=None,
    name: str,
    category: str = "todo",
    position: float = 0.0,
    is_default: bool = False,
) -> IssueStatus:
    """Insert a status row directly (deterministic positions, no auto-seeding)."""
    async with session_factory() as session, session.begin():
        status = IssueStatus(
            workspace_id=workspace.id,
            project_id=project_id,
            name=name,
            category=category,
            position=position,
            is_default=is_default,
            color="#4c9aff",
        )
        session.add(status)
    return status


async def _make_project_row(session_factory, workspace, *, key: str):
    """Insert a project row directly (bypasses ProjectService status seeding)."""
    from mesh.db.models.project import Project

    async with session_factory() as session, session.begin():
        project = Project(workspace_id=workspace.id, name=f"Project {key}", key=key)
        session.add(project)
    return project


# ---------------------------------------------------------------------------
# numbering (§2.4 / §5.1 / T15)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_create_issue_with_project_takes_project_key_and_seq(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="WEB")

    first = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="first", project_id=project["id"]),
    )
    second = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="second", project_id=project["id"]),
    )
    assert first["identifier"] == "WEB-1"
    assert first["identifier_namespace_key"] == "WEB"
    assert first["number"] == 1
    assert second["identifier"] == "WEB-2"


@pytest.mark.unit
async def test_create_issue_without_project_uses_inbox_prefix(
    session_factory, issue_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="inbox item")
    )
    assert created["identifier"].startswith("WS-")
    assert created["project_id"] is None


@pytest.mark.unit
async def test_inbox_prefix_reads_workspace_settings(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.models.workspace import Workspace

        ws = await session.get(Workspace, workspace.id)
        ws.settings = {**ws.settings, "inbox_issue_prefix": "IN"}
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="custom prefix")
    )
    assert created["identifier"] == "IN-1"


@pytest.mark.unit
async def test_concurrent_creation_same_project_no_duplicates_no_gaps(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="CONC")

    async def _create(index: int):
        return await issue_service.create_issue(
            actor=owner,
            workspace_id=workspace.id,
            body=CreateIssueRequest(title=f"issue {index}", project_id=project["id"]),
        )

    results = await asyncio.gather(*[_create(i) for i in range(12)])
    numbers = sorted(r["number"] for r in results)
    assert numbers == list(range(1, 13))
    identifiers = {r["identifier"] for r in results}
    assert len(identifiers) == 12


@pytest.mark.unit
async def test_concurrent_creation_without_project_no_duplicates(
    session_factory, issue_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")

    async def _create(index: int):
        return await issue_service.create_issue(
            actor=owner,
            workspace_id=workspace.id,
            body=CreateIssueRequest(title=f"inbox {index}"),
        )

    results = await asyncio.gather(*[_create(i) for i in range(10)])
    numbers = sorted(r["number"] for r in results)
    assert numbers == list(range(1, 11))


@pytest.mark.unit
async def test_deleted_issue_number_never_reused(session_factory, issue_service, project_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="REUSE")
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="doomed", project_id=project["id"]),
    )
    await issue_service.delete_issue(
        actor=owner, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"])
    )
    next_issue = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="survivor", project_id=project["id"]),
    )
    assert next_issue["identifier"] == "REUSE-2"
    # tombstone still present with reserved identifier
    async with session_factory() as session:
        tomb = await session.scalar(
            select(Issue).where(Issue.id == uuid.UUID(created["id"]))
        )
        assert tomb is not None and tomb.deleted_at is not None
        assert tomb.identifier == "REUSE-1"


@pytest.mark.unit
async def test_get_by_identifier_and_uuid_return_same_issue(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="ADDR")
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="addressable", project_id=project["id"]),
    )
    by_uuid = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"])
    )
    by_identifier = await issue_service.get_issue_by_identifier(
        viewer=owner, workspace_id=workspace.id, identifier="ADDR-1"
    )
    assert by_uuid["id"] == by_identifier["id"] == created["id"]


# ---------------------------------------------------------------------------
# two-layer state (§1.2.3 / §5.2) + seeding (README §6.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_default_statuses_self_heal_on_first_use(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)  # raw ORM: no seeding ran
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="seeded")
    )
    assert created["state_category"] == "todo"  # Todo is the creation default
    async with session_factory() as session:
        count = (
            (
                await session.execute(
                    select(IssueStatus).where(
                        IssueStatus.workspace_id == workspace.id,
                        IssueStatus.project_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 7
        defaults = [s for s in count if s.is_default]
        assert len(defaults) == 1


@pytest.mark.unit
async def test_state_category_tracks_status_changes(session_factory, issue_service, project_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="flow")
    )
    async with session_factory() as session:
        done_status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "done",
                IssueStatus.project_id.is_(None),
            )
        )
        review_status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "in_review",
                IssueStatus.project_id.is_(None),
            )
        )
    updated = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=done_status.id),
    )
    assert updated["state_category"] == "done"
    assert updated["completed_at"] is not None
    # leaving done clears completed_at
    updated2 = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=review_status.id),
    )
    assert updated2["state_category"] == "in_review"
    assert updated2["completed_at"] is None


@pytest.mark.unit
async def test_status_name_unique_per_scope(session_factory, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="QA", category="in_review"
    )
    with pytest.raises(ConflictError) as exc_info:
        await status_service.create_status(
            actor=owner, workspace_id=workspace.id, name="QA", category="in_progress"
        )
    assert exc_info.value.code == "status_name_taken"


@pytest.mark.unit
async def test_second_default_replaces_first_in_same_transaction(
    session_factory, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Fast Track",
        category="todo",
        is_default=True,
    )
    async with session_factory() as session:
        defaults = (
            (
                await session.execute(
                    select(IssueStatus).where(
                        IssueStatus.workspace_id == workspace.id,
                        IssueStatus.project_id.is_(None),
                        IssueStatus.is_default.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(defaults) == 1
        assert defaults[0].id == uuid.UUID(created["id"])


@pytest.mark.unit
async def test_unset_default_without_replacement_refused(session_factory, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    statuses = await status_service.list_statuses(workspace_id=workspace.id)
    default = next(s for s in statuses if s["is_default"])
    with pytest.raises(BusinessRuleError) as exc_info:
        await status_service.update_status(
            actor=owner,
            workspace_id=workspace.id,
            status_id=uuid.UUID(default["id"]),
            patch=_status_patch(is_default=False),
            is_unset=lambda v: v is None,
        )
    assert exc_info.value.code == "default_status_required"


def _status_patch(**kwargs):
    from mesh.issue.statuses import StatusPatch

    base = {"name": None, "color": None, "position": None, "category": None, "is_default": None}
    base.update(kwargs)
    return StatusPatch(**base)


@pytest.mark.unit
async def test_resolve_default_status_fallback_is_workspace_scoped(session_factory):
    """MES-46 M1: the no-category fallback must resolve the caller's own default.

    Two workspaces each carry a single workspace-level default at a distinct
    position (B sorts first globally). Without the ``workspace_id`` predicate
    the fallback query returns B's default for A — a cross-tenant leak.
    """
    from mesh.issue.statuses import resolve_default_status

    ws_a = await _make_workspace(session_factory)
    ws_b = await _make_workspace(session_factory)
    default_a = await _make_status(
        session_factory, ws_a, name="A Default", position=10.0, is_default=True
    )
    default_b = await _make_status(
        session_factory, ws_b, name="B Default", position=1.0, is_default=True
    )

    async with session_factory() as session:
        resolved_a = await resolve_default_status(session, workspace_id=ws_a.id, project_id=None)
    assert resolved_a.id == default_a.id
    assert resolved_a.workspace_id == ws_a.id

    async with session_factory() as session:
        resolved_b = await resolve_default_status(session, workspace_id=ws_b.id, project_id=None)
    assert resolved_b.id == default_b.id
    assert resolved_b.workspace_id == ws_b.id


@pytest.mark.unit
async def test_resolve_default_status_project_scope_is_workspace_scoped(session_factory):
    """MES-46 M1: the project-scope fallback must not leak another tenant's row.

    A has a workspace-level default (pos 5.0) and a project-private default
    (pos 8.0); B has a workspace-level default at pos 0.5 that matches the
    ``project_id IS NULL`` arm of the filter. Unfiltered, B's row wins the
    ORDER BY for A's resolution.
    """
    from mesh.issue.statuses import resolve_default_status

    ws_a = await _make_workspace(session_factory)
    ws_b = await _make_workspace(session_factory)
    project_a = await _make_project_row(session_factory, ws_a, key=f"K{uuid.uuid4().hex[:4].upper()}")
    default_a_ws = await _make_status(
        session_factory, ws_a, name="A WS Default", position=5.0, is_default=True
    )
    await _make_status(
        session_factory,
        ws_a,
        project_id=project_a.id,
        name="A Proj Default",
        position=8.0,
        is_default=True,
    )
    default_b_ws = await _make_status(
        session_factory, ws_b, name="B WS Default", position=0.5, is_default=True
    )

    async with session_factory() as session:
        resolved = await resolve_default_status(
            session, workspace_id=ws_a.id, project_id=project_a.id
        )
    # Lowest position within A's own rows (5.0) — never B's 0.5.
    assert resolved.id == default_a_ws.id
    assert resolved.workspace_id == ws_a.id
    assert resolved.id != default_b_ws.id


@pytest.mark.unit
async def test_delete_referenced_status_restricted(session_factory, issue_service, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="refd")
    )
    with pytest.raises(ConflictError) as exc_info:
        await status_service.delete_status(
            actor=owner,
            workspace_id=workspace.id,
            status_id=uuid.UUID(created["status_id"]),
        )
    assert exc_info.value.code == "status_in_use"


# ---------------------------------------------------------------------------
# PATCH diff semantics / optimistic concurrency (§3.4 / §6.9 / T9)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_empty_diff_is_noop_no_version_bump(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="noop")
    )
    again = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(title="noop"),  # same value
    )
    assert again["version"] == created["version"] == 1
    async with session_factory() as session:
        trails = (
            (
                await session.execute(
                    select(IssueActivity).where(IssueActivity.issue_id == uuid.UUID(created["id"]))
                )
            )
            .scalars()
            .all()
        )
        assert trails == []


@pytest.mark.unit
async def test_version_conflict_returns_409(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="raced")
    )
    await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(title="raced v2"),
        expected_version=1,
    )
    with pytest.raises(ConflictError) as exc_info:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            patch=IssuePatch(title="stale write"),
            expected_version=1,
        )
    assert exc_info.value.code == "conflict"


@pytest.mark.unit
async def test_activity_trail_records_old_and_new(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="trail", priority="low"),
    )
    await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(priority="urgent"),
    )
    items, _ = await issue_service.list_activity(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"])
    )
    priority_rows = [row for row in items if row["field"] == "priority"]
    assert len(priority_rows) == 1
    assert priority_rows[0]["old_value"] == "low"
    assert priority_rows[0]["new_value"] == "urgent"
    assert priority_rows[0]["actor"]["id"] == str(owner.id)


@pytest.mark.unit
async def test_assignee_must_be_active_member(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    outsider = uuid.uuid4()
    with pytest.raises(BusinessRuleError) as exc_info:
        await issue_service.create_issue(
            actor=owner,
            workspace_id=workspace.id,
            body=CreateIssueRequest(title="bad assignee", assignee_id=str(outsider)),
        )
    assert exc_info.value.code == "assignee_not_member"


@pytest.mark.unit
async def test_guest_cannot_create_projectless_issue(session_factory, issue_service):
    from mesh.errors import ForbiddenError

    workspace = await _make_workspace(session_factory)
    guest = await _make_member(session_factory, workspace, role="guest")
    with pytest.raises(ForbiddenError):
        await issue_service.create_issue(
            actor=guest, workspace_id=workspace.id, body=CreateIssueRequest(title="guest")
        )


# ---------------------------------------------------------------------------
# list: filters, limits, sort, group (§3.2 / §6.14 / §5.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_list_filters_and_search(session_factory, issue_service, project_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="LIST")
    a = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="alpha bug", project_id=project["id"], priority="high"),
    )
    await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="beta feature", project_id=project["id"], priority="low"),
    )
    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, priority="high"
    )
    assert [item["id"] for item in result["data"]] == [a["id"]]
    search = await issue_service.list_issues(viewer=owner, workspace_id=workspace.id, q="LIST-2")
    assert len(search["data"]) == 1
    assert search["data"][0]["identifier"] == "LIST-2"


@pytest.mark.unit
async def test_filter_condition_limit_returns_filter_too_complex(session_factory, issue_service):
    from mesh.issue.filters import FilterTooComplexError

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    too_many = {"and": [{"field": "priority", "op": "eq", "value": "low"}] * (MAX_FILTER_CONDITIONS + 1)}
    with pytest.raises(FilterTooComplexError):
        await issue_service.list_issues(viewer=owner, workspace_id=workspace.id, filters=too_many)


@pytest.mark.unit
async def test_filter_depth_limit(session_factory, issue_service):
    from mesh.issue.filters import FilterTooComplexError

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    deep: dict = {"field": "priority", "op": "eq", "value": "low"}
    for _ in range(MAX_FILTER_DEPTH + 1):
        deep = {"and": [deep]}
    with pytest.raises(FilterTooComplexError):
        await issue_service.list_issues(viewer=owner, workspace_id=workspace.id, filters=deep)


@pytest.mark.unit
async def test_structured_filters_compile(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="s1", priority="high")
    )
    await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="s2", priority="low")
    )
    result = await issue_service.list_issues(
        viewer=owner,
        workspace_id=workspace.id,
        filters={"or": [
            {"field": "priority", "op": "eq", "value": "high"},
            {"field": "title", "op": "eq", "value": "s2"},
        ]},
    )
    assert len(result["data"]) == 2


@pytest.mark.unit
async def test_group_by_state_category_overall_cursor(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    for title in ("g1", "g2"):
        await issue_service.create_issue(
            actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title=title)
        )
    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, group_by="state_category"
    )
    assert "groups" in result and "next_cursor" in result
    assert all("cursor" not in group for group in result["groups"])  # no per-group cursors
    todo_group = next(g for g in result["groups"] if g["key"] == "todo")
    assert todo_group["count"] == 2
    assert len(todo_group["data"]) == 2


@pytest.mark.unit
async def test_priority_sort_semantic_order(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    for priority in ("low", "urgent", "medium"):
        await issue_service.create_issue(
            actor=owner,
            workspace_id=workspace.id,
            body=CreateIssueRequest(title=f"p-{priority}", priority=priority),
        )
    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, sort="priority", order="desc"
    )
    priorities = [item["priority"] for item in result["data"]]
    assert priorities == ["urgent", "medium", "low"]


# ---------------------------------------------------------------------------
# templates (§3.9)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_template_instantiate_uses_same_creation_path(
    session_factory, issue_service, template_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(project_service, actor=owner, workspace=workspace, key="TPL")
    template = await template_service.create_template(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueTemplateRequest(
            name="Bug Report",
            template_body={"priority": "high", "project_id": project["id"], "label_ids": ["x"]},
        ),
    )
    created = await template_service.instantiate(
        actor=owner,
        workspace_id=workspace.id,
        template_id=uuid.UUID(template["id"]),
        body=InstantiateIssueTemplateRequest(title="from template"),
    )
    assert created["identifier"] == "TPL-1"
    assert created["priority"] == "high"
    skipped_fields = {s["field"] for s in created["skipped_fields"]}
    assert "label_ids" in skipped_fields  # label module pending (MES-32)


@pytest.mark.unit
async def test_template_stale_status_degrades(session_factory, issue_service, template_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    template = await template_service.create_template(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueTemplateRequest(
            name="Stale", template_body={"status_id": str(uuid.uuid4())}
        ),
    )
    created = await template_service.instantiate(
        actor=owner,
        workspace_id=workspace.id,
        template_id=uuid.UUID(template["id"]),
        body=InstantiateIssueTemplateRequest(title="still created"),
    )
    assert created["state_category"] == "todo"  # fell back to default
    assert {"field": "status_id", "reason": "reference_stale"} in created["skipped_fields"]


# ---------------------------------------------------------------------------
# assign trigger emission (§6.9 / §3.7)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_assign_to_agent_emits_issue_assigned_outbox(session_factory, issue_service):
    from mesh.db.models.outbox import OutboxEvent

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    agent = await _make_member(session_factory, workspace, agent=True)
    await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="for agent", assignee_id=str(agent.id)),
    )
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == "issue.assigned",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        payload = events[0].payload
        assert payload["trigger"] == "assign"
        assert payload["action"] == "enqueue"
        assert payload["agent_member_id"] == str(agent.id)
        assert events[0].idempotency_key  # §6.5 key present


@pytest.mark.unit
async def test_reassign_agent_supersedes_previous(session_factory, issue_service):
    from mesh.db.models.outbox import OutboxEvent

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    agent_a = await _make_member(session_factory, workspace, agent=True)
    agent_b = await _make_member(session_factory, workspace, agent=True)
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="handoff", assignee_id=str(agent_a.id)),
    )
    await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(assignee_id=agent_b.id),
    )
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == "issue.assigned",
                    )
                )
            )
            .scalars()
            .all()
        )
        actions = sorted((e.payload["action"], e.payload["agent_member_id"]) for e in events)
        assert ("enqueue", str(agent_a.id)) in actions
        assert ("enqueue", str(agent_b.id)) in actions
        assert ("supersede", str(agent_a.id)) in actions


@pytest.mark.unit
async def test_assign_trigger_handler_publishes(session_factory):
    from mesh.db.models.outbox import OutboxEvent
    from mesh.issue.triggers import assign_trigger_handler

    workspace = await _make_workspace(session_factory)
    async with session_factory() as session, session.begin():
        event = OutboxEvent(
            workspace_id=workspace.id,
            event_type="issue.assigned",
            payload={"issue_id": str(uuid.uuid4()), "action": "enqueue"},
        )
        session.add(event)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(OutboxEvent).where(OutboxEvent.workspace_id == workspace.id))
        await assign_trigger_handler(session, row)  # no raise → relay marks published

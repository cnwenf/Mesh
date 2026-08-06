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
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
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
from mesh.runtime.service import RuntimeService
from tests.unit.runtime_support import make_settings


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


async def _make_member(
    session_factory, workspace, *, role="member", agent=False
) -> Member:
    async with session_factory() as session, session.begin():
        if agent:
            from mesh.db.models.agent import Agent
            from mesh.db.models.user import User

            # Agent roster rows reference a real agents row (composite FK);
            # the agent needs a human owner (agents.owner_user_id NOT NULL).
            owner = User(
                email=f"{uuid.uuid4().hex[:12]}@corp.com",
                password_hash="x",
                display_name="Agent Owner",
            )
            session.add(owner)
            await session.flush()
            agent_row = Agent(
                workspace_id=workspace.id, name="Test Agent", owner_user_id=owner.id
            )
            session.add(agent_row)
            await session.flush()
            member = Member(
                workspace_id=workspace.id,
                member_type="agent",
                agent_id=agent_row.id,
                role=role if role != "owner" else "member",
            )
        else:
            from mesh.db.models.user import User

            user = User(
                email=f"{uuid.uuid4().hex[:12]}@corp.com",
                password_hash="x",
                display_name="Tester",
            )
            session.add(user)
            await session.flush()
            member = Member(
                workspace_id=workspace.id,
                member_type="human",
                user_id=user.id,
                role=role,
            )
        session.add(member)
    return member


async def _make_project(project_service, *, actor, workspace, key=None) -> dict:
    return await project_service.create_project(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateProjectRequest(
            name=f"Project {uuid.uuid4().hex[:6]}",
            key=key or f"K{uuid.uuid4().hex[:4].upper()}",
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
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="WEB"
    )

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
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="inbox item"),
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
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="custom prefix"),
    )
    assert created["identifier"] == "IN-1"


@pytest.mark.unit
async def test_concurrent_creation_same_project_no_duplicates_no_gaps(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="CONC"
    )

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
async def test_deleted_issue_number_never_reused(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="REUSE"
    )
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
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="ADDR"
    )
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
async def test_state_category_tracks_status_changes(
    session_factory, issue_service, project_service
):
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
async def test_execution_output_review_only_accepts_latest_completed_execution(
    session_factory, issue_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="review output"),
    )
    async with session_factory() as session:
        review_status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "in_review",
                IssueStatus.project_id.is_(None),
            )
        )
    reviewed = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=review_status.id),
    )
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        old_execution = TaskExecution(
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            trigger="assign",
            status="completed",
            queued_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
        )
        current_execution = TaskExecution(
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            trigger="assign",
            status="completed",
            queued_at=now,
            finished_at=now + timedelta(minutes=1),
        )
        session.add_all([old_execution, current_execution])
        await session.flush()
        old_id, current_id = old_execution.id, current_execution.id

    with pytest.raises(ConflictError) as stale:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            patch=IssuePatch(
                review_execution_id=old_id,
                review_decision="rejected",
            ),
            expected_version=reviewed["version"],
        )
    assert stale.value.code == "stale_execution_output"

    rejected = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(
            review_execution_id=current_id,
            review_decision="rejected",
        ),
        expected_version=reviewed["version"],
    )
    assert rejected["state_category"] == "in_review"
    assert rejected["version"] == reviewed["version"] + 1
    async with session_factory() as session:
        audit = await session.scalar(
            select(IssueActivity).where(
                IssueActivity.issue_id == uuid.UUID(created["id"]),
                IssueActivity.field == "execution_output_review",
            )
        )
    assert audit.new_value == {
        "execution_id": str(current_id),
        "decision": "rejected",
    }
    execution_detail = await RuntimeService(
        session_factory, make_settings()
    ).get_execution(workspace_id=workspace.id, execution_id=current_id)
    assert execution_detail["output_review"] == {
        "decision": "rejected",
        "decided_by_member_id": str(owner.id),
        "decided_at": audit.created_at.isoformat(),
    }

    with pytest.raises(ConflictError) as duplicate:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            patch=IssuePatch(
                review_execution_id=current_id,
                review_decision="rejected",
            ),
            expected_version=rejected["version"],
        )
    assert duplicate.value.code == "execution_output_already_reviewed"


@pytest.mark.unit
async def test_execution_output_approval_binds_execution_and_done_transition(
    session_factory, issue_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    created = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="approve output"),
    )
    async with session_factory() as session:
        statuses = (
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
    by_category = {status.category: status for status in statuses}
    reviewed = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=by_category["in_review"].id),
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        execution = TaskExecution(
            workspace_id=workspace.id,
            issue_id=uuid.UUID(created["id"]),
            trigger="assign",
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add(execution)
        await session.flush()
        execution_id = execution.id

    approved = await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(
            status_id=by_category["done"].id,
            review_execution_id=execution_id,
            review_decision="approved",
        ),
        expected_version=reviewed["version"],
    )
    assert approved["state_category"] == "done"
    assert approved["completed_at"] is not None


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
async def test_unset_default_without_replacement_refused(
    session_factory, status_service
):
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

    base = {
        "name": None,
        "color": None,
        "position": None,
        "category": None,
        "is_default": None,
    }
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
        resolved_a = await resolve_default_status(
            session, workspace_id=ws_a.id, project_id=None
        )
    assert resolved_a.id == default_a.id
    assert resolved_a.workspace_id == ws_a.id

    async with session_factory() as session:
        resolved_b = await resolve_default_status(
            session, workspace_id=ws_b.id, project_id=None
        )
    assert resolved_b.id == default_b.id
    assert resolved_b.workspace_id == ws_b.id


@pytest.mark.unit
async def test_resolve_default_status_project_scope_is_workspace_scoped(
    session_factory,
):
    """MES-46 M1: the project-scope fallback must not leak another tenant's row.

    A has a workspace-level default (pos 5.0) and a project-private default
    (pos 8.0); B has a workspace-level default at pos 0.5 that matches the
    ``project_id IS NULL`` arm of the filter. Unfiltered, B's row wins the
    ORDER BY for A's resolution.
    """
    from mesh.issue.statuses import resolve_default_status

    ws_a = await _make_workspace(session_factory)
    ws_b = await _make_workspace(session_factory)
    project_a = await _make_project_row(
        session_factory, ws_a, key=f"K{uuid.uuid4().hex[:4].upper()}"
    )
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
async def test_delete_referenced_status_restricted(
    session_factory, issue_service, status_service
):
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
                    select(IssueActivity).where(
                        IssueActivity.issue_id == uuid.UUID(created["id"])
                    )
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
            actor=guest,
            workspace_id=workspace.id,
            body=CreateIssueRequest(title="guest"),
        )


# ---------------------------------------------------------------------------
# list: filters, limits, sort, group (§3.2 / §6.14 / §5.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_list_filters_and_search(session_factory, issue_service, project_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="LIST"
    )
    a = await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(
            title="alpha bug", project_id=project["id"], priority="high"
        ),
    )
    await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(
            title="beta feature", project_id=project["id"], priority="low"
        ),
    )
    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, priority="high"
    )
    assert [item["id"] for item in result["data"]] == [a["id"]]
    search = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, q="LIST-2"
    )
    assert len(search["data"]) == 1
    assert search["data"][0]["identifier"] == "LIST-2"


@pytest.mark.unit
async def test_q_search_escapes_like_wildcards(session_factory, issue_service):
    """L5:q 中的 % _ 按字面匹配(LIKE 转义),不再充当通配符。"""
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    for title in ("100%", "100X", "a_b", "axb"):
        await issue_service.create_issue(
            actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title=title)
        )

    percent = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, q="100%"
    )
    assert [item["title"] for item in percent["data"]] == ["100%"]

    underscore = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, q="a_b"
    )
    assert [item["title"] for item in underscore["data"]] == ["a_b"]

    # 旧实现 `_` 通配会命中 "100%"("1_0" ~ "100");转义后零命中
    wildcard = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, q="1_0"
    )
    assert wildcard["data"] == []


@pytest.mark.unit
async def test_filter_condition_limit_returns_filter_too_complex(
    session_factory, issue_service
):
    from mesh.issue.filters import FilterTooComplexError

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    too_many = {
        "and": [{"field": "priority", "op": "eq", "value": "low"}]
        * (MAX_FILTER_CONDITIONS + 1)
    }
    with pytest.raises(FilterTooComplexError):
        await issue_service.list_issues(
            viewer=owner, workspace_id=workspace.id, filters=too_many
        )


@pytest.mark.unit
async def test_flat_and_tree_conditions_share_one_budget(
    session_factory, issue_service
):
    """L6:扁平查询参数与 filters 树条件合计 ≤20(§6.14 单一预算)。"""
    from datetime import date

    from mesh.issue.filters import FilterTooComplexError

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    flat = {
        "status_id": uuid.uuid4(),
        "state_category": "todo",
        "priority": "low",
        "assignee_id": uuid.uuid4(),
        "reporter_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "cycle_id": uuid.uuid4(),
        "milestone_id": uuid.uuid4(),
        "parent_id": uuid.uuid4(),
        "due_before": date(2026, 1, 2),
        "due_after": date(2026, 1, 1),
        "q": "x",
    }  # 扁平 12 条(全部槽位)
    assert len(flat) == 12

    # 12 + 9 = 21 → 400 filter_too_complex(旧实现两者独立计数,各自不超)
    tree_9 = {"and": [{"field": "priority", "op": "eq", "value": "low"}] * 9}
    with pytest.raises(FilterTooComplexError):
        await issue_service.list_issues(
            viewer=owner, workspace_id=workspace.id, filters=tree_9, **flat
        )

    # 12 + 8 = 20 → 通过条件数闸门(随机 UUID 谓词无命中属预期)
    tree_8 = {"and": [{"field": "priority", "op": "eq", "value": "low"}] * 8}
    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, filters=tree_8, **flat
    )
    assert result["data"] == []


@pytest.mark.unit
async def test_filter_depth_limit(session_factory, issue_service):
    from mesh.issue.filters import FilterTooComplexError

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    deep: dict = {"field": "priority", "op": "eq", "value": "low"}
    for _ in range(MAX_FILTER_DEPTH + 1):
        deep = {"and": [deep]}
    with pytest.raises(FilterTooComplexError):
        await issue_service.list_issues(
            viewer=owner, workspace_id=workspace.id, filters=deep
        )


@pytest.mark.unit
async def test_structured_filters_compile(session_factory, issue_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="s1", priority="high"),
    )
    await issue_service.create_issue(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="s2", priority="low"),
    )
    result = await issue_service.list_issues(
        viewer=owner,
        workspace_id=workspace.id,
        filters={
            "or": [
                {"field": "priority", "op": "eq", "value": "high"},
                {"field": "title", "op": "eq", "value": "s2"},
            ]
        },
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
    assert all(
        "cursor" not in group for group in result["groups"]
    )  # no per-group cursors
    todo_group = next(g for g in result["groups"] if g["key"] == "todo")
    assert todo_group["count"] == 2
    assert len(todo_group["data"]) == 2


@pytest.mark.unit
async def test_group_by_label_projects_multi_label_and_none_bucket(
    session_factory, issue_service
):
    """HIGH-A: group_by=label projects each issue into every label group it
    belongs to, counts the full filtered set, and lands unlabelled issues in
    the canonical ``__none__`` bucket ordered last (issue.md §3.2)."""
    from mesh.labels.association import IssueLabelService
    from mesh.labels.service import LabelService

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    labels = LabelService(session_factory)
    label_assoc = IssueLabelService(issue_service)
    bug = await labels.create_label(
        actor=owner, workspace_id=workspace.id, name="bug", color="#e5484d"
    )
    ux = await labels.create_label(
        actor=owner, workspace_id=workspace.id, name="ux", color="#30a46c"
    )

    ids = {}
    for title in ("a", "b", "c"):
        rendered = await issue_service.create_issue(
            actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title=title)
        )
        ids[title] = uuid.UUID(rendered["id"])
    # a carries bug + ux; b carries bug; c carries no label.
    await label_assoc.add_label(
        actor=owner, workspace_id=workspace.id,
        issue_id=ids["a"], label_id=uuid.UUID(bug["id"]),
    )
    await label_assoc.add_label(
        actor=owner, workspace_id=workspace.id,
        issue_id=ids["a"], label_id=uuid.UUID(ux["id"]),
    )
    await label_assoc.add_label(
        actor=owner, workspace_id=workspace.id,
        issue_id=ids["b"], label_id=uuid.UUID(bug["id"]),
    )

    result = await issue_service.list_issues(
        viewer=owner, workspace_id=workspace.id, group_by="label"
    )
    assert "groups" in result and "next_cursor" in result
    assert all("cursor" not in group for group in result["groups"])
    by_key = {group["key"]: group for group in result["groups"]}
    # multi-label: issue a appears in BOTH the bug and ux groups.
    assert by_key[bug["id"]]["count"] == 2
    assert {item["id"] for item in by_key[bug["id"]]["data"]} == {
        str(ids["a"]), str(ids["b"])
    }
    assert by_key[ux["id"]]["count"] == 1
    assert {item["id"] for item in by_key[ux["id"]]["data"]} == {str(ids["a"])}
    # unlabelled issue lands in the canonical __none__ bucket, ordered last.
    assert result["groups"][-1]["key"] == "__none__"
    assert by_key["__none__"]["count"] == 1
    assert {item["id"] for item in by_key["__none__"]["data"]} == {str(ids["c"])}


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
    project = await _make_project(
        project_service, actor=owner, workspace=workspace, key="TPL"
    )
    template = await template_service.create_template(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueTemplateRequest(
            name="Bug Report",
            template_body={
                "priority": "high",
                "project_id": project["id"],
                "label_ids": ["x"],
            },
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
async def test_template_stale_status_degrades(
    session_factory, issue_service, template_service
):
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
    assert {"field": "status_id", "reason": "reference_stale"} in created[
        "skipped_fields"
    ]


# ---------------------------------------------------------------------------
# assign trigger emission (§6.9 / §3.7)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_assign_to_agent_emits_issue_assigned_outbox(
    session_factory, issue_service
):
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
        # The orchestration entry derives the §6.5 enqueue key from this.
        assert payload["trigger_event_id"]
        assert events[0].idempotency_key  # purpose-tagged domain event key


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
        actions = sorted(
            (e.payload["action"], e.payload["agent_member_id"]) for e in events
        )
        assert ("enqueue", str(agent_a.id)) in actions
        assert ("enqueue", str(agent_b.id)) in actions
        assert ("supersede", str(agent_a.id)) in actions


@pytest.mark.unit
async def test_assign_event_key_is_purpose_tagged(session_factory):
    """Domain event keys never collide with the pure §6.5 enqueue key."""
    from mesh.agent.triggers import enqueue_idempotency_key
    from mesh.issue.triggers import assign_event_idempotency_key

    agent_id, issue_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    domain_key = assign_event_idempotency_key(
        agent_key=agent_id, issue_id=issue_id, trigger_event_id=event_id
    )
    enqueue_key = enqueue_idempotency_key(
        agent_id=agent_id, issue_id=issue_id, trigger_event_id=event_id
    )
    assert domain_key != enqueue_key
    # The enqueue key is exactly the README §6.5 formula.
    import hashlib

    assert (
        enqueue_key
        == hashlib.sha256(f"{agent_id}|{issue_id}|{event_id}".encode()).hexdigest()
    )

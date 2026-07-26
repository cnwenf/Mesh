"""M-5 / M-6 (MES-54): status definition changes stay consistent.

M-5: recategorizing a status must propagate the FULL state-machine
contract to every referencing issue — ``state_category`` AND
``completed_at`` maintenance, an OCC ``version`` bump, the
``issue_activity`` trail and the ``issue.updated``/``issue.moved``
events (visibility-aware channels), exactly like a direct per-issue
status change; paged so a hot status stays bounded.

M-6: a scope can never be drained to zero defaults — deleting the last
``is_default`` status of a scope is refused with 409
``last_default_status`` (status_in_use-style conflict), so issue
creation in that scope can never start failing 422.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from mesh.db.models.member import Member
from mesh.errors import ConflictError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import StatusPatch, StatusService
from mesh.project.schemas import CreateProjectRequest
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


def _is_manager(member: Member) -> bool:
    return member.role in ("owner", "admin")


def _unset(value: object) -> bool:
    return value is None


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory) -> ProjectService:
    return ProjectService(session_factory)


@pytest.fixture
def status_service(session_factory) -> StatusService:
    return StatusService(session_factory, is_workspace_manager=_is_manager)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Cat WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="owner") -> Member:
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Cat")
        session.add(user)
        await session.flush()
        member = Member(workspace_id=workspace.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _make_project(project_service, *, actor, workspace, key, visibility="public") -> dict:
    return await project_service.create_project(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateProjectRequest(name=f"P {key}", key=key, visibility=visibility),
    )


async def _make_issue(issue_service, *, actor, workspace, title="t", **kwargs) -> dict:
    return await issue_service.create_issue(
        actor=actor, workspace_id=workspace.id, body=CreateIssueRequest(title=title, **kwargs)
    )


async def _recategorize(status_service, *, actor, workspace, status_id, category) -> dict:
    return await status_service.update_status(
        actor=actor,
        workspace_id=workspace.id,
        status_id=uuid.UUID(status_id),
        patch=StatusPatch(category=category),
        is_unset=_unset,
    )


async def _issue_row(session_factory, issue_id: str) -> dict:
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT state_category, completed_at, version FROM issues WHERE id = :id"),
                {"id": uuid.UUID(issue_id)},
            )
        ).one()
    return {"state_category": row[0], "completed_at": row[1], "version": row[2]}


async def _activity_rows(session_factory, issue_id: str) -> list:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT field, old_value, new_value FROM issue_activity"
                        " WHERE issue_id = :id ORDER BY created_at, id"
                    ),
                    {"id": uuid.UUID(issue_id)},
                )
            ).all()
        )


async def _realtime_events(session_factory, workspace, *, event: str) -> list[dict]:
    """All outbox realtime events of a type: {channel, data}."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload->>'channel' AS channel, payload->'data' AS data"
                    " FROM outbox_events WHERE workspace_id = :ws"
                    " AND payload->>'event' = :event ORDER BY created_at, id"
                ),
                {"ws": workspace.id, "event": event},
            )
        ).all()
    out = []
    for channel, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        out.append({"channel": channel, "data": data})
    return out


# ---------------------------------------------------------------------------
# M-5: category change propagates the full state-machine contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_category_change_to_done_stamps_completed_at_version_trail_events(
    session_factory, issue_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    status = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="QA Gate", category="in_review"
    )
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace, status_id=status["id"])
    before = await _issue_row(session_factory, issue["id"])
    assert before["completed_at"] is None

    await _recategorize(
        status_service,
        actor=owner,
        workspace=workspace,
        status_id=status["id"],
        category="done",
    )

    after = await _issue_row(session_factory, issue["id"])
    # denormalized category + completed_at stamp + OCC version bump
    assert after["state_category"] == "done"
    assert after["completed_at"] is not None
    assert after["version"] == before["version"] + 1
    # activity trail row (same shape as a direct status change)
    trail = {(r[0], r[1], r[2]) for r in await _activity_rows(session_factory, issue["id"])}
    assert ("state_category", "in_review", "done") in trail
    # events: issue.updated + issue.moved, inbox issue → both channels
    updated = await _realtime_events(session_factory, workspace, event="issue.updated")
    moved = await _realtime_events(session_factory, workspace, event="issue.moved")
    mine_updated = [e for e in updated if e["data"]["id"] == issue["id"]]
    assert {e["channel"] for e in mine_updated} == {
        f"issue:{issue['id']}",
        f"workspace:{workspace.id}:issues",
    }
    payload = mine_updated[0]["data"]
    assert payload["changes"] == {"state_category": "done"}
    assert payload["version"] == after["version"]
    mine_moved = [e for e in moved if e["data"]["id"] == issue["id"]]
    assert mine_moved[0]["data"]["from"] == {"state_category": "in_review"}
    assert mine_moved[0]["data"]["to"] == {"state_category": "done"}


@pytest.mark.unit
async def test_category_change_from_done_clears_completed_at(session_factory, issue_service, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    status = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Shipped", category="done"
    )
    # creation on a done-category status stamps completed_at
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace, status_id=status["id"])
    before = await _issue_row(session_factory, issue["id"])
    assert before["completed_at"] is not None

    await _recategorize(
        status_service,
        actor=owner,
        workspace=workspace,
        status_id=status["id"],
        category="in_progress",
    )

    after = await _issue_row(session_factory, issue["id"])
    assert after["state_category"] == "in_progress"
    assert after["completed_at"] is None
    assert after["version"] == before["version"] + 1


@pytest.mark.unit
async def test_category_change_private_project_stays_off_workspace_channel(
    session_factory, issue_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    private = await _make_project(
        project_service, actor=owner, workspace=workspace, key="CATP", visibility="private"
    )
    status = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Secret Gate", category="todo"
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=private["id"],
        status_id=status["id"],
    )
    await _recategorize(
        status_service,
        actor=owner,
        workspace=workspace,
        status_id=status["id"],
        category="in_progress",
    )

    updated = await _realtime_events(session_factory, workspace, event="issue.updated")
    mine = [e for e in updated if e["data"]["id"] == issue["id"]]
    # private-project issue: detail channel ONLY (issue-service parity)
    assert [e["channel"] for e in mine] == [f"issue:{issue['id']}"]
    after = await _issue_row(session_factory, issue["id"])
    assert after["state_category"] == "in_progress"
    assert after["version"] == 2


@pytest.mark.unit
async def test_category_change_resyncs_all_issues_across_pages(
    session_factory, issue_service, status_service
):
    from mesh.issue.statuses import CATEGORY_RESCAN_BATCH_SIZE

    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    status = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Bulk Gate", category="todo"
    )
    base = await _make_issue(issue_service, actor=owner, workspace=workspace, status_id=status["id"])
    extra = CATEGORY_RESCAN_BATCH_SIZE + 100  # forces at least two pages
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issues (workspace_id, project_id,"
                " identifier_namespace_key, number, identifier, title, status_id,"
                " state_category, priority, position, version, created_at, updated_at)"
                " SELECT i.workspace_id, i.project_id, i.identifier_namespace_key,"
                " 9000 + g.g, i.identifier_namespace_key || '-' || (9000 + g.g),"
                " 'bulk-' || g.g, i.status_id, i.state_category, 'none',"
                " i.position + g.g, 1, now(), now()"
                " FROM issues i, generate_series(1, :extra) g"
                " WHERE i.id = :id"
            ),
            {"extra": extra, "id": uuid.UUID(base["id"])},
        )
    total = extra + 1

    await _recategorize(
        status_service,
        actor=owner,
        workspace=workspace,
        status_id=status["id"],
        category="done",
    )

    async with session_factory() as session:
        synced = (
            await session.execute(
                text(
                    "SELECT count(*) FROM issues WHERE status_id = :sid"
                    " AND state_category = 'done' AND completed_at IS NOT NULL"
                    " AND version = 2"
                ),
                {"sid": uuid.UUID(status["id"])},
            )
        ).scalar_one()
        trail_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM issue_activity ia JOIN issues i"
                    " ON i.id = ia.issue_id"
                    " WHERE i.status_id = :sid AND ia.field = 'state_category'"
                    " AND ia.old_value = '\"todo\"' AND ia.new_value = '\"done\"'"
                ),
                {"sid": uuid.UUID(status["id"])},
            )
        ).scalar_one()
    assert synced == total
    assert trail_count == total


# ---------------------------------------------------------------------------
# M-6: the last default status of a scope cannot be deleted
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_delete_last_workspace_default_rejected(session_factory, issue_service, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    # seed the workspace scope and find its (single) default status
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace)
    default_status_id = issue["status_id"]
    # move the issue off the default so the RESTRICT guard cannot fire first
    other = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Side", category="todo"
    )
    from mesh.issue.service import IssuePatch

    await issue_service.update_issue(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(status_id=uuid.UUID(other["id"])),
    )
    with pytest.raises(ConflictError) as exc_info:
        await status_service.delete_status(
            actor=owner,
            workspace_id=workspace.id,
            status_id=uuid.UUID(default_status_id),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "last_default_status"
    assert exc_info.value.details == {"status_id": default_status_id}


@pytest.mark.unit
async def test_delete_last_project_default_rejected(session_factory, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    from mesh.project.service import ProjectService

    project_service = ProjectService(session_factory)
    project = await project_service.create_project(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateProjectRequest(name="Scope", key="M6P", visibility="public"),
    )
    default = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Proj Default",
        category="todo",
        is_default=True,
        project_id=uuid.UUID(project["id"]),
    )
    with pytest.raises(ConflictError) as exc_info:
        await status_service.delete_status(
            actor=owner,
            workspace_id=workspace.id,
            status_id=uuid.UUID(default["id"]),
        )
    assert exc_info.value.code == "last_default_status"


@pytest.mark.unit
async def test_delete_non_default_status_still_allowed(session_factory, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    extra = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Extra", category="backlog"
    )
    result = await status_service.delete_status(
        actor=owner, workspace_id=workspace.id, status_id=uuid.UUID(extra["id"])
    )
    assert result == {"id": extra["id"], "deleted": True}


@pytest.mark.unit
async def test_delete_default_after_handoff_allowed(session_factory, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    old_default = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Old Default",
        category="todo",
        is_default=True,
    )
    # hand the default off in-transaction (create with is_default=True)
    await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="New Default",
        category="todo",
        is_default=True,
    )
    # no longer the last default → deletable
    result = await status_service.delete_status(
        actor=owner, workspace_id=workspace.id, status_id=uuid.UUID(old_default["id"])
    )
    assert result["deleted"] is True


@pytest.mark.unit
async def test_delete_referenced_default_reports_status_in_use_first(
    session_factory, issue_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace)
    # referenced AND the last default: the in-use error wins (existing
    # contract preserved — M-6 guard must not shadow it)
    with pytest.raises(ConflictError) as exc_info:
        await status_service.delete_status(
            actor=owner,
            workspace_id=workspace.id,
            status_id=uuid.UUID(issue["status_id"]),
        )
    assert exc_info.value.code == "status_in_use"

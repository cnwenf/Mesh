"""M-7 (MES-54) + M-3 entering-done parity.

M-7: a private→public move broadcasts ``issue.project_changed`` on
channels every workspace member can read (the workspace list channel AND
the issue channel — the card just became public, so any member may join
it). The plan's source-side copies carry private-project readable
metadata (status names, milestone titles); both broadcast copies are
redacted to structural markers (field/reason/category/from_project_id),
the full manifest staying in the permission-gated activity trail.
Public sources keep the full payload (nothing to protect).
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from mesh.db.models.member import Member
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService, redact_move_payload
from mesh.issue.schemas import BulkChanges, BulkRequest, CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import StatusService
from mesh.project.schemas import CreateMilestoneRequest, CreateProjectRequest
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


def _is_manager(member: Member) -> bool:
    return member.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory) -> ProjectService:
    return ProjectService(session_factory)


@pytest.fixture
def status_service(session_factory) -> StatusService:
    return StatusService(session_factory, is_workspace_manager=_is_manager)


@pytest.fixture
def move_service(issue_service) -> MoveService:
    return MoveService(issue_service)


@pytest.fixture
def bulk_service(issue_service, move_service) -> BulkService:
    return BulkService(issue_service, move_service)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Redact WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="owner") -> Member:
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Red")
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


async def _project_changed_events(session_factory, workspace) -> list[dict]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload->>'channel' AS channel, payload->'data' AS data"
                    " FROM outbox_events WHERE workspace_id = :ws"
                    " AND payload->>'event' = 'issue.project_changed'"
                    " ORDER BY created_at, id"
                ),
                {"ws": workspace.id},
            )
        ).all()
    out = []
    for channel, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        out.append({"channel": channel, "data": data})
    return out


def _assert_no_private_source_leak(data: dict, *, private_status_name: str, milestone_title: str):
    blob = json.dumps(data, ensure_ascii=False)
    assert private_status_name not in blob
    assert milestone_title not in blob
    # no readable source-side copies survive: mapped `from` is a category
    # marker ONLY, cleared entries carry no items
    for mapped in data["mapped_fields"]:
        if "from" in mapped:
            assert set(mapped["from"].keys()) == {"category"}
    for cleared in data["cleared_fields"]:
        assert "items" not in cleared


# ---------------------------------------------------------------------------
# pure redactor
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_redact_move_payload_keeps_markers_drops_readable_source() -> None:
    payload = {
        "id": "iss-1",
        "from_project_id": "prj-private",
        "to_project_id": "prj-public",
        "version": 4,
        "mapped_fields": [
            {
                "field": "status",
                "from": {"id": "st-1", "name": "Secret Dev", "category": "in_progress"},
                "to": {"id": "st-2", "name": "In Progress", "category": "in_progress"},
                "reason": "项目私有 status → 目标项目同 category 默认 status",
            }
        ],
        "cleared_fields": [
            {
                "field": "milestone_id",
                "items": [{"id": "ms-1", "title": "Secret Milestone"}],
                "reason": "项目私有里程碑",
            }
        ],
    }
    redacted = redact_move_payload(payload)
    # structural envelope untouched
    assert redacted["from_project_id"] == "prj-private"
    assert redacted["to_project_id"] == "prj-public"
    assert redacted["version"] == 4
    mapped = redacted["mapped_fields"][0]
    assert mapped["from"] == {"category": "in_progress"}  # category marker only
    assert mapped["to"]["name"] == "In Progress"  # public target stays readable
    assert mapped["reason"]  # canonical reason string preserved
    cleared = redacted["cleared_fields"][0]
    assert cleared == {"field": "milestone_id", "reason": "项目私有里程碑"}
    # the input payload is NOT mutated (immutability — callers keep the full copy)
    assert payload["mapped_fields"][0]["from"]["name"] == "Secret Dev"


# ---------------------------------------------------------------------------
# explicit move
# ---------------------------------------------------------------------------


async def _private_to_public_setup(session_factory, issue_service, project_service, status_service):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    source = await _make_project(
        project_service, actor=owner, workspace=workspace, key="RDS", visibility="private"
    )
    target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="RDT", visibility="public"
    )
    private_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Secret Dev",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=workspace.id,
        project_id=uuid.UUID(source["id"]),
        body=CreateMilestoneRequest(title="Secret Milestone"),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=private_status["id"],
        milestone_id=milestone["id"],
    )
    return workspace, owner, source, target, issue


@pytest.mark.unit
async def test_private_to_public_move_redacts_both_broadcast_copies(
    session_factory, issue_service, move_service, project_service, status_service
):
    workspace, owner, source, target, issue = await _private_to_public_setup(
        session_factory, issue_service, project_service, status_service
    )
    await move_service.move(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
        confirm=True,
        expected_version=issue["version"],
    )
    events = await _project_changed_events(session_factory, workspace)
    # issue channel + workspace channel — BOTH must be redacted
    assert {e["channel"] for e in events} == {
        f"issue:{issue['id']}",
        f"workspace:{workspace.id}:issues",
    }
    for event in events:
        _assert_no_private_source_leak(
            event["data"],
            private_status_name="Secret Dev",
            milestone_title="Secret Milestone",
        )
        assert event["data"]["from_project_id"] == source["id"]
    # the permission-gated trail still carries the FULL manifest
    async with session_factory() as session:
        trail = (
            await session.execute(
                text("SELECT field, old_value, new_value FROM issue_activity WHERE issue_id = :id"),
                {"id": uuid.UUID(issue["id"])},
            )
        ).all()
    assert ("project_id", source["id"], target["id"]) in trail


@pytest.mark.unit
async def test_public_to_public_move_keeps_full_payload(
    session_factory, issue_service, move_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    source = await _make_project(
        project_service, actor=owner, workspace=workspace, key="PBS", visibility="public"
    )
    target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="PBT", visibility="public"
    )
    open_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Open Dev",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=open_status["id"],
    )
    await move_service.move(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
        confirm=True,
        expected_version=issue["version"],
    )
    events = await _project_changed_events(session_factory, workspace)
    assert events
    for event in events:
        blob = json.dumps(event["data"], ensure_ascii=False)
        # a public source has no secrets — the manifest stays complete
        assert "Open Dev" in blob
        assert event["data"]["mapped_fields"][0]["from"]["name"] == "Open Dev"


# ---------------------------------------------------------------------------
# bulk move parity
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bulk_private_to_public_move_redacts_broadcast(
    session_factory, issue_service, bulk_service, project_service, status_service
):
    workspace, owner, source, target, issue = await _private_to_public_setup(
        session_factory, issue_service, project_service, status_service
    )
    result = await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(
            issue_ids=[issue["id"]],
            changes=BulkChanges(project_id=target["id"]),
            confirm=True,
        ),
    )
    assert result["succeeded"] == 1
    events = await _project_changed_events(session_factory, workspace)
    assert events
    for event in events:
        _assert_no_private_source_leak(
            event["data"],
            private_status_name="Secret Dev",
            milestone_title="Secret Milestone",
        )


# ---------------------------------------------------------------------------
# M-3 entering-done direction (parity guard for the MES-48 completed_at sync)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bulk_move_mapping_into_done_stamps_completed_at(
    session_factory, issue_service, bulk_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    source = await _make_project(
        project_service, actor=owner, workspace=workspace, key="EDS", visibility="public"
    )
    target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="EDT", visibility="public"
    )
    # a project-private status in the DONE category: moving out maps it to
    # the workspace-level done default — entering the mapped done status
    # must stamp completed_at exactly like the explicit move does
    private_done = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="Local Done",
        category="done",
        project_id=uuid.UUID(source["id"]),
    )
    todo_status = await status_service.create_status(
        actor=owner, workspace_id=workspace.id, name="Plain Todo", category="todo"
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=todo_status["id"],
    )
    # force the issue onto the private done status WITHOUT a completed_at
    # stamp (stale denormalization — the bulk mapping must repair it)
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE issues SET status_id = :sid, state_category = 'done',"
                " completed_at = NULL WHERE id = :id"
            ),
            {"sid": uuid.UUID(private_done["id"]), "id": uuid.UUID(issue["id"])},
        )

    await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(
            issue_ids=[issue["id"]],
            changes=BulkChanges(project_id=target["id"]),
            confirm=True,
        ),
    )
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(issue["id"])
    )
    # mapped to the workspace done default → stamped, not left stale-NULL
    assert got["state_category"] == "done"
    assert got["completed_at"] is not None

"""Cross-project move × label-property association tests (issue.md §3.8).

A move out of a project clears the associations that cannot apply outside
it: project-private labels and values of project-scoped field definitions.
Workspace-level labels / values are KEPT. The preview plan reports them in
``cleared_fields``; apply deletes the rows and broadcasts the convergence
events (issue.labels_changed with the survivors, issue.custom_field_changed
with null values).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.issue import Issue
from mesh.db.models.label import IssueCustomFieldValue, IssueLabel, Label
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService
from mesh.issue.schemas import BulkChanges, BulkRequest, CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.labels.association import FieldValueService, IssueLabelService
from mesh.labels.service import LabelService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _setup(session_factory):
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Admin", password_hash="x", status="active",
        )
        session.add(user)
    async with session_factory() as session, session.begin():
        admin = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id,
            role="admin", status="active", joined_at=FIXED_NOW,
        )
        session.add(admin)
    async with session_factory() as session, session.begin():
        project_a = Project(
            workspace_id=workspace.id, name="A", key="PJA", visibility="public"
        )
        project_b = Project(
            workspace_id=workspace.id, name="B", key="PJB", visibility="public"
        )
        session.add_all([project_a, project_b])

    issues = IssueService(session_factory, clock=_clock)
    labels = LabelService(session_factory, clock=_clock)
    return workspace, admin, project_a, project_b, issues, labels


async def _build_moved_issue(session_factory):
    workspace, admin, project_a, project_b, issues, labels = await _setup(
        session_factory
    )
    issue_labels = IssueLabelService(issues, clock=_clock)
    field_values = FieldValueService(issues, clock=_clock)
    moves = MoveService(issues)

    ws_label = await labels.create_label(
        actor=admin, workspace_id=workspace.id, name="ws-tag", color="#111111"
    )
    private_label = await labels.create_label(
        actor=admin, workspace_id=workspace.id, name="a-only", color="#222222",
        project_id=project_a.id,
    )
    ws_field = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="WS notes", field_key="ws_notes", field_type="text",
    )
    a_field = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="A notes", field_key="a_notes", field_type="text",
        project_id=project_a.id,
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id,
        body=CreateIssueRequest(title="moving", project_id=str(project_a.id)),
    )
    issue_id = uuid.UUID(created["id"])
    for label_id in (uuid.UUID(ws_label["id"]), uuid.UUID(private_label["id"])):
        await issue_labels.add_label(
            actor=admin, workspace_id=workspace.id, issue_id=issue_id,
            label_id=label_id,
        )
    await field_values.set_values(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        values=[
            {"field_def_id": ws_field["id"], "value_text": "kept"},
            {"field_def_id": a_field["id"], "value_text": "dropped"},
        ],
    )
    return {
        "workspace": workspace, "admin": admin, "project_a": project_a,
        "project_b": project_b, "issues": issues, "moves": moves,
        "issue_id": issue_id,
        "ws_label": uuid.UUID(ws_label["id"]),
        "private_label": uuid.UUID(private_label["id"]),
        "ws_field": ws_field, "a_field": a_field,
    }


async def test_preview_reports_private_associations_as_cleared(session_factory):
    env = await _build_moved_issue(session_factory)
    preview_plan = await env["moves"].preview(
        viewer=env["admin"], workspace_id=env["workspace"].id,
        issue_id=env["issue_id"], target_project_id=env["project_b"].id,
    )
    cleared = {c["field"]: c for c in preview_plan["cleared_fields"]}
    assert [i["id"] for i in cleared["labels"]["items"]] == [str(env["private_label"])]
    assert [i["field_key"] for i in cleared["custom_field_values"]["items"]] == ["a_notes"]
    # Workspace-level associations stay listed as kept.
    assert "工作区级 labels" in preview_plan["kept_fields"]
    assert "工作区级自定义字段值" in preview_plan["kept_fields"]
    # The interim skip markers are gone now that the module landed.
    assert "skipped_modules" not in preview_plan


async def _payloads(session_factory):
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "realtime.publish"
                    )
                )
            ).scalars().all()
        )


async def test_move_clears_private_associations_and_broadcasts(session_factory):
    env = await _build_moved_issue(session_factory)
    issue_id = env["issue_id"]
    # §3.8 两步式:确认必须回带预览发出的 version(乐观锁,MES-48 契约)。
    preview = await env["moves"].preview(
        viewer=env["admin"], workspace_id=env["workspace"].id,
        issue_id=issue_id, target_project_id=env["project_b"].id,
    )
    await env["moves"].move(
        actor=env["admin"], workspace_id=env["workspace"].id,
        issue_id=issue_id, target_project_id=env["project_b"].id, confirm=True,
        expected_version=preview["version"],
    )

    # DB: only the workspace-level label + value survive.
    async with session_factory() as session:
        remaining_labels = (
            (
                await session.execute(
                    select(IssueLabel.label_id).where(IssueLabel.issue_id == issue_id)
                )
            ).scalars().all()
        )
        remaining_values = (
            (
                await session.execute(
                    select(
                        IssueCustomFieldValue.field_def_id,
                        IssueCustomFieldValue.value_text,
                    ).where(IssueCustomFieldValue.issue_id == issue_id)
                )
            ).all()
        )
    assert set(remaining_labels) == {env["ws_label"]}
    assert remaining_values == [(uuid.UUID(env["ws_field"]["id"]), "kept")]
    # The private label DEFINITION still exists (only the link is cleared).
    async with session_factory() as session:
        assert await session.scalar(
            select(Label).where(Label.id == env["private_label"])
        ) is not None

    # Events from the move itself — discriminated by CONTENT, not position:
    # post-move frames carry the survivors (ws_label only), setup frames
    # still carry the private label.
    payloads = await _payloads(session_factory)
    label_events = [
        p for p in payloads if p.get("event") == "issue.labels_changed"
        and p["data"]["issue_id"] == str(issue_id)
        and str(env["private_label"]) not in [x["id"] for x in p["data"]["labels"]]
    ]
    # Emitted on detail + workspace channels (target is public).
    assert {e["channel"].split(":")[0] for e in label_events} == {"issue", "workspace"}
    for event in label_events:
        assert [label["id"] for label in event["data"]["labels"]] == [str(env["ws_label"])]
    # Post-move value frames carry null; setup frames carry real values.
    value_events = [
        p for p in payloads if p.get("event") == "issue.custom_field_changed"
        and p["data"]["issue_id"] == str(issue_id)
        and p["data"]["value"] is None
    ]
    assert {e["data"]["field_key"] for e in value_events} == {"a_notes"}


async def test_move_to_workspace_level_clears_project_associations(session_factory):
    env = await _build_moved_issue(session_factory)
    preview = await env["moves"].preview(
        viewer=env["admin"], workspace_id=env["workspace"].id,
        issue_id=env["issue_id"], target_project_id=None,
    )
    await env["moves"].move(
        actor=env["admin"], workspace_id=env["workspace"].id,
        issue_id=env["issue_id"], target_project_id=None, confirm=True,
        expected_version=preview["version"],
    )
    async with session_factory() as session:
        remaining_labels = (
            (
                await session.execute(
                    select(IssueLabel.label_id).where(
                        IssueLabel.issue_id == env["issue_id"]
                    )
                )
            ).scalars().all()
        )
    assert set(remaining_labels) == {env["ws_label"]}


async def test_bulk_move_clears_private_associations_keeps_workspace_level(
    session_factory,
):
    """L3 parity: bulk-move applies the same association clearing as the
    explicit move — private label/value rows deleted, workspace-level kept,
    convergence events emitted, issue lands in the target project."""
    env = await _build_moved_issue(session_factory)
    bulk = BulkService(env["issues"], env["moves"])

    result = await bulk.execute(
        actor=env["admin"],
        workspace_id=env["workspace"].id,
        body=BulkRequest(
            issue_ids=[str(env["issue_id"])],
            changes=BulkChanges(project_id=str(env["project_b"].id)),
            confirm=True,
        ),
    )
    assert result["succeeded"] == 1 and result["failed"] == 0

    # Issue moved; private associations cleared, workspace-level kept.
    async with session_factory() as session:
        moved = await session.scalar(
            select(Issue).where(Issue.id == env["issue_id"])
        )
        remaining_labels = set(
            (
                await session.execute(
                    select(IssueLabel.label_id).where(
                        IssueLabel.issue_id == env["issue_id"]
                    )
                )
            ).scalars().all()
        )
        remaining_values = {
            row.field_def_id: row.value_text
            for row in (
                await session.execute(
                    select(IssueCustomFieldValue).where(
                        IssueCustomFieldValue.issue_id == env["issue_id"]
                    )
                )
            ).scalars().all()
        }
    assert moved.project_id == env["project_b"].id
    assert remaining_labels == {env["ws_label"]}
    assert set(remaining_values) == {uuid.UUID(env["ws_field"]["id"])}
    assert remaining_values[uuid.UUID(env["ws_field"]["id"])] == "kept"

    # Convergence events — content-discriminated (setup frames carry the
    # private label / real values; post-move frames carry survivors / nulls).
    events = await _payloads(session_factory)
    label_events = [e for e in events if e.get("event") == "issue.labels_changed"]
    value_events = [
        e for e in events if e.get("event") == "issue.custom_field_changed"
    ]
    assert any(
        e["data"]["issue_id"] == str(env["issue_id"])
        and [x["id"] for x in e["data"]["labels"]] == [str(env["ws_label"])]
        for e in label_events
    )
    assert any(
        e["data"]["issue_id"] == str(env["issue_id"])
        and e["data"]["field_key"] == "a_notes"
        and e["data"]["value"] is None
        for e in value_events
    )

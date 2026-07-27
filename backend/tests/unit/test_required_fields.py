"""Required custom-field validation hook tests (label-property.md §4.5).

The hook fires on the issue module's two mandated occasions — save (any
non-empty PATCH) and status-category transition — evaluated through
IssueService.update_issue against the real PostgreSQL schema. Covers the
``required_on`` grammar (empty = save, explicit save / status:<category>),
scope applicability, inactive-field exemption, the 422
``required_field_missing`` envelope with the missing-field details, and the
documented create-exemption (fields are filled post-create on the detail
page; blocking creation would make required fields unfillable).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.issue import IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssuePatch, IssueService
from mesh.labels.association import FieldValueService
from mesh.labels.service import FieldDefPatch, LabelService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _setup(session_factory, *, required_on):
    """Workspace + admin + one required field with the given required_on."""
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
    labels = LabelService(session_factory, clock=_clock)
    issues = IssueService(session_factory, clock=_clock)
    values = FieldValueService(issues, clock=_clock)
    field = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Acceptor", field_key="acceptor", field_type="text",
        is_required=True, required_on=required_on,
    )
    return workspace, admin, issues, values, field


async def _status_id(session_factory, workspace, category: str) -> uuid.UUID:
    async with session_factory() as session:
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == category,
                IssueStatus.project_id.is_(None),
            )
        )
    return status.id


async def _fill_required(values, admin, workspace, issue_id, field) -> None:
    await values.set_values(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        values=[{"field_def_id": field["id"], "value_text": "filled"}],
    )


# ---------------------------------------------------------------------------
# status:<category> occasion
# ---------------------------------------------------------------------------


async def test_transition_to_done_blocked_until_required_filled(session_factory):
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["status:done"]
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    issue_id = uuid.UUID(created["id"])
    done_id = await _status_id(session_factory, workspace, "done")

    # Unrelated PATCH passes — the gate is on the done transition only.
    await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        patch=IssuePatch(title="renamed"),
    )

    # Transition → done without the value: 422 with the missing list.
    with pytest.raises(BusinessRuleError) as excinfo:
        await issues.update_issue(
            actor=admin, workspace_id=workspace.id, issue_id=issue_id,
            patch=IssuePatch(status_id=done_id),
        )
    assert excinfo.value.code == "required_field_missing"
    missing = excinfo.value.details["missing"]
    assert missing == [{"field_def_id": field["id"], "name": "Acceptor"}]

    # Fill it, then the transition succeeds.
    await _fill_required(values, admin, workspace, issue_id, field)
    updated = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        patch=IssuePatch(status_id=done_id),
    )
    assert updated["state_category"] == "done"


async def test_transition_to_other_category_not_blocked(session_factory):
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["status:done"]
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    issue_id = uuid.UUID(created["id"])
    in_progress_id = await _status_id(session_factory, workspace, "in_progress")
    updated = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        patch=IssuePatch(status_id=in_progress_id),
    )
    assert updated["state_category"] == "in_progress"
    _ = (values, field)  # gate does not fire for non-done categories


# ---------------------------------------------------------------------------
# save occasion (explicit and empty-required_on default)
# ---------------------------------------------------------------------------


async def test_save_gate_blocks_any_patch_when_required_missing(session_factory):
    for required_on in ([], ["save"]):  # empty means "save" (§2.4)
        workspace, admin, issues, values, field = await _setup(
            session_factory, required_on=required_on
        )
        created = await issues.create_issue(
            actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
        )
        issue_id = uuid.UUID(created["id"])
        with pytest.raises(BusinessRuleError) as excinfo:
            await issues.update_issue(
                actor=admin, workspace_id=workspace.id, issue_id=issue_id,
                patch=IssuePatch(title="renamed"),
            )
        assert excinfo.value.code == "required_field_missing"
        await _fill_required(values, admin, workspace, issue_id, field)
        updated = await issues.update_issue(
            actor=admin, workspace_id=workspace.id, issue_id=issue_id,
            patch=IssuePatch(title="renamed"),
        )
        assert updated["title"] == "renamed"


async def test_empty_diff_patch_not_blocked(session_factory):
    """§6.9: an empty PATCH is a no-op — no save, no gate."""
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["save"]
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    issue_id = uuid.UUID(created["id"])
    result = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=issue_id,
        patch=IssuePatch(),  # nothing set → UNSET everywhere
    )
    assert result["title"] == "t"
    _ = (values, field)


async def test_create_is_exempt_from_required_gate(session_factory):
    """Creating with a missing required field succeeds — values are filled
    post-create on the detail page; blocking create would make the field
    unfillable (documented interpretation of §4.5 'save')."""
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["save"]
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    assert created["id"]
    _ = (values, field)


# ---------------------------------------------------------------------------
# scope / activity applicability
# ---------------------------------------------------------------------------


async def test_inactive_required_field_not_enforced(session_factory):
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["save"]
    )
    labels = LabelService(session_factory, clock=_clock)
    await labels.update_field_def(
        actor=admin, workspace_id=workspace.id,
        field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(is_active=False),
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    updated = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(title="renamed"),
    )
    assert updated["title"] == "renamed"
    _ = values


async def test_optional_field_not_enforced(session_factory):
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["save"]
    )
    labels = LabelService(session_factory, clock=_clock)
    await labels.update_field_def(
        actor=admin, workspace_id=workspace.id,
        field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(is_required=False),
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    updated = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(title="renamed"),
    )
    assert updated["title"] == "renamed"
    _ = values


async def test_non_required_field_never_gates(session_factory):
    """A field with is_required=false is invisible to the hook entirely."""
    workspace, admin, issues, values, field = await _setup(
        session_factory, required_on=["status:done"]
    )
    labels = LabelService(session_factory, clock=_clock)
    await labels.update_field_def(
        actor=admin, workspace_id=workspace.id,
        field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(is_required=False),
    )
    created = await issues.create_issue(
        actor=admin, workspace_id=workspace.id, body=CreateIssueRequest(title="t")
    )
    done_id = await _status_id(session_factory, workspace, "done")
    updated = await issues.update_issue(
        actor=admin, workspace_id=workspace.id, issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=done_id),
    )
    assert updated["state_category"] == "done"
    _ = values

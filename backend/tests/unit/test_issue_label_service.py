"""Issue ↔ label association service tests — direct calls, real PostgreSQL.

Covers label-property.md §2.3 / §3.1 / §3.5 (association layer): list / add /
remove / whole-set replace with the §6.14 envelope shape, project-scope
enforcement (422 label_scope_mismatch), idempotent no-op semantics (§6.9 —
no event without a change), If-Match optimistic concurrency, label merge
(§3.2/§4.4), outbox event emission on the issue channels (detail always,
workspace list channel only for workspace-level / public-project issues),
audit trail, and issue-level authorization reuse.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.label import IssueCustomFieldValue, IssueLabel, Label
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssuePatch, IssueService
from mesh.labels.association import IssueLabelService
from mesh.labels.required_fields import _required_value_is_present
from mesh.labels.service import LabelService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _add_user(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Tester",
            password_hash="x",
            status="active",
        )
        session.add(user)
    return user.id


class Ctx:
    """Shared test context: workspace, admin member, composed services."""

    def __init__(self, session_factory, workspace, admin, issues, labels):
        self.sf = session_factory
        self.workspace = workspace
        self.admin = admin
        self.issues = issues
        self.labels = labels
        self.label_service = LabelService(session_factory, clock=_clock)
        self.association = IssueLabelService(issues, clock=_clock)


async def _setup(session_factory) -> Ctx:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        admin = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role="admin",
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(admin)
    issue_service = IssueService(session_factory, clock=_clock)
    label_service = LabelService(session_factory, clock=_clock)
    # Two workspace-level labels + issue in the workspace-level inbox.
    bug = await label_service.create_label(
        actor=admin, workspace_id=workspace.id, name="bug", color="#e5484d"
    )
    feat = await label_service.create_label(
        actor=admin, workspace_id=workspace.id, name="feature", color="#30a46c"
    )
    issue = await issue_service.create_issue(
        actor=admin,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="inbox issue"),
    )
    return Ctx(
        session_factory,
        workspace,
        admin,
        issue_service,
        {"bug": bug, "feature": feat, "issue": issue},
    )


async def _make_project(session_factory, workspace, *, key: str, visibility: str = "public"):
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=workspace.id, name=f"Project {key}", key=key, visibility=visibility
        )
        session.add(project)
    return project


async def _outbox_events(session_factory, event_name: str | None = None) -> list[dict]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent.payload).where(
                    OutboxEvent.event_type == "realtime.publish"
                )
            )
        ).scalars().all()
    if event_name is not None:
        return [row for row in rows if row.get("event") == event_name]
    return list(rows)


async def _db_label_links(session_factory, issue_id: uuid.UUID) -> set[uuid.UUID]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IssueLabel.label_id).where(IssueLabel.issue_id == issue_id)
            )
        ).scalars().all()
    return set(rows)


def _required_row(**values):
    columns = {
        "value_text": None,
        "value_number": None,
        "value_date": None,
        "value_member_id": None,
        "value_boolean": None,
        "value_json": None,
    }
    columns.update(values)
    return SimpleNamespace(**columns)


@pytest.mark.parametrize(
    ("field_type", "row", "expected"),
    [
        ("text", None, False),
        ("text", _required_row(value_text=""), False),
        ("text", _required_row(value_text="value"), True),
        ("textarea", _required_row(value_text=""), False),
        ("textarea", _required_row(value_text="details"), True),
        ("url", _required_row(value_text=""), False),
        ("url", _required_row(value_text="https://example.com"), True),
        ("number", _required_row(value_number=Decimal("0")), True),
        ("date", _required_row(value_date=FIXED_NOW), True),
        ("datetime", _required_row(value_date=FIXED_NOW), True),
        ("member", _required_row(value_member_id=None), False),
        ("member", _required_row(value_member_id=uuid.uuid4()), True),
        ("boolean", _required_row(value_boolean=False), True),
        ("boolean", _required_row(value_boolean=True), True),
        ("single_select", _required_row(value_json=None), False),
        ("single_select", _required_row(value_json=""), False),
        ("single_select", _required_row(value_json=str(uuid.uuid4())), True),
        ("multi_select", _required_row(value_json=[]), False),
        ("multi_select", _required_row(value_json=[str(uuid.uuid4())]), True),
        ("multi_select", _required_row(value_json="not-an-array"), False),
    ],
)
def test_required_value_presence_is_type_aware(field_type, row, expected):
    definition = SimpleNamespace(type=field_type)

    assert _required_value_is_present(definition, row) is expected


async def test_required_member_value_nullified_by_fk_blocks_status_transition(
    session_factory,
):
    ctx = await _setup(session_factory)
    doomed_user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        doomed = Member(
            workspace_id=ctx.workspace.id,
            member_type="human",
            user_id=doomed_user_id,
            role="member",
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(doomed)

    field = await ctx.label_service.create_field_def(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name="Acceptor",
        field_key=f"acceptor_{uuid.uuid4().hex[:8]}",
        field_type="member",
        is_required=True,
        required_on=["status:done"],
    )
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    async with session_factory() as session, session.begin():
        done_status_id = await session.scalar(
            select(IssueStatus.id).where(
                IssueStatus.workspace_id == ctx.workspace.id,
                IssueStatus.category == "done",
            )
        )
        assert done_status_id is not None
        session.add(
            IssueCustomFieldValue(
                workspace_id=ctx.workspace.id,
                issue_id=issue_id,
                field_def_id=uuid.UUID(field["id"]),
                value_member_id=doomed.id,
            )
        )

    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM members WHERE id = :id"), {"id": doomed.id})

    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.issues.update_issue(
            actor=ctx.admin,
            workspace_id=ctx.workspace.id,
            issue_id=issue_id,
            patch=IssuePatch(status_id=done_status_id),
            expected_version=ctx.labels["issue"]["version"],
        )
    assert excinfo.value.code == "required_field_missing"
    assert excinfo.value.details["missing"] == [
        {"field_def_id": field["id"], "name": "Acceptor"}
    ]

    async with session_factory() as session:
        issue = await session.scalar(select(Issue).where(Issue.id == issue_id))
    assert issue.state_category != "done"


# ---------------------------------------------------------------------------
# add / list / remove
# ---------------------------------------------------------------------------


async def test_add_label_persists_and_broadcasts(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    label_id = uuid.UUID(ctx.labels["bug"]["id"])

    result = await ctx.association.add_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        issue_id=issue_id,
        label_id=label_id,
    )
    assert [item["id"] for item in result["labels"]] == [str(label_id)]

    # Durable: a real join row, not just an HTTP echo.
    assert await _db_label_links(session_factory, issue_id) == {label_id}

    # Outbox unique write path: detail channel always + workspace list channel
    # (workspace-level issue → visible to the whole workspace).
    # One outbox row per channel; detail + workspace channels must both exist.
    async with session_factory() as session:
        all_rows = (
            (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "realtime.publish"
                    )
                )
            ).scalars().all()
        )
    events = [row for row in all_rows if row.get("event") == "issue.labels_changed"]
    assert len(events) == 2
    assert events[0]["data"]["issue_id"] == str(issue_id)
    assert [label["id"] for label in events[0]["data"]["labels"]] == [str(label_id)]
    label_channels = {row["channel"] for row in events}
    assert label_channels == {
        f"issue:{issue_id}",
        f"workspace:{ctx.workspace.id}:issues",
    }


async def test_add_label_is_idempotent_without_event(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    label_id = uuid.UUID(ctx.labels["bug"]["id"])
    await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=label_id,
    )
    # Second add: same response, NO second event (§6.9). One add emits two
    # outbox rows (detail + workspace channel) — the count must not grow.
    before = len(await _outbox_events(session_factory, "issue.labels_changed"))
    assert before == 2
    result = await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=label_id,
    )
    assert [item["id"] for item in result["labels"]] == [str(label_id)]
    events = await _outbox_events(session_factory, "issue.labels_changed")
    assert len(events) == before


async def test_list_and_remove_labels(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    bug_id = uuid.UUID(ctx.labels["bug"]["id"])
    feat_id = uuid.UUID(ctx.labels["feature"]["id"])
    for label_id in (bug_id, feat_id):
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            label_id=label_id,
        )
    listing = await ctx.association.list_issue_labels(
        viewer=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id
    )
    assert {item["id"] for item in listing} == {str(bug_id), str(feat_id)}

    removed = await ctx.association.remove_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=bug_id,
    )
    assert [item["id"] for item in removed["labels"]] == [str(feat_id)]
    assert await _db_label_links(session_factory, issue_id) == {feat_id}


async def test_remove_unattached_label_is_idempotent(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    label_id = uuid.UUID(ctx.labels["bug"]["id"])
    result = await ctx.association.remove_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=label_id,
    )
    assert result["labels"] == []
    assert await _outbox_events(session_factory, "issue.labels_changed") == []


async def test_add_label_unknown_label_or_issue_404(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    with pytest.raises(NotFoundError):
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            label_id=uuid.uuid4(),
        )
    with pytest.raises(NotFoundError):
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=uuid.uuid4(),
            label_id=uuid.UUID(ctx.labels["bug"]["id"]),
        )


async def test_soft_deleted_issue_is_not_found(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    await ctx.issues.delete_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id
    )
    with pytest.raises(NotFoundError):
        await ctx.association.list_issue_labels(
            viewer=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id
        )


# ---------------------------------------------------------------------------
# scope enforcement (§2.3 note — 422 label_scope_mismatch)
# ---------------------------------------------------------------------------


async def test_project_label_scope_enforcement(session_factory):
    ctx = await _setup(session_factory)
    project_a = await _make_project(session_factory, ctx.workspace, key="PJA")
    project_b = await _make_project(session_factory, ctx.workspace, key="PJB")
    scoped = await ctx.label_service.create_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="client-a", color="#888888", project_id=project_a.id,
    )
    issue_a = await ctx.issues.create_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title="in A", project_id=str(project_a.id)),
    )
    issue_b = await ctx.issues.create_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title="in B", project_id=str(project_b.id)),
    )
    inbox = uuid.UUID(ctx.labels["issue"]["id"])  # workspace-level issue
    scoped_id = uuid.UUID(scoped["id"])

    # Same project → OK.
    ok = await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=uuid.UUID(issue_a["id"]), label_id=scoped_id,
    )
    assert [item["id"] for item in ok["labels"]] == [str(scoped_id)]
    # Other project → 422 label_scope_mismatch.
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=uuid.UUID(issue_b["id"]), label_id=scoped_id,
        )
    assert excinfo.value.code == "label_scope_mismatch"
    # Workspace-level issue → also a scope mismatch.
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=inbox, label_id=scoped_id,
        )
    assert excinfo.value.code == "label_scope_mismatch"


async def test_private_project_issue_events_skip_workspace_channel(session_factory):
    ctx = await _setup(session_factory)
    private = await _make_project(
        session_factory, ctx.workspace, key="SEC", visibility="private"
    )
    issue = await ctx.issues.create_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title="secret", project_id=str(private.id)),
    )
    issue_id = uuid.UUID(issue["id"])
    await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=uuid.UUID(ctx.labels["bug"]["id"]),
    )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "realtime.publish"
                    )
                )
            ).scalars().all()
        )
    label_channels = {
        row["channel"]
        for row in rows
        if row.get("event") == "issue.labels_changed"
    }
    # Private project: detail channel ONLY (mirrors IssueService emission).
    assert label_channels == {f"issue:{issue_id}"}


# ---------------------------------------------------------------------------
# whole-set replace (PUT)
# ---------------------------------------------------------------------------


async def test_replace_labels_syncs_the_set(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    bug_id = uuid.UUID(ctx.labels["bug"]["id"])
    feat_id = uuid.UUID(ctx.labels["feature"]["id"])
    await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=bug_id,
    )
    # Replace {bug} → {bug, feature, bug(dup)} — deduped, single event.
    result = await ctx.association.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_ids=[bug_id, feat_id, bug_id],
    )
    assert {item["id"] for item in result["labels"]} == {str(bug_id), str(feat_id)}
    assert await _db_label_links(session_factory, issue_id) == {bug_id, feat_id}
    # Replace → empty clears everything.
    result = await ctx.association.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_ids=[],
    )
    assert result["labels"] == []
    assert await _db_label_links(session_factory, issue_id) == set()


async def test_replace_labels_no_op_emits_no_event(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    bug_id = uuid.UUID(ctx.labels["bug"]["id"])
    await ctx.association.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_ids=[bug_id],
    )
    before = len(await _outbox_events(session_factory, "issue.labels_changed"))
    result = await ctx.association.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_ids=[bug_id],
    )
    assert [item["id"] for item in result["labels"]] == [str(bug_id)]
    after = len(await _outbox_events(session_factory, "issue.labels_changed"))
    assert after == before


async def test_replace_labels_unknown_label_404(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    with pytest.raises(NotFoundError) as excinfo:
        await ctx.association.replace_labels(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            label_ids=[uuid.uuid4()],
        )
    assert "label_ids" in (excinfo.value.details or {})


async def test_replace_labels_scope_mismatch(session_factory):
    ctx = await _setup(session_factory)
    project_a = await _make_project(session_factory, ctx.workspace, key="PJA")
    project_b = await _make_project(session_factory, ctx.workspace, key="PJB")
    scoped = await ctx.label_service.create_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="scoped", color="#888888", project_id=project_a.id,
    )
    issue_b = await ctx.issues.create_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title="in B", project_id=str(project_b.id)),
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.association.replace_labels(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=uuid.UUID(issue_b["id"]),
            label_ids=[uuid.UUID(scoped["id"])],
        )
    assert excinfo.value.code == "label_scope_mismatch"


async def test_replace_labels_if_match_conflict(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    with pytest.raises(ConflictError):
        await ctx.association.replace_labels(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            label_ids=[uuid.UUID(ctx.labels["bug"]["id"])],
            if_match='"2000-01-01T00:00:00Z"',
        )
    # A matching If-Match (the issue's current updated_at) succeeds.
    async with session_factory() as session:
        issue = await session.scalar(select(Issue).where(Issue.id == issue_id))
        stamp = issue.updated_at.isoformat().replace("+00:00", "Z")
    result = await ctx.association.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_ids=[uuid.UUID(ctx.labels["bug"]["id"])],
        if_match=stamp,
    )
    assert len(result["labels"]) == 1


# ---------------------------------------------------------------------------
# authorization (issue.md gates reused)
# ---------------------------------------------------------------------------


async def test_member_role_can_label_workspace_issue(session_factory):
    ctx = await _setup(session_factory)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=ctx.workspace.id, member_type="human", user_id=user_id,
            role="member", status="active", joined_at=FIXED_NOW,
        )
        session.add(member)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    result = await ctx.association.add_label(
        actor=member, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=uuid.UUID(ctx.labels["bug"]["id"]),
    )
    assert len(result["labels"]) == 1


async def test_outsider_cannot_read_labels(session_factory):
    """A member of ANOTHER workspace gets 404 (tenant resolver + gate)."""
    ctx = await _setup(session_factory)
    # Second workspace with its own admin.
    async with session_factory() as session, session.begin():
        other_ws = Workspace(name="Other", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(other_ws)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        outsider = Member(
            workspace_id=other_ws.id, member_type="human", user_id=user_id,
            role="admin", status="active", joined_at=FIXED_NOW,
        )
        session.add(outsider)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    # The service runs under the caller's resolved workspace; passing the
    # wrong tenant yields 404 (RLS + workspace predicate), never data.
    with pytest.raises(NotFoundError):
        await ctx.association.list_issue_labels(
            viewer=outsider, workspace_id=other_ws.id, issue_id=issue_id
        )


# ---------------------------------------------------------------------------
# merge (§3.2 / §4.4)
# ---------------------------------------------------------------------------


async def test_merge_label_migrates_issues_and_deletes_source(session_factory):
    ctx = await _setup(session_factory)
    defect = await ctx.label_service.create_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, name="defect", color="#aa0000"
    )
    bug_id = uuid.UUID(ctx.labels["bug"]["id"])
    defect_id = uuid.UUID(defect["id"])
    # 3 issues: two carry defect (one of them also already has bug → dedup),
    # one carries only bug (untouched by the merge).
    ids = []
    for i in range(3):
        created = await ctx.issues.create_issue(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            body=CreateIssueRequest(title=f"issue {i}"),
        )
        ids.append(uuid.UUID(created["id"]))
    for issue_id in ids:
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, label_id=bug_id,
        )
    for issue_id in ids[:2]:
        await ctx.association.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, label_id=defect_id,
        )
    # Clear the setup-phase outbox rows so every labels_changed row observed
    # below comes from the merge itself (the outbox has no ordering guarantee
    # across rows of one transaction, so positional slicing is unreliable).
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM outbox_events"))

    result = await ctx.label_service.merge_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        source_label_id=defect_id, target_label_id=bug_id,
    )
    assert result["merged_issue_count"] == 2
    assert result["target_label"]["id"] == str(bug_id)

    # Source label gone; every carrier ends with exactly [bug] (deduped).
    async with session_factory() as session:
        assert await session.scalar(select(Label).where(Label.id == defect_id)) is None
    for issue_id in ids:
        assert await _db_label_links(session_factory, issue_id) == {bug_id}

    # Events: the outbox was cleared before the merge, so everything observed
    # now was emitted BY it. One labels_changed per affected issue per channel.
    merge_events = await _outbox_events(session_factory, "issue.labels_changed")
    assert len(merge_events) == 4  # 2 affected issues × 2 channels
    merge_by_issue: dict[str, list[dict]] = {}
    for event in merge_events:
        merge_by_issue.setdefault(event["data"]["issue_id"], []).append(event)
    assert set(merge_by_issue) == {str(ids[0]), str(ids[1])}
    for issue_events in merge_by_issue.values():
        assert {e["channel"].split(":")[0] for e in issue_events} == {"issue", "workspace"}
        for event in issue_events:
            assert [label["id"] for label in event["data"]["labels"]] == [str(bug_id)]
    deleted = await _outbox_events(session_factory, "label.deleted")
    assert any(
        event["data"]["id"] == str(defect_id)
        and event["data"].get("merged_into") == str(bug_id)
        for event in deleted
    )


async def test_merge_label_rejects_self_and_unknown(session_factory):
    ctx = await _setup(session_factory)
    bug_id = uuid.UUID(ctx.labels["bug"]["id"])
    with pytest.raises(ConflictError):
        await ctx.label_service.merge_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            source_label_id=bug_id, target_label_id=bug_id,
        )
    with pytest.raises(NotFoundError):
        await ctx.label_service.merge_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            source_label_id=uuid.uuid4(), target_label_id=bug_id,
        )


async def test_merge_label_rejects_project_target_from_a_broader_or_other_scope(
    session_factory,
):
    ctx = await _setup(session_factory)
    first = await _make_project(session_factory, ctx.workspace, key="PONE")
    second = await _make_project(session_factory, ctx.workspace, key="PTWO")
    first_label = await ctx.label_service.create_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name="first private",
        color="#aa0000",
        project_id=first.id,
    )
    second_label = await ctx.label_service.create_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name="second private",
        color="#0000aa",
        project_id=second.id,
    )
    workspace_label_id = uuid.UUID(ctx.labels["bug"]["id"])
    first_label_id = uuid.UUID(first_label["id"])
    second_label_id = uuid.UUID(second_label["id"])

    for source_id, target_id, source_project_id in (
        (workspace_label_id, first_label_id, None),
        (first_label_id, second_label_id, first.id),
    ):
        with pytest.raises(BusinessRuleError) as excinfo:
            await ctx.label_service.merge_label(
                actor=ctx.admin,
                workspace_id=ctx.workspace.id,
                source_label_id=source_id,
                target_label_id=target_id,
            )
        assert excinfo.value.code == "label_scope_mismatch"
        assert excinfo.value.details == {
            "source_label_id": str(source_id),
            "source_project_id": str(source_project_id)
            if source_project_id is not None
            else None,
            "target_label_id": str(target_id),
            "target_project_id": str(first.id if target_id == first_label_id else second.id),
        }

    # Both rejected transactions are non-mutating.
    async with session_factory() as session:
        surviving = set(
            (
                await session.execute(
                    select(Label.id).where(
                        Label.id.in_([workspace_label_id, first_label_id, second_label_id])
                    )
                )
            ).scalars()
        )
    assert surviving == {workspace_label_id, first_label_id, second_label_id}


async def test_merge_label_allows_same_project_source_into_project_target(session_factory):
    ctx = await _setup(session_factory)
    project = await _make_project(session_factory, ctx.workspace, key="SAME")
    source = await ctx.label_service.create_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name="private source",
        color="#aa0000",
        project_id=project.id,
    )
    target = await ctx.label_service.create_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name="private target",
        color="#0000aa",
        project_id=project.id,
    )

    result = await ctx.label_service.merge_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        source_label_id=uuid.UUID(source["id"]),
        target_label_id=uuid.UUID(target["id"]),
    )

    assert result["merged_issue_count"] == 0
    assert result["target_label"]["id"] == target["id"]


async def test_merge_carries_across_when_target_absent_on_issue(session_factory):
    ctx = await _setup(session_factory)
    defect = await ctx.label_service.create_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, name="defect", color="#aa0000"
    )
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    defect_id = uuid.UUID(defect["id"])
    await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=defect_id,
    )
    await ctx.label_service.merge_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        source_label_id=defect_id, target_label_id=uuid.UUID(ctx.labels["feature"]["id"]),
    )
    assert await _db_label_links(session_factory, issue_id) == {
        uuid.UUID(ctx.labels["feature"]["id"])
    }


async def test_audit_rows_written(session_factory):
    ctx = await _setup(session_factory)
    issue_id = uuid.UUID(ctx.labels["issue"]["id"])
    await ctx.association.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        label_id=uuid.UUID(ctx.labels["bug"]["id"]),
    )
    async with session_factory() as session:
        actions = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM audit_logs "
                        "WHERE resource_type = 'issue' ORDER BY created_at"
                    )
                )
            ).scalars().all()
        )
    assert "issue.label_added" in list(actions)

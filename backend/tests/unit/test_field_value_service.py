"""Issue custom-field value service tests — direct calls, real PostgreSQL.

Covers label-property.md §2.6 / §3.1 / §3.3 / §3.5 (association layer):
whole-form PUT with per-type validation and the named 422 codes
(invalid_field_value / field_inactive), exactly-one-value-column enforcement,
enum option membership, member workspace membership, clear semantics,
no-op event suppression (§6.9), outbox emission of issue.custom_field_changed
on the issue channels, listing with field-definition snapshots, If-Match
optimistic concurrency and scope applicability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.label import IssueCustomFieldValue
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.labels.association import FieldValueService
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
    def __init__(self, session_factory, workspace, admin, issue_service, fields):
        self.sf = session_factory
        self.workspace = workspace
        self.admin = admin
        self.issues = issue_service
        self.service = FieldValueService(issue_service, clock=_clock)
        self.fields = fields  # field_key -> rendered def dict


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
        agent = Member(
            workspace_id=workspace.id,
            member_type="agent",
            agent_id=uuid.uuid4(),
            role="member",
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add_all([admin, agent])
    label_service = LabelService(session_factory, clock=_clock)
    issue_service = IssueService(session_factory, clock=_clock)

    async def _field(**kwargs):
        if "type" in kwargs:
            kwargs["field_type"] = kwargs.pop("type")
        return await label_service.create_field_def(
            actor=admin, workspace_id=workspace.id, **kwargs
        )

    fields = {
        "notes": await _field(name="Notes", field_key="notes", type="text"),
        "users": await _field(
            name="Affected users", field_key="users", type="number",
            config={"min": 0, "max": 1000000, "precision": 0},
        ),
        "launch": await _field(name="Launch day", field_key="launch", type="date"),
        "severity": await _field(
            name="Severity", field_key="severity", type="single_select",
            options=[
                {"name": "Minor", "color": "#888888", "position": 0},
                {"name": "Major", "color": "#f5a623", "position": 1},
            ],
        ),
        "modules": await _field(
            name="Modules", field_key="modules", type="multi_select",
            options=[
                {"name": "api", "position": 0},
                {"name": "web", "position": 1},
            ],
        ),
        "acceptor": await _field(name="Acceptor", field_key="acceptor", type="member"),
        "needs_docs": await _field(
            name="Needs docs", field_key="needs_docs", type="boolean"
        ),
        "design": await _field(
            name="Design link", field_key="design", type="url",
            config={"require_https": True},
        ),
    }
    return Ctx(session_factory, workspace, admin, issue_service, fields)


async def _create_issue(ctx: Ctx, title: str = "issue", project_id: str | None = None):
    created = await ctx.issues.create_issue(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title=title, project_id=project_id),
    )
    return uuid.UUID(created["id"])


async def _value_rows(session_factory, issue_id: uuid.UUID) -> list:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(IssueCustomFieldValue).where(
                        IssueCustomFieldValue.issue_id == issue_id
                    )
                )
            ).scalars().all()
        )


async def _events(session_factory, event_name: str) -> list[dict]:
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
    return [row for row in rows if row.get("event") == event_name]


def _option(field: dict, name: str) -> str:
    return next(o["id"] for o in field["options"] if o["name"] == name)


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


async def test_list_values_shows_defs_with_empty_values(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    listing = await ctx.service.list_values(
        viewer=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id
    )
    # All applicable active defs, values empty, ordered by position.
    keys = [entry["field_def"]["field_key"] for entry in listing]
    assert set(keys) == set(ctx.fields)
    assert all(entry["value"] is None for entry in listing)
    severity = next(e for e in listing if e["field_def"]["field_key"] == "severity")
    assert [o["name"] for o in severity["field_def"]["options"]] == ["Minor", "Major"]


# ---------------------------------------------------------------------------
# happy path per type — stored in exactly the right column
# ---------------------------------------------------------------------------


async def test_set_values_all_types_roundtrip(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    member_id = ctx.admin.id
    values = [
        {"field_def_id": ctx.fields["notes"]["id"], "value_text": "hello"},
        {"field_def_id": ctx.fields["users"]["id"], "value_number": 1500},
        {"field_def_id": ctx.fields["launch"]["id"], "value_date": "2026-08-01"},
        {"field_def_id": ctx.fields["severity"]["id"],
         "value_json": _option(ctx.fields["severity"], "Major")},
        {"field_def_id": ctx.fields["modules"]["id"],
         "value_json": [_option(ctx.fields["modules"], "api")]},
        {"field_def_id": ctx.fields["acceptor"]["id"], "value_member_id": str(member_id)},
        {"field_def_id": ctx.fields["needs_docs"]["id"], "value_boolean": True},
        {"field_def_id": ctx.fields["design"]["id"],
         "value_text": "https://design.example.com/a"},
    ]
    listing = await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=values,
    )
    by_key = {e["field_def"]["field_key"]: e["value"] for e in listing}
    assert by_key["notes"]["value_text"] == "hello"
    assert by_key["users"]["value_number"] == 1500
    assert by_key["launch"]["value_date"] == "2026-08-01T00:00:00Z"
    assert by_key["severity"]["value_json"] == _option(ctx.fields["severity"], "Major")
    assert by_key["modules"]["value_json"] == [_option(ctx.fields["modules"], "api")]
    assert by_key["acceptor"]["value_member_id"] == str(member_id)
    assert by_key["acceptor"]["value_member"]["member_type"] == "human"
    assert by_key["needs_docs"]["value_boolean"] is True
    assert by_key["design"]["value_text"] == "https://design.example.com/a"

    # Exactly one value column non-NULL per row (num_nonnulls backstop).
    rows = await _value_rows(session_factory, issue_id)
    assert len(rows) == len(values)
    for row in rows:
        non_null = [
            column for column in (
                row.value_text, row.value_number, row.value_date,
                row.value_member_id, row.value_boolean, row.value_json,
            ) if column is not None
        ]
        assert len(non_null) == 1


async def test_agent_member_value_renders_agent_type(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    async with session_factory() as session:
        agent = await session.scalar(
            select(Member).where(
                Member.workspace_id == ctx.workspace.id,
                Member.member_type == "agent",
            )
        )
    listing = await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{
            "field_def_id": ctx.fields["acceptor"]["id"],
            "value_member_id": str(agent.id),
        }],
    )
    entry = next(e for e in listing if e["value"] is not None)
    assert entry["value"]["value_member"]["member_type"] == "agent"


# ---------------------------------------------------------------------------
# events (§3.5 — per-field issue.custom_field_changed)
# ---------------------------------------------------------------------------


async def test_set_values_emits_per_field_events(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[
            {"field_def_id": ctx.fields["notes"]["id"], "value_text": "a"},
            {"field_def_id": ctx.fields["needs_docs"]["id"], "value_boolean": False},
        ],
    )
    events = await _events(session_factory, "issue.custom_field_changed")
    # 2 fields × 2 channels (workspace-level issue).
    assert len(events) == 4
    by_field = {e["data"]["field_key"]: e["data"] for e in events
                if e["channel"].startswith("issue:")}
    assert by_field["notes"]["value"]["value_text"] == "a"
    assert by_field["needs_docs"]["value"]["value_boolean"] is False
    assert all(e["data"]["issue_id"] == str(issue_id) for e in events)


async def test_no_change_emits_no_event(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    payload = [{"field_def_id": ctx.fields["notes"]["id"], "value_text": "same"}]
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=payload,
    )
    before = len(await _events(session_factory, "issue.custom_field_changed"))
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=payload,
    )
    after = len(await _events(session_factory, "issue.custom_field_changed"))
    assert after == before


async def test_clear_value_emits_null_event_and_deletes_row(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": ctx.fields["notes"]["id"], "value_text": "temp"}],
    )
    listing = await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": ctx.fields["notes"]["id"], "value_text": None}],
    )
    assert next(e for e in listing if e["field_def"]["field_key"] == "notes")["value"] is None
    assert await _value_rows(session_factory, issue_id) == []
    events = await _events(session_factory, "issue.custom_field_changed")
    clears = [e for e in events if e["data"]["value"] is None]
    assert clears and all(
        e["data"]["field_def_id"] == ctx.fields["notes"]["id"] for e in clears
    )


async def test_empty_multi_select_clears(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    opt = _option(ctx.fields["modules"], "api")
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": ctx.fields["modules"]["id"], "value_json": [opt]}],
    )
    await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": ctx.fields["modules"]["id"], "value_json": []}],
    )
    assert await _value_rows(session_factory, issue_id) == []


# ---------------------------------------------------------------------------
# negative validation matrix (§3.3 named codes)
# ---------------------------------------------------------------------------


async def _expect_invalid(coro, reason: str):
    with pytest.raises(BusinessRuleError) as excinfo:
        await coro
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "invalid_field_value"
    assert excinfo.value.details["reason"] == reason


async def test_wrong_value_column_rejected(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["users"]["id"], "value_text": "1500"}],
        ),
        "wrong_value_column",
    )


async def test_multiple_value_columns_rejected(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{
                "field_def_id": ctx.fields["notes"]["id"],
                "value_text": "x", "value_boolean": True,
            }],
        ),
        "exactly_one_value_column",
    )


async def test_inactive_field_rejected(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    label_service = LabelService(ctx.sf, clock=_clock)
    from mesh.labels.service import FieldDefPatch

    await label_service.update_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        field_def_id=uuid.UUID(ctx.fields["notes"]["id"]),
        patch=FieldDefPatch(is_active=False),
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["notes"]["id"], "value_text": "x"}],
        )
    assert excinfo.value.code == "field_inactive"
    # Inactive defs disappear from the listing too (§4.3 hidden but kept).
    listing = await ctx.service.list_values(
        viewer=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id
    )
    assert "notes" not in {e["field_def"]["field_key"] for e in listing}


async def test_unknown_field_404(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    with pytest.raises(NotFoundError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": str(uuid.uuid4()), "value_text": "x"}],
        )


async def test_project_scoped_def_not_applicable_to_other_project(session_factory):
    ctx = await _setup(session_factory)
    async with ctx.sf() as session, session.begin():
        project_a = Project(
            workspace_id=ctx.workspace.id, name="A", key="PJA", visibility="public"
        )
        project_b = Project(
            workspace_id=ctx.workspace.id, name="B", key="PJB", visibility="public"
        )
        session.add_all([project_a, project_b])
    label_service = LabelService(ctx.sf, clock=_clock)
    scoped = await label_service.create_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="Only A", field_key="only_a", field_type="text", project_id=project_a.id,
    )
    issue_b = await _create_issue(ctx, project_id=str(project_b.id))
    with pytest.raises(NotFoundError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_b,
            values=[{"field_def_id": scoped["id"], "value_text": "x"}],
        )
    # But it applies to project A's issues.
    issue_a = await _create_issue(ctx, project_id=str(project_a.id))
    listing = await ctx.service.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_a,
        values=[{"field_def_id": scoped["id"], "value_text": "ok"}],
    )
    entry = next(e for e in listing if e["field_def"]["field_key"] == "only_a")
    assert entry["value"]["value_text"] == "ok"


async def test_enum_option_membership_validated(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["severity"]["id"],
                     "value_json": str(uuid.uuid4())}],
        ),
        "option_not_in_field",
    )
    # Option from ANOTHER field is also rejected.
    foreign = _option(ctx.fields["modules"], "api")
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["severity"]["id"],
                     "value_json": foreign}],
        ),
        "option_not_in_field",
    )
    # Inactive option rejected on new writes (§2.6).
    label_service = LabelService(ctx.sf, clock=_clock)
    from mesh.labels.service import OptionPatch

    minor_id = _option(ctx.fields["severity"], "Minor")
    await label_service.update_option(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        field_def_id=uuid.UUID(ctx.fields["severity"]["id"]),
        option_id=uuid.UUID(minor_id),
        patch=OptionPatch(is_active=False),
    )
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["severity"]["id"],
                     "value_json": minor_id}],
        ),
        "option_not_in_field",
    )
    # multi_select with one unknown option id.
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["modules"]["id"],
                     "value_json": [str(uuid.uuid4())]}],
        ),
        "option_not_in_field",
    )


async def test_number_bounds_and_precision(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = ctx.fields["users"]["id"]
    for bad, reason in (
        (-1, "number_below_min"),
        (1000001, "number_above_max"),
        (1.5, "number_precision_exceeded"),
        ("1500", "number_value_invalid"),
        (True, "number_value_invalid"),
    ):
        await _expect_invalid(
            ctx.service.set_values(
                actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
                values=[{"field_def_id": field, "value_number": bad}],
            ),
            reason,
        )


async def test_url_validation(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = ctx.fields["design"]["id"]
    for bad, reason in (
        ("not a url", "url_value_invalid"),
        ("http://insecure.example.com", "url_https_required"),
        (123, "url_value_invalid"),
    ):
        await _expect_invalid(
            ctx.service.set_values(
                actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
                values=[{"field_def_id": field, "value_text": bad}],
            ),
            reason,
        )


async def test_date_validation(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = ctx.fields["launch"]["id"]
    for bad in ("2026-13-99", "yesterday", 20260801):
        await _expect_invalid(
            ctx.service.set_values(
                actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
                values=[{"field_def_id": field, "value_date": bad}],
            ),
            "date_value_invalid",
        )


async def test_boolean_and_text_validation(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["needs_docs"]["id"],
                     "value_boolean": "yes"}],
        ),
        "boolean_value_invalid",
    )
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["notes"]["id"],
                     "value_text": "x" * 2001}],
        ),
        "text_value_invalid",
    )


async def test_member_value_must_be_workspace_member(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = ctx.fields["acceptor"]["id"]
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": field, "value_member_id": str(uuid.uuid4())}],
        ),
        "member_not_in_workspace",
    )
    await _expect_invalid(
        ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": field, "value_member_id": "not-a-uuid"}],
        ),
        "member_value_invalid",
    )


async def test_request_shape_validation(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    with pytest.raises(ValidationError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values="nope",  # type: ignore[arg-type]
        )
    # duplicate field_def_id in one request.
    with pytest.raises(ValidationError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[
                {"field_def_id": ctx.fields["notes"]["id"], "value_text": "a"},
                {"field_def_id": ctx.fields["notes"]["id"], "value_text": "b"},
            ],
        )
    # over the per-PUT cap.
    with pytest.raises(ValidationError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": str(uuid.uuid4()), "value_text": "x"}] * 51,
        )


async def test_if_match_conflict_and_success(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    with pytest.raises(ConflictError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": ctx.fields["notes"]["id"], "value_text": "x"}],
            if_match='"1999-01-01T00:00:00Z"',
        )


async def test_unknown_issue_404(session_factory):
    ctx = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await ctx.service.list_values(
            viewer=ctx.admin, workspace_id=ctx.workspace.id, issue_id=uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# cross-tenant backstops (README §9 T1, service level)
# ---------------------------------------------------------------------------


async def test_cross_tenant_field_def_not_found(session_factory):
    """A field def from another workspace resolves to 404, never a write."""
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    async with ctx.sf() as session, session.begin():
        other_ws = Workspace(name="B", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(other_ws)
    other_admin_id = await _add_user(session_factory)
    async with ctx.sf() as session, session.begin():
        other_admin = Member(
            workspace_id=other_ws.id, member_type="human",
            user_id=other_admin_id, role="admin", status="active",
            joined_at=FIXED_NOW,
        )
        session.add(other_admin)
    other_service = LabelService(ctx.sf, clock=_clock)
    foreign = await other_service.create_field_def(
        actor=other_admin, workspace_id=other_ws.id,
        name="Foreign", field_key="foreign", field_type="text",
    )
    with pytest.raises(NotFoundError):
        await ctx.service.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": foreign["id"], "value_text": "sneaky"}],
        )
    assert await _value_rows(session_factory, issue_id) == []

"""Label-property definition-layer service tests — direct calls, real PostgreSQL.

Covers label-property.md §2/§3/§5 (definition layer): label CRUD with
scope-internal uniqueness (partial-expression unique index, README §6.3),
custom-field definitions (10 types, per-type config/default validation with
named 422 codes), enum option CRUD, the §3.4 authorization matrix (workspace
admin / project lead), §6.14 optimistic concurrency, §6.7 event emission via
the outbox unique write path, audit trail, and cross-tenant isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.label import CustomFieldDef, CustomFieldOption, Label
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.labels.service import (
    FieldDefPatch,
    LabelPatch,
    LabelService,
    OptionPatch,
)

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


async def _add_user(session_factory, display_name: str = "User") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name=display_name,
            password_hash="x",
            status="active",
        )
        session.add(user)
    return user.id


async def _setup(session_factory, *, role: str = "admin"):
    """Workspace + one human member; returns (workspace, member, service)."""
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role=role,
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(member)
    return workspace, member, LabelService(session_factory, clock=_clock)


async def _second_member(session_factory, workspace, *, role: str = "member") -> Member:
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role=role,
            status="active",
            joined_at=FIXED_NOW,
        )
        session.add(member)
    return member


async def _create_project(
    session_factory, workspace, *, lead: Member | None = None, visibility: str = "public"
) -> Project:
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=workspace.id,
            name=f"P-{uuid.uuid4().hex[:6]}",
            key=f"K{uuid.uuid4().hex[:4].upper()}",
            visibility=visibility,
            lead_member_id=lead.id if lead is not None else None,
        )
        session.add(project)
    return project


async def _realtime_events(session_factory, event_name: str | None = None) -> list[dict]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent.payload).where(
                    OutboxEvent.event_type == "realtime.publish"
                )
            )
        ).scalars().all()
    payloads = [row for row in rows]
    if event_name is not None:
        payloads = [p for p in payloads if p.get("event") == event_name]
    return payloads


async def _audit_actions(session_factory) -> list[str]:
    from mesh.db.models.audit import AuditLog

    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog.action))).scalars().all()
    return list(rows)


# ===========================================================================
# labels — CRUD
# ===========================================================================


async def test_create_label_workspace_scope_defaults(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#e5484d"
    )
    assert label["name"] == "bug"
    assert label["color"] == "#e5484d"
    assert label["project_id"] is None
    assert label["scope"] == "workspace"
    assert label["description"] is None
    # created_at/updated_at are RFC3339 UTC (DB clock; _clock only drives
    # manual updated_at bumps on UPDATE).
    assert label["created_at"].endswith("Z")
    assert label["updated_at"].endswith("Z")
    # Durable row + event + audit.
    async with session_factory() as session:
        row = await session.scalar(select(Label).where(Label.id == uuid.UUID(label["id"])))
    assert row.name == "bug"
    events = await _realtime_events(session_factory, "label.created")
    assert len(events) == 1
    assert events[0]["channel"] == f"workspace:{workspace.id}:labels"
    assert events[0]["data"]["id"] == label["id"]
    assert await _audit_actions(session_factory) == ["label.created"]


async def test_create_label_project_scope_by_admin(session_factory):
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    label = await service.create_label(
        actor=member,
        workspace_id=workspace.id,
        name="客户A",
        color="#888888",
        project_id=project.id,
        description="private tag",
    )
    assert label["scope"] == "project"
    assert label["project_id"] == str(project.id)
    # Public project → both the detail channel and the workspace channel.
    events = await _realtime_events(session_factory, "label.created")
    channels = sorted(event["channel"] for event in events)
    assert channels == [
        f"project:{project.id}",
        f"workspace:{workspace.id}:labels",
    ]


async def test_create_label_private_project_only_detail_channel(session_factory):
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace, visibility="private")
    await service.create_label(
        actor=member,
        workspace_id=workspace.id,
        name="secret",
        color="#123456",
        project_id=project.id,
    )
    events = await _realtime_events(session_factory, "label.created")
    assert [event["channel"] for event in events] == [f"project:{project.id}"]


async def test_create_label_validation_errors(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.create_label(actor=member, workspace_id=workspace.id, name="", color="#ffffff")
    with pytest.raises(ValidationError):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="x" * 51, color="#ffffff"
        )
    with pytest.raises(ValidationError):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="ok", color="red"
        )
    with pytest.raises(ValidationError):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="ok", color="#fff"
        )
    with pytest.raises(ValidationError):
        await service.create_label(
            actor=member,
            workspace_id=workspace.id,
            name="ok",
            color="#ffffff",
            description="x" * 501,
        )
    assert await _realtime_events(session_factory) == []


async def test_create_label_duplicate_same_scope_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    await service.create_label(actor=member, workspace_id=workspace.id, name="bug", color="#ffffff")
    with pytest.raises(ConflictError) as excinfo:
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="bug", color="#000000"
        )
    assert excinfo.value.code == "label_name_taken"


async def test_create_label_same_name_different_scopes_ok(session_factory):
    workspace, member, service = await _setup(session_factory)
    project_a = await _create_project(session_factory, workspace)
    project_b = await _create_project(session_factory, workspace)
    ws_label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    label_a = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff",
        project_id=project_a.id,
    )
    label_b = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff",
        project_id=project_b.id,
    )
    assert len({ws_label["id"], label_a["id"], label_b["id"]}) == 3


async def test_create_label_unknown_project_not_found(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(NotFoundError):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="x", color="#ffffff",
            project_id=uuid.uuid4(),
        )


async def test_create_label_auth_matrix(session_factory):
    workspace, admin, service = await _setup(session_factory, role="admin")
    member = await _second_member(session_factory, workspace, role="member")
    guest = await _second_member(session_factory, workspace, role="guest")
    project = await _create_project(session_factory, workspace)
    # Plain member: workspace-level forbidden, project-level forbidden.
    with pytest.raises(ForbiddenError):
        await service.create_label(actor=member, workspace_id=workspace.id, name="a", color="#ffffff")
    with pytest.raises(ForbiddenError):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name="b", color="#ffffff",
            project_id=project.id,
        )
    with pytest.raises(ForbiddenError):
        await service.create_label(actor=guest, workspace_id=workspace.id, name="c", color="#ffffff")


async def test_create_label_project_lead_allowed(session_factory):
    workspace, admin, service = await _setup(session_factory, role="admin")
    lead = await _second_member(session_factory, workspace, role="member")
    project = await _create_project(session_factory, workspace, lead=lead)
    label = await service.create_label(
        actor=lead, workspace_id=workspace.id, name="lead-tag", color="#ffffff",
        project_id=project.id,
    )
    assert label["project_id"] == str(project.id)
    # Lead via project_members.role='lead' (not the projects.lead_member_id).
    other = await _second_member(session_factory, workspace, role="member")
    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=project.id,
                member_id=other.id, role="lead",
            )
        )
    label2 = await service.create_label(
        actor=other, workspace_id=workspace.id, name="lead-tag-2", color="#ffffff",
        project_id=project.id,
    )
    assert label2["name"] == "lead-tag-2"
    # But the lead cannot create workspace-level labels.
    with pytest.raises(ForbiddenError):
        await service.create_label(
            actor=lead, workspace_id=workspace.id, name="ws-level", color="#ffffff"
        )


async def test_update_label_changes_and_event(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    updated = await service.update_label(
        actor=member,
        workspace_id=workspace.id,
        label_id=uuid.UUID(label["id"]),
        patch=LabelPatch(name="defect", color="#000000", description="renamed"),
    )
    assert updated["name"] == "defect"
    assert updated["color"] == "#000000"
    assert updated["description"] == "renamed"
    events = await _realtime_events(session_factory, "label.updated")
    assert len(events) == 1
    assert events[0]["data"]["name"] == "defect"
    # Clear description back to null.
    cleared = await service.update_label(
        actor=member,
        workspace_id=workspace.id,
        label_id=uuid.UUID(label["id"]),
        patch=LabelPatch(description=None),
    )
    assert cleared["description"] is None


async def test_update_label_no_change_is_noop(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    before = await _realtime_events(session_factory)
    again = await service.update_label(
        actor=member,
        workspace_id=workspace.id,
        label_id=uuid.UUID(label["id"]),
        patch=LabelPatch(name="bug"),
    )
    assert again["name"] == "bug"
    assert await _realtime_events(session_factory) == before


async def test_update_label_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    with pytest.raises(ValidationError):
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(color="nope"),
        )
    with pytest.raises(ValidationError):
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(name="z" * 51),
        )
    with pytest.raises(ValidationError):
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(description="x" * 501),
        )


async def test_update_label_rename_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    await service.create_label(actor=member, workspace_id=workspace.id, name="bug", color="#ffffff")
    other = await service.create_label(
        actor=member, workspace_id=workspace.id, name="feature", color="#ffffff"
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(other["id"]),
            patch=LabelPatch(name="bug"),
        )
    assert excinfo.value.code == "label_name_taken"


async def test_update_label_if_match_optimistic_concurrency(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    stale = label["updated_at"]
    await service.update_label(
        actor=member,
        workspace_id=workspace.id,
        label_id=uuid.UUID(label["id"]),
        patch=LabelPatch(name="bug2"),
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(name="bug3"),
            if_match=stale,
        )
    assert excinfo.value.code == "conflict"
    # Fresh version passes (also tolerates quoted/unquoted and Z forms).
    fresh = await service.update_label(
        actor=member,
        workspace_id=workspace.id,
        label_id=uuid.UUID(label["id"]),
        patch=LabelPatch(name="bug3"),
        if_match=f'"{FIXED_NOW.isoformat().replace("+00:00", "Z")}"',
    )
    assert fresh["name"] == "bug3"
    # Garbage If-Match never matches.
    with pytest.raises(ConflictError):
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(name="bug4"),
            if_match="not-a-timestamp",
        )


async def test_update_label_auth_and_not_found(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    label = await service.create_label(
        actor=admin, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    with pytest.raises(ForbiddenError):
        await service.update_label(
            actor=member,
            workspace_id=workspace.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(name="hax"),
        )
    with pytest.raises(NotFoundError):
        await service.update_label(
            actor=admin,
            workspace_id=workspace.id,
            label_id=uuid.uuid4(),
            patch=LabelPatch(name="x"),
        )


async def test_delete_label_cascade_event_audit(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    result = await service.delete_label(
        actor=member, workspace_id=workspace.id, label_id=uuid.UUID(label["id"])
    )
    assert result == {"id": label["id"], "deleted": True}
    async with session_factory() as session:
        assert await session.scalar(select(Label).where(Label.id == uuid.UUID(label["id"]))) is None
    events = await _realtime_events(session_factory, "label.deleted")
    assert len(events) == 1
    assert events[0]["data"]["id"] == label["id"]
    assert "label.deleted" in await _audit_actions(session_factory)


async def test_delete_label_forbidden_for_member(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    label = await service.create_label(
        actor=admin, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    with pytest.raises(ForbiddenError):
        await service.delete_label(
            actor=member, workspace_id=workspace.id, label_id=uuid.UUID(label["id"])
        )


async def test_list_labels_pagination_and_scopes(session_factory):
    workspace, member, service = await _setup(session_factory)
    project = await _create_project(session_factory, workspace)
    for i in range(3):
        await service.create_label(
            actor=member, workspace_id=workspace.id, name=f"ws-{i}", color="#ffffff"
        )
    await service.create_label(
        actor=member, workspace_id=workspace.id, name="proj", color="#ffffff",
        project_id=project.id,
    )
    page1, cursor = await service.list_labels(viewer=member, workspace_id=workspace.id, limit=2)
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = await service.list_labels(
        viewer=member, workspace_id=workspace.id, limit=2, cursor=cursor
    )
    assert len(page2) == 2
    assert cursor2 is None
    all_names = {item["name"] for item in page1 + page2}
    assert all_names == {"ws-0", "ws-1", "ws-2", "proj"}
    # project_id filter returns project labels PLUS workspace-level ones.
    scoped, _ = await service.list_labels(
        viewer=member, workspace_id=workspace.id, project_id=project.id
    )
    assert {item["name"] for item in scoped} == {"ws-0", "ws-1", "ws-2", "proj"}
    # Invalid limit / cursor.
    with pytest.raises(ValidationError):
        await service.list_labels(viewer=member, workspace_id=workspace.id, limit=0)
    with pytest.raises(ValidationError):
        await service.list_labels(viewer=member, workspace_id=workspace.id, cursor="junk")


async def test_list_labels_private_project_visibility(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    private = await _create_project(session_factory, workspace, visibility="private")
    await service.create_label(
        actor=admin, workspace_id=workspace.id, name="hidden", color="#ffffff",
        project_id=private.id,
    )
    await service.create_label(
        actor=admin, workspace_id=workspace.id, name="shared", color="#ffffff"
    )
    # Non-member regular user: only the workspace-level label.
    visible, _ = await service.list_labels(viewer=member, workspace_id=workspace.id)
    assert [item["name"] for item in visible] == ["shared"]
    # Explicit private project scope: 403 for members, 404 for guests.
    with pytest.raises(ForbiddenError):
        await service.list_labels(viewer=member, workspace_id=workspace.id, project_id=private.id)
    guest = await _second_member(session_factory, workspace, role="guest")
    with pytest.raises(NotFoundError):
        await service.list_labels(viewer=guest, workspace_id=workspace.id, project_id=private.id)
    # Admin sees everything.
    all_labels, _ = await service.list_labels(viewer=admin, workspace_id=workspace.id)
    assert {item["name"] for item in all_labels} == {"hidden", "shared"}
    # A project member sees the private project's labels.
    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=private.id,
                member_id=member.id, role="member",
            )
        )
    member_visible, _ = await service.list_labels(viewer=member, workspace_id=workspace.id)
    assert {item["name"] for item in member_visible} == {"hidden", "shared"}


async def test_list_labels_guest_only_public_and_granted(session_factory):
    workspace, admin, service = await _setup(session_factory)
    guest = await _second_member(session_factory, workspace, role="guest")
    public = await _create_project(session_factory, workspace, visibility="public")
    private = await _create_project(session_factory, workspace, visibility="private")
    await service.create_label(
        actor=admin, workspace_id=workspace.id, name="pub", color="#ffffff",
        project_id=public.id,
    )
    await service.create_label(
        actor=admin, workspace_id=workspace.id, name="priv", color="#ffffff",
        project_id=private.id,
    )
    visible, _ = await service.list_labels(viewer=guest, workspace_id=workspace.id)
    assert {item["name"] for item in visible} == {"pub"}
    # Grant the guest the private project → it appears.
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=workspace.id, project_id=private.id,
                member_id=guest.id, permission="read",
            )
        )
    visible2, _ = await service.list_labels(viewer=guest, workspace_id=workspace.id)
    assert {item["name"] for item in visible2} == {"pub", "priv"}


async def test_label_cross_workspace_isolation(session_factory):
    workspace_a, admin_a, service = await _setup(session_factory)
    workspace_b, _, _ = await _setup(session_factory)
    label = await service.create_label(
        actor=admin_a, workspace_id=workspace_a.id, name="bug", color="#ffffff"
    )
    # Loading workspace A's label under workspace B's tenant → 404.
    with pytest.raises(NotFoundError):
        await service.update_label(
            actor=admin_a,
            workspace_id=workspace_b.id,
            label_id=uuid.UUID(label["id"]),
            patch=LabelPatch(name="x"),
        )
    with pytest.raises(NotFoundError):
        await service.delete_label(
            actor=admin_a, workspace_id=workspace_b.id, label_id=uuid.UUID(label["id"])
        )
    items, _ = await service.list_labels(viewer=admin_a, workspace_id=workspace_b.id)
    assert items == []


# ===========================================================================
# custom field definitions — CRUD + validation
# ===========================================================================


async def test_create_field_def_all_types(session_factory):
    workspace, member, service = await _setup(session_factory)
    types = (
        "text", "textarea", "number", "date", "datetime",
        "single_select", "multi_select", "member", "boolean", "url",
    )
    for index, field_type in enumerate(types):
        field = await service.create_field_def(
            actor=member,
            workspace_id=workspace.id,
            name=f"F-{field_type}",
            field_key=f"f_{field_type}",
            field_type=field_type,
            position=float(index),
        )
        assert field["type"] == field_type
        assert field["is_active"] is True
        assert field["is_required"] is False
        assert field["required_on"] == []
        assert field["config"] == {}
        assert field["options"] == []
    events = await _realtime_events(session_factory, "custom_field.updated")
    assert len(events) == 10
    assert all(event["data"]["change"] == "created" for event in events)
    assert all(event["channel"] == f"workspace:{workspace.id}:custom_fields" for event in events)


async def test_create_field_def_with_initial_options(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member,
        workspace_id=workspace.id,
        name="严重程度",
        field_key="severity",
        field_type="single_select",
        options=[
            {"name": "Minor", "color": "#888888", "position": 0},
            {"name": "Major", "color": "#f5a623", "position": 1},
            {"name": "Critical", "color": "#e5484d", "position": 2},
        ],
    )
    assert [option["name"] for option in field["options"]] == ["Minor", "Major", "Critical"]
    assert all(option["id"] for option in field["options"])
    assert field["options"][1]["color"] == "#f5a623"


async def test_create_field_def_key_and_name_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="x",
            field_key="Bad-Key", field_type="text",
        )
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="x",
            field_key="1startswithdigit", field_type="text",
        )
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="x" * 101,
            field_key="ok_key", field_type="text",
        )
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="x",
            field_key="ok_key", field_type="formula",
        )
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="x",
            field_key="ok_key", field_type="text", required_on=["whenever"],
        )


async def test_create_field_def_duplicate_key_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="A",
        field_key="severity", field_type="text",
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="B",
            field_key="severity", field_type="number",
        )
    assert excinfo.value.code == "field_key_taken"
    # Same key in a project scope does not conflict with workspace scope.
    project = await _create_project(session_factory, workspace)
    ok = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="C",
        field_key="severity", field_type="text", project_id=project.id,
    )
    assert ok["project_id"] == str(project.id)


async def test_create_field_def_config_validation(session_factory):
    workspace, member, service = await _setup(session_factory)

    async def _create(field_type: str, config: dict) -> None:
        await service.create_field_def(
            actor=member, workspace_id=workspace.id,
            name=f"N-{uuid.uuid4().hex[:6]}", field_key=f"k_{uuid.uuid4().hex[:8]}",
            field_type=field_type, config=config,
        )

    # Unknown keys per type → 422 invalid_field_config.
    for field_type, bad in (
        ("text", {"precision": 2}),
        ("number", {"format": "%Y"}),
        ("single_select", {"precision": 1}),
        ("boolean", {"anything": True}),
    ):
        with pytest.raises(BusinessRuleError) as excinfo:
            await _create(field_type, bad)
        assert excinfo.value.code == "invalid_field_config"
    # Bad number config shapes.
    for bad in (
        {"precision": -1}, {"precision": "2"}, {"unit": "x" * 21},
        {"min": "0"}, {"min": 5, "max": 1},
    ):
        with pytest.raises(BusinessRuleError) as excinfo:
            await _create("number", bad)
        assert excinfo.value.code == "invalid_field_config"
    with pytest.raises(BusinessRuleError):
        await _create("date", {"format": 42})
    with pytest.raises(BusinessRuleError):
        await _create("url", {"require_https": "yes"})
    # Valid configs pass.
    ok = await _create("number", {"precision": 2, "unit": "人", "min": 0, "max": 10})
    assert ok is None  # _create returns None; no raise == pass


async def test_create_field_def_default_value_validation(session_factory):
    workspace, member, service = await _setup(session_factory)

    async def _create(field_type: str, default, config: dict | None = None):
        return await service.create_field_def(
            actor=member, workspace_id=workspace.id,
            name=f"N-{uuid.uuid4().hex[:6]}", field_key=f"k_{uuid.uuid4().hex[:8]}",
            field_type=field_type, default_value=default, config=config or {},
        )

    # Shape mismatches → 422 invalid_field_config.
    for field_type, bad in (
        ("text", 42),
        ("number", "fifteen"),
        ("boolean", "true"),
        ("date", "26/07/2026"),
        ("datetime", "someday"),
        ("url", "not a url"),
        ("member", "6a2f0000-0000-0000-0000-000000000001"),
        ("single_select", "opt-x"),
        ("multi_select", ["opt-x"]),
    ):
        with pytest.raises(BusinessRuleError) as excinfo:
            await _create(field_type, bad)
        assert excinfo.value.code == "invalid_field_config"
    # number config bounds + precision.
    with pytest.raises(BusinessRuleError):
        await _create("number", 50, {"min": 0, "max": 10})
    with pytest.raises(BusinessRuleError):
        await _create("number", 3.14159, {"precision": 2})
    # Valid defaults pass and round-trip through JSONB.
    field = await _create("number", 1500, {"precision": 0, "unit": "users"})
    assert field["default_value"] == 1500
    ok_text = await _create("text", "hello")
    assert ok_text["default_value"] == "hello"
    ok_date = await _create("date", "2026-07-26")
    assert ok_date["default_value"] == "2026-07-26"
    ok_bool = await _create("boolean", False)
    assert ok_bool["default_value"] is False
    ok_url = await _create("url", "https://mesh.example/design")
    assert ok_url["default_value"] == "https://mesh.example/design"


async def test_create_field_def_options_on_non_select_rejected(session_factory):
    workspace, member, service = await _setup(session_factory)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="N",
            field_key="k1", field_type="text", options=[{"name": "x"}],
        )
    assert excinfo.value.code == "invalid_field_config"
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="N",
            field_key="k2", field_type="single_select",
            options=[{"name": "a"}, {"name": "a"}],
        )
    with pytest.raises(ValidationError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="N",
            field_key="k3", field_type="single_select",
            options=[{"name": "a", "color": "purple"}],
        )


async def test_create_field_def_auth_matrix(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    with pytest.raises(ForbiddenError):
        await service.create_field_def(
            actor=member, workspace_id=workspace.id, name="N",
            field_key="k1", field_type="text",
        )
    lead = await _second_member(session_factory, workspace, role="member")
    project = await _create_project(session_factory, workspace, lead=lead)
    ok = await service.create_field_def(
        actor=lead, workspace_id=workspace.id, name="N2",
        field_key="k2", field_type="text", project_id=project.id,
    )
    assert ok["project_id"] == str(project.id)


async def test_update_field_def_fields_and_event(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="Impact",
        field_key="impact", field_type="number",
    )
    updated = await service.update_field_def(
        actor=member,
        workspace_id=workspace.id,
        field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(
            name="Affected users",
            is_required=True,
            required_on=["save", "status:done"],
            config={"precision": 0, "unit": "users"},
            default_value=100,
            position=5.0,
        ),
    )
    assert updated["name"] == "Affected users"
    assert updated["is_required"] is True
    assert updated["required_on"] == ["save", "status:done"]
    assert updated["config"] == {"precision": 0, "unit": "users"}
    assert updated["default_value"] == 100
    events = await _realtime_events(session_factory, "custom_field.updated")
    assert events[-1]["data"]["change"] == "updated"
    assert "custom_field.updated" in await _audit_actions(session_factory)


async def test_update_field_def_deactivate_reactivate(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="N",
        field_key="k", field_type="text",
    )
    off = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(is_active=False),
    )
    assert off["is_active"] is False
    on = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(is_active=True),
    )
    assert on["is_active"] is True
    # is_active filter on the list.
    inactive, _ = await service.list_field_defs(
        viewer=member, workspace_id=workspace.id, is_active=False
    )
    assert inactive == []


async def test_update_field_def_no_change_noop(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="N",
        field_key="k", field_type="text",
    )
    events_before = await _realtime_events(session_factory)
    again = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(name="N"),
    )
    assert again["name"] == "N"
    assert await _realtime_events(session_factory) == events_before


async def test_update_field_def_validation(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="N",
        field_key="k", field_type="number",
    )
    fid = uuid.UUID(field["id"])
    with pytest.raises(ValidationError):
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(name=""),
        )
    with pytest.raises(ValidationError):
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(required_on=["status:"]),
        )
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(config={"format": "%Y"}),
        )
    assert excinfo.value.code == "invalid_field_config"
    with pytest.raises(BusinessRuleError):
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(default_value="NaN-ish"),
        )


async def test_update_field_def_enum_default_membership(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="Severity",
        field_key="severity", field_type="single_select",
        options=[{"name": "Minor"}, {"name": "Major"}],
    )
    fid = uuid.UUID(field["id"])
    minor_id = field["options"][0]["id"]
    major_id = field["options"][1]["id"]
    # Valid active option id.
    ok = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=fid,
        patch=FieldDefPatch(default_value=minor_id),
    )
    assert ok["default_value"] == minor_id
    # Unknown option id.
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(default_value=str(uuid.uuid4())),
        )
    assert excinfo.value.code == "invalid_field_config"
    # Inactive option cannot become the default.
    await service.update_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid,
        option_id=uuid.UUID(major_id), patch=OptionPatch(is_active=False),
    )
    with pytest.raises(BusinessRuleError):
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            patch=FieldDefPatch(default_value=major_id),
        )
    # multi_select: array of ids.
    multi = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="Modules",
        field_key="modules", field_type="multi_select",
        options=[{"name": "api"}, {"name": "web"}],
    )
    ids = [option["id"] for option in multi["options"]]
    ok_multi = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(multi["id"]),
        patch=FieldDefPatch(default_value=ids),
    )
    assert ok_multi["default_value"] == ids
    # Clearing the default with explicit null.
    cleared = await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=fid,
        patch=FieldDefPatch(default_value=None),
    )
    assert cleared["default_value"] is None


async def test_update_field_def_if_match_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="N",
        field_key="k", field_type="text",
    )
    stale = field["updated_at"]
    await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
        patch=FieldDefPatch(name="N2"),
    )
    with pytest.raises(ConflictError):
        await service.update_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
            patch=FieldDefPatch(name="N3"), if_match=stale,
        )


async def test_delete_field_def_cascades_options(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="Severity",
        field_key="severity", field_type="single_select",
        options=[{"name": "Minor"}, {"name": "Major"}],
    )
    fid = uuid.UUID(field["id"])
    result = await service.delete_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=fid
    )
    assert result == {"id": field["id"], "deleted": True}
    async with session_factory() as session:
        assert await session.scalar(select(CustomFieldDef).where(CustomFieldDef.id == fid)) is None
        options = (
            await session.execute(select(CustomFieldOption).where(CustomFieldOption.field_def_id == fid))
        ).scalars().all()
    assert options == []
    events = await _realtime_events(session_factory, "custom_field.updated")
    assert events[-1]["data"]["change"] == "deleted"
    assert events[-1]["data"]["id"] == field["id"]


async def test_delete_field_def_auth(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    field = await service.create_field_def(
        actor=admin, workspace_id=workspace.id, name="N",
        field_key="k", field_type="text",
    )
    with pytest.raises(ForbiddenError):
        await service.delete_field_def(
            actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"])
        )


async def test_list_field_defs_filters_and_visibility(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    project = await _create_project(session_factory, workspace)
    await service.create_field_def(
        actor=admin, workspace_id=workspace.id, name="WS field",
        field_key="ws_field", field_type="text",
    )
    await service.create_field_def(
        actor=admin, workspace_id=workspace.id, name="P field",
        field_key="p_field", field_type="text", project_id=project.id,
    )
    scoped, _ = await service.list_field_defs(
        viewer=member, workspace_id=workspace.id, project_id=project.id
    )
    assert {item["field_key"] for item in scoped} == {"ws_field", "p_field"}
    all_fields, cursor = await service.list_field_defs(viewer=member, workspace_id=workspace.id)
    assert len(all_fields) == 2
    assert cursor is None
    # is_active filter + pagination smoke.
    page, cursor = await service.list_field_defs(
        viewer=member, workspace_id=workspace.id, limit=1
    )
    assert len(page) == 1 and cursor is not None


# ===========================================================================
# enum options CRUD
# ===========================================================================


async def _select_field(session_factory, service, workspace, member, field_type="single_select"):
    return await service.create_field_def(
        actor=member, workspace_id=workspace.id,
        name=f"S-{uuid.uuid4().hex[:6]}", field_key=f"s_{uuid.uuid4().hex[:8]}",
        field_type=field_type,
    )


async def test_create_option_and_event(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    option = await service.create_option(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
        name="Critical", color="#e5484d", position=3,
    )
    assert option["name"] == "Critical"
    assert option["field_def_id"] == field["id"]
    events = await _realtime_events(session_factory, "custom_field_option.updated")
    assert len(events) == 1
    assert events[0]["data"]["field_def_id"] == field["id"]
    assert events[0]["data"]["option"]["name"] == "Critical"
    assert events[0]["data"]["change"] == "created"


async def test_create_option_rejects_non_select_and_inactive(session_factory):
    workspace, member, service = await _setup(session_factory)
    text_field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="T",
        field_key="t1", field_type="text",
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_option(
            actor=member, workspace_id=workspace.id,
            field_def_id=uuid.UUID(text_field["id"]), name="x",
        )
    assert excinfo.value.code == "invalid_field_config"
    inactive = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="S",
        field_key="s1", field_type="single_select",
    )
    await service.update_field_def(
        actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(inactive["id"]),
        patch=FieldDefPatch(is_active=False),
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_option(
            actor=member, workspace_id=workspace.id,
            field_def_id=uuid.UUID(inactive["id"]), name="x",
        )
    assert excinfo.value.code == "field_inactive"


async def test_create_option_duplicate_name_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    fid = uuid.UUID(field["id"])
    await service.create_option(actor=member, workspace_id=workspace.id, field_def_id=fid, name="A")
    with pytest.raises(ConflictError) as excinfo:
        await service.create_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid, name="A"
        )
    assert excinfo.value.code == "conflict"
    with pytest.raises(ValidationError):
        await service.create_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid, name=""
        )
    with pytest.raises(ValidationError):
        await service.create_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            name="B", color="orange",
        )


async def test_update_option_and_if_match(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    fid = uuid.UUID(field["id"])
    option = await service.create_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid, name="A"
    )
    oid = uuid.UUID(option["id"])
    updated = await service.update_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid, option_id=oid,
        patch=OptionPatch(name="A2", color="#abcdef", position=9.5, is_active=False),
    )
    assert updated["name"] == "A2"
    assert updated["color"] == "#abcdef"
    assert updated["position"] == 9.5
    assert updated["is_active"] is False
    # No-op path.
    same = await service.update_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid, option_id=oid,
        patch=OptionPatch(name="A2"),
    )
    assert same["name"] == "A2"
    # If-Match: stale version conflicts.
    with pytest.raises(ConflictError):
        await service.update_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid, option_id=oid,
            patch=OptionPatch(name="A3"), if_match=option["updated_at"],
        )
    events = await _realtime_events(session_factory, "custom_field_option.updated")
    assert events[-1]["data"]["change"] == "updated"


async def test_update_option_rename_conflict(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    fid = uuid.UUID(field["id"])
    a = await service.create_option(actor=member, workspace_id=workspace.id, field_def_id=fid, name="A")
    b = await service.create_option(actor=member, workspace_id=workspace.id, field_def_id=fid, name="B")
    with pytest.raises(ConflictError):
        await service.update_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            option_id=uuid.UUID(b["id"]), patch=OptionPatch(name="A"),
        )
    # Option of another field cannot be addressed through this field.
    with pytest.raises(NotFoundError):
        await service.update_option(
            actor=member, workspace_id=workspace.id, field_def_id=uuid.UUID(field["id"]),
            option_id=uuid.uuid4(), patch=OptionPatch(name="X"),
        )
    assert a["id"] != b["id"]


async def test_delete_option(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    fid = uuid.UUID(field["id"])
    option = await service.create_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid, name="A"
    )
    result = await service.delete_option(
        actor=member, workspace_id=workspace.id, field_def_id=fid,
        option_id=uuid.UUID(option["id"]),
    )
    assert result == {"id": option["id"], "deleted": True}
    events = await _realtime_events(session_factory, "custom_field_option.updated")
    assert events[-1]["data"]["change"] == "deleted"
    assert events[-1]["data"]["id"] == option["id"]


async def test_options_auth_matrix(session_factory):
    workspace, admin, service = await _setup(session_factory)
    member = await _second_member(session_factory, workspace, role="member")
    field = await _select_field(session_factory, service, workspace, admin)
    fid = uuid.UUID(field["id"])
    with pytest.raises(ForbiddenError):
        await service.create_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid, name="X"
        )
    # Reading options is allowed to any workspace member.
    option = await service.create_option(
        actor=admin, workspace_id=workspace.id, field_def_id=fid, name="Y"
    )
    items, _ = await service.list_options(viewer=member, workspace_id=workspace.id, field_def_id=fid)
    assert [item["name"] for item in items] == ["Y"]
    with pytest.raises(ForbiddenError):
        await service.delete_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid,
            option_id=uuid.UUID(option["id"]),
        )


async def test_list_options_pagination(session_factory):
    workspace, member, service = await _setup(session_factory)
    field = await _select_field(session_factory, service, workspace, member)
    fid = uuid.UUID(field["id"])
    for i in range(3):
        await service.create_option(
            actor=member, workspace_id=workspace.id, field_def_id=fid, name=f"opt-{i}"
        )
    page, cursor = await service.list_options(
        viewer=member, workspace_id=workspace.id, field_def_id=fid, limit=2
    )
    assert len(page) == 2 and cursor is not None
    rest, cursor2 = await service.list_options(
        viewer=member, workspace_id=workspace.id, field_def_id=fid, limit=2, cursor=cursor
    )
    assert len(rest) == 1 and cursor2 is None


# ===========================================================================
# workspace resolution (SECURITY DEFINER lookups) + RLS floor
# ===========================================================================


async def test_resolve_workspace_functions(session_factory):
    workspace, member, service = await _setup(session_factory)
    label = await service.create_label(
        actor=member, workspace_id=workspace.id, name="bug", color="#ffffff"
    )
    field = await service.create_field_def(
        actor=member, workspace_id=workspace.id, name="N",
        field_key="k", field_type="text",
    )
    assert await service.resolve_label_workspace(uuid.UUID(label["id"])) == workspace.id
    assert await service.resolve_field_def_workspace(uuid.UUID(field["id"])) == workspace.id
    assert await service.resolve_label_workspace(uuid.uuid4()) is None
    assert await service.resolve_field_def_workspace(uuid.uuid4()) is None

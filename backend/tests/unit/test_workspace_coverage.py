"""Edge-branch coverage for the workspace services (settings key validation,
restore/delete guards, list cursors, scoped sweeps, FK re-raise paths)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.workspace.invitations import InvitationService, _effective_status
from mesh.workspace.service import (
    WorkspacePatch,
    WorkspaceService,
    _violates,
    change_inbox_prefix,
    occupy_project_prefix,
)

pytestmark = pytest.mark.unit


async def _seed_user(session_factory, email: str) -> User:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": email},
            )
        ).scalar_one()
    return User(id=user_id, email=email, display_name="U")


async def _setup(session_factory, slug: str):
    service = WorkspaceService(session_factory)
    user = await _seed_user(session_factory, f"{slug}@corp.com")
    created = await service.create_workspace(user=user, name="Cov", slug=slug)
    from sqlalchemy import select

    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == created["id"], Member.user_id == user.id
            )
        )
    return service, user, created, member


# --- settings known-key validation (workspace.md §2.2) ---------------------------


@pytest.mark.parametrize(
    "settings,code",
    [
        ({"default_status_set": 5}, "validation_error"),
        ({"default_project_visibility": 1}, "validation_error"),
        ({"new_member_default_role": 2}, "validation_error"),
        ({"default_priorities": [1, 2]}, "validation_error"),
        ({"default_priorities": "high"}, "validation_error"),
        ({"inbox_issue_prefix": 7}, "validation_error"),
        ({"invitation_max_uses_cap": "ten"}, "validation_error"),
        ({"invitation_max_uses_cap": True}, "validation_error"),
        ({"invitation_max_lifetime_hours_cap": 0}, "validation_error"),
        ({"seat_limit": "fifty"}, "validation_error"),
        ({"feature_flags": "on"}, "validation_error"),
    ],
)
async def test_settings_known_key_type_errors(session_factory, settings, code):
    service, user, _created, member = await _setup(
        session_factory, f"set-err-{uuid.uuid4().hex[:8]}"
    )
    async with session_factory() as session:
        workspace = await session.get(_workspace_model(), _uuid(_created["id"]))
    with pytest.raises((ValidationError, BusinessRuleError)) as excinfo:
        await service.update_workspace(
            actor=member, workspace=workspace, patch=WorkspacePatch(settings=settings)
        )
    assert excinfo.value.code == code


async def test_settings_valid_theme_and_flags_merge(session_factory):
    service, user, created, member = await _setup(session_factory, "set-ok")
    async with session_factory() as session:
        workspace = await session.get(_workspace_model(), _uuid(created["id"]))
    updated = await service.update_workspace(
        actor=member,
        workspace=workspace,
        patch=WorkspacePatch(
            settings={
                "default_theme": "dark",
                "default_status_set": "extended",
                "default_priorities": ["low", "high"],
                "new_member_default_role": "guest",
                "feature_flags": {"autopilot": True},
                "seat_limit": None,
            }
        ),
    )
    assert updated["settings"]["default_theme"] == "dark"
    assert updated["settings"]["default_priorities"] == ["low", "high"]
    assert updated["settings"]["feature_flags"] == {"autopilot": True}

    with pytest.raises(BusinessRuleError) as theme:
        await service.update_workspace(
            actor=member,
            workspace=workspace,
            patch=WorkspacePatch(settings={"default_theme": "neon"}),
        )
    assert theme.value.code == "validation_error"
    assert theme.value.status_code == 422


# --- lifecycle guards --------------------------------------------------------------


async def test_update_deleted_workspace_404(session_factory):
    service, user, created, member = await _setup(session_factory, "upd-del")
    await service.delete_workspace(
        actor=member, workspace=_ws_obj(created), confirm_slug="upd-del"
    )
    async with session_factory() as session:
        workspace = await session.get(_workspace_model(), _uuid(created["id"]))
    with pytest.raises(NotFoundError):
        await service.update_workspace(
            actor=member, workspace=workspace, patch=WorkspacePatch(name="X")
        )


async def test_delete_deleted_workspace_404(session_factory):
    service, user, created, member = await _setup(session_factory, "del-del")
    await service.delete_workspace(
        actor=member, workspace=_ws_obj(created), confirm_slug="del-del"
    )
    async with session_factory() as session:
        workspace = await session.get(_workspace_model(), _uuid(created["id"]))
    with pytest.raises(NotFoundError):
        await service.delete_workspace(
            actor=member, workspace=workspace, confirm_slug="del-del"
        )


async def test_restore_guards(session_factory):
    service, user, created, member = await _setup(session_factory, "res-guards")

    # Unknown workspace → 404.
    with pytest.raises(NotFoundError):
        await service.restore_workspace(actor=member, workspace_id=uuid.uuid4())

    # Actor not in the workspace → 404.
    outsider_user = await _seed_user(session_factory, "res-outsider@corp.com")
    async with session_factory() as session, session.begin():
        other_ws_id = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('O', 'res-other') RETURNING id")
            )
        ).scalar_one()
        outsider = Member(
            workspace_id=other_ws_id,
            member_type="human",
            user_id=outsider_user.id,
            role="owner",
        )
        session.add(outsider)
        await session.flush()
        outsider_id = outsider.id
    async with session_factory() as session:
        from sqlalchemy import select

        outsider_member = await session.scalar(select(Member).where(Member.id == outsider_id))
    await service.delete_workspace(
        actor=member, workspace=_ws_obj(created), confirm_slug="res-guards"
    )
    with pytest.raises(NotFoundError):
        await service.restore_workspace(
            actor=outsider_member, workspace_id=_uuid(created["id"])
        )

    # Admin (not owner) → 403.
    async with session_factory() as session, session.begin():
        admin_user = await _seed_user_in(session, "res-admin@corp.com")
        admin = Member(
            workspace_id=_uuid(created["id"]),
            member_type="human",
            user_id=admin_user,
            role="admin",
        )
        session.add(admin)
        await session.flush()
        admin_id = admin.id
    from sqlalchemy import select

    async with session_factory() as session:
        admin_member = await session.scalar(select(Member).where(Member.id == admin_id))
    with pytest.raises(ForbiddenError):
        await service.restore_workspace(
            actor=admin_member, workspace_id=_uuid(created["id"])
        )

    # Restored → calling restore again is a no-op returning the workspace.
    restored = await service.restore_workspace(
        actor=member, workspace_id=_uuid(created["id"])
    )
    assert restored["slug"] == "res-guards"
    again = await service.restore_workspace(
        actor=member, workspace_id=_uuid(created["id"])
    )
    assert again["slug"] == "res-guards"


async def _seed_user_in(session, email: str):
    return (
        await session.execute(
            text("INSERT INTO users (email, display_name) VALUES (:e, 'A') RETURNING id"),
            {"e": email},
        )
    ).scalar_one()


# --- prefix registry FK re-raise + _violates helper ---------------------------------


async def test_prefix_helpers_reraise_foreign_integrity_errors(session_factory):
    """Non-unique violations (FK here) propagate unchanged, not as 409/422."""
    missing_ws = uuid.uuid4()
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await change_inbox_prefix(session, workspace_id=missing_ws, new_key="NEW")
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await occupy_project_prefix(
                session,
                workspace_id=missing_ws,
                key="PRJ",
                project_id=uuid.uuid4(),
            )


def test_violates_matches_constraint_name_and_message():
    named = SimpleNamespace(constraint_name="uq_workspaces_slug")
    assert _violates(IntegrityError("stmt", {}, None), "x") is False
    assert _violates(SimpleNamespace(orig=named), "uq_workspaces_slug") is True
    unnamed = SimpleNamespace(orig=None)
    assert _violates(unnamed, "uq_workspaces_slug") is False


# --- invitation list cursor + scoped sweep + effective status ----------------------


async def test_invitation_list_pagination_cursor(session_factory):
    ws_service, user, created, member = await _setup(session_factory, "inv-page")
    inv_service = InvitationService(session_factory)
    for _ in range(3):
        await inv_service.create_invitations(
            actor=member, workspace_id=_uuid(created["id"])
        )
    page1, cursor = await inv_service.list_invitations(
        workspace_id=_uuid(created["id"]), limit=2
    )
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = await inv_service.list_invitations(
        workspace_id=_uuid(created["id"]), limit=2, cursor=cursor
    )
    assert len(page2) == 1
    assert cursor2 is None
    assert {i["id"] for i in page1}.isdisjoint({i["id"] for i in page2})


async def test_sweep_scoped_to_workspace(session_factory):
    ws_service = WorkspaceService(session_factory)
    inv_service = InvitationService(session_factory)
    user_a = await _seed_user(session_factory, "sweep-a@corp.com")
    user_b = await _seed_user(session_factory, "sweep-b@corp.com")
    created_a = await ws_service.create_workspace(user=user_a, name="A", slug="sweep-scope-a")
    created_b = await ws_service.create_workspace(user=user_b, name="B", slug="sweep-scope-b")
    async with session_factory() as session:
        from sqlalchemy import select

        member_a = await session.scalar(
            select(Member).where(
                Member.workspace_id == created_a["id"], Member.user_id == user_a.id
            )
        )
        member_b = await session.scalar(
            select(Member).where(
                Member.workspace_id == created_b["id"], Member.user_id == user_b.id
            )
        )
    inv_a = (
        await inv_service.create_invitations(
            actor=member_a, workspace_id=_uuid(created_a["id"])
        )
    )[0]
    inv_b = (
        await inv_service.create_invitations(
            actor=member_b, workspace_id=_uuid(created_b["id"])
        )
    )[0]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id IN (:a, :b)"
            ),
            {"a": inv_a["id"], "b": inv_b["id"]},
        )
    # Scoped sweep touches only workspace A.
    swept = await inv_service.sweep_expired(workspace_id=_uuid(created_a["id"]))
    assert swept == 1
    from sqlalchemy import select

    async with session_factory() as session:
        from mesh.db.models.workspace import WorkspaceInvitation

        status_b = await session.scalar(
            select(WorkspaceInvitation.status).where(
                WorkspaceInvitation.id == inv_b["id"]
            )
        )
    assert status_b == "active"  # B untouched by the scoped sweep


def test_effective_status_branches():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    future = now + timedelta(days=1)
    past = now - timedelta(days=1)
    active_fresh = SimpleNamespace(status="active", expires_at=future)
    assert _effective_status(active_fresh, now=now) == "active"
    active_stale = SimpleNamespace(status="active", expires_at=past)
    assert _effective_status(active_stale, now=now) == "expired"
    revoked = SimpleNamespace(status="revoked", expires_at=future)
    assert _effective_status(revoked, now=now) == "revoked"


# --- helpers ------------------------------------------------------------------------


def _uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _workspace_model():
    from mesh.db.models.workspace import Workspace

    return Workspace


def _ws_obj(created: dict):
    from mesh.db.models.workspace import Workspace

    return Workspace(
        id=_uuid(created["id"]),
        name=created["name"],
        slug=created["slug"],
        timezone=created.get("timezone", "UTC"),
        settings=created.get("settings", {}),
    )

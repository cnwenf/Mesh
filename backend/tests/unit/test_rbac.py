"""RBAC adjudicator tests (auth.md §2.7 matrix, workspace.md §3.4, README §6.2).

The adjudicator is the middleware link between "authenticated user" (auth
core) and "may touch this workspace's resources": membership gate → role
matrix → guest resource hook. Non-members get 404, never 403 — workspace
existence must not leak (workspace.md §5.3).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mesh.auth.rbac import (
    PERMISSION_MATRIX,
    ROLE_RANK,
    WorkspaceContext,
    assert_guest_project_visible,
    resolve_workspace_by_slug,
    resolve_workspace_context,
    role_satisfies,
)
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, NotFoundError

pytestmark = pytest.mark.unit


async def _seed_user(db_session, email: str) -> User:
    async with db_session.begin():
        user_id = (
            await db_session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": email},
            )
        ).scalar_one()
    user = User(id=user_id, email=email, display_name="U")
    return user


async def _seed_member(db_session, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str,
                       status: str = "active") -> uuid.UUID:
    async with db_session.begin():
        return (
            await db_session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, :role, :status) RETURNING id"
                ),
                {"ws": workspace_id, "u": user_id, "role": role, "status": status},
            )
        ).scalar_one()


# --- role matrix --------------------------------------------------------------


def test_role_matrix_matches_auth_spec():
    # auth.md §2.7 resource × role matrix (built-in roles).
    assert PERMISSION_MATRIX["workspace:billing"] == frozenset({"owner"})
    assert PERMISSION_MATRIX["workspace:settings"] == frozenset({"owner", "admin"})
    assert PERMISSION_MATRIX["workspace:manage_members"] == frozenset({"owner", "admin"})
    assert PERMISSION_MATRIX["project:manage"] == frozenset({"owner", "admin"})
    assert PERMISSION_MATRIX["issue:write"] == frozenset({"owner", "admin", "member"})
    assert PERMISSION_MATRIX["agent:trigger"] == frozenset({"owner", "admin", "member"})
    assert PERMISSION_MATRIX["agent:manage"] == frozenset({"owner", "admin"})
    assert PERMISSION_MATRIX["comment:write"] == frozenset({"owner", "admin", "member", "guest"})
    # guest reads issues only through the project-visibility hook (§5.3).
    assert "guest" in PERMISSION_MATRIX["issue:read"]


def test_role_satisfies():
    assert role_satisfies("owner", "workspace:billing")
    assert not role_satisfies("admin", "workspace:billing")
    assert role_satisfies("admin", "workspace:settings")
    assert not role_satisfies("member", "workspace:settings")
    assert role_satisfies("member", "issue:write")
    assert not role_satisfies("guest", "issue:write")
    assert not role_satisfies("guest", "agent:trigger")


def test_role_rank_ordering():
    assert ROLE_RANK["guest"] < ROLE_RANK["member"] < ROLE_RANK["admin"] < ROLE_RANK["owner"]


# --- workspace membership gate -------------------------------------------------


async def test_member_resolves_context(db_session, workspace_factory):
    workspace = await workspace_factory()
    user = await _seed_user(db_session, "member@corp.com")
    member_id = await _seed_member(db_session, workspace.id, user.id, "admin")

    context = await resolve_workspace_context(
        db_session, user=user, workspace_id=workspace.id
    )
    assert isinstance(context, WorkspaceContext)
    assert context.workspace.id == workspace.id
    assert context.member.id == member_id
    assert context.member.role == "admin"


async def test_non_member_gets_404_not_403(db_session, workspace_factory):
    workspace = await workspace_factory()
    outsider = await _seed_user(db_session, "outsider@corp.com")
    with pytest.raises(NotFoundError):
        await resolve_workspace_context(db_session, user=outsider, workspace_id=workspace.id)


async def test_unknown_workspace_and_non_member_errors_are_identical(db_session, workspace_factory):
    """Existence must not leak: unknown id and foreign id produce the same 404."""
    workspace = await workspace_factory()
    outsider = await _seed_user(db_session, "leak-check@corp.com")
    with pytest.raises(NotFoundError) as foreign:
        await resolve_workspace_context(db_session, user=outsider, workspace_id=workspace.id)
    with pytest.raises(NotFoundError) as missing:
        await resolve_workspace_context(
            db_session, user=outsider, workspace_id=uuid.uuid4()
        )
    assert foreign.value.message == missing.value.message


async def test_soft_deleted_workspace_is_404_even_for_owner(db_session, workspace_factory):
    workspace = await workspace_factory()
    owner = await _seed_user(db_session, "owner-deleted@corp.com")
    await _seed_member(db_session, workspace.id, owner.id, "owner")
    async with db_session.begin():
        await db_session.execute(
            text("UPDATE workspaces SET deleted_at = now() WHERE id = :ws"),
            {"ws": workspace.id},
        )
    with pytest.raises(NotFoundError):
        await resolve_workspace_context(db_session, user=owner, workspace_id=workspace.id)


async def test_disabled_member_treated_as_non_member(db_session, workspace_factory):
    workspace = await workspace_factory()
    user = await _seed_user(db_session, "disabled@corp.com")
    await _seed_member(db_session, workspace.id, user.id, "member", status="disabled")
    with pytest.raises(NotFoundError):
        await resolve_workspace_context(db_session, user=user, workspace_id=workspace.id)


async def test_permission_check_raises_403_for_member_with_insufficient_role(
    db_session, workspace_factory
):
    workspace = await workspace_factory()
    user = await _seed_user(db_session, "plain-member@corp.com")
    await _seed_member(db_session, workspace.id, user.id, "member")
    # Membership is fine (no permission requested)…
    await resolve_workspace_context(db_session, user=user, workspace_id=workspace.id)
    # …but settings management requires admin+.
    with pytest.raises(ForbiddenError):
        await resolve_workspace_context(
            db_session, user=user, workspace_id=workspace.id, permission="workspace:settings"
        )


async def test_permission_check_passes_for_admin(db_session, workspace_factory):
    workspace = await workspace_factory()
    user = await _seed_user(db_session, "admin-perm@corp.com")
    await _seed_member(db_session, workspace.id, user.id, "admin")
    context = await resolve_workspace_context(
        db_session, user=user, workspace_id=workspace.id, permission="workspace:settings"
    )
    assert context.member.role == "admin"


# --- slug resolution + redirects ------------------------------------------------


async def test_slug_resolution_current_and_historic(db_session, workspace_factory):
    workspace = await workspace_factory(slug="acme-team")
    user = await _seed_user(db_session, "slug@corp.com")
    await _seed_member(db_session, workspace.id, user.id, "member")

    context = await resolve_workspace_by_slug(db_session, user=user, slug="acme-team")
    resolved_id = context.workspace.id
    assert resolved_id == workspace.id
    await db_session.rollback()  # end the resolver's implicit transaction

    # Rename: old slug now resolves through workspace_slug_history.
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO workspace_slug_history (workspace_id, old_slug) "
                "VALUES (:ws, 'acme-team')"
            ),
            {"ws": workspace.id},
        )
        await db_session.execute(
            text("UPDATE workspaces SET slug = 'acme-corp' WHERE id = :ws"),
            {"ws": workspace.id},
        )
    redirected = await resolve_workspace_by_slug(db_session, user=user, slug="acme-team")
    assert redirected.workspace.id == workspace.id
    assert redirected.workspace.slug == "acme-corp"


async def test_slug_unknown_is_404(db_session, workspace_factory):
    await workspace_factory(slug="some-ws")
    user = await _seed_user(db_session, "slug-404@corp.com")
    with pytest.raises(NotFoundError):
        await resolve_workspace_by_slug(db_session, user=user, slug="never-existed")


# --- guest project visibility hook ----------------------------------------------


async def test_guest_project_visibility_hook(db_session, workspace_factory):
    workspace = await workspace_factory()
    guest_user = await _seed_user(db_session, "guest@corp.com")
    guest_id = await _seed_member(db_session, workspace.id, guest_user.id, "guest")
    member_user = await _seed_user(db_session, "regular@corp.com")
    member_id = await _seed_member(db_session, workspace.id, member_user.id, "member")
    project_id = uuid.uuid4()  # projects table lands later; the hook only checks grants

    member = Member(id=member_id, workspace_id=workspace.id, member_type="human", role="member")
    # Non-guest roles are unrestricted by the hook.
    await assert_guest_project_visible(db_session, member=member, project_id=project_id)

    guest = Member(id=guest_id, workspace_id=workspace.id, member_type="human", role="guest")
    with pytest.raises(NotFoundError):
        await assert_guest_project_visible(db_session, member=guest, project_id=project_id)
    await db_session.rollback()  # end the implicit transaction before seeding

    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO member_project_access (workspace_id, member_id, project_id, permission) "
                "VALUES (:ws, :m, :p, 'read')"
            ),
            {"ws": workspace.id, "m": guest_id, "p": project_id},
        )
    await assert_guest_project_visible(db_session, member=guest, project_id=project_id)
    # A different project stays invisible.
    with pytest.raises(NotFoundError):
        await assert_guest_project_visible(db_session, member=guest, project_id=uuid.uuid4())

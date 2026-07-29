"""RBAC adjudicator — workspace membership gate + role matrix (auth.md §2.7).

The middleware chain per README §6.14 is: parse Bearer → workspace membership
→ RBAC → rate limit. The auth core supplies the first link (token → user);
this module supplies the next two for tenant-scoped endpoints:

1. **Membership gate** — the principal must have an ``active`` roster entry in
   the workspace. Anything else — unknown id, foreign id, soft-deleted
   workspace, disabled/removed member — is the SAME 404, so workspace
   existence never leaks (workspace.md §5.3).
2. **Role matrix** — endpoints declare the permission they need; the
   declarative matrix (auth.md §2.7) decides. Unmet → 403.

Guest project-level visibility (M12) is a resource-level hook the project
module consults: guests see only projects explicitly shared through
``member_project_access``.

Every resolver sets the tenant GUC (``mesh.workspace_id``) on the session
before reading tenant tables, so the same code path is correct under RLS
(restricted app role) and without it (owner role / unit tests).
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)

# Throttle for the users.last_active_workspace_id backfill (per-process): once
# a (user, workspace) pair has been written this run, skip repeat writes.
# Bounded LRU (evicts oldest pairs past the cap) so a long-lived process
# serving many distinct pairs cannot grow this set without limit (LOW fix).
_LAST_WS_WRITTEN_MAX = 4096
_LAST_WS_WRITTEN: OrderedDict[tuple[uuid.UUID, uuid.UUID], None] = OrderedDict()


def _remember_ws_write(pair: tuple[uuid.UUID, uuid.UUID]) -> None:
    _LAST_WS_WRITTEN[pair] = None
    while len(_LAST_WS_WRITTEN) > _LAST_WS_WRITTEN_MAX:
        _LAST_WS_WRITTEN.popitem(last=False)

# Role seniority (member.md §2.2 fixed enum; no custom roles — YAGNI).
ROLE_RANK: dict[str, int] = {"guest": 0, "member": 1, "admin": 2, "owner": 3}

# auth.md §2.7 resource × role permission matrix (built-in roles). Guest's
# issue:read is additionally restricted by the project-visibility hook.
PERMISSION_MATRIX: dict[str, frozenset[str]] = {
    "workspace:settings": frozenset({"owner", "admin"}),
    "workspace:manage_members": frozenset({"owner", "admin"}),
    "workspace:billing": frozenset({"owner"}),
    "project:manage": frozenset({"owner", "admin"}),
    "issue:read": frozenset({"owner", "admin", "member", "guest"}),
    "issue:write": frozenset({"owner", "admin", "member"}),
    "comment:write": frozenset({"owner", "admin", "member", "guest"}),
    "chat:write": frozenset({"owner", "admin", "member"}),  # MES-67 L3: guest 不得聊天/触发执行
    "agent:trigger": frozenset({"owner", "admin", "member"}),
    "agent:manage": frozenset({"owner", "admin"}),
    "autopilot:manage": frozenset({"owner", "admin"}),
    "token:manage": frozenset({"owner", "admin", "member"}),  # members: own tokens only
}

# One generic message for every "you cannot see this workspace" outcome —
# unknown id, foreign id and deleted workspace must be indistinguishable.
_WORKSPACE_NOT_FOUND = "workspace not found"


def role_satisfies(role: str, permission: str) -> bool:
    """True when ``role`` holds ``permission`` in the §2.7 matrix."""
    return role in PERMISSION_MATRIX.get(permission, frozenset())


@dataclass(frozen=True)
class WorkspaceContext:
    """A resolved tenant access: the workspace and the caller's roster entry."""

    workspace: Workspace
    member: Member


async def resolve_workspace_context(
    session: AsyncSession,
    *,
    user: User,
    workspace_id: uuid.UUID,
    permission: str | None = None,
) -> WorkspaceContext:
    """Gate a user into a workspace; enforce ``permission`` when given.

    Sets the tenant GUC, then loads the non-deleted workspace and the user's
    active member row. Raises 404 for every invisible case and 403 when the
    role matrix denies ``permission``.
    """
    await set_tenant_context(session, workspace_id)
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    if workspace is None:
        raise NotFoundError(_WORKSPACE_NOT_FOUND)
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == user.id,
            Member.status == "active",
        )
    )
    if member is None:
        # Same 404 as an unknown workspace — existence must not leak (§5.3).
        raise NotFoundError(_WORKSPACE_NOT_FOUND)
    if permission is not None and not role_satisfies(member.role, permission):
        raise ForbiddenError("insufficient role for this action")
    await _backfill_last_active_workspace(session, user=user, workspace_id=workspace.id)
    return WorkspaceContext(workspace=workspace, member=member)


async def _backfill_last_active_workspace(
    session: AsyncSession, *, user: User, workspace_id: uuid.UUID
) -> None:
    """Best-effort users.last_active_workspace_id hint (search-command-palette.md §3.4).

    Throttled per process via ``_LAST_WS_WRITTEN``: each (user, workspace) pair
    writes at most once per run, and the UPDATE itself is a no-op when the
    value already matches (IS DISTINCT FROM). Never fails the request — the
    column is a restoration hint, authorization never reads it.
    """
    pair = (user.id, workspace_id)
    if pair in _LAST_WS_WRITTEN:
        return
    try:
        await session.execute(
            text(
                "UPDATE users SET last_active_workspace_id = :ws "
                "WHERE id = :uid AND last_active_workspace_id IS DISTINCT FROM :ws"
            ),
            {"ws": workspace_id, "uid": user.id},
        )
        _remember_ws_write(pair)
    except Exception:  # noqa: BLE001 — best-effort hint, never fail the request
        logger.debug("last_active_workspace_id backfill skipped", exc_info=True)


async def resolve_workspace_by_slug(
    session: AsyncSession,
    *,
    user: User,
    slug: str,
    permission: str | None = None,
) -> WorkspaceContext:
    """Resolve a workspace by current slug or a historic slug (W6 redirect).

    The historic-slug read goes through the SECURITY DEFINER function: the
    caller has no tenant context yet (the workspace is the unknown), and the
    RLS policy on ``workspace_slug_history`` is fail-closed without the GUC.
    The membership gate afterwards runs under the policies as usual.
    """
    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.slug == slug, Workspace.deleted_at.is_(None))
    )
    if workspace_id is None:
        # Old slugs redirect to the workspace that released them (§2.5).
        workspace_id = (
            await session.execute(
                text("SELECT mesh_workspace_id_by_old_slug(:slug)"), {"slug": slug}
            )
        ).scalar()
    if workspace_id is None:
        raise NotFoundError(_WORKSPACE_NOT_FOUND)
    return await resolve_workspace_context(
        session, user=user, workspace_id=workspace_id, permission=permission
    )


async def resolve_workspace_by_ref(
    session: AsyncSession,
    *,
    user: User,
    ref: str,
    permission: str | None = None,
) -> WorkspaceContext:
    """Resolve a workspace path/query reference: UUID **or slug** (§3.1).

    ``{ws}`` references are 「UUID 或 slug」 (search-command-palette.md §3.1,
    same shape as the issue/project collection endpoints): a UUID goes
    straight to the membership gate; any other value is resolved as a
    current slug, falling back to a historic slug (workspace.md §2.5).
    Unresolvable values are a 404 — never leak what shape of id exists.
    """
    try:
        parsed = uuid.UUID(ref)
    except ValueError:
        return await resolve_workspace_by_slug(
            session, user=user, slug=ref, permission=permission
        )
    return await resolve_workspace_context(
        session, user=user, workspace_id=parsed, permission=permission
    )


def require_workspace(permission: str | None = None):
    """FastAPI dependency factory for ``/workspaces/{workspace_id}`` routes.

    Usage: ``context: WorkspaceContext = Depends(require_workspace("workspace:settings"))``.
    The path segment accepts a UUID or a slug — see
    :func:`resolve_workspace_by_ref`.
    """

    async def _dependency(
        workspace_id: str,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> WorkspaceContext:
        return await resolve_workspace_by_ref(
            session, user=user, ref=workspace_id, permission=permission
        )

    return _dependency


async def assert_guest_project_visible(
    session: AsyncSession, *, member: Member, project_id: uuid.UUID
) -> None:
    """Guest project-level visibility hook (member.md M12 / workspace.md scope 4).

    Non-guest roles pass unconditionally; a guest may only see projects with an
    explicit ``member_project_access`` grant. Missing grant → 404 (project
    existence must not leak either). The caller's session must already carry
    the tenant GUC.
    """
    if member.role != "guest":
        return
    granted = await session.scalar(
        select(MemberProjectAccess.id).where(
            MemberProjectAccess.workspace_id == member.workspace_id,
            MemberProjectAccess.member_id == member.id,
            MemberProjectAccess.project_id == project_id,
        )
    )
    if granted is None:
        raise NotFoundError("project not found")

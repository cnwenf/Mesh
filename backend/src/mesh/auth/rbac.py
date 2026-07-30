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
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal
from mesh.db.models.member import Member, MemberProjectAccess
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


def assert_scope(actor: Member, permission: str) -> None:
    """Scope-intersection gate (auth.md §2.5.1: effective = scopes ∩ role).

    Credentials issued with a non-empty scope set (PAT / agent tokens, device
    sessions) are additionally restricted to the granted permissions — the
    role matrix is the ceiling, the scope set the floor. Web sessions carry
    an empty set (``principal_scopes`` unset) and stay purely role-based, so
    this is a no-op for browser traffic. The attribute is attached to the
    roster row by :func:`resolve_workspace_context` — it is request-scoped
    state, never persisted.
    """
    scopes = getattr(actor, "principal_scopes", None)
    if scopes and permission not in scopes:
        raise ForbiddenError(
            "token scope does not cover this action",
            code="forbidden",
            details={"required_scope": permission},
        )


@dataclass(frozen=True)
class WorkspaceContext:
    """A resolved tenant access: the workspace and the caller's roster entry."""

    workspace: Workspace
    member: Member


async def resolve_workspace_context(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    permission: str | None = None,
) -> WorkspaceContext:
    """Gate a principal into a workspace; enforce ``permission`` when given.

    Sets the tenant GUC, then loads the non-deleted workspace and the
    principal's active member row. Raises 404 for every invisible case and
    403 when the role matrix denies ``permission`` — or when a credential
    whose scope set (∩ role at issuance) does not cover the permission tries
    to use it (auth.md §2.5.1: effective permissions are scopes ∩ role).

    * Session principals resolve membership by ``members.user_id``; a device
      session (access JWT carrying a ``workspace_id`` binding, auth.md §2.4)
      may ONLY address its bound workspace — naming another → 403 (cli.md
      §4.2 R2-H1).
    * PAT/agent principals are workspace-scoped at issuance: the request must
      target ``principal.workspace_id`` (else the uniform 404), and the
      member row is the token's ``owner_member_id`` holder.
    """
    await set_tenant_context(session, workspace_id)

    if principal.kind in ("pat", "agent"):
        if principal.workspace_id != workspace_id:
            # A PAT cannot reach into other workspaces — same 404 as an
            # invisible workspace (no existence leak, §5.3).
            raise NotFoundError(_WORKSPACE_NOT_FOUND)
    elif principal.workspace_id is not None and principal.workspace_id != workspace_id:
        # Device session bound elsewhere — explicit 403 (cli.md §4.2).
        raise ForbiddenError("session is bound to a different workspace")

    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    if workspace is None:
        raise NotFoundError(_WORKSPACE_NOT_FOUND)

    if principal.kind in ("pat", "agent"):
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == principal.member_id,
                Member.status == "active",
            )
        )
    else:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.user_id == principal.user_id,
                Member.status == "active",
            )
        )
    if member is None:
        # Same 404 as an unknown workspace — existence must not leak (§5.3).
        raise NotFoundError(_WORKSPACE_NOT_FOUND)
    # Request-scoped scope set for the service-layer ∩ gates (assert_scope);
    # empty for web sessions ⇒ role-based only. Not persisted.
    member.principal_scopes = principal.scopes
    if permission is not None:
        if not role_satisfies(member.role, permission):
            raise ForbiddenError("insufficient role for this action")
        # Scope gate: non-empty scope sets (PAT/agent tokens, device
        # sessions) additionally restrict to the granted permissions —
        # web sessions carry an empty set and stay purely role-based.
        if principal.scopes and permission not in principal.scopes:
            raise ForbiddenError(
                "token scope does not cover this action",
                code="forbidden",
                details={"required_scope": permission},
            )
    await _backfill_last_active_workspace(
        session, principal=principal, workspace_id=workspace.id
    )
    return WorkspaceContext(workspace=workspace, member=member)


async def _backfill_last_active_workspace(
    session: AsyncSession, *, principal: AuthenticatedPrincipal, workspace_id: uuid.UUID
) -> None:
    """Best-effort users.last_active_workspace_id hint (search-command-palette.md §3.4).

    Throttled per process via ``_LAST_WS_WRITTEN``: each (user, workspace) pair
    writes at most once per run, and the UPDATE itself is a no-op when the
    value already matches (IS DISTINCT FROM). Never fails the request — the
    column is a restoration hint, authorization never reads it. Only
    user-backed principals (sessions, PATs) carry a ``user_id``; principals
    without one (e.g. agent credentials) are skipped — the hint is a
    per-human-user UI restoration aid.
    """
    if principal.user_id is None:
        return
    pair = (principal.user_id, workspace_id)
    if pair in _LAST_WS_WRITTEN:
        return
    try:
        await session.execute(
            text(
                "UPDATE users SET last_active_workspace_id = :ws "
                "WHERE id = :uid AND last_active_workspace_id IS DISTINCT FROM :ws"
            ),
            {"ws": workspace_id, "uid": principal.user_id},
        )
        _remember_ws_write(pair)
    except Exception:  # noqa: BLE001 — best-effort hint, never fail the request
        logger.debug("last_active_workspace_id backfill skipped", exc_info=True)


async def resolve_workspace_by_slug(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
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
        session, principal=principal, workspace_id=workspace_id, permission=permission
    )


async def resolve_workspace_by_ref(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
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
            session, principal=principal, slug=ref, permission=permission
        )
    return await resolve_workspace_context(
        session, principal=principal, workspace_id=parsed, permission=permission
    )


def require_workspace(permission: str | None = None):
    """FastAPI dependency factory for ``/workspaces/{workspace_id}`` routes.

    Usage: ``context: WorkspaceContext = Depends(require_workspace("workspace:settings"))``.
    The path segment accepts a UUID or a slug — see
    :func:`resolve_workspace_by_ref`; an unresolvable value is a 404 (not a
    400 — never leak what shape of id exists). Accepts every credential kind
    the unified Bearer gate routes (session JWT / mesh_pat_ / mesh_agt_;
    auth.md §2.5.1 review H7).
    """

    async def _dependency(
        workspace_id: str,
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        session: AsyncSession = Depends(get_session),
    ) -> WorkspaceContext:
        return await resolve_workspace_by_ref(
            session, principal=principal, ref=workspace_id, permission=permission
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

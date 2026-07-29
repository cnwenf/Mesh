"""Workspace HTTP routes (workspace.md §3.1).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Membership/RBAC come from the adjudicator (mesh.auth.rbac); write endpoints
additionally rate-limit per principal+IP (auth.md §3.6 general write class).
Dangerous operations (delete) require the owner role and a typed-slug
confirmation in the body.

The invitation preview endpoint is public by design (landing page) and
returns only limited fields; acceptance requires a logged-in user.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal, get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_by_slug
from mesh.db.models.user import User
from mesh.workspace.invitations import InvitationService
from mesh.workspace.schemas import (
    AcceptInvitationRequest,
    CreateInvitationRequest,
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    UpdateWorkspaceRequest,
)
from mesh.workspace.service import WorkspacePatch, WorkspaceService

router = APIRouter(prefix="/api/v1", tags=["workspace"])

# auth.md §3.6 — general API write class: 120 req/min per token/user.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"workspace-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _workspace_service(request: Request) -> WorkspaceService:
    return request.app.state.workspace_service


def _invitation_service(request: Request) -> InvitationService:
    return request.app.state.invitation_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


# --- workspaces -----------------------------------------------------------------


@router.post("/workspaces", status_code=201)
async def create_workspace(
    body: CreateWorkspaceRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _workspace_service(request)
    created = await service.create_workspace(
        user=user,
        name=body.name,
        slug=body.slug,
        timezone=body.timezone,
        logo_url=body.logo_url,
        settings=body.settings,
    )
    return {"data": created}


@router.get("/workspaces")
async def list_workspaces(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    user: User = Depends(get_current_user),
) -> dict:
    service = _workspace_service(request)
    items, next_cursor = await service.list_workspaces(user=user, limit=limit, cursor=cursor)
    return {"data": items, "next_cursor": next_cursor}


# NOTE: registered BEFORE /workspaces/{workspace_id} so the literal path wins.
@router.get("/workspaces/by-slug/{slug}")
async def get_workspace_by_slug(
    slug: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from mesh.workspace.service import workspace_to_dict

    context = await resolve_workspace_by_slug(session, principal=principal, slug=slug)
    return {"data": workspace_to_dict(context.workspace, my_role=context.member.role)}


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    from mesh.workspace.service import workspace_to_dict

    return {
        "data": workspace_to_dict(context.workspace, my_role=context.member.role)
    }


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    body: UpdateWorkspaceRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("workspace:settings")),
) -> dict:
    service = _workspace_service(request)
    updated = await service.update_workspace(
        actor=context.member,
        workspace=context.workspace,
        patch=WorkspacePatch(
            name=body.name,
            slug=body.slug,
            logo_url=body.logo_url,
            timezone=body.timezone,
            settings=body.settings,
        ),
    )
    return {"data": updated}


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    body: DeleteWorkspaceRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _workspace_service(request)
    await service.delete_workspace(
        actor=context.member,
        workspace=context.workspace,
        confirm_slug=body.confirm_slug,
    )
    return {"data": {"status": "deleted"}}


@router.post("/workspaces/{workspace_id}/restore")
async def restore_workspace(
    request: Request,
    workspace_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    import uuid

    from mesh.errors import NotFoundError

    try:
        parsed = uuid.UUID(workspace_id)
    except ValueError as exc:
        raise NotFoundError("workspace not found") from exc
    service = _workspace_service(request)
    # Membership + owner enforcement happens inside the service (the workspace
    # is soft-deleted, so the membership gate cannot resolve it up front).
    actor = await _request_actor(request, parsed, user)
    restored = await service.restore_workspace(actor=actor, workspace_id=parsed)
    return {"data": restored}


async def _request_actor(request: Request, workspace_id, user: User):
    """Load the caller's member row for service calls that predate context
    resolution (restore operates on a soft-deleted workspace)."""
    from sqlalchemy import select

    from mesh.db.models.member import Member
    from mesh.db.tenant import set_tenant_context

    factory = request.app.state.session_factory
    async with factory() as session:
        # members is RLS-protected; scope the read to this tenant up front.
        await set_tenant_context(session, workspace_id)
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.user_id == user.id,
                Member.status == "active",
            )
        )
    if member is None:
        from mesh.errors import NotFoundError

        raise NotFoundError("workspace not found")
    return member


# --- invitations ------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
async def create_invitations(
    body: CreateInvitationRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _invitation_service(request)
    results = await service.create_invitations(
        actor=context.member,
        workspace_id=context.workspace.id,
        emails=body.emails,
        role=body.role,
        max_uses=body.max_uses,
        expires_in_hours=body.expires_in_hours,
    )
    return {"data": results, "next_cursor": None}


@router.get("/workspaces/{workspace_id}/invitations")
async def list_invitations(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    service = _invitation_service(request)
    items, next_cursor = await service.list_invitations(
        workspace_id=context.workspace.id, limit=limit, cursor=cursor
    )
    return {"data": items, "next_cursor": next_cursor}


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}")
async def revoke_invitation(
    request: Request,
    invitation_id: str,
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    import uuid

    from mesh.errors import ValidationError

    try:
        parsed = uuid.UUID(invitation_id)
    except ValueError as exc:
        raise ValidationError("invalid invitation id") from exc
    service = _invitation_service(request)
    revoked = await service.revoke_invitation(
        actor=context.member, workspace_id=context.workspace.id, invitation_id=parsed
    )
    return {"data": revoked}


@router.post("/invitations/accept")
async def accept_invitation(
    body: AcceptInvitationRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    service = _invitation_service(request)
    result = await service.accept_invitation(user=user, token=body.token, **_client_meta(request))
    return {"data": result}


@router.get("/invitations/preview")
async def preview_invitation(request: Request, token: str) -> dict:
    """Public landing-page preview — limited fields only, no auth required."""
    service = _invitation_service(request)
    return {"data": await service.preview_invitation(token=token)}


# Member roster endpoints (list/detail/add/update/remove/reassign/project-access)
# live in the member module (mesh.member.routes) — member.md owns them; this
# module keeps workspace + invitation routes only.


__all__ = ["router"]

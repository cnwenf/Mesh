"""Member roster HTTP routes (member.md §3.1).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Reading the roster needs workspace membership; role/status changes, adding,
removing, reassignment and guest project sharing need ``admin``. The PATCH
endpoint is gated at membership only so a member can edit their OWN
``display_override`` — admin-or-self is enforced in the service (member.md
§3.4). Last-owner / agent-owner protections live server-side, never the client.

Presence (``GET /members/{id}/presence``) is spec-optional (§3.1 "(可选)") and
deferred: there is no presence source yet, and a workspace-less path cannot be
made RLS-safe without a definer bypass (YAGNI). ``member.presence`` stays
registered in the §6.7 vocabulary for when presence lands.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.auth.service import user_to_dict
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.member.schemas import (
    AddMemberRequest,
    GrantProjectAccessRequest,
    ReassignRequest,
    UpdateMemberRequest,
)
from mesh.member.service import UNSET, MemberPatch, MemberService

router = APIRouter(prefix="/api/v1", tags=["member"])

# auth.md §3.6 — general API write class: 120 req/min per token/user.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_NOT_FOUND = "member not found"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _member_service(request: Request) -> MemberService:
    return request.app.state.member_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"member-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(value: str) -> uuid.UUID:
    """Parse a path id; non-UUID → 404 (never leak what shape of id exists)."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(_NOT_FOUND) from exc


def _body_uuid(value: str, *, field: str) -> uuid.UUID:
    """Parse a body/query id; non-UUID → 400 validation_error."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: value[:64]}) from exc


# --- roster query + detail -----------------------------------------------------


@router.get("/workspaces/{workspace_id}/members")
async def list_members(
    request: Request,
    member_type: str = "all",
    status: str = "default",
    role: str | None = None,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _member_service(request)
    items, next_cursor = await service.list_members(
        workspace_id=context.workspace.id,
        member_type=member_type,
        status=status,
        role=role,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.get("/workspaces/{workspace_id}/agents/available")
async def list_available_agents(
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    service = _member_service(request)
    items, next_cursor = await service.list_available_agents(
        actor=context.member, workspace_id=context.workspace.id
    )
    return {"data": items, "next_cursor": next_cursor}


# NOTE: registered BEFORE /members/{member_id} so the literal path wins on POST.
@router.post("/workspaces/{workspace_id}/members/reassign")
async def reassign_issues(
    body: ReassignRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    result = await service.reassign_issues(
        actor=context.member,
        workspace_id=context.workspace.id,
        from_member_id=_body_uuid(body.from_member_id, field="from_member_id"),
        to_member_id=_body_uuid(body.to_member_id, field="to_member_id"),
        statuses=body.statuses,
        **_client_meta(request),
    )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/members", status_code=201)
async def add_member(
    body: AddMemberRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    created = await service.add_member(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_type=body.member_type,
        user_id=_body_uuid(body.user_id, field="user_id") if body.user_id else None,
        agent_id=_body_uuid(body.agent_id, field="agent_id") if body.agent_id else None,
        role=body.role,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/members/{member_id}")
async def get_member(
    request: Request,
    member_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _member_service(request)
    detail = await service.get_member(
        workspace_id=context.workspace.id, member_id=_path_uuid(member_id)
    )
    return {"data": detail}


@router.patch("/workspaces/{workspace_id}/members/{member_id}")
async def update_member(
    body: UpdateMemberRequest,
    request: Request,
    response: Response,
    member_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    # display_override is tri-state: omitted = keep, null = clear, string = set.
    display = (
        body.display_override if "display_override" in body.model_fields_set else UNSET
    )
    result = await service.update_member(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_id=_path_uuid(member_id),
        patch=MemberPatch(
            role=body.role,
            status=body.status,
            display_override=display,
        ),
        **_client_meta(request),
    )
    return {"data": result}


@router.delete("/workspaces/{workspace_id}/members/{member_id}")
async def remove_member(
    request: Request,
    response: Response,
    member_id: str,
    reassign_to: str | None = None,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    result = await service.remove_member(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_id=_path_uuid(member_id),
        reassign_to=_body_uuid(reassign_to, field="reassign_to") if reassign_to else None,
        **_client_meta(request),
    )
    return {"data": result}


# --- guest project-level visibility (M12) --------------------------------------


@router.get("/workspaces/{workspace_id}/members/{member_id}/project-access")
async def list_project_access(
    request: Request,
    member_id: str,
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    service = _member_service(request)
    items = await service.list_project_access(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_id=_path_uuid(member_id),
    )
    return {"data": items, "next_cursor": None}


@router.post("/workspaces/{workspace_id}/members/{member_id}/project-access", status_code=201)
async def grant_project_access(
    body: GrantProjectAccessRequest,
    request: Request,
    response: Response,
    member_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    result = await service.grant_project_access(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_id=_path_uuid(member_id),
        project_id=_body_uuid(body.project_id, field="project_id"),
        permission=body.permission,
    )
    return {"data": result}


@router.delete("/workspaces/{workspace_id}/members/{member_id}/project-access/{project_id}")
async def revoke_project_access(
    request: Request,
    response: Response,
    member_id: str,
    project_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _member_service(request)
    result = await service.revoke_project_access(
        actor=context.member,
        workspace_id=context.workspace.id,
        member_id=_path_uuid(member_id),
        project_id=_path_uuid(project_id),
    )
    return {"data": result}


# --- /users/me: identity + memberships across workspaces -----------------------


@router.get("/users/me")
async def get_me_with_memberships(
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    service = _member_service(request)
    memberships = await service.list_user_memberships(user=user)
    return {"data": {"user": user_to_dict(user), "memberships": memberships}}


__all__ = ["router"]

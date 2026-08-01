"""Agent HTTP routes (agent.md §3.1 / §3.4 / §3.5).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Reads need workspace membership (+ visibility for private agents, service
layer); writes need ``agent:manage`` (owner/admin). Action verbs use the
``:verb`` suffix convention (``:pause`` / ``:rollback`` / ``:transfer``,
§3.1) to separate them from read/write endpoints.

Creation lives at ``POST /workspaces/{ws}/agents`` — the backend half of
the members roster's SINGLE ``[ + 新建 Agent ]`` entry (agent.md §4.2 /
README §6.12, T35): there is no second creation entry (``POST
/workspaces/{ws}/members`` keeps rejecting ``member_type='agent'``).

``/agents/{id}/tools`` is the capability-grant thin wrapper required by
agent.md §2.5: it mutates skill installation grants, never a separate tool
catalog.  Both the exact Spec route and the workspace-qualified compatibility
route share one service.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from mesh.agent.schemas import (
    AgentToolBindResponse,
    AgentToolErrorResponse,
    AgentToolListResponse,
    AgentToolResponse,
    BindToolsRequest,
    CreateAgentRequest,
    LifecycleRequest,
    PatchAgentRequest,
    PatchToolRequest,
    TransferRequest,
    UpdateConfigRequest,
)
from mesh.agent.service import UNSET, AgentProfilePatch, AgentService
from mesh.agent.tools import AgentToolService
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal, get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["agent"])

# auth.md §3.6 — general API write class: 120 req/min per token/user.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_NOT_FOUND = "agent not found"

_TOOL_ERROR_RESPONSES = {
    400: {
        "model": AgentToolErrorResponse,
        "description": "Invalid request shape, capability key, or permission value.",
    },
    401: {"model": AgentToolErrorResponse, "description": "Missing or invalid bearer token."},
    403: {
        "model": AgentToolErrorResponse,
        "description": "Agent ownership, role, or token scope does not permit the mutation.",
    },
    404: {"model": AgentToolErrorResponse, "description": "Agent or capability grant not found."},
    409: {
        "model": AgentToolErrorResponse,
        "description": "Duplicate or ambiguous capability binding.",
    },
    422: {
        "model": AgentToolErrorResponse,
        "description": "Capability is outside the bound skill's approved authorization ceiling.",
    },
    429: {"model": AgentToolErrorResponse, "description": "Write rate limit exceeded."},
}


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service


def _agent_tool_service(request: Request) -> AgentToolService:
    return request.app.state.agent_tool_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"agent-write:{user.id}:{_client_ip(request)}",
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


# --- CRUD -----------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/agents", status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    if body.default_runtime_id is not None:
        raise ValidationError(
            "default_runtime_id is not available yet",
            code="runtimes_not_available",
            details={"field": "default_runtime_id"},
        )
    if body.skill_ids:
        raise ValidationError(
            "skill bindings are not available yet",
            code="skills_not_available",
            details={"field": "skill_ids"},
        )
    if body.capabilities:
        raise ValidationError(
            "capability grants are not available yet",
            code="skills_not_available",
            details={"field": "capabilities"},
        )
    service = _agent_service(request)
    created = await service.create_agent(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        avatar_url=body.avatar_url,
        role_tag=body.role_tag,
        slug=body.slug,
        bio=body.bio,
        visibility=body.visibility,
        system_instructions=body.system_instructions,
        model_config=body.agent_model_config,
        trigger_on_assign=body.trigger_on_assign,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/agents")
async def list_agents(
    request: Request,
    status: str = "all",
    visibility: str = "all",
    owner_id: str | None = None,
    q: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _agent_service(request)
    parsed_owner = _body_uuid(owner_id, field="owner_id") if owner_id is not None else None
    items, next_cursor = await service.list_agents(
        actor=context.member,
        workspace_id=context.workspace.id,
        status=status,
        visibility=visibility,
        owner_id=parsed_owner,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}")
async def get_agent(
    request: Request,
    workspace_id: str,
    agent_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _agent_service(request)
    detail = await service.get_agent(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
    )
    return {"data": detail}


@router.patch("/workspaces/{workspace_id}/agents/{agent_id}")
async def update_agent(
    body: PatchAgentRequest,
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    fields = body.model_fields_set
    patch = AgentProfilePatch(
        name=body.name if "name" in fields else UNSET,
        avatar_url=body.avatar_url if "avatar_url" in fields else UNSET,
        role_tag=body.role_tag if "role_tag" in fields else UNSET,
        slug=body.slug if "slug" in fields else UNSET,
        bio=body.bio if "bio" in fields else UNSET,
        visibility=body.visibility if "visibility" in fields else UNSET,
        trigger_on_assign=body.trigger_on_assign if "trigger_on_assign" in fields else UNSET,
    )
    service = _agent_service(request)
    updated = await service.update_agent(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        patch=patch,
        **_client_meta(request),
    )
    return {"data": updated}


@router.delete("/workspaces/{workspace_id}/agents/{agent_id}", status_code=204)
async def delete_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> None:
    await _rate_limit_write(request, user, response)
    service = _agent_service(request)
    await service.delete_agent(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        **_client_meta(request),
    )


# --- capability grants (agent.md §2.5 / §3.1) ---------------------------------


async def _list_tools(*, request: Request, actor, workspace_id: uuid.UUID, agent_id: uuid.UUID) -> dict:
    items = await _agent_tool_service(request).list_tools(
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    return {"data": items, "next_cursor": None}


async def _bind_tools(
    *,
    body: BindToolsRequest,
    request: Request,
    response: Response,
    user: User,
    actor,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> dict:
    await _rate_limit_write(request, user, response)
    items = await _agent_tool_service(request).bind_tools(
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
        grants=body.grants(),
        **_client_meta(request),
    )
    return {"data": items if body.is_batch else items[0]}


async def _patch_tool(
    *,
    body: PatchToolRequest,
    request: Request,
    response: Response,
    user: User,
    actor,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    capability_key: str,
) -> dict:
    await _rate_limit_write(request, user, response)
    item = await _agent_tool_service(request).update_tool(
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
        capability=capability_key,
        permission=body.permission,
        enabled=body.enabled,
        **_client_meta(request),
    )
    return {"data": item}


async def _delete_tool(
    *,
    request: Request,
    response: Response,
    user: User,
    actor,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    capability_key: str,
) -> None:
    await _rate_limit_write(request, user, response)
    await _agent_tool_service(request).unbind_tool(
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
        capability=capability_key,
        **_client_meta(request),
    )


@router.get(
    "/workspaces/{workspace_id}/agents/{agent_id}/tools",
    response_model=AgentToolListResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def list_agent_tools_in_workspace(
    request: Request,
    agent_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _list_tools(
        request=request,
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
    )


@router.post(
    "/workspaces/{workspace_id}/agents/{agent_id}/tools",
    status_code=201,
    response_model=AgentToolBindResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def bind_agent_tools_in_workspace(
    body: BindToolsRequest,
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _bind_tools(
        body=body,
        request=request,
        response=response,
        user=user,
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
    )


@router.patch(
    "/workspaces/{workspace_id}/agents/{agent_id}/tools/{capability_key:path}",
    response_model=AgentToolResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def patch_agent_tool_in_workspace(
    body: PatchToolRequest,
    request: Request,
    response: Response,
    agent_id: str,
    capability_key: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _patch_tool(
        body=body,
        request=request,
        response=response,
        user=user,
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        capability_key=capability_key,
    )


@router.delete(
    "/workspaces/{workspace_id}/agents/{agent_id}/tools/{capability_key:path}",
    status_code=204,
    responses=_TOOL_ERROR_RESPONSES,
)
async def delete_agent_tool_in_workspace(
    request: Request,
    response: Response,
    agent_id: str,
    capability_key: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> None:
    await _delete_tool(
        request=request,
        response=response,
        user=user,
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        capability_key=capability_key,
    )


@router.get(
    "/agents/{agent_id}/tools",
    response_model=AgentToolListResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def list_agent_tools_exact(
    request: Request,
    agent_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    user: User = Depends(get_current_user),
) -> dict:
    parsed_id = _path_uuid(agent_id)
    workspace_id, actor = await _agent_tool_service(request).resolve_actor(
        principal=principal, agent_id=parsed_id
    )
    return await _list_tools(
        request=request,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=parsed_id,
    )


@router.post(
    "/agents/{agent_id}/tools",
    status_code=201,
    response_model=AgentToolBindResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def bind_agent_tools_exact(
    body: BindToolsRequest,
    request: Request,
    response: Response,
    agent_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    user: User = Depends(get_current_user),
) -> dict:
    parsed_id = _path_uuid(agent_id)
    workspace_id, actor = await _agent_tool_service(request).resolve_actor(
        principal=principal, agent_id=parsed_id
    )
    return await _bind_tools(
        body=body,
        request=request,
        response=response,
        user=user,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=parsed_id,
    )


@router.patch(
    "/agents/{agent_id}/tools/{capability_key:path}",
    response_model=AgentToolResponse,
    responses=_TOOL_ERROR_RESPONSES,
)
async def patch_agent_tool_exact(
    body: PatchToolRequest,
    request: Request,
    response: Response,
    agent_id: str,
    capability_key: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    user: User = Depends(get_current_user),
) -> dict:
    parsed_id = _path_uuid(agent_id)
    workspace_id, actor = await _agent_tool_service(request).resolve_actor(
        principal=principal, agent_id=parsed_id
    )
    return await _patch_tool(
        body=body,
        request=request,
        response=response,
        user=user,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=parsed_id,
        capability_key=capability_key,
    )


@router.delete(
    "/agents/{agent_id}/tools/{capability_key:path}",
    status_code=204,
    responses=_TOOL_ERROR_RESPONSES,
)
async def delete_agent_tool_exact(
    request: Request,
    response: Response,
    agent_id: str,
    capability_key: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    user: User = Depends(get_current_user),
) -> None:
    parsed_id = _path_uuid(agent_id)
    workspace_id, actor = await _agent_tool_service(request).resolve_actor(
        principal=principal, agent_id=parsed_id
    )
    await _delete_tool(
        request=request,
        response=response,
        user=user,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=parsed_id,
        capability_key=capability_key,
    )


# --- configuration versions ---------------------------------------------------


@router.patch("/workspaces/{workspace_id}/agents/{agent_id}/config")
async def update_config(
    body: UpdateConfigRequest,
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    fields = body.model_fields_set
    service = _agent_service(request)
    updated = await service.update_config(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        model_config=body.agent_model_config if "agent_model_config" in fields else None,
        system_instructions=body.system_instructions if "system_instructions" in fields else UNSET,
        change_summary=body.change_summary,
        **_client_meta(request),
    )
    return {"data": updated}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/config-versions")
async def list_config_versions(
    request: Request,
    agent_id: str,
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _agent_service(request)
    items, next_cursor = await service.list_config_versions(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/config-versions/{version_id}:rollback")
async def rollback_config(
    request: Request,
    response: Response,
    agent_id: str,
    version_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _agent_service(request)
    updated = await service.rollback_config(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        version_id=_path_uuid(version_id),
        **_client_meta(request),
    )
    return {"data": updated}


# --- lifecycle (§4.8) ------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:pause")
async def pause_agent(
    request: Request,
    response: Response,
    agent_id: str,
    body: LifecycleRequest | None = None,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _agent_service(request)
    updated = await service.transition_lifecycle(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        action="pause",
        reason=body.reason if body is not None else None,
        in_flight_policy=body.in_flight_policy if body is not None else "finish_current",
        **_client_meta(request),
    )
    return {"data": updated}


async def _simple_transition(
    request: Request,
    response: Response,
    user: User,
    context: WorkspaceContext,
    *,
    agent_id: str,
    action: str,
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _agent_service(request)
    return {
        "data": await service.transition_lifecycle(
            actor=context.member,
            workspace_id=context.workspace.id,
            agent_id=_path_uuid(agent_id),
            action=action,
            **_client_meta(request),
        )
    }


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:resume")
async def resume_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _simple_transition(
        request, response, user, context, agent_id=agent_id, action="resume"
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:disable")
async def disable_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _simple_transition(
        request, response, user, context, agent_id=agent_id, action="disable"
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:enable")
async def enable_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _simple_transition(
        request, response, user, context, agent_id=agent_id, action="enable"
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:archive")
async def archive_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _simple_transition(
        request, response, user, context, agent_id=agent_id, action="archive"
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:restore")
async def restore_agent(
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _simple_transition(
        request, response, user, context, agent_id=agent_id, action="restore"
    )


# --- ownership transfer ----------------------------------------------------------


@router.post("/workspaces/{workspace_id}/agents/{agent_id}:transfer")
async def transfer_agent(
    body: TransferRequest,
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _agent_service(request)
    updated = await service.transfer_ownership(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id),
        new_owner_user_id=_body_uuid(body.new_owner_user_id, field="new_owner_user_id"),
        **_client_meta(request),
    )
    return {"data": updated}

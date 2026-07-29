"""Project module routes (project.md §3, README §6.14 envelopes).

Workspace-scoped paths use the ``require_workspace`` membership gate;
workspace-less paths (``/projects/{id}``, ``/milestones/{id}``,
``/cycles/{id}``, ``/project-templates/{id}``) resolve the tenant workspace
through the narrow SECURITY DEFINER lookups (migration 0006) and then run
the same membership gate. Resource-level authorization (visibility /
project roles / lead actions) lives in the service layer.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_context
from mesh.errors import NotFoundError, ValidationError
from mesh.project.schemas import (
    AddProjectMemberRequest,
    AddProjectUpdateRequest,
    CreateCycleRequest,
    CreateMilestoneRequest,
    CreateProjectRequest,
    CreateProjectTemplateRequest,
    InstantiateProjectTemplateRequest,
    UpdateCycleRequest,
    UpdateMilestoneRequest,
    UpdateProjectMemberRequest,
    UpdateProjectRequest,
    UpdateProjectTemplateRequest,
)
from mesh.project.service import (
    UNSET,
    CyclePatch,
    MilestonePatch,
    ProjectPatch,
    ProjectService,
)

router = APIRouter(prefix="/api/v1", tags=["project"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_PROJECT_NOT_FOUND = "project not found"
_MILESTONE_NOT_FOUND = "milestone not found"
_CYCLE_NOT_FOUND = "cycle not found"
_TEMPLATE_NOT_FOUND = "project template not found"


def _project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": request.client.host if request.client is not None else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(
    request: Request, principal: AuthenticatedPrincipal, response: Response
) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"project-write:{principal.user_id or principal.member_id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(raw: str, *, message: str) -> uuid.UUID:
    """Path ids that are not UUIDs are 404s (never leak id shape, §5.3)."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _body_uuid(raw: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


def _tri(value, present: bool):
    """Tri-state PATCH resolution: omitted → UNSET, else value (may be None)."""
    return value if present else UNSET


async def _context_for(
    request: Request,
    principal: AuthenticatedPrincipal,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    not_found_message: str,
) -> WorkspaceContext:
    """Run the membership gate for a workspace-less path (workspace.md §5.3).

    The resolver already proved the resource exists SOMEWHERE; if the caller
    is not a member of that workspace the gate raises "workspace not found".
    That message differs from the "<resource> not found" an unknown id gets —
    a two-message existence oracle for arbitrary UUIDs. Rewriting the gate
    404 to the resource message makes the two cases indistinguishable; no
    content leaks either way (the service-layer read gate still runs).
    """
    try:
        return await resolve_workspace_context(
            session, principal=principal, workspace_id=workspace_id, permission=None
        )
    except NotFoundError as exc:
        raise NotFoundError(not_found_message) from exc


# ----------------------------------------------------------------------
# projects
# ----------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, principal, response)
    created = await _project_service(request).create_project(
        actor=context.member,
        workspace_id=context.workspace.id,
        body=body,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/projects")
async def list_projects(
    request: Request,
    status: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    archived: bool = Query(default=False),
    mine: bool = Query(default=False),
    lead_member_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    lead_id = _body_uuid(lead_member_id, field="lead_member_id") if lead_member_id else None
    items, next_cursor = await _project_service(request).list_projects(
        viewer=context.member,
        workspace_id=context.workspace.id,
        status=status,
        visibility=visibility,
        archived=archived,
        mine=mine,
        lead_member_id=lead_id,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.get_project(
        viewer=context.member, workspace_id=workspace_id, project_id=parsed
    )
    return {"data": data}


@router.patch("/projects/{project_id}")
async def update_project(
    body: UpdateProjectRequest,
    request: Request,
    response: Response,
    project_id: str,
    if_match: str | None = Header(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    fields = body.model_fields_set
    patch = ProjectPatch(
        name=_tri(body.name, "name" in fields),
        description=_tri(body.description, "description" in fields),
        icon=_tri(body.icon, "icon" in fields),
        color=_tri(body.color, "color" in fields),
        status=_tri(body.status, "status" in fields),
        health=_tri(body.health, "health" in fields),
        visibility=_tri(body.visibility, "visibility" in fields),
        lead_member_id=_tri(
            _body_uuid(body.lead_member_id, field="lead_member_id")
            if body.lead_member_id is not None
            else None,
            "lead_member_id" in fields,
        ),
        start_date=_tri(body.start_date, "start_date" in fields),
        target_date=_tri(body.target_date, "target_date" in fields),
    )
    data = await service.update_project(
        actor=context.member,
        workspace_id=workspace_id,
        project_id=parsed,
        patch=patch,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.delete_project(
        actor=context.member, workspace_id=workspace_id, project_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


@router.post("/projects/{project_id}/archive")
async def archive_project(
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.archive_project(
        actor=context.member, workspace_id=workspace_id, project_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


@router.post("/projects/{project_id}/unarchive")
async def unarchive_project(
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.unarchive_project(
        actor=context.member, workspace_id=workspace_id, project_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# health / status updates trail
# ----------------------------------------------------------------------


@router.post("/projects/{project_id}/updates", status_code=201)
async def add_project_update(
    body: AddProjectUpdateRequest,
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.add_update(
        actor=context.member, workspace_id=workspace_id, project_id=parsed, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.get("/projects/{project_id}/updates")
async def list_project_updates(
    project_id: str,
    request: Request,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    items, next_cursor = await service.list_updates(
        viewer=context.member, workspace_id=workspace_id, project_id=parsed,
        limit=limit, cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


# ----------------------------------------------------------------------
# milestones
# ----------------------------------------------------------------------


@router.get("/projects/{project_id}/milestones")
async def list_milestones(
    project_id: str,
    request: Request,
    state: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    items, next_cursor = await service.list_milestones(
        viewer=context.member, workspace_id=workspace_id, project_id=parsed,
        state=state, limit=limit, cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/projects/{project_id}/milestones", status_code=201)
async def create_milestone(
    body: CreateMilestoneRequest,
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.create_milestone(
        actor=context.member, workspace_id=workspace_id, project_id=parsed, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/milestones/{milestone_id}")
async def update_milestone(
    body: UpdateMilestoneRequest,
    milestone_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(milestone_id, message=_MILESTONE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_milestone_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_MILESTONE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_MILESTONE_NOT_FOUND
    )
    fields = body.model_fields_set
    patch = MilestonePatch(
        title=_tri(body.title, "title" in fields),
        description=_tri(body.description, "description" in fields),
        target_date=_tri(body.target_date, "target_date" in fields),
        state=_tri(body.state, "state" in fields),
    )
    data = await service.update_milestone(
        actor=context.member, workspace_id=workspace_id, milestone_id=parsed, patch=patch,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/milestones/{milestone_id}")
async def delete_milestone(
    milestone_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(milestone_id, message=_MILESTONE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_milestone_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_MILESTONE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_MILESTONE_NOT_FOUND
    )
    data = await service.delete_milestone(
        actor=context.member, workspace_id=workspace_id, milestone_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# cycles
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/cycles")
async def list_cycles(
    request: Request,
    state: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    parsed_project = _body_uuid(project_id, field="project_id") if project_id else None
    items, next_cursor = await _project_service(request).list_cycles(
        viewer=context.member,
        workspace_id=context.workspace.id,
        state=state,
        project_id=parsed_project,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/cycles", status_code=201)
async def create_cycle(
    body: CreateCycleRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, principal, response)
    data = await _project_service(request).create_cycle(
        actor=context.member, workspace_id=context.workspace.id, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/cycles/{cycle_id}")
async def update_cycle(
    body: UpdateCycleRequest,
    cycle_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(cycle_id, message=_CYCLE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_cycle_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_CYCLE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_CYCLE_NOT_FOUND
    )
    fields = body.model_fields_set
    patch = CyclePatch(
        name=_tri(body.name, "name" in fields),
        starts_at=_tri(body.starts_at, "starts_at" in fields),
        ends_at=_tri(body.ends_at, "ends_at" in fields),
        state=_tri(body.state, "state" in fields),
        auto_roll=_tri(body.auto_roll, "auto_roll" in fields),
    )
    data = await service.update_cycle(
        actor=context.member, workspace_id=workspace_id, cycle_id=parsed, patch=patch,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# project members
# ----------------------------------------------------------------------


@router.get("/projects/{project_id}/members")
async def list_project_members(
    project_id: str,
    request: Request,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    items, next_cursor = await service.list_project_members(
        viewer=context.member, workspace_id=workspace_id, project_id=parsed,
        limit=limit, cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/projects/{project_id}/members", status_code=201)
async def add_project_member(
    body: AddProjectMemberRequest,
    project_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    body.member_id = str(_body_uuid(body.member_id, field="member_id"))
    data = await service.add_project_member(
        actor=context.member, workspace_id=workspace_id, project_id=parsed, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/projects/{project_id}/members/{member_id}")
async def update_project_member(
    body: UpdateProjectMemberRequest,
    project_id: str,
    member_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed_project = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    parsed_member = _path_uuid(member_id, message="project member not found")
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed_project)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.update_project_member(
        actor=context.member,
        workspace_id=workspace_id,
        project_id=parsed_project,
        member_id=parsed_member,
        body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/projects/{project_id}/members/{member_id}")
async def remove_project_member(
    project_id: str,
    member_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed_project = _path_uuid(project_id, message=_PROJECT_NOT_FOUND)
    parsed_member = _path_uuid(member_id, message="project member not found")
    service = _project_service(request)
    workspace_id = await service.resolve_project_workspace(parsed_project)
    if workspace_id is None:
        raise NotFoundError(_PROJECT_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_PROJECT_NOT_FOUND
    )
    data = await service.remove_project_member(
        actor=context.member,
        workspace_id=workspace_id,
        project_id=parsed_project,
        member_id=parsed_member,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# project templates (project.md §3.2b)
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/project-templates")
async def list_project_templates(
    request: Request,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items, next_cursor = await _project_service(request).list_templates(
        viewer=context.member, workspace_id=context.workspace.id, limit=limit, cursor=cursor
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/project-templates", status_code=201)
async def create_project_template(
    body: CreateProjectTemplateRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, principal, response)
    data = await _project_service(request).create_template(
        actor=context.member, workspace_id=context.workspace.id, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/project-templates/{template_id}")
async def update_project_template(
    body: UpdateProjectTemplateRequest,
    template_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_template_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.update_template(
        actor=context.member, workspace_id=workspace_id, template_id=parsed, body=body,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/project-templates/{template_id}")
async def delete_project_template(
    template_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_template_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.delete_template(
        actor=context.member, workspace_id=workspace_id, template_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


@router.post("/project-templates/{template_id}/instantiate", status_code=201)
async def instantiate_project_template(
    body: InstantiateProjectTemplateRequest,
    template_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response)
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = _project_service(request)
    workspace_id = await service.resolve_template_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        request, principal, session, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.instantiate_template(
        actor=context.member, workspace_id=workspace_id, template_id=parsed, body=body,
        **_client_meta(request),
    )
    return {"data": data}

"""Label & custom-field definition routes (label-property.md §3.1, definition layer).

Workspace-scoped collections use the ``require_workspace`` membership gate;
workspace-less paths (``/labels/{id}``, ``/custom-fields/{id}``,
``/custom-fields/{id}/options[/{opt_id}]``) resolve the tenant through the
narrow SECURITY DEFINER lookups (migration 0008) and then run the same
membership gate. Scope-level authorization (admin / project lead) and all
type-config validation live in the service layer (§3.4).

Only the definition-layer endpoints of §3.1 are exposed here; the
issue-association endpoints (issue label picker, PUT custom-field-values,
merge) land with the issue-module increment.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_context
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.labels.schemas import (
    CreateCustomFieldRequest,
    CreateLabelRequest,
    CreateOptionRequest,
    UpdateCustomFieldRequest,
    UpdateLabelRequest,
    UpdateOptionRequest,
)
from mesh.labels.service import (
    UNSET,
    FieldDefPatch,
    LabelPatch,
    LabelService,
    OptionPatch,
)

router = APIRouter(prefix="/api/v1", tags=["labels"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_LABEL_NOT_FOUND = "label not found"
_FIELD_DEF_NOT_FOUND = "custom field not found"
_OPTION_NOT_FOUND = "option not found"


def _label_service(request: Request) -> LabelService:
    return request.app.state.label_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": request.client.host if request.client is not None else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"labels-write:{user.id}:{client_ip}",
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


def _query_uuid(raw: str | None, *, field: str) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


def _tri(value, present: bool):
    """Tri-state PATCH resolution: omitted → UNSET, else value (may be None)."""
    return value if present else UNSET


async def _context_for(
    request: Request,
    user: User,
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> WorkspaceContext:
    return await resolve_workspace_context(
        session, user=user, workspace_id=workspace_id, permission=None
    )


# ----------------------------------------------------------------------
# labels
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/labels")
async def list_labels(
    request: Request,
    project_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items, next_cursor = await _label_service(request).list_labels(
        viewer=context.member,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(project_id, field="project_id"),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/labels", status_code=201)
async def create_label(
    body: CreateLabelRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    created = await _label_service(request).create_label(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        color=body.color,
        description=body.description,
        project_id=_query_uuid(body.project_id, field="project_id"),
        **_client_meta(request),
    )
    return {"data": created}


@router.patch("/labels/{label_id}")
async def update_label(
    body: UpdateLabelRequest,
    request: Request,
    response: Response,
    label_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(label_id, message=_LABEL_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_label_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_LABEL_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    fields = body.model_fields_set
    patch = LabelPatch(
        name=_tri(body.name, "name" in fields),
        color=_tri(body.color, "color" in fields),
        description=_tri(body.description, "description" in fields),
    )
    data = await service.update_label(
        actor=context.member,
        workspace_id=workspace_id,
        label_id=parsed,
        patch=patch,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/labels/{label_id}")
async def delete_label(
    request: Request,
    response: Response,
    label_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(label_id, message=_LABEL_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_label_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_LABEL_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    data = await service.delete_label(
        actor=context.member,
        workspace_id=workspace_id,
        label_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# custom field definitions
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/custom-fields")
async def list_custom_fields(
    request: Request,
    project_id: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items, next_cursor = await _label_service(request).list_field_defs(
        viewer=context.member,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(project_id, field="project_id"),
        is_active=is_active,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/custom-fields", status_code=201)
async def create_custom_field(
    body: CreateCustomFieldRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    created = await _label_service(request).create_field_def(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        field_key=body.field_key,
        field_type=body.type,
        project_id=_query_uuid(body.project_id, field="project_id"),
        is_required=body.is_required,
        required_on=body.required_on,
        default_value=body.default_value,
        config=body.config,
        position=body.position,
        options=[option.model_dump() for option in body.options],
        **_client_meta(request),
    )
    return {"data": created}


@router.patch("/custom-fields/{field_def_id}")
async def update_custom_field(
    body: UpdateCustomFieldRequest,
    request: Request,
    response: Response,
    field_def_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    fields = body.model_fields_set
    patch = FieldDefPatch(
        name=_tri(body.name, "name" in fields),
        is_required=_tri(body.is_required, "is_required" in fields),
        required_on=_tri(body.required_on, "required_on" in fields),
        default_value=_tri(body.default_value, "default_value" in fields),
        config=_tri(body.config, "config" in fields),
        position=_tri(body.position, "position" in fields),
        is_active=_tri(body.is_active, "is_active" in fields),
    )
    data = await service.update_field_def(
        actor=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed,
        patch=patch,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/custom-fields/{field_def_id}")
async def delete_custom_field(
    request: Request,
    response: Response,
    field_def_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    data = await service.delete_field_def(
        actor=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# enum options
# ----------------------------------------------------------------------


@router.get("/custom-fields/{field_def_id}/options")
async def list_options(
    request: Request,
    field_def_id: str,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    items, next_cursor = await service.list_options(
        viewer=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/custom-fields/{field_def_id}/options", status_code=201)
async def create_option(
    body: CreateOptionRequest,
    request: Request,
    response: Response,
    field_def_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    data = await service.create_option(
        actor=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed,
        name=body.name,
        color=body.color,
        position=body.position,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/custom-fields/{field_def_id}/options/{option_id}")
async def update_option(
    body: UpdateOptionRequest,
    request: Request,
    response: Response,
    field_def_id: str,
    option_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed_field = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    parsed_option = _path_uuid(option_id, message=_OPTION_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed_field)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    fields = body.model_fields_set
    patch = OptionPatch(
        name=_tri(body.name, "name" in fields),
        color=_tri(body.color, "color" in fields),
        position=_tri(body.position, "position" in fields),
        is_active=_tri(body.is_active, "is_active" in fields),
    )
    data = await service.update_option(
        actor=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed_field,
        option_id=parsed_option,
        patch=patch,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/custom-fields/{field_def_id}/options/{option_id}")
async def delete_option(
    request: Request,
    response: Response,
    field_def_id: str,
    option_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed_field = _path_uuid(field_def_id, message=_FIELD_DEF_NOT_FOUND)
    parsed_option = _path_uuid(option_id, message=_OPTION_NOT_FOUND)
    service = _label_service(request)
    workspace_id = await service.resolve_field_def_workspace(parsed_field)
    if workspace_id is None:
        raise NotFoundError(_FIELD_DEF_NOT_FOUND)
    context = await _context_for(request, user, session, workspace_id)
    data = await service.delete_option(
        actor=context.member,
        workspace_id=workspace_id,
        field_def_id=parsed_field,
        option_id=parsed_option,
        **_client_meta(request),
    )
    return {"data": data}

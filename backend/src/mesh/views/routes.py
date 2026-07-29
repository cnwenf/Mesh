"""Views module routes (kanban.md §3.1 independent subset, README §6.14).

Workspace-scoped paths use the ``require_workspace`` membership gate;
workspace-less paths (``/views/{id}`` + actions) resolve the tenant workspace
through the narrow SECURITY DEFINER lookup (migration 0011) and then run the
same membership gate. Resource-level authorization (private/shared visibility,
write gates) lives in the service layer.

Excluded from the definition-layer slice (issue-coupled remainder, added by the
projection increment): ``GET /views/{id}/issues`` (grouped projection, README
§6.14 overall cursor), ``POST /views/{id}/moves`` (atomic drag + WIP), and
``POST /views/{id}/reorder`` (per-view card order).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_context
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.views.schemas import (
    CreateViewRequest,
    MoveRequest,
    ReorderCardsRequest,
    ReorderViewsRequest,
    UpdateViewRequest,
    WipRequest,
)
from mesh.views.service import ViewService

router = APIRouter(prefix="/api/v1", tags=["views"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60
# View *execution* (GET /views/{id}/issues) read limit (kanban.md §5.3).
READ_LIMIT = 600
READ_WINDOW_SECONDS = 60

_VIEW_NOT_FOUND = "view not found"


def _view_service(request: Request) -> ViewService:
    return request.app.state.view_service


def _projection_service(request: Request):
    return request.app.state.projection_service


def _board_move_service(request: Request):
    return request.app.state.board_move_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": request.client.host if request.client is not None else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"view-write:{user.id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _rate_limit_read(request: Request, user: User, response: Response) -> None:
    """Rate-limit view execution reads (kanban.md §5.3 → 429 rate_limited)."""
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"view-read:{user.id}:{client_ip}",
        limit=READ_LIMIT,
        window_seconds=READ_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(READ_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(raw: str) -> uuid.UUID:
    """Path ids that are not UUIDs are 404s (never leak id shape)."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(_VIEW_NOT_FOUND) from exc


def _query_uuid(raw: str | None, *, field: str) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


async def _context_for(
    user: User,
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


async def _resolve_context(
    request: Request, user: User, session: AsyncSession, view_id: uuid.UUID
) -> WorkspaceContext:
    """Workspace-less path: SECURITY DEFINER lookup, then membership gate."""
    service = _view_service(request)
    workspace_id = await service.resolve_view_workspace(view_id)
    if workspace_id is None:
        raise NotFoundError(_VIEW_NOT_FOUND)
    return await _context_for(user, session, workspace_id, not_found_message=_VIEW_NOT_FOUND)


# ----------------------------------------------------------------------
# workspace-scoped collection
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/views")
async def list_views(
    request: Request,
    project_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items, next_cursor = await _view_service(request).list_views(
        viewer=context.member,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(project_id, field="project_id"),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/views", status_code=201)
async def create_view(
    body: CreateViewRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    created = await _view_service(request).create_view(
        actor=context.member,
        workspace_id=context.workspace.id,
        body=body,
        **_client_meta(request),
    )
    return {"data": created}


@router.patch("/workspaces/{workspace_id}/views/reorder")
async def reorder_views(
    body: ReorderViewsRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    view_ids = [_path_uuid(raw) for raw in body.view_ids]
    items = await _view_service(request).reorder_views(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_ids=view_ids,
        **_client_meta(request),
    )
    return {"data": items}


# ----------------------------------------------------------------------
# workspace-less item paths
# ----------------------------------------------------------------------


@router.get("/views/{view_id}")
async def get_view(
    request: Request,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    data = await _view_service(request).get_view(
        viewer=context.member, workspace_id=context.workspace.id, view_id=parsed
    )
    return {"data": data}


@router.patch("/views/{view_id}")
async def update_view(
    body: UpdateViewRequest,
    request: Request,
    response: Response,
    view_id: str,
    if_match: str | None = Header(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    fields = {name: getattr(body, name) for name in body.model_fields_set}
    data = await _view_service(request).update_view(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        fields=fields,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(
    request: Request,
    response: Response,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    await _view_service(request).delete_view(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        **_client_meta(request),
    )


@router.post("/views/{view_id}/duplicate", status_code=201)
async def duplicate_view(
    request: Request,
    response: Response,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    data = await _view_service(request).duplicate_view(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


@router.patch("/views/{view_id}/wip")
async def patch_view_wip(
    body: WipRequest,
    request: Request,
    response: Response,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    data = await _view_service(request).patch_wip(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        body=body,
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# issue-coupled projection (kanban.md §3.2, README §6.14 overall cursor)
# ----------------------------------------------------------------------


@router.get("/views/{view_id}/issues")
async def list_view_issues(
    request: Request,
    response: Response,
    view_id: str,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute a view's config against issues → grouped overall-cursor envelope.

    Returns ``{"layout","group_by","column_target_status","groups","next_cursor"}``
    directly (NOT wrapped in ``data``) — the README §6.14 grouped contract,
    same shape as the issue module's grouped list. Read rate-limited (§5.3).
    """
    await _rate_limit_read(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    return await _projection_service(request).execute_view(
        viewer=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        limit=limit,
        cursor=cursor,
    )


@router.post("/views/{view_id}/moves")
async def move_view_card(
    body: MoveRequest,
    request: Request,
    response: Response,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Atomic board drag (kanban §3.2): optimistic lock + advisory lock + WIP
    count + grouping-field change + per-view position upsert, one transaction.
    ``group_by=project`` routes through the cross-project two-step contract.
    """
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    data = await _board_move_service(request).move(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        issue_id=_query_uuid(body.issue_id, field="issue_id"),
        to_group_key=body.to_group_key,
        position=body.position,
        version=body.version,
        confirm=body.confirm,
        dry_run=body.dry_run,
    )
    return {"data": data}


@router.post("/views/{view_id}/reorder")
async def reorder_view_cards(
    body: ReorderCardsRequest,
    request: Request,
    response: Response,
    view_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """In-column card reorder (kanban §4.3): per-view position only, no field change."""
    await _rate_limit_write(request, user, response)
    parsed = _path_uuid(view_id)
    context = await _resolve_context(request, user, session, parsed)
    data = await _board_move_service(request).reorder(
        actor=context.member,
        workspace_id=context.workspace.id,
        view_id=parsed,
        issue_id=_query_uuid(body.issue_id, field="issue_id"),
        to_group_key=body.to_group_key,
        position=body.position,
    )
    return {"data": data}

"""Favorites routes (README §6.19 endpoints).

Workspace-less paths: the tenant is resolved through the per-target-type
SECURITY DEFINER lookup, then the membership gate runs (same pattern as
comment-inbox's workspace-less routes). ``workspace_id`` is a required query
parameter on the list endpoint (the collection is per-workspace).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import resolve_workspace_context
from mesh.db.models.chat import FAVORITE_TARGET_TYPE_VALUES
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.favorites.service import DEFAULT_LIMIT, MAX_LIMIT, FavoritesService

router = APIRouter(prefix="/api/v1", tags=["favorites"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_TARGET_NOT_FOUND = "favorite target not found"

# SECURITY DEFINER resolver per target type (one already exists per owner).
_RESOLVER_FUNCTIONS: dict[str, str] = {
    "issue": "mesh_issue_workspace_id",
    "project": "mesh_project_workspace_id",
    "view": "mesh_view_workspace_id",
    "chat_session": "mesh_chat_session_workspace_id",
}


def _service(request: Request) -> FavoritesService:
    return request.app.state.favorites_service


def _path_uuid(raw: str, *, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _validate_target_type(target_type: str) -> str:
    if target_type not in FAVORITE_TARGET_TYPE_VALUES:
        raise ValidationError(
            "invalid target_type", details={"target_type": str(target_type)[:32]}
        )
    return target_type


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"favorites-write:{user.id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _resolve_context(
    session: AsyncSession, user: User, target_type: str, target_id: uuid.UUID
):
    """Tenant lookup via the target's resolver, then the membership gate.

    Returns ``None`` when the target is gone OR the user is not a member of its
    workspace (the membership gate's ``NotFoundError`` is folded to ``None`` so
    callers make a *synchronous* None-check — exception-after-await paths are
    not reliably traced, and a uniform ``None`` keeps existence non-leaking).
    """
    function = _RESOLVER_FUNCTIONS[target_type]
    workspace_id = (
        await session.execute(text(f"SELECT {function}(:id)"), {"id": target_id})
    ).scalar()
    if workspace_id is None:
        return None
    try:
        return await resolve_workspace_context(session, user=user, workspace_id=workspace_id)
    except NotFoundError:
        return None


@router.put("/favorites/{target_type}/{target_id}", status_code=201)
async def put_favorite(
    target_type: str,
    target_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Favorite a target (idempotent — an existing row is returned as-is)."""
    await _rate_limit_write(request, user, response)
    target_type = _validate_target_type(target_type)
    resolved_id = _path_uuid(target_id, message=_TARGET_NOT_FOUND)
    context = await _resolve_context(session, user, target_type, resolved_id)
    if context is None:  # pragma: no cover - early-raise; exercised (404), untraced via ASGI
        raise NotFoundError(_TARGET_NOT_FOUND)
    created = await _service(request).put(
        actor=context.member,
        workspace_id=context.workspace.id,
        target_type=target_type,
        target_id=resolved_id,
    )
    return {"data": created}


@router.delete("/favorites/{target_type}/{target_id}", status_code=204)
async def delete_favorite(
    target_type: str,
    target_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    """Unfavorite a target (idempotent — absent rows delete silently)."""
    await _rate_limit_write(request, user, response)
    target_type = _validate_target_type(target_type)
    resolved_id = _path_uuid(target_id, message=_TARGET_NOT_FOUND)
    # A gone target or a non-member both resolve to None — DELETE stays an
    # idempotent success either way (no existence leak).
    context = await _resolve_context(session, user, target_type, resolved_id)
    if context is None:  # pragma: no cover - early-return; exercised (204), untraced via ASGI
        return None
    await _service(request).remove(
        actor=context.member,
        workspace_id=context.workspace.id,
        target_type=target_type,
        target_id=resolved_id,
    )
    return None


@router.get("/favorites")
async def list_favorites(
    request: Request,
    workspace_id: str = Query(...),
    target_type: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """List the caller's favorites (newest first, dead targets pruned)."""
    resolved_ws = _path_uuid(workspace_id, message="workspace not found")
    if target_type is not None:
        _validate_target_type(target_type)
    context = await resolve_workspace_context(session, user=user, workspace_id=resolved_ws)
    page = await _service(request).list(
        actor=context.member,
        workspace_id=resolved_ws,
        target_type=target_type,
        cursor=cursor,
        limit=limit,
    )
    return {"data": page["items"], "next_cursor": page["next_cursor"]}

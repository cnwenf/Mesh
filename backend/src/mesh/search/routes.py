"""Global search endpoint (search-command-palette.md §3).

``GET /api/v1/workspaces/{workspace_id}/search`` — the workspace scope is
resolved ONLY from the path (H4): no query/header/token second source.
Object results only; command items are merged client-side (§3.1). Raw
``q`` values never leave the handler — not into logs, errors or metrics
(§5.3 全通道不落搜索日志).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.search.service import SearchService

router = APIRouter(prefix="/api/v1", tags=["search"])

# Read-side throttle (§3.5 rate_limited 429 + Retry-After).
SEARCH_RATE_LIMIT = 120
SEARCH_RATE_WINDOW_SECONDS = 60
RATE_LIMIT_BUCKET = "search"


def _search(request: Request) -> SearchService:
    return request.app.state.search_service  # type: ignore[no-any-return]


async def _rate_limit_search(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"{RATE_LIMIT_BUCKET}:{user.id}:{client_ip}",
        limit=SEARCH_RATE_LIMIT,
        window_seconds=SEARCH_RATE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(SEARCH_RATE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


@router.get("/workspaces/{workspace_id}/search")
async def search_workspace(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
    q: str | None = Query(default=None, max_length=None),
    types: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
) -> dict:
    """Unified object search over the six workspace resource types (§3.2)."""
    await _rate_limit_search(request, user, response)
    return await _search(request).search(
        viewer=context.member,
        workspace=context.workspace,
        q=q,
        types=types,
        limit=limit,
        cursor=cursor,
    )

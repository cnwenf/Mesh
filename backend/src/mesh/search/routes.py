"""Global search endpoint (search-command-palette.md §3, README §6.14).

``GET /workspaces/{workspace_id}/search`` — object results only; command
entries are merged client-side (spec §3.1). Membership gate via
``require_workspace`` (agent tokens participate as their member row); rate
limited like analytics reads (300/60s) with ``X-RateLimit-*`` headers.

PRIVACY (§5.3): ``q`` is validated and forwarded to the service — it is
NEVER echoed into error details, logs or metrics by this module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.errors import ValidationError
from mesh.search import schemas
from mesh.search.norm import search_norm

router = APIRouter(prefix="/api/v1", tags=["search"])

READ_LIMIT = 300
READ_WINDOW_SECONDS = 60


def _service(request: Request):
    return request.app.state.search_service


async def _rate_limit_read(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"search-read:{user.id}:{client_ip}",
        limit=READ_LIMIT,
        window_seconds=READ_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(READ_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _parse_types(raw: str | None) -> tuple[str, ...]:
    """types csv → deduped subset of the whitelist; invalid → 400."""
    if raw is None:
        return schemas.SEARCH_TYPES
    requested: list[str] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk not in schemas.SEARCH_TYPES:
            # Detail carries only the invalid token's length — never query text.
            raise ValidationError(
                "invalid types value",
                details={"types": f"invalid value ({len(chunk)} chars)"},
            )
        if chunk not in requested:
            requested.append(chunk)
    return tuple(requested) or schemas.SEARCH_TYPES


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return schemas.DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValidationError(
            "invalid limit", details={"limit": raw[:32]}, code="validation_error"
        ) from exc
    if limit < 1 or limit > schemas.MAX_LIMIT:
        raise ValidationError(
            f"limit must be between 1 and {schemas.MAX_LIMIT}",
            details={"limit": str(limit)},
            code="validation_error",
        )
    return limit


_EMPTY_RESULT: dict = {"data": [], "next_cursor": None}


@router.get("/workspaces/{workspace_id}/search")
async def search(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    await _rate_limit_read(request, user, response)
    types = _parse_types(qp.get("types"))
    limit = _parse_limit(qp.get("limit"))
    raw_q = qp.get("q")
    q = (raw_q or "").strip()
    if len(q) > schemas.MAX_QUERY_LENGTH:
        # No q content in the detail — only the over-length fact (§5.3).
        raise ValidationError(
            f"q exceeds {schemas.MAX_QUERY_LENGTH} characters",
            code="validation_error",
        )
    # Empty / whitespace-only / normalization-empty → empty object result
    # (spec §3.2: the empty-state data flow is assembled client-side).
    if not q or not search_norm(q).strip():
        return _EMPTY_RESULT
    return await _service(request).search(
        actor=context.member,
        workspace=context.workspace,
        q=q,
        types=types,
        limit=limit,
        cursor=qp.get("cursor"),
    )

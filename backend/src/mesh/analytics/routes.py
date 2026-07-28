"""Analytics routes (analytics.md §3, README §6.14 envelopes).

Workspace-scoped read endpoints under ``/workspaces/{workspace_id}``; the
membership gate is ``require_workspace`` and every resource-level gate
(project visibility, private-agent visibility, visibility-set filtering)
lives in ``AnalyticsService``. Rate limited per user+ip (read buckets;
``refresh=true`` uses a tighter bucket, §2.6/§6.14).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from mesh.analytics.service import AnalyticsService
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["analytics"])

READ_LIMIT = 300
READ_WINDOW_SECONDS = 60
REFRESH_LIMIT = 30
REFRESH_WINDOW_SECONDS = 60


def _service(request: Request) -> AnalyticsService:
    return request.app.state.analytics_service


async def _rate_limit_read(request: Request, user: User, response: Response, *, refresh: bool) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"analytics-read:{user.id}:{client_ip}",
        limit=READ_LIMIT,
        window_seconds=READ_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(READ_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)
    if refresh:
        await limiter.check(
            f"analytics-refresh:{user.id}:{client_ip}",
            limit=REFRESH_LIMIT,
            window_seconds=REFRESH_WINDOW_SECONDS,
        )


def _path_uuid(raw: str, *, message: str) -> uuid.UUID:
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


def _query_uuid_list(raw: str | None, *, field: str) -> list[uuid.UUID] | None:
    if raw is None:
        return None
    out: list[uuid.UUID] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(uuid.UUID(chunk))
        except ValueError as exc:
            raise ValidationError(
                f"invalid {field}", details={field: chunk[:64]}
            ) from exc
    return out or None


def _bool(raw: str | None) -> bool:
    return (raw or "").lower() in ("1", "true", "yes")


def _limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValidationError(
            "invalid limit", details={"limit": raw[:32]}, code="invalid_limit"
        ) from exc


@router.get("/workspaces/{workspace_id}/analytics/cycle-time")
async def get_cycle_time(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).cycle_time(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(qp.get("project_id"), field="project_id"),
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        from_category=qp.get("from_category"),
        tz=qp.get("tz"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/analytics/velocity")
async def get_velocity(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).velocity(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(qp.get("project_id"), field="project_id"),
        cycle_ids=_query_uuid_list(qp.get("cycle_ids"), field="cycle_ids"),
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        tz=qp.get("tz"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/analytics/throughput")
async def get_throughput(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).throughput(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(qp.get("project_id"), field="project_id"),
        project_ids=_query_uuid_list(qp.get("project_ids"), field="project_ids"),
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        granularity=qp.get("granularity"),
        tz=qp.get("tz"),
        calendar_timezone=qp.get("calendar_timezone"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/analytics/workload")
async def get_workload(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    await _rate_limit_read(request, user, response, refresh=False)
    result = await _service(request).workload(
        actor=context.member,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(qp.get("project_id"), field="project_id"),
        member_type=qp.get("member_type"),
        cursor=qp.get("cursor"),
        limit=_limit(qp.get("limit")),
    )
    return {"data": result["rows"], "next_cursor": result["next_cursor"]}


@router.get("/workspaces/{workspace_id}/analytics/burndown")
async def get_burndown(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).burndown(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        cycle_id=_query_uuid(qp.get("cycle_id"), field="cycle_id"),
        milestone_id=_query_uuid(qp.get("milestone_id"), field="milestone_id"),
        metric=qp.get("metric"),
        tz=qp.get("tz"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/analytics/agents/stats")
async def get_agent_stats(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).agent_stats(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        agent_id=_query_uuid(qp.get("agent_id"), field="agent_id"),
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/dashboards/project/{project_id}")
async def get_project_dashboard(
    request: Request,
    response: Response,
    project_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).project_dashboard(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        project_id=_path_uuid(project_id, message="project not found"),
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        cycle_id=_query_uuid(qp.get("cycle_id"), field="cycle_id"),
        tz=qp.get("tz"),
        refresh=refresh,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/dashboards/workspace")
async def get_workspace_dashboard(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    qp = request.query_params
    refresh = _bool(qp.get("refresh"))
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).workspace_dashboard(
        actor=context.member,
        user=user,
        workspace_id=context.workspace.id,
        win_from=qp.get("from"),
        win_to=qp.get("to"),
        granularity=qp.get("granularity"),
        tz=qp.get("tz"),
        calendar_timezone=qp.get("calendar_timezone"),
        refresh=refresh,
    )
    return {"data": data}

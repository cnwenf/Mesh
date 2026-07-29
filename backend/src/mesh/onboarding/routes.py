"""Onboarding routes (onboarding.md §3.1).

Self-service endpoints (``/onboarding/*``) take ``?workspace_id=`` and are
owner-only: the membership gate resolves the caller's member row and every
operation targets THAT row (anti-IDOR — there is no member_id parameter to
tamper with). The admin reset endpoint nests under ``/workspaces/{ws}/`` and
requires ``workspace:manage_members``.

Envelope / errors / HTTP semantics follow README §6.14; completions are
idempotent through the §3.5 conditional-UPDATE guards even without an
``Idempotency-Key`` header.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_context
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.onboarding.schemas import OnboardingResetRequest
from mesh.onboarding.service import OnboardingService

router = APIRouter(prefix="/api/v1", tags=["onboarding"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_WORKSPACE_NOT_FOUND = "workspace not found"


def _service(request: Request) -> OnboardingService:
    return request.app.state.onboarding_service


def _workspace_uuid(raw: str | None) -> uuid.UUID:
    # §3.3:workspace_id 缺失/非法 = 400 validation_error(参数校验);合法 UUID
    # 但非成员 = 404 not_found(不泄漏存在性,§5.3)由成员资格门裁决。
    if not raw:
        raise ValidationError("workspace_id is required", details={"field": "workspace_id"})
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(
            "invalid workspace_id", details={"field": "workspace_id"}
        ) from exc


async def _self_context(
    session: AsyncSession, user: User, workspace_id: str | None
) -> WorkspaceContext:
    """Membership gate for ?workspace_id= self-service endpoints."""
    return await resolve_workspace_context(
        session, user=user, workspace_id=_workspace_uuid(workspace_id)
    )


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"onboarding-write:{user.id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


@router.get("/onboarding/state")
async def get_onboarding_state(
    request: Request,
    workspace_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The caller's checklist progress (lazy seed+reconcile fallback, §3.5)."""
    context = await _self_context(session, user, workspace_id)
    state = await _service(request).get_state(
        workspace_id=context.workspace.id, member_id=context.member.id
    )
    return {"data": state}


@router.post("/onboarding/steps/{step_key}/complete")
async def complete_onboarding_step(
    request: Request,
    response: Response,
    step_key: str,
    workspace_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Manually complete a step (idempotent — no-op on repeat, §3.5)."""
    context = await _self_context(session, user, workspace_id)
    await _rate_limit_write(request, user, response)
    step = await _service(request).complete_step_manual(
        workspace_id=context.workspace.id,
        member_id=context.member.id,
        step_key=step_key,
    )
    return {"data": step}


@router.post("/onboarding/dismiss")
async def dismiss_onboarding(
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Dismiss the checklist (idempotent — first dismissed_at wins)."""
    context = await _self_context(session, user, workspace_id)
    await _rate_limit_write(request, user, response)
    result = await _service(request).dismiss(
        workspace_id=context.workspace.id, member_id=context.member.id
    )
    return {"data": result}


@router.post("/onboarding/restore")
async def restore_onboarding(
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Restore a dismissed checklist (idempotent)."""
    context = await _self_context(session, user, workspace_id)
    await _rate_limit_write(request, user, response)
    result = await _service(request).restore(
        workspace_id=context.workspace.id, member_id=context.member.id
    )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/onboarding/reset")
async def reset_onboarding(
    request: Request,
    response: Response,
    body: OnboardingResetRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
) -> dict:
    """Admin/owner reset of one member's checklist (§3.4)."""
    await _rate_limit_write(request, user, response)
    try:
        member_id = uuid.UUID(body.member_id)
    except ValueError as exc:
        raise NotFoundError("member not found") from exc
    state = await _service(request).reset(
        workspace_id=context.workspace.id,
        member_id=member_id,
        checklist=body.checklist,
    )
    return {"data": state}

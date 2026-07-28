"""Autopilot console routes (autopilot.md §3.1) + inbound webhook (§3.2).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Writes require ``autopilot:manage`` (admin/owner); reads need workspace
membership; the kill switch needs ``workspace:settings`` (admin). The
inbound webhook endpoint is the ONE exception to Bearer auth — it is
HMAC-signature verified (autopilot.md §3 / auth.md) and returns the bare
JSON contract external systems expect (NOT the §6.14 envelope).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.autopilot import approvals as approvals_mod
from mesh.autopilot import webhook as webhook_mod
from mesh.autopilot.schemas import (
    CreateAutopilotRequest,
    CreateWebhookSecretRequest,
    KillSwitchRequest,
    PatchAutopilotRequest,
    PreviewScheduleRequest,
    RunDecisionRequest,
    TestRunRequest,
)
from mesh.autopilot.service import AutopilotService
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import NotFoundError
from mesh.runtime.approvals import decide_approval

router = APIRouter(prefix="/api/v1", tags=["autopilot"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60


def _service(request: Request) -> AutopilotService:
    return request.app.state.autopilot_service


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _path_uuid(value: str, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(f"{what} not found") from exc


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"autopilot-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


# ---------------------------------------------------------------------------
# Rule CRUD / lifecycle
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/autopilots", status_code=201)
async def create_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateAutopilotRequest,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).create_rule(
        workspace_id=context.workspace.id,
        creator=context.member,
        payload=body.model_dump(exclude_none=False),
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/autopilots")
async def list_autopilots(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    status: str | None = None,
    trigger_type: str | None = None,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    return await _service(request).list_rules(
        workspace_id=context.workspace.id,
        status=status,
        trigger_type=trigger_type,
        search=search,
        cursor=cursor,
        limit=limit,
    )


@router.get("/workspaces/{workspace_id}/autopilots/kill-switch")
async def get_kill_switch_state(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    """Aggregate kill-switch state (any active rule ⇒ switch off)."""
    listing = await _service(request).list_rules(
        workspace_id=context.workspace.id, status="active", limit=1
    )
    return {"data": {"kill_switch": not listing["data"]}}


@router.post("/workspaces/{workspace_id}/autopilots/kill-switch")
async def set_kill_switch(
    request: Request,
    response: Response,
    workspace_id: str,
    body: KillSwitchRequest,
    context: WorkspaceContext = Depends(require_workspace("workspace:settings")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).kill_switch(
        workspace_id=context.workspace.id, enabled=body.enabled, reason=body.reason
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/autopilots/{autopilot_id}")
async def get_autopilot(
    request: Request,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    data = await _service(request).get_rule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
    )
    return {"data": data}


@router.patch("/workspaces/{workspace_id}/autopilots/{autopilot_id}")
async def update_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    autopilot_id: str,
    body: PatchAutopilotRequest,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).update_rule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
        patch=body.model_dump(exclude_unset=True),
    )
    return {"data": data}


@router.delete("/workspaces/{workspace_id}/autopilots/{autopilot_id}", status_code=204)
async def delete_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    await _rate_limit_write(request, user, response)
    await _service(request).delete_rule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
    )
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/autopilots/{autopilot_id}/pause")
async def pause_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).pause_rule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/autopilots/{autopilot_id}/resume")
async def resume_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).resume_rule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/autopilots/preview-schedule")
async def preview_schedule_stateless(
    request: Request,
    workspace_id: str,
    body: PreviewScheduleRequest,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    """Stateless cron preview — live "next 5 runs" in the editor, usable in
    create mode before any rule exists (autopilot.md §4.2)."""
    data = _service(request).preview_schedule_params(
        cron=body.cron, timezone=body.timezone, count=body.count
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/webhook-events")
async def list_webhook_events(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    autopilot_id: str | None = None,
    process_status: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """Inbound event audit trail (autopilot.md §4.1 最近事件)."""
    return await _service(request).list_webhook_events(
        workspace_id=context.workspace.id,
        autopilot_id=_path_uuid(autopilot_id, what="autopilot rule") if autopilot_id else None,
        process_status=process_status,
        cursor=cursor,
        limit=limit,
    )


@router.post("/workspaces/{workspace_id}/autopilots/{autopilot_id}/test-run")
async def test_run_autopilot(
    request: Request,
    response: Response,
    workspace_id: str,
    autopilot_id: str,
    body: TestRunRequest | None = None,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await _rate_limit_write(request, user, response)
    body = body or TestRunRequest()
    status_code, data = await _service(request).test_run(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
        actor=context.member,
        simulate_payload=body.simulate_trigger_payload,
        dry_run=body.dry_run,
    )
    return JSONResponse(status_code=status_code, content={"data": data})


@router.get("/workspaces/{workspace_id}/autopilots/{autopilot_id}/runs")
async def list_autopilot_runs(
    request: Request,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    return await _service(request).list_runs(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
        status=status,
        cursor=cursor,
        limit=limit,
    )


@router.get("/workspaces/{workspace_id}/autopilots/{autopilot_id}/preview-schedule")
async def preview_autopilot_schedule(
    request: Request,
    workspace_id: str,
    autopilot_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    count: int = 5,
) -> dict:
    data = await _service(request).preview_schedule(
        workspace_id=context.workspace.id,
        rule_id=_path_uuid(autopilot_id, what="autopilot rule"),
        count=count,
    )
    return {"data": data}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/autopilot-runs/{run_id}")
async def get_autopilot_run(
    request: Request,
    workspace_id: str,
    run_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    data = await _service(request).get_run(
        workspace_id=context.workspace.id, run_id=_path_uuid(run_id, what="autopilot run")
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/autopilot-runs/{run_id}/artifacts")
async def list_run_artifacts(
    request: Request,
    workspace_id: str,
    run_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return await _service(request).list_run_artifacts(
        workspace_id=context.workspace.id, run_id=_path_uuid(run_id, what="autopilot run")
    )


@router.post("/workspaces/{workspace_id}/autopilot-runs/{run_id}/cancel")
async def cancel_run(
    request: Request,
    response: Response,
    workspace_id: str,
    run_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).cancel_run(
        workspace_id=context.workspace.id, run_id=_path_uuid(run_id, what="autopilot run")
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/autopilot-runs/{run_id}/approve")
async def approve_run(
    request: Request,
    response: Response,
    workspace_id: str,
    run_id: str,
    body: RunDecisionRequest | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    """Thin wrapper over POST /api/v1/approvals/{id}/approve (README §6.10)."""
    await _rate_limit_write(request, user, response)
    return await _decide_run(request, context=context, run_id=run_id, body=body, approve=True)


@router.post("/workspaces/{workspace_id}/autopilot-runs/{run_id}/reject")
async def reject_run(
    request: Request,
    response: Response,
    workspace_id: str,
    run_id: str,
    body: RunDecisionRequest | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    """Thin wrapper over POST /api/v1/approvals/{id}/reject (README §6.10)."""
    await _rate_limit_write(request, user, response)
    return await _decide_run(request, context=context, run_id=run_id, body=body, approve=False)


async def _decide_run(
    request: Request,
    *,
    context: WorkspaceContext,
    run_id: str,
    body: RunDecisionRequest | None,
    approve: bool,
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        approval = await approvals_mod.decide_run_approval(
            session,
            workspace_id=context.workspace.id,
            run_id=_path_uuid(run_id, what="autopilot run"),
        )
        approval_id = approval.id
    decision = await decide_approval(
        session_factory,
        approval_id=approval_id,
        workspace_id=context.workspace.id,
        member=context.member,
        approve=approve,
        comment=(body.comment if body else None),
    )
    return {"data": decision}


# ---------------------------------------------------------------------------
# Webhook secrets (§3.1)
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/webhook-secrets", status_code=201)
async def create_webhook_secret(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateWebhookSecretRequest | None = None,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    """Create a credential pair — token + secret shown EXACTLY ONCE (§5.3)."""
    await _rate_limit_write(request, user, response)
    body = body or CreateWebhookSecretRequest()
    data = await _service(request).create_webhook_secret(
        workspace_id=context.workspace.id, member=context.member, label=body.label
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/webhook-secrets/{secret_id}/rotate", status_code=201)
async def rotate_webhook_secret(
    request: Request,
    response: Response,
    workspace_id: str,
    secret_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).rotate_webhook_secret(
        workspace_id=context.workspace.id,
        secret_id=_path_uuid(secret_id, what="webhook secret"),
        member=context.member,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/webhook-secrets")
async def list_webhook_secrets(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace("autopilot:manage")),
) -> dict:
    return await _service(request).list_webhook_secrets(workspace_id=context.workspace.id)


# ---------------------------------------------------------------------------
# Inbound webhook — HMAC signature auth, NOT Bearer (§3.2)
# ---------------------------------------------------------------------------


@router.post("/webhooks/inbound/{token}")
async def inbound_webhook(request: Request, token: str) -> JSONResponse:
    """HMAC-verified external event intake → dedup → audit → route (§2.5).

    Returns the bare JSON contract with external systems — signature
    failure is 401 and NEVER dispatches.
    """
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    raw_body = await request.body()
    headers = dict(request.headers)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        status_code, body = await webhook_mod.process_inbound(
            session,
            token=token,
            raw_body=raw_body,
            headers=headers,
            signing_secret=settings.jwt_secret,
            now=now,
            tolerance=settings.autopilot_webhook_timestamp_tolerance,
        )
    return JSONResponse(status_code=status_code, content=body)


__all__ = ["router"]

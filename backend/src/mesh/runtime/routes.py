"""Console API routes (runtime.md §3.1) + unified approvals (README §6.10).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Writes require ``agent:manage`` (admin/owner — the runtime fleet is
automation infrastructure); reads need workspace membership. Log streaming:
WebSocket is the primary channel (``execution:{id}:logs`` via the realtime
gateway); the SSE endpoint here is the proxy-friendly fallback sharing the
exact same offset protocol (§3.3/§4.9).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.runtime import approvals as approvals_mod
from mesh.runtime import logs as logs_mod
from mesh.runtime.attempts import cancel_execution, freeze_execution
from mesh.runtime.enqueue import queue_depth
from mesh.runtime.schemas import (
    ApprovalDecideRequest,
    CreateCredentialRequest,
    CreateRuntimeRequest,
    PatchRuntimeRequest,
)
from mesh.runtime.service import RuntimeService

router = APIRouter(prefix="/api/v1", tags=["runtime"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60
SSE_POLL_SECONDS = 1.0
SSE_MAX_SECONDS = 600


def _service(request: Request) -> RuntimeService:
    return request.app.state.runtime_service


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _path_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError("runtime not found") from exc


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"runtime-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


# ---------------------------------------------------------------------------
# Runtime CRUD / lifecycle
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/runtimes")
async def list_runtimes(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    status: str | None = None,
    kind: str | None = None,
    labels: str | None = None,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    # H3 (§3.1): labels filter "k1:v1,k2:v2" → JSONB containment match.
    parsed_labels: dict[str, str] | None = None
    if labels:
        parsed_labels = {}
        for pair in labels.split(","):
            key, sep, value = pair.partition(":")
            if not sep or not key:
                raise ValidationError(
                    "labels filter must be key:value pairs",
                    code="invalid_request",
                    details={"labels": labels},
                )
            parsed_labels[key.strip()] = value.strip()
    service = _service(request)
    result = await service.list_runtimes(
        workspace_id=context.workspace.id,
        status=status,
        kind=kind,
        labels=parsed_labels,
        search=search,
        cursor=cursor,
        limit=limit,
    )
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        result["queue_depth"] = await queue_depth(session, context.workspace.id)
    return result


@router.post("/workspaces/{workspace_id}/runtimes", status_code=201)
async def create_runtime(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateRuntimeRequest,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _service(request)
    data = await service.create_runtime(
        workspace_id=context.workspace.id,
        member=context.member,
        name=body.name,
        kind=body.kind,
        labels=body.labels,
        max_concurrent=body.max_concurrent,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/runtimes/{runtime_id}")
async def get_runtime(
    request: Request,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _service(request)
    data = await service.get_runtime(
        workspace_id=context.workspace.id, runtime_id=_path_uuid(runtime_id)
    )
    return {"data": data}


@router.patch("/workspaces/{workspace_id}/runtimes/{runtime_id}")
async def patch_runtime(
    request: Request,
    response: Response,
    workspace_id: str,
    runtime_id: str,
    body: PatchRuntimeRequest,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _service(request)
    data = await service.patch_runtime(
        workspace_id=context.workspace.id,
        runtime_id=_path_uuid(runtime_id),
        name=body.name,
        labels=body.labels,
        max_concurrent=body.max_concurrent,
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/runtimes/{runtime_id}:pause")
async def pause_runtime(
    request: Request,
    response: Response,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).pause_runtime(
        workspace_id=context.workspace.id, runtime_id=_path_uuid(runtime_id)
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/runtimes/{runtime_id}:resume")
async def resume_runtime(
    request: Request,
    response: Response,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).resume_runtime(
        workspace_id=context.workspace.id, runtime_id=_path_uuid(runtime_id)
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/runtimes/{runtime_id}/tokens:rotate")
async def rotate_runtime_token(
    request: Request,
    response: Response,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).rotate_runtime_token(
        workspace_id=context.workspace.id, runtime_id=_path_uuid(runtime_id)
    )
    return {"data": data}


@router.delete("/workspaces/{workspace_id}/runtimes/{runtime_id}", status_code=204)
async def delete_runtime(
    request: Request,
    response: Response,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    await _rate_limit_write(request, user, response)
    await _service(request).decommission_runtime(
        workspace_id=context.workspace.id, runtime_id=_path_uuid(runtime_id)
    )
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/runtimes/{runtime_id}/executions")
async def list_runtime_executions(
    request: Request,
    workspace_id: str,
    runtime_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    return await _service(request).list_executions(
        workspace_id=context.workspace.id,
        runtime_id=_path_uuid(runtime_id),
        status=status,
        cursor=cursor,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Executions: detail / cancel / freeze / logs
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/executions")
async def list_executions(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    agent_id: str | None = None,
    issue_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    return await _service(request).list_executions(
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id) if agent_id else None,
        issue_id=_path_uuid(issue_id) if issue_id else None,
        status=status,
        cursor=cursor,
        limit=limit,
    )


@router.get("/workspaces/{workspace_id}/executions/{execution_id}")
async def get_execution(
    request: Request,
    workspace_id: str,
    execution_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    data = await _service(request).get_execution(
        workspace_id=context.workspace.id, execution_id=_path_uuid(execution_id)
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/executions/{execution_id}:cancel")
async def cancel_execution_route(
    request: Request,
    response: Response,
    workspace_id: str,
    execution_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:trigger")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await cancel_execution(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        execution_id=_path_uuid(execution_id),
        member_id=context.member.id,
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/executions/{execution_id}:freeze")
async def freeze_execution_route(
    request: Request,
    response: Response,
    workspace_id: str,
    execution_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await freeze_execution(
        request.app.state.session_factory,
        workspace_id=context.workspace.id,
        execution_id=_path_uuid(execution_id),
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/executions/{execution_id}/logs")
async def get_execution_logs(
    request: Request,
    workspace_id: str,
    execution_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    offset: int = 0,
    stream: str | None = None,
    limit: int = 1000,
) -> dict:
    data = await logs_mod.read_execution_logs(
        request.app.state.session_factory,
        request.app.state.storage,
        workspace_id=context.workspace.id,
        execution_id=_path_uuid(execution_id),
        offset=max(offset, 0),
        stream=stream if stream in ("stdout", "stderr") else None,
        max_lines=max(1, min(limit, 5000)),
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/executions/{execution_id}/logs/stream")
async def stream_execution_logs(
    request: Request,
    workspace_id: str,
    execution_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    offset: int = 0,
) -> StreamingResponse:
    """SSE fallback channel (§3.3) — same offset protocol as the WS primary.

    Three-phase: storage backfill → live tail polling → ``end`` frame.
    """
    session_factory = request.app.state.session_factory
    storage = request.app.state.storage
    ws_id = context.workspace.id
    exec_id = _path_uuid(execution_id)

    async def generate():
        cursor = max(offset, 0)
        deadline = asyncio.get_event_loop().time() + SSE_MAX_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            if await request.is_disconnected():
                return
            payload = await logs_mod.read_execution_logs(
                session_factory,
                storage,
                workspace_id=ws_id,
                execution_id=exec_id,
                offset=cursor,
            )
            for line in payload["lines"]:
                cursor = max(cursor, line["offset"] + len(line["line"].encode("utf-8")) + 1)
                frame = {
                    "type": "log",
                    "stream": line["stream"],
                    "offset": line["offset"],
                    "line": line["line"],
                }
                yield f"data: {json.dumps(frame)}\n\n"
            status = payload["execution_status"]
            terminal = status in ("completed", "failed", "timeout", "cancelled")
            if terminal and not payload["lines"]:
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "end", "status": status, "final_offset": cursor}
                    )
                    + "\n\n"
                )
                return
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "heartbeat",
                        "server_time": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n\n"
            )
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Credentials (plaintext only ever IN)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/credentials")
async def list_credentials(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
) -> dict:
    return await _service(request).list_credentials(workspace_id=context.workspace.id)


@router.post("/workspaces/{workspace_id}/credentials", status_code=201)
async def create_credential(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateCredentialRequest,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await _service(request).create_credential(
        workspace_id=context.workspace.id,
        name=body.name,
        kind=body.kind,
        scope=body.scope,
        value=body.value,
        env_name=body.env_name,
        redact_in_logs=body.redact_in_logs,
        expires_in_seconds=body.expires_in_seconds,
    )
    return {"data": data}


@router.delete("/workspaces/{workspace_id}/credentials/{credential_id}", status_code=204)
async def delete_credential(
    request: Request,
    response: Response,
    workspace_id: str,
    credential_id: str,
    context: WorkspaceContext = Depends(require_workspace("agent:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    await _rate_limit_write(request, user, response)
    await _service(request).delete_credential(
        workspace_id=context.workspace.id, credential_id=_path_uuid(credential_id)
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Approvals (README §6.10 — unified inbox under the workspace)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/approvals")
async def list_approvals(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    role: str | None = None,
    status: str | None = None,
) -> dict:
    from sqlalchemy import select

    from mesh.db.models.runtime import Approval
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        stmt = select(Approval).where(Approval.workspace_id == context.workspace.id)
        if status:
            stmt = stmt.where(Approval.status == status)
        if role == "mine":
            # F9 (§6.10): "待我审批" unified inbox = pending approvals
            # (decision permission is enforced on the decide endpoints).
            stmt = stmt.where(Approval.status == "pending")
        stmt = stmt.order_by(Approval.requested_at.desc()).limit(100)
        rows = (await session.execute(stmt)).scalars().all()
        return {
            "data": [approvals_mod._approval_response(a, execution_status=None) for a in rows],
            "next_cursor": None,
        }


@router.get("/workspaces/{workspace_id}/approvals/{approval_id}")
async def get_approval(
    request: Request,
    workspace_id: str,
    approval_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    from sqlalchemy import select

    from mesh.db.models.runtime import Approval
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        approval = (
            await session.execute(
                select(Approval).where(
                    Approval.id == _path_uuid(approval_id),
                    Approval.workspace_id == context.workspace.id,
                )
            )
        ).scalar_one_or_none()
        if approval is None:
            raise NotFoundError("approval not found")
        return {"data": approvals_mod._approval_response(approval, execution_status=None)}


@router.post("/workspaces/{workspace_id}/approvals/{approval_id}/approve")
async def approve_approval(
    request: Request,
    response: Response,
    workspace_id: str,
    approval_id: str,
    body: ApprovalDecideRequest,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await approvals_mod.decide_approval(
        request.app.state.session_factory,
        approval_id=_path_uuid(approval_id),
        workspace_id=context.workspace.id,
        member=context.member,
        approve=True,
        comment=body.comment,
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/approvals/{approval_id}/reject")
async def reject_approval(
    request: Request,
    response: Response,
    workspace_id: str,
    approval_id: str,
    body: ApprovalDecideRequest,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    data = await approvals_mod.decide_approval(
        request.app.state.session_factory,
        approval_id=_path_uuid(approval_id),
        workspace_id=context.workspace.id,
        member=context.member,
        approve=False,
        comment=body.comment,
    )
    return {"data": data}

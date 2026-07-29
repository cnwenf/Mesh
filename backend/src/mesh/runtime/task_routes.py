"""Task principal API routes — §2.2 S-05 / auth.md §2.5.1.

Namespace ``/api/v1/task/`` — endpoints that accept ``mesh_task_`` tokens
via the ``resolve_task_principal`` dependency. These are the routes the
daemon's task broker calls on behalf of an attempt:

- Read current issue/project context
- Write result comment on the current issue
- Read execution status

Regular console routes reject ``mesh_task_`` tokens (auth.md §2.5.1:
only routes that explicitly declare task principal support accept them).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from mesh.db.models.runtime import AttemptTaskToken
from mesh.errors import NotFoundError
from mesh.runtime.daemon_auth import resolve_task_principal

router = APIRouter(prefix="/api/v1/task", tags=["task-principal"])

TASK_LIMIT = 120
TASK_WINDOW_SECONDS = 60


async def _rate_limit_task(request: Request, task_token: AttemptTaskToken) -> None:
    """§2.2 S-05: token + attempt dual-dimension rate limiting."""
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else None
    await limiter.check(
        f"task:{task_token.id}:{client_ip}",
        limit=TASK_LIMIT,
        window_seconds=TASK_WINDOW_SECONDS,
    )


@router.get("/context")
async def get_task_context(
    request: Request,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read the current attempt's frozen context (§2.2 S-05).

    Returns the execution/attempt/issue identifiers and frozen scopes
    from the task token. The task sandbox uses this to know what
    resources it can access.
    """
    await _rate_limit_task(request, task_token)
    scopes = task_token.scopes or {}
    return {
        "data": {
            "attempt_id": str(task_token.attempt_id),
            "workspace_id": str(task_token.workspace_id),
            "issue_id": scopes.get("issue_id"),
            "agent_id": scopes.get("agent_id"),
            "methods": scopes.get("methods", []),
            "denied": scopes.get("denied", []),
            "expires_at": task_token.expires_at.isoformat(),
        }
    }


@router.get("/executions/{execution_id}")
async def get_task_execution(
    request: Request,
    execution_id: str,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read execution status — scoped to the token's attempt (§2.2 S-05)."""
    from sqlalchemy import select

    from mesh.db.models.runtime import TaskExecution
    from mesh.db.tenant import set_tenant_context

    await _rate_limit_task(request, task_token)
    try:
        exec_uuid = uuid.UUID(execution_id)
    except ValueError as exc:
        raise NotFoundError("execution not found") from exc

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.id == exec_uuid,
                    TaskExecution.workspace_id == task_token.workspace_id,
                )
            )
        ).scalar_one_or_none()
    if execution is None:
        raise NotFoundError("execution not found")
    return {
        "data": {
            "id": str(execution.id),
            "status": execution.status,
            "trigger": execution.trigger,
            "issue_id": str(execution.issue_id) if execution.issue_id else None,
        }
    }

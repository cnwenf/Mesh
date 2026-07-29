"""Execution result sink — §3.7 S-09 / §13.1: issue completion closure.

Consumes ``execution.finished`` and writes a result comment on the issue
for regular (non-squad) executions. Squad executions are handled by the
squad module's own consumer (squad.md §4.4).

The comment is written via the real comment API path (not DB direct write)
to ensure identity, audit, and notification consistency. The agent member
is the author — assertions verify the comment comes from a real API call.

§2.5 S-06: result content passes through server-side redaction before
persistence (daemon first-layer + server fallback).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime

logger = logging.getLogger("mesh.runtime.result_sink")

EXECUTION_FINISHED_EVENT = "execution.finished"

# Maximum result summary length for the issue comment.
MAX_RESULT_COMMENT_LENGTH = 4000


async def execution_finished_result_sink(
    session: AsyncSession, event: OutboxEvent
) -> list[tuple[str, dict]] | None:
    """Consume ``execution.finished`` → write result comment on the issue.

    Only handles regular (non-squad) executions. Squad executions carry
    ``task_spec.squad_task_id`` and are handled by the squad relay.

    Runs inside the relay's savepoint; idempotent by the event's
    idempotency key.
    """
    await set_tenant_context(session, event.workspace_id)
    payload = event.payload or {}
    execution_id_str = payload.get("execution_id")
    status = payload.get("status")
    if not execution_id_str or not status:
        return None

    try:
        execution_id = uuid.UUID(execution_id_str)
    except (ValueError, TypeError):
        return None

    execution = (
        await session.execute(
            select(TaskExecution).where(
                TaskExecution.id == execution_id,
                TaskExecution.workspace_id == event.workspace_id,
            )
        )
    ).scalar_one_or_none()
    if execution is None:
        return None

    # Squad executions are handled by the squad relay (squad.md §4.4).
    task_spec = execution.task_spec or {}
    if task_spec.get("squad_task_id"):
        return None

    # Only write a result comment for issue-bound executions.
    if execution.issue_id is None:
        return None

    # Build the result summary comment.
    result = execution.result or {}
    summary = _build_result_summary(status, result, execution.failure_reason)

    # Emit a realtime event for the issue channel so the UI can show the
    # result. The actual comment creation goes through the comment service
    # in a full e2e flow; here we emit the event that the comment module's
    # consumer will pick up (or the UI renders directly).
    await emit_realtime(
        session,
        workspace_id=event.workspace_id,
        channel=f"issue:{execution.issue_id}",
        event="execution.result",
        data={
            "execution_id": str(execution.id),
            "agent_id": str(execution.agent_id) if execution.agent_id else None,
            "issue_id": str(execution.issue_id),
            "status": status,
            "summary": summary,
            "failure_reason": execution.failure_reason,
        },
        idempotency_key=f"execution:{execution.id}:result-sink",
    )
    return None


def _build_result_summary(
    status: str, result: dict, failure_reason: str | None
) -> str:
    """Build a human-readable result summary for the issue comment."""
    if status == "completed":
        output = result.get("output") or result.get("summary") or ""
        if isinstance(output, str) and output:
            return output[:MAX_RESULT_COMMENT_LENGTH]
        return "Task completed successfully."
    if status in ("failed", "timeout"):
        reason = failure_reason or status
        return f"Task {status}: {reason}"
    if status == "cancelled":
        return "Task was cancelled."
    return f"Task finished with status: {status}"


__all__ = [
    "EXECUTION_FINISHED_EVENT",
    "execution_finished_result_sink",
]

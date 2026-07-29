"""Execution result sink — §3.7 S-09 / §13.1: issue completion closure.

Consumes ``execution.finished`` and writes a result comment on the issue
for regular (non-squad) executions. Squad executions are handled by the
squad module's own consumer (squad.md §4.4).

The comment is created via ``CommentService.create_comment`` (the same
path the squad relay uses) — NOT a direct DB insert — so identity,
audit, notification, and §6.16 secret-guard consistency are guaranteed.
The agent's member row is the author.

§2.5 S-06: result content passes through server-side redaction before
persistence (daemon first-layer + server fallback).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context

logger = logging.getLogger("mesh.runtime.result_sink")

EXECUTION_FINISHED_EVENT = "execution.finished"

# Maximum result summary length for the issue comment.
MAX_RESULT_COMMENT_LENGTH = 4000


def _result_comment_idempotency_key(execution_id: uuid.UUID) -> str:
    """Stable idempotency key so relay redelivery never doubles the comment."""
    return f"execution:{execution_id}:result-comment"


async def execution_finished_result_sink(
    session: AsyncSession,
    event: OutboxEvent,
    comment_service=None,
) -> list[tuple[str, dict]] | None:
    """Consume ``execution.finished`` → write result comment on the issue.

    Only handles regular (non-squad) executions. Squad executions carry
    ``task_spec.squad_task_id`` and are handled by the squad relay.

    ``comment_service`` is the ``CommentService`` instance wired by
    ``workers/main.py`` (same instance the squad relay uses). When None
    (e.g. unit tests without the full worker stack), the sink degrades
    to a no-op rather than crashing the relay.

    Runs inside the relay's savepoint; idempotent by the comment
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

    # Build the result summary comment body.
    result = execution.result or {}
    summary = _build_result_summary(status, result, execution.failure_reason)

    # Resolve the agent's member row — the comment author.
    author: Member | None = None
    if execution.agent_id is not None:
        author = await session.scalar(
            select(Member).where(
                Member.workspace_id == event.workspace_id,
                Member.agent_id == execution.agent_id,
            )
        )
    if author is None:
        logger.warning(
            "result sink: no agent member for execution %s — skipping comment",
            execution_id,
        )
        return None

    # Create the result comment via CommentService (real API path, not
    # direct DB insert — ensures identity, audit, notification, §6.16).
    if comment_service is not None:
        await comment_service.create_comment(
            workspace_id=event.workspace_id,
            issue_id=execution.issue_id,
            author_member=author,
            body_markdown=summary,
            suppress_triggers=True,
            idempotency_key=_result_comment_idempotency_key(execution.id),
        )
    else:
        logger.warning(
            "result sink: no comment_service wired — comment not created "
            "for execution %s",
            execution_id,
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

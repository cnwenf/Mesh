"""Outbox relay handlers for the squad module (README §6.6).

Two derived-action consumers, both registered on the relay by
``workers/main.py``:

* ``squad.plan_decided`` — emitted by the unified approvals entry
  (``runtime/approvals.py``) and the reaper when a ``squad_plan`` approval is
  approved / rejected / expired. Applies the decision onto the root task
  (squad.md §6.10).
* ``execution.finished`` — emitted by ``runtime/attempts.py`` on any execution
  terminal state. Correlates the execution back to its squad task via
  ``task_spec.squad_task_id`` and maps the terminal state (squad.md §4.4).
  When a squad-assigned root reaches ``done`` it also writes the leader's
  aggregate summary back to the parent issue as a comment (§S8 / §4.3-7).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.squad import IssueSquadAssignment, SquadTask
from mesh.db.tenant import set_tenant_context
from mesh.squad.tasks import (
    apply_plan_decision_tx,
    load_task,
    observe_execution_finished_tx,
)

# Writeback comment bodies are capped so a pathological result_summary can
# never approach the 1 MiB comment byte guard.
_SUMMARY_WRITEBACK_MAX_CHARS = 4000

logger = logging.getLogger(__name__)


def summary_writeback_key(root_id: uuid.UUID) -> str:
    """The ONE idempotency key both writeback paths share (§S8): the relay
    (agent aggregator run) and the service (synchronous human-leader resolve)
    post with this key, so whichever lands first wins and the other no-ops —
    the parent issue never gets duplicate summary comments."""
    return f"squad-task:{root_id}:summary-writeback"


def summary_writeback_body(title_snapshot: str, result_summary: str) -> str:
    """Shared comment body for the §S8/§4.3-7 parent-issue summary writeback."""
    summary = (result_summary or "")[:_SUMMARY_WRITEBACK_MAX_CHARS]
    return (
        f"**Squad task completed — {title_snapshot}**\n\n"
        f"Leader summary:\n\n{summary}"
    )


def _parse(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def squad_plan_decided_handler(session: AsyncSession, event: OutboxEvent) -> None:
    """Apply a squad_plan approval decision onto the root task."""
    await set_tenant_context(session, event.workspace_id)
    payload = event.payload or {}
    task_id = _parse(payload.get("subject_task_id"))
    decision = str(payload.get("decision") or "")
    if task_id is None or decision not in ("approved", "rejected", "expired"):
        return
    try:
        task = await load_task(session, workspace_id=event.workspace_id, task_id=task_id, for_update=True)
    except Exception:  # noqa: BLE001 — task gone; nothing to apply
        return
    await apply_plan_decision_tx(
        session,
        workspace_id=event.workspace_id,
        task=task,
        decision=decision,
        now=datetime.now(UTC),
    )


async def _writeback_root_summary_tx(
    session: AsyncSession, event: OutboxEvent, comment_service
) -> None:
    """If this execution's tree root just finished ``done`` on a squad
    assignment, post the leader's aggregate summary to the parent issue.

    Idempotent by comment idempotency key — relay replays never duplicate the
    writeback. The visibility check runs on the relay's own session (which
    sees the uncommitted observe effects); the comment commits on the comment
    service's connection, so a crash between the two can at most leave one
    orphan comment, never a duplicate or a loss.
    """
    if comment_service is None:
        return
    payload = event.payload or {}
    execution_id = _parse(payload.get("execution_id"))
    if execution_id is None:
        return
    execution = await session.scalar(
        select(TaskExecution).where(
            TaskExecution.workspace_id == event.workspace_id, TaskExecution.id == execution_id
        )
    )
    if execution is None:
        return
    task_id = _parse((execution.task_spec or {}).get("squad_task_id"))
    if task_id is None:
        return
    try:
        task = await load_task(session, workspace_id=event.workspace_id, task_id=task_id)
    except Exception:  # noqa: BLE001 — task gone; nothing to write back
        return
    root_id = task.root_task_id or task.id
    root = task if task.id == root_id else (
        await session.execute(
            select(SquadTask).where(
                SquadTask.workspace_id == event.workspace_id, SquadTask.id == root_id
            )
        )
    ).scalar_one_or_none()
    if root is None or root.status != "done" or not root.result_summary:
        return
    assigned = await session.scalar(
        select(IssueSquadAssignment.id).where(
            IssueSquadAssignment.workspace_id == event.workspace_id,
            IssueSquadAssignment.root_task_id == root.id,
        )
    )
    if assigned is None:
        return  # not a squad-assigned root
    author = None
    if root.orchestrator_id is not None:
        author = await session.scalar(
            select(Member).where(
                Member.workspace_id == event.workspace_id, Member.id == root.orchestrator_id
            )
        )
    if author is None:
        return
    await comment_service.create_comment(
        workspace_id=event.workspace_id,
        issue_id=root.issue_id,
        author_member=author,
        body_markdown=summary_writeback_body(root.title_snapshot, root.result_summary),
        suppress_triggers=True,
        idempotency_key=summary_writeback_key(root.id),
    )


def make_squad_execution_finished_handler(comment_service=None):
    """Build the ``execution.finished`` handler, optionally wiring the
    §S8/§4.3-7 summary writeback to the parent issue via ``comment_service``."""

    async def handler(session: AsyncSession, event: OutboxEvent) -> None:
        """Observe an execution terminal state and map it onto the squad task."""
        await set_tenant_context(session, event.workspace_id)
        payload = event.payload or {}
        execution_id = _parse(payload.get("execution_id"))
        if execution_id is None:
            return
        await observe_execution_finished_tx(
            session,
            workspace_id=event.workspace_id,
            execution_id=execution_id,
            status=str(payload.get("status") or ""),
            failure_reason=payload.get("failure_reason"),
            now=datetime.now(UTC),
        )
        try:
            await _writeback_root_summary_tx(session, event, comment_service)
        except Exception:  # noqa: BLE001 — writeback is a best-effort side
            # effect; a failure must not bounce the terminal observation.
            logger.exception(
                "squad summary writeback failed for execution %s", payload.get("execution_id")
            )

    return handler


# Default (writeback-less) handler for callers that only want observation.
squad_execution_finished_handler = make_squad_execution_finished_handler()


__all__ = [
    "squad_plan_decided_handler",
    "squad_execution_finished_handler",
    "make_squad_execution_finished_handler",
    "summary_writeback_body",
    "summary_writeback_key",
]

"""Issue-facing projection of the runtime execution lifecycle.

Runtime remains the source of truth for executions and attempts. This module
only performs the issue-owned side effects required by agent.md §4.7:
an authoritative issue-channel frame, a readable activity entry, and the two
automatic semantic status changes (start → in progress, success → in review).
All writes happen in the caller's transaction with the runtime transition.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.issue.statuses import resolve_default_status
from mesh.outbox.service import emit_realtime

_PHASES = frozenset(
    {
        "queued",
        "claimed",
        "started",
        "progress",
        "awaiting_approval",
        "requeued",
        "completed",
        "failed",
        "timeout",
        "cancelled",
    }
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _agent_member_id(
    session: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID | None
) -> uuid.UUID | None:
    if agent_id is None:
        return None
    return await session.scalar(
        select(Member.id).where(
            Member.workspace_id == workspace_id,
            Member.agent_id == agent_id,
        )
    )


async def _project_is_public(session: AsyncSession, issue: Issue) -> bool:
    if issue.project_id is None:
        return True
    visibility = await session.scalar(
        select(Project.visibility).where(
            Project.workspace_id == issue.workspace_id,
            Project.id == issue.project_id,
            Project.deleted_at.is_(None),
        )
    )
    return visibility == "public"


async def emit_workspace_execution_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID | None,
    event: str,
    data: dict,
    idempotency_key: str,
) -> bool:
    """Publish a workspace execution frame only when it is workspace-visible.

    A workspace channel cannot filter individual frames per subscriber. An
    issue-bound execution therefore enters that broad stream only for a public
    or project-less issue; private-project runs remain on ``issue:{id}`` and
    ``execution:{id}``, whose subscription checkers enforce the resource ACL.
    Returns whether a frame was emitted (useful for contract tests).
    """
    if issue_id is not None:
        issue = await session.scalar(
            select(Issue).where(
                Issue.workspace_id == workspace_id,
                Issue.id == issue_id,
                Issue.deleted_at.is_(None),
            )
        )
        if issue is None or not await _project_is_public(session, issue):
            return False
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"workspace:{workspace_id}:executions",
        event=event,
        data=data,
        idempotency_key=idempotency_key,
    )
    return True


async def _move_semantic_status(
    session: AsyncSession,
    *,
    issue: Issue,
    target_category: str,
    actor_member_id: uuid.UUID | None,
) -> None:
    """Apply the automatic semantic transition without inventing a status id.

    The workspace/project's configured status for the target category is used;
    terminal human decisions are never regressed by a late runtime frame.
    """
    if issue.state_category == target_category:
        return
    if issue.state_category in {"done", "cancelled"}:
        return
    if target_category == "in_progress" and issue.state_category == "in_review":
        return

    target = await resolve_default_status(
        session,
        workspace_id=issue.workspace_id,
        project_id=issue.project_id,
        category=target_category,
    )
    old_category = issue.state_category
    now = datetime.now(UTC)
    issue.status_id = target.id
    issue.state_category = target.category
    issue.completed_at = now if target.category == "done" else None
    issue.version += 1
    issue.updated_at = now
    session.add(
        IssueActivity(
            workspace_id=issue.workspace_id,
            issue_id=issue.id,
            actor_member_id=actor_member_id,
            field="state_category",
            old_value=old_category,
            new_value=target.category,
        )
    )
    updated = {
        "id": str(issue.id),
        "changes": {
            "status_id": str(target.id),
            "state_category": target.category,
        },
        "version": issue.version,
        "visibility": {
            "project_id": str(issue.project_id) if issue.project_id else None,
            "state_category": target.category,
        },
        "updated_at": _iso(now),
    }
    moved = {
        "id": str(issue.id),
        "from": {"state_category": old_category},
        "to": {"state_category": target.category},
    }
    for event, data in (("issue.updated", updated), ("issue.moved", moved)):
        await emit_realtime(
            session,
            workspace_id=issue.workspace_id,
            channel=f"issue:{issue.id}",
            event=event,
            data=data,
            idempotency_key=f"execution-status:{issue.id}:{target.category}:{issue.version}:{event}",
        )
        if await _project_is_public(session, issue):
            await emit_realtime(
                session,
                workspace_id=issue.workspace_id,
                channel=f"workspace:{issue.workspace_id}:issues",
                event=event,
                data=data,
                idempotency_key=(
                    f"execution-status:{issue.id}:{target.category}:{issue.version}:{event}:workspace"
                ),
            )


async def record_issue_execution_phase(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID | None,
    execution_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    phase: str,
    attempt_id: uuid.UUID | None = None,
    runtime_id: uuid.UUID | None = None,
    runtime_name: str | None = None,
    failure_reason: str | None = None,
    comment_id: uuid.UUID | None = None,
    agent_name: str | None = None,
    event_key: str | None = None,
) -> None:
    """Project one execution phase onto its issue, if it has one.

    The payload always carries the final logical execution id and the roster
    member id for the Agent; temporary outbox ids never enter the UI contract.
    """
    if issue_id is None or phase not in _PHASES:
        return
    issue = await session.scalar(
        select(Issue)
        .where(
            Issue.workspace_id == workspace_id,
            Issue.id == issue_id,
            Issue.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if issue is None:
        return

    member_id = await _agent_member_id(
        session,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if phase == "queued":
        await _move_semantic_status(
            session,
            issue=issue,
            target_category="in_progress",
            actor_member_id=member_id,
        )
        session.add(
            IssueActivity(
                workspace_id=workspace_id,
                issue_id=issue.id,
                actor_member_id=member_id,
                field="execution",
                old_value=None,
                new_value={"state": "started", "execution_id": str(execution_id)},
            )
        )
    elif phase == "completed":
        await _move_semantic_status(
            session,
            issue=issue,
            target_category="in_review",
            actor_member_id=member_id,
        )

    payload = {
        "execution_id": str(execution_id),
        "issue_id": str(issue.id),
        "agent_id": str(agent_id) if agent_id else None,
        "agent_member_id": str(member_id) if member_id else None,
        "agent_name": agent_name,
        "attempt_id": str(attempt_id) if attempt_id else None,
        "runtime_id": str(runtime_id) if runtime_id else None,
        "runtime_name": runtime_name,
        "status": phase,
        "failure_reason": failure_reason,
        "comment_id": str(comment_id) if comment_id else None,
    }
    phase_key = event_key or (str(attempt_id) if attempt_id else "logical")
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"issue:{issue.id}",
        event=f"execution.{phase}",
        data=payload,
        idempotency_key=f"execution:{execution_id}:issue:{phase}:{phase_key}",
    )


__all__ = ["emit_workspace_execution_event", "record_issue_execution_phase"]

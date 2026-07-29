"""触发者归属与 aha 证据链(onboarding.md §1.2.1 步骤 4/5,R3/R4,T34)。

R4 严格 trigger_member_id 归属:assign 经 `issue_activity` 分派留痕 actor
(建 issue 即分派无留痕时回退 reporter),mention 经 `execution.enqueue`
outbox 幂等键 → `comment_mentions.triggered_execution_id`(skeleton 锚点)
→ 评论作者。末步证据链:agent 回评(含聚合组 latest_comment_id)+ 该成员
触发的已完成执行 → 四元组 evidence。
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution

_IDEMPOTENCY_PREFIX = "ws"
_ASSIGN_FIELD = "assignee_id"
_TRIGGER_TRIGGERS = ("assign", "mention")


def _parse_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


# --- trigger attribution (R3/R4 — strict trigger_member_id) --------------------


def _scoped_key(workspace_id: uuid.UUID, key: str) -> str:
    return f"{_IDEMPOTENCY_PREFIX}:{workspace_id}:{key}"


async def _mention_trigger_member(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution: TaskExecution
) -> uuid.UUID | None:
    """@-trigger owner = the MENTION comment's author (§1.2.1 step 4).

    ``comment_mentions.triggered_execution_id`` stores the ``execution.enqueue``
    OUTBOX EVENT id (the skeleton anchor), so the chain is execution →
    outbox row (via the §6.5 idempotency key, workspace-scoped on the outbox
    side) → comment_mentions → comments.author_id.
    """
    if not execution.idempotency_key:
        return None
    outbox_id = await session.scalar(
        select(OutboxEvent.id).where(
            OutboxEvent.workspace_id == workspace_id,
            OutboxEvent.idempotency_key == _scoped_key(workspace_id, execution.idempotency_key),
        )
    )
    if outbox_id is None:
        return None
    return await session.scalar(
        select(Comment.author_id)
        .join(
            CommentMention,
            and_(
                CommentMention.comment_id == Comment.id,
                CommentMention.workspace_id == Comment.workspace_id,
            ),
        )
        .where(
            CommentMention.workspace_id == workspace_id,
            CommentMention.triggered_execution_id == outbox_id,
            CommentMention.deleted_at.is_(None),
        )
        .order_by(CommentMention.created_at.asc())
        .limit(1)
    )


async def _assign_trigger_member(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution: TaskExecution
) -> uuid.UUID | None:
    """Assign-trigger owner = the member who performed the assignment.

    Resolved from the append-only ``issue_activity`` trail: the latest
    ``field='assignee_id'`` row whose new value is this execution's agent
    (member) and which predates the enqueue. When NO assignee-activity row
    predates the enqueue, the agent was assigned AT ISSUE CREATION (the
    create path writes no activity row) — the dispatching member is then
    the issue creator (``reporter_id``), the one who chose the assignee.
    """
    if execution.agent_id is None or execution.issue_id is None:
        return None
    agent_member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.agent_id == execution.agent_id,
            Member.member_type == "agent",
        )
    )
    if agent_member is None:
        return None
    actor = await session.scalar(
        select(IssueActivity.actor_member_id)
        .where(
            IssueActivity.workspace_id == workspace_id,
            IssueActivity.issue_id == execution.issue_id,
            IssueActivity.field == _ASSIGN_FIELD,
            IssueActivity.actor_member_id.is_not(None),
            IssueActivity.new_value == func.to_jsonb(str(agent_member.id)),
            IssueActivity.created_at <= execution.queued_at,
        )
        .order_by(IssueActivity.created_at.desc())
        .limit(1)
    )
    if actor is not None:
        return actor
    assignee_edits_before = await session.scalar(
        select(func.count(IssueActivity.id)).where(
            IssueActivity.workspace_id == workspace_id,
            IssueActivity.issue_id == execution.issue_id,
            IssueActivity.field == _ASSIGN_FIELD,
            IssueActivity.created_at <= execution.queued_at,
        )
    )
    if assignee_edits_before:
        return None  # reassigned by others before this trigger — not creation
    issue = await session.scalar(
        select(Issue).where(
            Issue.workspace_id == workspace_id, Issue.id == execution.issue_id
        )
    )
    return issue.reporter_id if issue is not None else None


async def resolve_execution_trigger_member(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution: TaskExecution
) -> uuid.UUID | None:
    """The member who triggered this execution (assign/mention only)."""
    if execution.trigger == "mention":
        return await _mention_trigger_member(
            session, workspace_id=workspace_id, execution=execution
        )
    if execution.trigger == "assign":
        return await _assign_trigger_member(
            session, workspace_id=workspace_id, execution=execution
        )
    return None


async def execution_triggered_by_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    execution: TaskExecution,
    member_id: uuid.UUID,
) -> bool:
    """True when ``member_id`` is THIS execution's trigger owner (R4)."""
    trigger_member = await resolve_execution_trigger_member(
        session, workspace_id=workspace_id, execution=execution
    )
    return trigger_member == member_id


# --- aha evidence chain (T34 final step) ---------------------------------------


async def evaluate_agent_reply_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    notification: Notification,
    member_id: uuid.UUID,
) -> dict | None:
    """Validate the §1.2.1 step-5 evidence chain for one read notification.

    Conditions (all required): the notification references a comment
    authored by an AGENT member; that comment's issue has a COMPLETED
    execution by that agent; the execution was triggered by ``member_id``
    (R4 — reading someone else's triggered execution never counts). Returns
    the persisted four-tuple evidence ``{execution_id, comment_id,
    notification_id, trigger_member_id}`` or None.
    """
    if notification.comment_id is None:
        return None
    comment = await session.scalar(
        select(Comment).where(
            Comment.workspace_id == workspace_id,
            Comment.id == notification.comment_id,
        )
    )
    # Aggregated inbox groups keep the group's FIRST comment on the row and
    # track the newest one under payload.latest_comment_id (comment-inbox.md
    # §2.6 aggregation): when the latest comment is the agent reply, it is
    # the evidence anchor (opening the group surfaces that reply).
    latest_raw = (notification.payload or {}).get("latest_comment_id")
    latest_id = _parse_uuid(latest_raw)
    if latest_id is not None and latest_id != notification.comment_id:
        latest = await session.scalar(
            select(Comment).where(
                Comment.workspace_id == workspace_id,
                Comment.id == latest_id,
            )
        )
        if latest is not None and latest.issue_id == (
            comment.issue_id if comment is not None else latest.issue_id
        ):
            comment = latest
    if (
        comment is None
        or comment.author_kind != "member"
        or comment.author_id is None
    ):
        return None
    author = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.id == comment.author_id,
        )
    )
    if author is None or author.member_type != "agent":
        return None

    execution: TaskExecution | None = None
    if notification.execution_id is not None:
        candidate = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.id == notification.execution_id,
            )
        )
        if candidate is not None and candidate.issue_id == comment.issue_id:
            execution = candidate
    if execution is None or execution.status != "completed":
        execution = await session.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.issue_id == comment.issue_id,
                TaskExecution.agent_id == author.agent_id,
                TaskExecution.status == "completed",
            )
            .order_by(TaskExecution.queued_at.desc())
            .limit(1)
        )
    if execution is None or execution.status != "completed":
        return None
    if not await execution_triggered_by_member(
        session, workspace_id=workspace_id, execution=execution, member_id=member_id
    ):
        return None
    return {
        "execution_id": str(execution.id),
        "comment_id": str(comment.id),
        "notification_id": str(notification.id),
        "trigger_member_id": str(member_id),
    }



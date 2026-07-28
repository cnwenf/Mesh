"""Shared factories for onboarding unit tests (onboarding.md).

Builds the cross-module fact rows (agent members, issues, executions,
mention chains, comments, notifications) the seeding/reconcile/consumer
logic reads — real rows in the real test database, no mocks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from mesh.db.models.agent import Agent
from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution


async def make_agent_member(session_factory, workspace, *, owner_user, name="Bot"):
    """Create an agent + its roster member row."""
    async with session_factory() as session, session.begin():
        agent = Agent(workspace_id=workspace.id, name=name, owner_user_id=owner_user.id)
        session.add(agent)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            member_type="agent",
            agent_id=agent.id,
            role="member",
            status="active",
        )
        session.add(member)
    return agent, member


async def make_issue(session_factory, workspace, *, reporter_id=None, assignee_id=None, title="t"):
    async with session_factory() as session, session.begin():
        status = IssueStatus(
            workspace_id=workspace.id, name=f"Todo {uuid.uuid4().hex[:6]}", category="todo"
        )
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key="inbox",
            number=abs(uuid.uuid4().int) % 1_000_000,
            identifier=f"T-{uuid.uuid4().hex[:6]}",
            title=title,
            status_id=status.id,
            state_category="todo",
            reporter_id=reporter_id,
            assignee_id=assignee_id,
        )
        session.add(issue)
    return issue


async def make_execution(
    session_factory,
    workspace,
    *,
    agent_id,
    issue_id=None,
    trigger="assign",
    status="completed",
    idempotency_key=None,
    queued_at=None,
):
    async with session_factory() as session, session.begin():
        execution = TaskExecution(
            workspace_id=workspace.id,
            agent_id=agent_id,
            issue_id=issue_id,
            trigger=trigger,
            status=status,
            idempotency_key=idempotency_key,
        )
        if queued_at is not None:
            execution.queued_at = queued_at
        session.add(execution)
    return execution


async def make_comment(session_factory, workspace, issue, *, author_member):
    async with session_factory() as session, session.begin():
        comment = Comment(
            workspace_id=workspace.id,
            issue_id=issue.id,
            author_kind="member",
            author_id=author_member.id,
            body_markdown="hello",
            body_text="hello",
        )
        session.add(comment)
    return comment


async def make_mention_execution(
    session_factory,
    workspace,
    *,
    comment,
    mentioned_agent_member,
    agent,
    issue,
    status="completed",
):
    """A mention-triggered execution with the full skeleton chain.

    Mirrors production: the ``execution.enqueue`` outbox event carries the
    scoped idempotency key; ``comment_mentions.triggered_execution_id``
    stores that OUTBOX EVENT id; the materialized execution carries the
    unscoped key.
    """
    key = f"mention-{uuid.uuid4().hex}"
    async with session_factory() as session, session.begin():
        outbox_event = OutboxEvent(
            workspace_id=workspace.id,
            event_type="execution.enqueue",
            payload={"trigger": "mention", "comment_id": str(comment.id)},
            idempotency_key=f"ws:{workspace.id}:{key}",
        )
        session.add(outbox_event)
        await session.flush()
        mention = CommentMention(
            workspace_id=workspace.id,
            comment_id=comment.id,
            mentioned_id=mentioned_agent_member.id,
            triggered_execution_id=outbox_event.id,
        )
        session.add(mention)
        execution = TaskExecution(
            workspace_id=workspace.id,
            agent_id=agent.id,
            issue_id=issue.id,
            trigger="mention",
            status=status,
            idempotency_key=key,
        )
        session.add(execution)
    return execution, outbox_event


async def make_assign_activity(session_factory, workspace, issue, *, actor_member, agent_member):
    """Append-only assign trail row (field='assignee_id', new = agent member id)."""
    async with session_factory() as session, session.begin():
        activity = IssueActivity(
            workspace_id=workspace.id,
            issue_id=issue.id,
            actor_member_id=actor_member.id,
            field="assignee_id",
            old_value=None,
            new_value=str(agent_member.id),
        )
        session.add(activity)
    return activity


async def make_notification(
    session_factory,
    workspace,
    *,
    recipient_member,
    comment=None,
    execution=None,
    read_at=None,
):
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=workspace.id,
            recipient_id=recipient_member.id,
            type="comment_created",
            priority="normal",
            comment_id=comment.id if comment is not None else None,
            execution_id=execution.id if execution is not None else None,
            read_at=read_at,
        )
        session.add(notification)
    return notification


def earlier(**overrides) -> datetime:
    return datetime.now(UTC) - timedelta(**overrides)

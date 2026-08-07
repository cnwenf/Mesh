"""CommentService + §6.9 mention-trigger semantics (real PostgreSQL).

Service-level coverage of comment-inbox.md §3.1/§3.5: CRUD, single-level
threading, reactions, idempotency, optimistic concurrency, system comments,
and every mention-trigger row of the README §6.9 matrix (via the outbox
rows the business transaction writes).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from mesh.comment_inbox.mentions import EXECUTION_ENQUEUE_EVENT
from mesh.comment_inbox.service import CommentService
from mesh.db.models.agent import Agent
from mesh.db.models.audit import AuditLog
from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    PayloadTooLargeError,
)
from mesh.issue.statuses import seed_default_statuses
from mesh.runtime.enqueue import enqueue_execution_handler

pytestmark = pytest.mark.unit


async def _workspace(factory) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _human(factory, workspace, name: str) -> Member:
    async with factory() as session, session.begin():
        user = User(email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@x.io", display_name=name)
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="member"
        )
        session.add(member)
    return member


async def _agent(factory, workspace, name: str) -> Member:
    async with factory() as session, session.begin():
        # 0017_agent enforces members.agent_id → agents; seed the agents row
        # (with an owner user) before the roster row.
        owner = User(email=f"agent-owner-{uuid.uuid4().hex[:8]}@x.io", display_name=name)
        session.add(owner)
        await session.flush()
        agent = Agent(workspace_id=workspace.id, name=name, owner_user_id=owner.id)
        session.add(agent)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            member_type="agent",
            agent_id=agent.id,
            role="member",
            display_override=name,
        )
        session.add(member)
    return member


async def _issue(factory, workspace, reporter: Member, title: str = "Bug") -> Issue:
    namespace = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        # Match the workspace-creation invariant so tests that materialize an
        # execution can project semantic status changes as production does.
        await seed_default_statuses(session, workspace_id=workspace.id)
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.project_id.is_(None),
                IssueStatus.category == "todo",
            )
        )
        assert status is not None
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=namespace,
            number=1,
            identifier=f"{namespace.upper()}-1",
            title=title,
            status_id=status.id,
            state_category="todo",
            reporter_id=reporter.id,
        )
        session.add(issue)
    return issue


@pytest.fixture
async def env(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Alice")
    other = await _human(session_factory, workspace, "Bob")
    agent = await _agent(session_factory, workspace, "code-reviewer")
    issue = await _issue(session_factory, workspace, author)
    service = CommentService(session_factory, max_agent_chain_depth=3)
    return {
        "factory": session_factory,
        "workspace": workspace,
        "author": author,
        "other": other,
        "agent": agent,
        "issue": issue,
        "service": service,
    }


async def _outbox_rows(factory, event_type: str) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == event_type)
            )
        ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# CRUD + threading
# ---------------------------------------------------------------------------


async def test_create_render_and_list(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="**hello** world",
    )
    assert created["body_html"] == "<p><strong>hello</strong> world</p>\n"
    assert created["author"]["name"] == "Alice"
    assert created["author"]["member_type"] == "human"
    assert created["reply_count"] is None  # counts only on single-get/list

    items, cursor = await service.list_comments(
        workspace_id=env["workspace"].id, issue_id=issue.id,
        viewer_member_id=author.id, member=author,
    )
    assert cursor is None
    assert len(items) == 1
    assert items[0]["reply_count"] == 1 * 0  # no replies yet
    assert items[0]["preview_replies"] == []


async def test_removed_agent_author_keeps_agent_identity_and_status(env):
    service, issue, agent = env["service"], env["issue"], env["agent"]
    await service.create_comment(
        workspace_id=env["workspace"].id,
        issue_id=issue.id,
        author_member=agent,
        body_markdown="historical agent comment",
    )
    async with env["factory"]() as session, session.begin():
        persisted_agent = await session.get(Member, agent.id)
        assert persisted_agent is not None
        persisted_agent.status = "removed"

    items, _ = await service.list_comments(
        workspace_id=env["workspace"].id,
        issue_id=issue.id,
        viewer_member_id=env["author"].id,
        member=env["author"],
    )

    assert items[0]["author"]["member_type"] == "agent"
    assert items[0]["author"]["status"] == "removed"


async def test_reply_threading_depth_one(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    root = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="root",
    )
    reply = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="reply", parent_id=uuid.UUID(root["id"]),
    )
    assert reply["thread_root_id"] == root["id"]
    # Replying to a reply is normalized to the root so storage remains one level.
    reply2 = await service.create_comment(
        workspace_id=env["workspace"].id,
        issue_id=issue.id,
        author_member=author,
        body_markdown="reply2",
        parent_id=uuid.UUID(reply["id"]),
    )
    assert reply2["parent_id"] == root["id"]
    assert reply2["thread_root_id"] == root["id"]

    items, _ = await service.list_comments(
        workspace_id=env["workspace"].id, issue_id=issue.id,
        viewer_member_id=author.id, member=author,
    )
    assert len(items) == 1  # top-level only
    assert items[0]["reply_count"] == 2
    assert items[0]["preview_replies"][0]["id"] == reply["id"]

    replies, _ = await service.list_replies(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(root["id"]),
        viewer_member_id=author.id, member=author,
    )
    assert [r["id"] for r in replies] == [reply["id"], reply2["id"]]


async def test_edit_sets_edited_and_optimistic_lock(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="v1",
    )
    assert created["edited_at"] is None
    updated = await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False, body_markdown="v2",
        expected_updated_at=created["updated_at"],
    )
    assert updated["edited_at"] is not None
    assert updated["body_markdown"] == "v2"
    with pytest.raises(ConflictError):
        await service.update_comment(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            editor_member=author, is_manager=False, body_markdown="v3",
            expected_updated_at=created["updated_at"],  # stale
        )


async def test_edit_forbidden_for_non_author_non_manager(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="mine",
    )
    with pytest.raises(ForbiddenError):
        await service.update_comment(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            editor_member=env["other"], is_manager=False, body_markdown="hijack",
        )


async def test_soft_delete_placeholder_and_gone(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="to delete",
    )
    await service.delete_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        actor_member=author, is_manager=False,
    )
    fetched = await service.get_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        viewer_member_id=author.id, member=author,
    )
    assert fetched["deleted_at"] is not None
    assert fetched["body_markdown"] == ""  # placeholder, body redacted
    with pytest.raises(GoneError):
        await service.delete_comment(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            actor_member=author, is_manager=False,
        )


async def test_resolve_reopen_only_thread_root(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    root = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="root",
    )
    reply = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="reply", parent_id=uuid.UUID(root["id"]),
    )
    with pytest.raises(BusinessRuleError) as exc:
        await service.set_thread_resolved(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(reply["id"]),
            actor_member=author, resolved=True,
        )
    assert exc.value.code == "not_thread_root"
    resolved = await service.set_thread_resolved(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(root["id"]),
        actor_member=author, resolved=True,
    )
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == {
        "id": str(author.id),
        "member_type": "human",
        "name": "Alice",
    }
    # A state mutation returns the same thread aggregate contract as the list:
    # resolving must not make the already-loaded replies disappear in clients.
    assert resolved["reply_count"] == 1
    assert [item["id"] for item in resolved["preview_replies"]] == [reply["id"]]
    reopened = await service.set_thread_resolved(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(root["id"]),
        actor_member=author, resolved=False,
    )
    assert reopened["resolved_at"] is None
    assert reopened["resolved_by"] is None
    assert reopened["reply_count"] == 1
    assert [item["id"] for item in reopened["preview_replies"]] == [reply["id"]]
    async with env["factory"]() as session:
        history = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.workspace_id == env["workspace"].id,
                        AuditLog.resource_type == "comment_thread",
                        AuditLog.resource_id == uuid.UUID(root["id"]),
                    )
                    .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                )
            )
            .scalars()
            .all()
        )
    assert [entry.action for entry in history] == [
        "comment.thread_resolved",
        "comment.thread_reopened",
    ]
    assert history[0].metadata_["resolved_by_id"] == str(author.id)
    assert history[1].metadata_["previous_resolved_by_id"] == str(author.id)
    assert history[1].metadata_["previous_resolved_at"] == history[0].metadata_["resolved_at"]


# ---------------------------------------------------------------------------
# reactions
# ---------------------------------------------------------------------------


async def test_reaction_add_duplicate_remove(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="react to me",
    )
    aggregation = await service.add_reaction(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        actor_member=author, emoji="👍",
    )
    assert aggregation[0]["emoji"] == "👍"
    assert aggregation[0]["count"] == 1
    assert aggregation[0]["reacted_by_me"] is True
    with pytest.raises(ConflictError):
        await service.add_reaction(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            actor_member=author, emoji="👍",
        )
    # second member reacts
    await service.add_reaction(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        actor_member=env["other"], emoji="👍",
    )
    listed = await service.list_reactions(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        viewer_member_id=author.id, member=author,
    )
    assert listed[0]["count"] == 2
    assert {a["name"] for a in listed[0]["actors"]} == {"Alice", "Bob"}
    await service.remove_reaction(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        actor_member=author, emoji="👍",
    )
    with pytest.raises(NotFoundError):
        await service.remove_reaction(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            actor_member=author, emoji="👍",
        )


async def test_reaction_emoji_validation(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="x",
    )
    from mesh.errors import ValidationError

    with pytest.raises(ValidationError):
        await service.add_reaction(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
            actor_member=author, emoji="   ",
        )


# ---------------------------------------------------------------------------
# idempotency + system comments + subscriptions
# ---------------------------------------------------------------------------


async def test_idempotency_key_returns_first_result(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    key = uuid.uuid4().hex
    first = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="once", idempotency_key=key,
    )
    second = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown="twice?", idempotency_key=key,
    )
    assert second["id"] == first["id"]
    assert second["body_markdown"] == "once"


async def test_concurrent_idempotency_key_uses_savepoint_and_returns_winner(env):
    """Two real PostgreSQL transactions that both pass SELECT-first converge.

    The barrier is placed in mention resolution, after the idempotency lookup
    and before INSERT, so this deterministically exercises the unique-index
    race rather than merely issuing two requests close together.
    """
    service, issue, author = env["service"], env["issue"], env["author"]
    original = service._resolve_mentions
    arrived = 0
    lock = asyncio.Lock()
    both_ready = asyncio.Event()

    async def synchronized_resolve(*args, **kwargs):
        nonlocal arrived
        rendered = await original(*args, **kwargs)
        async with lock:
            arrived += 1
            if arrived == 2:
                both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5)
        return rendered

    service._resolve_mentions = synchronized_resolve
    request_id = str(uuid.uuid4())
    first, second = await asyncio.wait_for(
        asyncio.gather(
            service.create_comment(
                workspace_id=env["workspace"].id,
                issue_id=issue.id,
                author_member=author,
                body_markdown="first transaction",
                idempotency_key=request_id,
            ),
            service.create_comment(
                workspace_id=env["workspace"].id,
                issue_id=issue.id,
                author_member=author,
                body_markdown="second transaction",
                idempotency_key=request_id,
            ),
        ),
        timeout=10,
    )

    assert first["id"] == second["id"]
    assert first["body_markdown"] == second["body_markdown"]
    assert first["client_request_id"] == request_id
    async with env["factory"]() as session:
        rows = (
            await session.execute(
                select(Comment).where(
                    Comment.workspace_id == env["workspace"].id,
                    Comment.idempotency_key == request_id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    realtime_rows = await _outbox_rows(env["factory"], "realtime.publish")
    created_frame = next(
        row for row in realtime_rows if row.payload.get("event") == "comment.created"
    )
    assert created_frame.payload["data"]["client_request_id"] == request_id


async def test_opaque_idempotency_key_is_not_reflected(env):
    created = await env["service"].create_comment(
        workspace_id=env["workspace"].id,
        issue_id=env["issue"].id,
        author_member=env["author"],
        body_markdown="opaque key",
        idempotency_key="caller-secret-or-opaque-value",
    )
    assert created["client_request_id"] is None


async def test_system_comment_not_deletable_via_api(env):
    service, issue = env["service"], env["issue"]
    system = await service.create_system_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id,
        body_markdown="状态变更为 **done**",
    )
    assert system["author_kind"] == "system"
    assert system["author"] is None
    with pytest.raises(ForbiddenError):
        await service.delete_comment(
            workspace_id=env["workspace"].id, comment_id=uuid.UUID(system["id"]),
            actor_member=env["author"], is_manager=True,
        )


async def test_system_comment_cannot_be_resolved_or_reopened(env):
    service, issue = env["service"], env["issue"]
    system = await service.create_system_comment(
        workspace_id=env["workspace"].id,
        issue_id=issue.id,
        body_markdown="状态变更为 **done**",
    )

    for resolved in (True, False):
        with pytest.raises(ForbiddenError):
            await service.set_thread_resolved(
                workspace_id=env["workspace"].id,
                comment_id=uuid.UUID(system["id"]),
                actor_member=env["author"],
                resolved=resolved,
            )


async def test_author_and_mentioned_subscribed(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=f"[@Bob-ish](mention://member/{env['other'].id}) look",
    )
    from mesh.db.models.notification import IssueSubscription

    async with env["factory"]() as session:
        rows = (
            await session.execute(
                select(IssueSubscription).where(
                    IssueSubscription.issue_id == issue.id
                )
            )
        ).scalars().all()
    by_member = {row.subscriber_id: row.reason for row in rows}
    assert by_member[author.id] == "participated"
    assert by_member[env["other"].id] == "mentioned"


# ---------------------------------------------------------------------------
# README §6.9 trigger matrix (mention path)
# ---------------------------------------------------------------------------


def _mention_link(member_id) -> str:
    return f"[@agent](mention://member/{member_id})"


async def test_publish_mention_agent_enqueues_once(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    body = f"{_mention_link(agent.id)} 跑一下 —— 再说一次 {_mention_link(agent.id)}"
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=body,
    )
    # Same comment, same agent twice → one pending enqueue. Until the runtime
    # materializes it, the API never exposes that outbox id as an execution id.
    assert created["triggered_execution_ids"] == []
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 1
    payload = enqueues[0].payload
    assert payload["trigger"] == "mention"
    assert payload["agent_member_id"] == str(agent.id)
    assert enqueues[0].idempotency_key is not None
    # Pending correlation is separate from the canonical execution field.
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention.triggered_execution_id is None
    assert mention.pending_trigger_event_id == enqueues[0].id

    # The producer has no task_executions row/id yet. It must not expose the
    # enqueue outbox id as though it were a materialized execution id; the
    # runtime enqueue consumer owns the canonical execution.queued frame.
    realtime = await _outbox_rows(env["factory"], "realtime.publish")
    assert [row for row in realtime if row.payload.get("event") == "execution.queued"] == []

    # Consume the real producer row. The canonical materializer must preserve
    # the originating comment id on its FINAL execution.queued frame so a
    # failed mention run can be retried from the placeholder card.
    async with env["factory"]() as session, session.begin():
        event = await session.get(OutboxEvent, enqueues[0].id)
        assert event is not None
        await enqueue_execution_handler(session, event)
    async with env["factory"]() as session:
        execution = (await session.execute(select(TaskExecution))).scalar_one()
        queued_frames = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "realtime.publish",
                    OutboxEvent.payload["event"].astext == "execution.queued",
                    OutboxEvent.payload["data"]["execution_id"].astext == str(execution.id),
                )
            )
        ).scalars().all()
    issue_frame = next(
        row for row in queued_frames if row.payload["channel"] == f"issue:{issue.id}"
    )
    assert issue_frame.payload["data"]["comment_id"] == created["id"]
    assert issue_frame.payload["data"]["execution_id"] == str(execution.id)
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention is not None
    assert mention.triggered_execution_id == execution.id
    assert mention.pending_trigger_event_id is None


async def test_suppress_triggers_notifies_only(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id), suppress_triggers=True,
    )
    assert created["triggered_execution_ids"] == []
    assert await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT) == []
    # the mention itself is still recorded (notify-only, not run)
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention is not None and mention.triggered_execution_id is None


async def test_no_trigger_permission_rejects_agent_mention(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    with pytest.raises(BusinessRuleError) as exc:
        await service.create_comment(
            workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
            body_markdown=_mention_link(agent.id), can_trigger_agents=False,
        )
    assert exc.value.code == "mention_invalid"


async def test_edit_add_mention_enqueues_only_added(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    agent_a = env["agent"]
    agent_b = await _agent(env["factory"], env["workspace"], "test-runner")
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=f"start {_mention_link(agent_a.id)}",
    )
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 1
    # Edit adds B: only B enqueues; neither pending outbox id is rendered as a
    # logical execution id before materialization.
    updated = await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False,
        body_markdown=f"start {_mention_link(agent_a.id)} plus {_mention_link(agent_b.id)}",
    )
    assert updated["triggered_execution_ids"] == []
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 2  # exactly one NEW enqueue for B
    agent_ids = {event.payload["agent_member_id"] for event in enqueues}
    assert agent_ids == {str(agent_a.id), str(agent_b.id)}


async def test_edit_remove_then_readd_mention_enqueues_fresh_execution(env):
    # L4: removing then RE-ADDing the same @agent on one comment must enqueue
    # a NEW execution — the edit path anchors the §6.5 key on a per-edit epoch
    # (comment.id + edited_at), so the old outbox row (still inside the
    # retention window) is not returned in place of a fresh enqueue.
    service, issue, author, agent = (
        env["service"], env["issue"], env["author"], env["agent"],
    )
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    first = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(first) == 1
    assert created["triggered_execution_ids"] == []

    # remove the mention — soft-delete, no enqueue, nothing cancelled (§6.9)
    await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False,
        body_markdown="no agent here anymore",
    )
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 1

    await asyncio.sleep(0.002)  # distinct edited_at epoch (µs clock resolution)
    readded = await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False,
        body_markdown=f"come back {_mention_link(agent.id)}",
    )
    rows = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(rows) == 2  # a brand-new enqueue, NOT the stale row replay
    assert {row.id for row in rows} != {first[0].id}
    assert readded["triggered_execution_ids"] == []
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention is not None
    assert mention.pending_trigger_event_id in {row.id for row in rows} - {first[0].id}


async def test_edit_unrelated_text_does_not_retrigger(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=f"{_mention_link(agent.id)} run",
    )
    await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False,
        body_markdown=f"{_mention_link(agent.id)} run (typo fixed)",
    )
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 1


async def test_edit_remove_mention_soft_deletes_no_cancel(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=f"{_mention_link(agent.id)} run",
    )
    await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False, body_markdown="no mentions now",
    )
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention.deleted_at is not None  # soft-deleted…
    # …and the enqueue event stands (no cancel semantics, §6.9)
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 1


async def test_new_comment_same_agent_enqueues_new_execution(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    first = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    second = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    assert first["triggered_execution_ids"] == second["triggered_execution_ids"] == []
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 2


async def test_agent_self_mention_does_not_trigger(env):
    service, issue, agent = env["service"], env["issue"], env["agent"]
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=agent,
        body_markdown=f"noting {_mention_link(agent.id)} myself",
    )
    assert created["triggered_execution_ids"] == []
    assert await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT) == []


async def test_agent_chain_depth_guard(env):
    service, issue = env["service"], env["issue"]
    agent_a = env["agent"]
    agent_b = await _agent(env["factory"], env["workspace"], "peer-agent")
    # human starts a thread; agents then alternate replies mentioning each
    # other — with max_chain_depth=3 the 4th agent comment's trigger drops.
    root = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=env["author"],
        body_markdown=f"{_mention_link(agent_a.id)} start",
    )
    assert len(await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)) == 1
    current_root = uuid.UUID(root["id"])
    enqueued = 1
    for speaker, target in [
        (agent_a, agent_b), (agent_b, agent_a), (agent_a, agent_b), (agent_b, agent_a)
    ]:
        await service.create_comment(
            workspace_id=env["workspace"].id, issue_id=issue.id, author_member=speaker,
            body_markdown=f"{_mention_link(target.id)} next",
            parent_id=current_root,
        )
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    # 1 (human→A) + 2 agent-triggered within depth, rest dropped by the guard
    assert len(enqueues) < 5
    # audit trail for the dropped trigger exists
    from mesh.db.models.audit import AuditLog

    async with env["factory"]() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "agent_trigger_skipped_chain_depth")
            )
        ).scalars().all()
    assert audits, "chain-depth drop must leave an audit record"
    assert enqueued == 1


# ---------------------------------------------------------------------------
# body limits + unknown mentions
# ---------------------------------------------------------------------------


async def test_unknown_mention_id_is_422(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    ghost = uuid.uuid4()
    with pytest.raises(BusinessRuleError) as exc:
        await service.create_comment(
            workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
            body_markdown=_mention_link(ghost),
        )
    assert exc.value.code == "mention_invalid"


async def test_body_too_large(env):
    service, issue, author = env["service"], env["issue"], env["author"]
    with pytest.raises(PayloadTooLargeError) as exc:
        await service.create_comment(
            workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
            body_markdown="x" * (1024 * 1024 + 1),
        )
    assert exc.value.code == "payload_too_large"
    assert exc.value.status_code == 413


async def test_cross_issue_parent_rejected_by_db(env):
    # README §6.2 rule 7: overlapping composite FK refuses cross-issue parenting.
    factory, workspace, author = env["factory"], env["workspace"], env["author"]
    other_issue = await _issue(factory, workspace, author, title="Other")
    async with factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        foreign_root = Comment(
            workspace_id=workspace.id, issue_id=other_issue.id,
            author_kind="member", author_id=author.id, body_markdown="foreign",
        )
        session.add(foreign_root)
        await session.flush()
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            session.add(
                Comment(
                    workspace_id=workspace.id, issue_id=env["issue"].id,
                    parent_id=foreign_root.id, thread_root_id=foreign_root.id,
                    author_kind="member", author_id=author.id, body_markdown="cross",
                )
            )
            await session.flush()


# ---------------------------------------------------------------------------
# HIGH-2: mention path passes the lifecycle/visibility guardrail gate
# ---------------------------------------------------------------------------


async def _set_agent_lifecycle(factory, agent_member: Member, lifecycle_status: str) -> None:
    async with factory() as session, session.begin():
        agent = await session.scalar(
            select(Agent).where(Agent.id == agent_member.agent_id)
        )
        assert agent is not None
        agent.lifecycle_status = lifecycle_status


async def _skipped_frames(factory, event_type: str = "realtime.publish") -> list:
    rows = await _outbox_rows(factory, event_type)
    return [r for r in rows if r.payload.get("event") == "agent.trigger_skipped"]


async def test_mention_paused_agent_skips_with_trigger_skipped(env):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    await _set_agent_lifecycle(env["factory"], agent, "paused")
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    assert created["triggered_execution_ids"] == []
    assert await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT) == []
    skipped = await _skipped_frames(env["factory"])
    assert len(skipped) == 1
    data = skipped[0].payload["data"]
    assert data["reason"] == "lifecycle_not_active"
    assert data["trigger"] == "mention"
    assert data["agent_id"] == str(agent.agent_id)
    assert skipped[0].payload["channel"] == f"workspace:{env['workspace'].id}:agents"


@pytest.mark.parametrize("lifecycle", ["disabled", "archived"])
async def test_mention_disabled_or_archived_agent_skips(env, lifecycle):
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    await _set_agent_lifecycle(env["factory"], agent, lifecycle)
    created = await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    assert created["triggered_execution_ids"] == []
    assert await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT) == []
    skipped = await _skipped_frames(env["factory"])
    assert [frame.payload["data"]["reason"] for frame in skipped] == ["lifecycle_not_active"]


async def test_mention_active_agent_still_triggers(env):
    """Control: an active agent is unaffected by the guardrail wiring."""
    service, issue, agent, author = env["service"], env["issue"], env["agent"], env["author"]
    await service.create_comment(
        workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
        body_markdown=_mention_link(agent.id),
    )
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 1
    assert await _skipped_frames(env["factory"]) == []


async def _private_agent_setup(session_factory):
    """A private agent plus its owner member and an unrelated stranger member."""
    workspace = await _workspace(session_factory)
    owner_user = User(
        email=f"owner-{uuid.uuid4().hex[:6]}@x.io", display_name="Owner"
    )
    async with session_factory() as session, session.begin():
        session.add(owner_user)
        await session.flush()
        owner_member = Member(
            workspace_id=workspace.id, member_type="human",
            user_id=owner_user.id, role="member",
        )
        session.add(owner_member)
    agent = await _agent(session_factory, workspace, "private-helper")
    # Re-point ownership at the owner member's user and make it private.
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(Agent).where(Agent.id == agent.agent_id))
        assert row is not None
        row.owner_user_id = owner_user.id
        row.visibility = "private"
    stranger = await _human(session_factory, workspace, "Stranger")
    issue = await _issue(session_factory, workspace, owner_member)
    service = CommentService(session_factory, max_agent_chain_depth=3)
    return {
        "factory": session_factory,
        "workspace": workspace,
        "owner_member": owner_member,
        "stranger": stranger,
        "agent": agent,
        "issue": issue,
        "service": service,
    }


async def test_mention_private_agent_by_non_owner_skips_visibility(session_factory):
    env = await _private_agent_setup(session_factory)
    created = await env["service"].create_comment(
        workspace_id=env["workspace"].id, issue_id=env["issue"].id,
        author_member=env["stranger"], body_markdown=_mention_link(env["agent"].id),
    )
    assert created["triggered_execution_ids"] == []
    assert await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT) == []
    skipped = await _skipped_frames(env["factory"])
    assert len(skipped) == 1
    data = skipped[0].payload["data"]
    assert data["reason"] == "visibility_private"
    assert data["trigger"] == "mention"


async def test_mention_private_agent_by_owner_triggers(session_factory):
    env = await _private_agent_setup(session_factory)
    created = await env["service"].create_comment(
        workspace_id=env["workspace"].id, issue_id=env["issue"].id,
        author_member=env["owner_member"], body_markdown=_mention_link(env["agent"].id),
    )
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 1
    assert enqueues[0].payload["trigger"] == "mention"
    assert await _skipped_frames(env["factory"]) == []
    assert created is not None

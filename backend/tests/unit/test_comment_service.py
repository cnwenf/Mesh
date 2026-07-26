"""CommentService + §6.9 mention-trigger semantics (real PostgreSQL).

Service-level coverage of comment-inbox.md §3.1/§3.5: CRUD, single-level
threading, reactions, idempotency, optimistic concurrency, system comments,
and every mention-trigger row of the README §6.9 matrix (via the outbox
rows the business transaction writes).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.comment_inbox.mentions import EXECUTION_ENQUEUE_EVENT
from mesh.comment_inbox.service import CommentService
from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
)

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
        member = Member(
            workspace_id=workspace.id,
            member_type="agent",
            agent_id=uuid.uuid4(),
            role="member",
            display_override=name,
        )
        session.add(member)
    return member


async def _issue(factory, workspace, reporter: Member, title: str = "Bug") -> Issue:
    namespace = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        status = IssueStatus(
            workspace_id=workspace.id,
            name=f"Todo-{namespace}",
            category="todo",
            is_default=False,
        )
        session.add(status)
        await session.flush()
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
    # replying to a reply is rejected (depth is exactly 1)
    with pytest.raises(BusinessRuleError) as exc:
        await service.create_comment(
            workspace_id=env["workspace"].id, issue_id=issue.id, author_member=author,
            body_markdown="reply2", parent_id=uuid.UUID(reply["id"]),
        )
    assert exc.value.code == "reply_depth_exceeded"

    items, _ = await service.list_comments(
        workspace_id=env["workspace"].id, issue_id=issue.id,
        viewer_member_id=author.id, member=author,
    )
    assert len(items) == 1  # top-level only
    assert items[0]["reply_count"] == 1
    assert items[0]["preview_replies"][0]["id"] == reply["id"]

    replies, _ = await service.list_replies(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(root["id"]),
        viewer_member_id=author.id,
    )
    assert [r["id"] for r in replies] == [reply["id"]]


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
        viewer_member_id=author.id,
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
    assert resolved["resolved_by"] == str(author.id)
    reopened = await service.set_thread_resolved(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(root["id"]),
        actor_member=author, resolved=False,
    )
    assert reopened["resolved_at"] is None


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
        viewer_member_id=author.id,
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
    # same comment, same agent twice → exactly one execution (uq_mentions)
    assert len(created["triggered_execution_ids"]) == 1
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 1
    payload = enqueues[0].payload
    assert payload["trigger"] == "mention"
    assert payload["agent_member_id"] == str(agent.id)
    assert enqueues[0].idempotency_key is not None
    # triggered_execution_id persisted on the mention row (skeleton = outbox id)
    async with env["factory"]() as session:
        mention = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(created["id"])
            )
        )
    assert mention.triggered_execution_id == enqueues[0].id


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
    # edit adds B: only B enqueues (one NEW execution; the response carries
    # every active trigger on the comment, so both A and B are listed)
    updated = await service.update_comment(
        workspace_id=env["workspace"].id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False,
        body_markdown=f"start {_mention_link(agent_a.id)} plus {_mention_link(agent_b.id)}",
    )
    assert set(updated["triggered_execution_ids"]) - set(
        created["triggered_execution_ids"]
    ) and len(updated["triggered_execution_ids"]) == 2
    enqueues = await _outbox_rows(env["factory"], EXECUTION_ENQUEUE_EVENT)
    assert len(enqueues) == 2  # exactly one NEW enqueue for B
    agent_ids = {event.payload["agent_member_id"] for event in enqueues}
    assert agent_ids == {str(agent_a.id), str(agent_b.id)}


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
    assert first["triggered_execution_ids"] != second["triggered_execution_ids"]
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
    with pytest.raises(BusinessRuleError) as exc:
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

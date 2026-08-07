"""Inbox & notification fan-out tests (README §6.13 matrix — real PostgreSQL).

Covers the relay-side fan-out handler: routing (subscribers ∪ mentioned ∪
assignee, self-suppression, mute, humans-only), the priority matrix, the 60 s
aggregation window, quiet hours, delivery ledger (R3 destination grain),
realtime emission, plus the inbox read side (list/filter/group, read/archive
state, unread count, preferences) and the digest sweep.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from mesh.comment_inbox.inbox import InboxService
from mesh.comment_inbox.notifications import (
    NotificationFanoutHandler,
    inbox_channel,
    send_digest_emails,
)
from mesh.db.models.agent import Agent
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.notification import (
    IssueSubscription,
    Notification,
    NotificationDelivery,
    NotificationPreference,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import NotFoundError, ValidationError

pytestmark = pytest.mark.unit

# Notification rows carry DB-side now() timestamps; the injected clock only
# drives window / quiet-hours math, so anchor it to the real present.
T0 = datetime.now(UTC).replace(microsecond=0)


async def _workspace(factory) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _human(factory, workspace, name: str, email: str | None = None) -> Member:
    async with factory() as session, session.begin():
        user = User(
            email=email or f"{name.lower()}-{uuid.uuid4().hex[:6]}@x.io",
            display_name=name,
        )
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


async def _issue(factory, workspace, reporter: Member, assignee: Member | None = None) -> Issue:
    namespace = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        status = IssueStatus(
            workspace_id=workspace.id, name=f"S-{namespace}", category="todo"
        )
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=namespace,
            number=1,
            identifier=f"{namespace.upper()}-1",
            title="Login broken",
            status_id=status.id,
            state_category="todo",
            reporter_id=reporter.id,
            assignee_id=assignee.id if assignee else None,
        )
        session.add(issue)
    return issue


async def _subscribe(factory, workspace, issue, member, reason="manual", muted=False) -> None:
    async with factory() as session, session.begin():
        session.add(
            IssueSubscription(
                workspace_id=workspace.id,
                issue_id=issue.id,
                subscriber_id=member.id,
                reason=reason,
                muted=muted,
            )
        )


async def _emit_fanout(factory, workspace, **kwargs) -> OutboxEvent:
    from mesh.comment_inbox.notifications import emit_notification_fanout

    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        event = await emit_notification_fanout(
            session, workspace_id=workspace.id, **kwargs
        )
    return event


async def _run_fanout(factory, event: OutboxEvent, *, clock=T0, mailer=None, window=60.0):
    handler = NotificationFanoutHandler(
        aggregation_window_seconds=window, mailer=mailer, clock=lambda: clock
    )
    async with factory() as session, session.begin():
        await set_tenant_context(session, event.workspace_id)
        await handler.handle(session, event)


async def _notifications(factory, recipient=None) -> list[Notification]:
    async with factory() as session:
        stmt = select(Notification)
        if recipient is not None:
            stmt = stmt.where(Notification.recipient_id == recipient.id)
        return list((await session.execute(stmt)).scalars().all())


async def _realtime_events(factory, event_name: str | None = None) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
            )
        ).scalars().all()
    if event_name is not None:
        rows = [row for row in rows if row.payload.get("event") == event_name]
    return rows


@pytest.fixture
async def env(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Alice")
    bob = await _human(session_factory, workspace, "Bob")
    carol = await _human(session_factory, workspace, "Carol")
    agent = await _agent(session_factory, workspace, "reviewer")
    issue = await _issue(session_factory, workspace, author, assignee=bob)
    inbox = InboxService(session_factory, clock=lambda: T0)
    return {
        "factory": session_factory,
        "workspace": workspace,
        "author": author,
        "bob": bob,
        "carol": carol,
        "agent": agent,
        "issue": issue,
        "inbox": inbox,
    }


# ---------------------------------------------------------------------------
# fan-out routing & matrix
# ---------------------------------------------------------------------------


async def test_comment_created_routes_to_subscribers_reporter_assignee_not_actor(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    await _subscribe(factory, workspace, issue, env["carol"], reason="participated")
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created",
        actor_member_id=env["author"].id,
        actor_name="Alice", actor_member_type="human",
        issue_id=issue.id,
        group_key=f"issue:{issue.id}:comment_created",
        title="Login broken", preview="I found the bug",
    )
    await _run_fanout(factory, event)
    rows = await _notifications(factory)
    recipients = {row.recipient_id for row in rows}
    # reporter (author → self-suppressed), assignee bob, subscriber carol
    assert recipients == {env["bob"].id, env["carol"].id}
    assert all(row.type == "comment_created" for row in rows)
    assert all(row.priority == "normal" for row in rows)
    # payload snapshot is renderable
    payload = rows[0].payload
    assert payload["actor_name"] == "Alice"
    assert payload["preview"] == "I found the bug"
    assert payload["count"] == 1


async def test_mentioned_is_critical_and_reaches_mentioned_humans(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    event = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned",
        actor_member_id=env["author"].id,
        actor_name="Alice", actor_member_type="human",
        issue_id=issue.id,
        recipient_ids=[env["carol"].id, env["agent"].id],
        group_key=f"issue:{issue.id}:mentioned",
        title="Login broken", preview="hey @Carol",
    )
    await _run_fanout(factory, event)
    rows = await _notifications(factory)
    # explicit carol + implicit reporter/assignee; AGENT recipient skipped
    assert env["agent"].id not in {row.recipient_id for row in rows}
    carol_rows = [row for row in rows if row.recipient_id == env["carol"].id]
    assert carol_rows and carol_rows[0].priority == "critical"


async def test_muted_subscription_suppresses_even_assignee(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    # bob is the assignee AND muted → muted wins (§6.13)
    await _subscribe(factory, workspace, issue, env["bob"], reason="assignee", muted=True)
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created",
        actor_member_id=env["author"].id,
        issue_id=issue.id, group_key=f"issue:{issue.id}:comment_created",
        title="t", preview="p",
    )
    await _run_fanout(factory, event)
    rows = await _notifications(factory)
    assert env["bob"].id not in {row.recipient_id for row in rows}


async def test_execution_success_not_in_inbox_without_subscription(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    event = await _emit_fanout(
        factory, workspace,
        notification_type="execution_finished", execution_status="completed",
        actor_member_id=env["agent"].id, actor_kind="member",
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        title="t", preview="done",
    )
    await _run_fanout(factory, event)
    assert await _notifications(factory) == []  # §6.13 R2: 留运行页


async def test_execution_success_in_inbox_with_explicit_subscription(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id,
                member_id=env["bob"].id,
                event_type="execution_finished",
                in_app=True,
                email="digest",
            )
        )
    event = await _emit_fanout(
        factory, workspace,
        notification_type="execution_finished", execution_status="completed",
        actor_member_id=env["agent"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        title="t", preview="done",
    )
    await _run_fanout(factory, event)
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].priority == "normal"  # success is NEVER critical
    assert rows[0].type == "execution_finished"


async def test_execution_failure_is_critical_in_inbox(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    event = await _emit_fanout(
        factory, workspace,
        notification_type="execution_finished", execution_status="failed",
        actor_member_id=env["agent"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        title="t", preview="boom",
    )
    await _run_fanout(factory, event)
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].priority == "critical"


async def test_private_project_access_revocation_blocks_failure_preview_and_email(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    await _subscribe(factory, workspace, issue, env["carol"], reason="participated")
    async with factory() as session, session.begin():
        project = Project(
            workspace_id=workspace.id,
            name=f"Private notifications {uuid.uuid4().hex[:6]}",
            key=f"N{uuid.uuid4().hex[:7].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
        stored_issue = await session.get(Issue, issue.id)
        assert stored_issue is not None
        stored_issue.project_id = project.id
        grant = ProjectMember(
            workspace_id=workspace.id,
            project_id=project.id,
            member_id=env["carol"].id,
            role="viewer",
        )
        session.add(grant)
        await session.flush()
        # Revocation happens after the historical subscription was created.
        await session.execute(
            delete(ProjectMember).where(ProjectMember.id == grant.id)
        )

    event = await _emit_fanout(
        factory,
        workspace,
        notification_type="execution_finished",
        execution_status="failed",
        actor_member_id=env["agent"].id,
        issue_id=issue.id,
        recipient_ids=[env["carol"].id],
        title="Agent run needs attention",
        preview="failure · already-redacted private log tail",
    )
    mailer = _RecordingMailer()
    await _run_fanout(factory, event, mailer=mailer)
    assert await _notifications(factory) == []
    assert mailer.sent == []


async def test_aggregation_window_merges_same_group(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    group = f"issue:{issue.id}:comment_created"
    for index in range(3):
        event = await _emit_fanout(
            factory, workspace,
            notification_type="comment_created",
            actor_member_id=env["carol"].id, actor_name="Carol",
            issue_id=issue.id, recipient_ids=[env["bob"].id],
            group_key=group, title="t", preview=f"msg {index}",
        )
        await _run_fanout(factory, event, clock=T0 + timedelta(seconds=10 * index))
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].payload["count"] == 3
    assert rows[0].payload["preview"] == "msg 2"  # latest wins


async def test_review_requested_carries_approval_id_to_payload_and_frame(env):
    """Unified approvals (README §6.10 / agent.md §5.4): review_requested
    notifications must carry the pending approval id end-to-end — stored
    payload snapshot AND the realtime ``notification.created`` frame — so an
    inbox row can offer inline approve/reject without a lookup."""
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    approval_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    # issue_id omitted on purpose: implicit issue routing (reporter/assignee) is
    # covered elsewhere — this contract is approval_id propagation only.
    event = await _emit_fanout(
        factory, workspace,
        notification_type="review_requested",
        actor_member_id=env["agent"].id, actor_name="reviewer",
        execution_id=execution_id,
        recipient_ids=[env["bob"].id],
        group_key=f"execution:{execution_id}:approval",
        title="Approval needed", preview="tool: shell",
        extra={"approval_id": str(approval_id)},
    )
    await _run_fanout(factory, event)

    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].payload["approval_id"] == str(approval_id)

    frames = await _realtime_events(factory, "notification.created")
    frames = [f for f in frames if f.workspace_id == workspace.id]
    assert len(frames) == 1
    assert frames[0].payload["data"]["approval_id"] == str(approval_id)
    assert frames[0].payload["data"]["type"] == "review_requested"


async def test_review_requested_aggregation_refreshes_approval_id(env):
    """A re-requested approval merged into the 60 s window refreshes the
    approval id so inline actions address the latest pending approval."""
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    execution_id = uuid.uuid4()
    group = f"execution:{execution_id}:approval"
    first_approval = uuid.uuid4()
    second_approval = uuid.uuid4()
    for approval_id, offset in ((first_approval, 0), (second_approval, 10)):
        event = await _emit_fanout(
            factory, workspace,
            notification_type="review_requested",
            execution_id=execution_id,
            recipient_ids=[env["bob"].id], group_key=group,
            title="Approval needed", preview="tool: shell",
            extra={"approval_id": str(approval_id)},
        )
        await _run_fanout(factory, event, clock=T0 + timedelta(seconds=offset))

    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].payload["count"] == 2
    assert rows[0].payload["approval_id"] == str(second_approval)


async def test_missing_snapshot_fields_are_backfilled_from_live_issue(env):
    """Producers that only carry ids (assigned / review_requested) must still
    render readable inbox text: the handler backfills issue_identifier/title
    from the live issue so group headers never stringify a bare "null"
    (MES-189 evidence finding)."""
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    execution_id = uuid.uuid4()
    event = await _emit_fanout(
        factory, workspace,
        notification_type="review_requested",
        execution_id=execution_id,
        issue_id=issue.id,
        recipient_ids=[env["bob"].id],
        group_key=f"execution:{execution_id}:approval",
        preview="tool: shell",
    )
    await _run_fanout(factory, event)

    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].payload["issue_identifier"] == issue.identifier
    assert rows[0].payload["title"] == issue.title

    frames = await _realtime_events(factory, "notification.created")
    frames = [f for f in frames if f.workspace_id == workspace.id]
    assert frames, "expected at least one notification.created frame"
    # issue_id also routes to the reporter, so assert the contract on every
    # frame: the §2.6 snapshot must be backfilled, never None.
    for frame in frames:
        data = frame.payload["data"]
        assert data["issue"]["identifier"] == issue.identifier
        assert data["issue"]["title"] == issue.title


async def test_existing_snapshot_fields_are_never_overwritten_by_backfill(env):
    """Backfill only fills what the producer omitted: producer-supplied text
    wins over the live issue's current values."""
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created",
        issue_id=issue.id,
        recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:comment_created",
        title="Producer title", preview="hi",
        extra={"issue_identifier": "PROD-9"},
    )
    await _run_fanout(factory, event)

    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 1
    assert rows[0].payload["issue_identifier"] == "PROD-9"
    assert rows[0].payload["title"] == "Producer title"


async def test_aggregation_outside_window_creates_new_row(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    group = f"issue:{issue.id}:comment_created"
    first = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=group, title="t", preview="a",
    )
    await _run_fanout(factory, first)
    first_row = (await _notifications(factory, env["bob"]))[0]
    second = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=group, title="t", preview="b",
    )
    # anchor past the window relative to the persisted row (DB now), not the
    # module-level T0 — robust regardless of suite position / wall-clock drift
    await _run_fanout(
        factory, second, clock=first_row.created_at + timedelta(seconds=120)
    )
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    assert len(rows) == 2


async def test_critical_resets_read_group_normal_does_not(env):
    factory, workspace, issue, inbox = (
        env["factory"], env["workspace"], env["issue"], env["inbox"],
    )
    group = f"issue:{issue.id}:mentioned"
    event = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=group, title="t", preview="first",
    )
    await _run_fanout(factory, event)
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    await inbox.mark_read(
        workspace_id=workspace.id, member=env["bob"], notification_id=rows[0].id, read=True
    )
    # normal aggregation: count increments, stays read (§6.13 累加不重置)
    normal_group = f"issue:{issue.id}:comment_created"
    event2 = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=normal_group, title="t", preview="x",
    )
    await _run_fanout(factory, event2)
    # read the comment_created row too
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    for row in rows:
        if row.read_at is None:
            await inbox.mark_read(
                workspace_id=workspace.id, member=env["bob"],
                notification_id=row.id, read=True,
            )
    # a NEW critical mention in the same (read) group resets unread
    event3 = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=group, title="t", preview="urgent",
    )
    await _run_fanout(factory, event3, clock=T0 + timedelta(seconds=30))
    rows = [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id]
    mention_row = next(row for row in rows if row.group_key == group)
    assert mention_row.read_at is None  # critical 重新置未读
    assert mention_row.payload["count"] == 2
    comment_row = next(row for row in rows if row.group_key == normal_group)
    assert comment_row.read_at is not None  # normal 不重置


async def test_quiet_hours_suppress_push_but_not_inbox_critical_pierces(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    # bob's quiet hours 22:00-07:00; T0 is 12:00 → use 23:00
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id, member_id=env["bob"].id,
                event_type="all", in_app=True, email="none",
                quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0),
            )
        )
    night = T0.replace(hour=23)
    # normal event during quiet hours: row exists, NO realtime push
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:comment_created", title="t", preview="p",
    )
    await _run_fanout(factory, event, clock=night)
    assert len(await _notifications(factory, env["bob"])) == 1
    bob_channel = inbox_channel(env["bob"].id)
    created = [
        row for row in await _realtime_events(factory, "notification.created")
        if row.payload.get("channel") == bob_channel
    ]
    assert created == []  # quiet hours: no popup/push
    # critical pierces quiet hours
    event2 = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:mentioned", title="t", preview="urgent",
    )
    await _run_fanout(factory, event2, clock=night)
    created = [
        row for row in await _realtime_events(factory, "notification.created")
        if row.payload.get("channel") == bob_channel
    ]
    assert len(created) == 1


async def test_preference_in_app_false_email_none_skips_everything(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id, member_id=env["bob"].id,
                event_type="comment_created", in_app=False, email="none",
            )
        )
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:comment_created", title="t", preview="p",
    )
    await _run_fanout(factory, event)
    assert [row for row in await _notifications(factory) if row.recipient_id == env["bob"].id] == []


async def test_delivery_ledger_in_app_and_dedup(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:comment_created", title="t", preview="p",
    )
    await _run_fanout(factory, event)
    rows = await _notifications(factory, env["bob"])
    async with factory() as session:
        deliveries = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == rows[0].id
                )
            )
        ).scalars().all()
    # in_app (sent) + the default digest email policy defers a pending row
    by_channel = {row.channel: row for row in deliveries}
    assert set(by_channel) == {"in_app", "email"}
    assert by_channel["in_app"].destination_key == ""  # single-destination
    assert by_channel["in_app"].state == "sent"
    assert by_channel["in_app"].error is None  # error carries failure reasons ONLY
    assert by_channel["email"].state == "pending"  # digest deferred


class _FailingMailer:
    async def deliver(self, email, kind, token):
        raise RuntimeError("smtp down")


class _RecordingMailer:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def deliver(self, email, kind, token):
        self.sent.append((email, kind))


async def _silence_alice(env) -> None:
    """The implicit reporter (Alice) also receives; keep her out of email assertions."""
    async with env["factory"]() as session, session.begin():
        await set_tenant_context(session, env["workspace"].id)
        session.add(
            NotificationPreference(
                workspace_id=env["workspace"].id, member_id=env["author"].id,
                event_type="all", in_app=False, email="none",
            )
        )


async def test_email_realtime_policy_sends_and_records(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    await _silence_alice(env)
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id, member_id=env["bob"].id,
                event_type="mentioned", in_app=True, email="realtime",
            )
        )
    mailer = _RecordingMailer()
    event = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:mentioned", title="t", preview="urgent",
    )
    await _run_fanout(factory, event, mailer=mailer)
    assert len(mailer.sent) == 1
    assert mailer.sent[0][1] == "notification_realtime"
    rows = await _notifications(factory, env["bob"])
    async with factory() as session:
        email_rows = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == rows[0].id,
                    NotificationDelivery.channel == "email",
                )
            )
        ).scalars().all()
    assert len(email_rows) == 1
    assert email_rows[0].state == "sent"
    assert email_rows[0].provider == "email_smtp"
    assert email_rows[0].external_target  # structured routing column


async def test_email_failure_records_reason_not_routing_data(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    await _silence_alice(env)
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id, member_id=env["bob"].id,
                event_type="mentioned", in_app=True, email="realtime",
            )
        )
    event = await _emit_fanout(
        factory, workspace,
        notification_type="mentioned", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:mentioned", title="t", preview="p",
    )
    await _run_fanout(factory, event, mailer=_FailingMailer())
    rows = await _notifications(factory, env["bob"])
    async with factory() as session:
        email_row = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == rows[0].id,
                NotificationDelivery.channel == "email",
            )
        )
    assert email_row.state == "failed"
    assert email_row.error == "RuntimeError"  # reason only
    assert "@" not in (email_row.error or "")  # no routing data in error (R3)


async def test_digest_policy_defers_to_pending_row(env):
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    await _silence_alice(env)
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id, member_id=env["bob"].id,
                event_type="comment_created", in_app=True, email="digest",
            )
        )
    mailer = _RecordingMailer()
    event = await _emit_fanout(
        factory, workspace,
        notification_type="comment_created", actor_member_id=env["carol"].id,
        issue_id=issue.id, recipient_ids=[env["bob"].id],
        group_key=f"issue:{issue.id}:comment_created", title="t", preview="p",
    )
    await _run_fanout(factory, event, mailer=mailer)
    assert mailer.sent == []  # not sent immediately
    # digest sweep sends and marks the ledger
    async with factory() as session, session.begin():
        sent = await send_digest_emails(session, mailer=mailer)
    assert sent == 1
    assert mailer.sent[0][1] == "notification_digest"
    # sweep is idempotent — nothing pending anymore
    async with factory() as session, session.begin():
        assert await send_digest_emails(session, mailer=mailer) == 0


# ---------------------------------------------------------------------------
# inbox read side
# ---------------------------------------------------------------------------


async def _seed_notifications(env, count: int = 3) -> list[Notification]:
    factory, workspace, issue = env["factory"], env["workspace"], env["issue"]
    for index in range(count):
        event = await _emit_fanout(
            factory, workspace,
            notification_type="comment_created", actor_member_id=env["carol"].id,
            actor_name="Carol", actor_member_type="human",
            issue_id=issue.id, recipient_ids=[env["bob"].id],
            title="Login broken", preview=f"comment {index}",
        )
        await _run_fanout(factory, event, clock=T0 + timedelta(seconds=70 * index))
    return await _notifications(factory, env["bob"])


async def test_unread_count_and_mark_read_broadcast(env):
    inbox = env["inbox"]
    rows = await _seed_notifications(env)
    count = await inbox.unread_count(workspace_id=env["workspace"].id, member=env["bob"])
    assert count == 3
    await inbox.mark_read(
        workspace_id=env["workspace"].id, member=env["bob"],
        notification_id=rows[0].id, read=True,
    )
    assert await inbox.unread_count(workspace_id=env["workspace"].id, member=env["bob"]) == 2
    # multi-end sync events were emitted on bob's inbox channel
    bob_channel = inbox_channel(env["bob"].id)
    read_events = [
        row for row in await _realtime_events(env["factory"], "notification.read")
        if row.payload.get("channel") == bob_channel
    ]
    count_events = [
        row for row in await _realtime_events(env["factory"], "inbox.unread_count")
        if row.payload.get("channel") == bob_channel
    ]
    assert read_events and count_events
    assert count_events[-1].payload["data"]["count"] == 2
    # mark unread restores
    await inbox.mark_read(
        workspace_id=env["workspace"].id, member=env["bob"],
        notification_id=rows[0].id, read=False,
    )
    assert await inbox.unread_count(workspace_id=env["workspace"].id, member=env["bob"]) == 3


async def test_list_filters_and_pagination(env):
    inbox = env["inbox"]
    await _seed_notifications(env, count=3)
    # one mention + one assigned
    for notification_type, actor in (
        ("mentioned", env["carol"]),
        ("assigned", env["carol"]),
    ):
        event = await _emit_fanout(
            env["factory"], env["workspace"],
            notification_type=notification_type, actor_member_id=actor.id,
            actor_name="Carol", actor_member_type="human",
            issue_id=env["issue"].id, recipient_ids=[env["bob"].id],
            title="t", preview="p",
        )
        await _run_fanout(env["factory"], event, clock=T0 + timedelta(minutes=10))
    listing = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], inbox_filter="unread",
    )
    assert len(listing["data"]) == 5
    mentions = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], inbox_filter="mentions",
    )
    assert [item["type"] for item in mentions["data"]] == ["mentioned"]
    assigned = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], inbox_filter="assigned",
    )
    assert [item["type"] for item in assigned["data"]] == ["assigned"]
    # §3.2: rows carry the issue snapshot (group headers render from it)
    comment_row = next(
        item for item in listing["data"] if item["type"] == "comment_created"
    )
    assert comment_row["issue"]["id"] == str(env["issue"].id)
    assert comment_row["issue"]["title"] == "Login broken"
    # pagination: limit 2 → cursor → next page
    page1 = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], limit=2,
    )
    assert len(page1["data"]) == 2 and page1["next_cursor"] is not None
    page2 = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], limit=2,
        cursor=page1["next_cursor"],
    )
    assert len(page2["data"]) == 2
    ids1 = {item["id"] for item in page1["data"]}
    ids2 = {item["id"] for item in page2["data"]}
    assert ids1.isdisjoint(ids2)
    # invalid filter → 400
    with pytest.raises(ValidationError):
        await inbox.list_notifications(
            workspace_id=env["workspace"].id, member=env["bob"], inbox_filter="bogus",
        )


async def test_grouped_listing_overall_cursor(env):
    inbox = env["inbox"]
    await _seed_notifications(env, count=3)  # same group (merged? no — 70 s apart)
    grouped = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], grouped=True,
    )
    # 3 comments 70 s apart → 3 distinct aggregation windows → 3 groups
    assert len(grouped["data"]) >= 1
    for group in grouped["data"]:
        assert group["group_key"]
        assert group["count"] >= 1


async def test_read_all_and_archive(env):
    inbox = env["inbox"]
    rows = await _seed_notifications(env, count=3)
    updated = await inbox.read_all(workspace_id=env["workspace"].id, member=env["bob"])
    assert updated == 3
    assert await inbox.unread_count(workspace_id=env["workspace"].id, member=env["bob"]) == 0
    archived = await inbox.archive_read(workspace_id=env["workspace"].id, member=env["bob"])
    assert archived == 3
    # archived rows leave the main listing
    listing = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"],
    )
    assert listing["data"] == []
    # single archive on an unread row also works
    rows = await _seed_notifications(env, count=1)
    result = await inbox.set_archived(
        workspace_id=env["workspace"].id, member=env["bob"], notification_id=rows[0].id,
    )
    assert result["archived_at"] is not None


async def test_archived_only_listing(env):
    """L202 归档视图:archived_only=True 只返回已归档通知(移出主视图,可回查)。"""
    inbox = env["inbox"]
    rows = await _seed_notifications(env, count=2)
    await inbox.set_archived(
        workspace_id=env["workspace"].id, member=env["bob"], notification_id=rows[0].id,
    )
    # 主视图只剩未归档行
    main = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"],
    )
    assert [item["id"] for item in main["data"]] == [str(rows[1].id)]
    # 归档视图只含已归档行,且 archived_at 非空
    archived = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"], archived_only=True,
    )
    assert [item["id"] for item in archived["data"]] == [str(rows[0].id)]
    assert all(item["archived_at"] is not None for item in archived["data"])
    # 分组形态同样支持(两行同 group_key,归档视图内仅 1 组 1 条)
    grouped = await inbox.list_notifications(
        workspace_id=env["workspace"].id, member=env["bob"],
        grouped=True, archived_only=True,
    )
    assert len(grouped["data"]) == 1
    assert grouped["data"][0]["count"] == 1


async def test_inbox_item_of_other_member_is_404(env):
    inbox = env["inbox"]
    rows = await _seed_notifications(env, count=1)
    with pytest.raises(NotFoundError):
        await inbox.mark_read(
            workspace_id=env["workspace"].id, member=env["carol"],  # not the recipient
            notification_id=rows[0].id, read=True,
        )


async def test_preferences_crud_and_quiet_hours_validation(env):
    inbox = env["inbox"]
    assert await inbox.get_preferences(workspace_id=env["workspace"].id, member=env["bob"]) == []
    result = await inbox.put_preferences(
        workspace_id=env["workspace"].id, member=env["bob"],
        entries=[
            {"event_type": "all", "in_app": True, "email": "digest",
             "quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
            {"event_type": "mentioned", "in_app": True, "email": "realtime"},
        ],
    )
    assert len(result) == 2
    by_type = {row["event_type"]: row for row in result}
    assert by_type["all"]["quiet_hours_start"] == "22:00:00"
    # update path
    result2 = await inbox.put_preferences(
        workspace_id=env["workspace"].id, member=env["bob"],
        entries=[{"event_type": "mentioned", "in_app": False, "email": "none"}],
    )
    mentioned = next(row for row in result2 if row["event_type"] == "mentioned")
    assert mentioned["in_app"] is False and mentioned["email"] == "none"
    # half-open quiet hours rejected
    with pytest.raises(ValidationError):
        await inbox.put_preferences(
            workspace_id=env["workspace"].id, member=env["bob"],
            entries=[{"event_type": "assigned", "quiet_hours_start": "22:00:00"}],
        )
    with pytest.raises(ValidationError):
        await inbox.put_preferences(
            workspace_id=env["workspace"].id, member=env["bob"],
            entries=[{"event_type": "assigned", "email": "carrier-pigeon"}],
        )


async def test_m3_preference_event_type_domain_rejected(env):
    # M3 / §2.7: event_type must be 'all' or a real notification type —
    # an unknown value would persist and never match a fan-out.
    inbox = env["inbox"]
    with pytest.raises(ValidationError) as exc:
        await inbox.put_preferences(
            workspace_id=env["workspace"].id, member=env["bob"],
            entries=[{"event_type": "not-a-real-type", "in_app": True, "email": "digest"}],
        )
    assert exc.value.code == "validation_error"
    # empty event_type is rejected (the existence guard fires before the domain check)
    with pytest.raises(ValidationError):
        await inbox.put_preferences(
            workspace_id=env["workspace"].id, member=env["bob"],
            entries=[{"event_type": "", "in_app": True, "email": "digest"}],
        )


async def test_read_all_rejects_invalid_filter(env):
    # L6 / §3.2: read-all honours the same filter set; an unknown filter raises
    # so a typo can never silently mark everything read. A valid `type` filter is
    # accepted (no matching rows → 0 updated).
    inbox = env["inbox"]
    with pytest.raises(ValidationError):
        await inbox.read_all(
            workspace_id=env["workspace"].id, member=env["bob"], inbox_filter="bogus",
        )
    assert await inbox.read_all(
        workspace_id=env["workspace"].id, member=env["bob"], notification_type="mentioned",
    ) == 0


async def test_issue_mute_unmute(env):
    inbox, factory = env["inbox"], env["factory"]
    result = await inbox.set_issue_muted(
        workspace_id=env["workspace"].id, issue_id=env["issue"].id,
        member=env["bob"], muted=True,
    )
    assert result["muted"] is True
    async with factory() as session:
        row = await session.scalar(
            select(IssueSubscription).where(
                IssueSubscription.issue_id == env["issue"].id,
                IssueSubscription.subscriber_id == env["bob"].id,
            )
        )
    assert row.muted is True
    await inbox.set_issue_muted(
        workspace_id=env["workspace"].id, issue_id=env["issue"].id,
        member=env["bob"], muted=False,
    )
    async with factory() as session:
        row = await session.scalar(
            select(IssueSubscription).where(
                IssueSubscription.issue_id == env["issue"].id,
                IssueSubscription.subscriber_id == env["bob"].id,
            )
        )
    assert row.muted is False

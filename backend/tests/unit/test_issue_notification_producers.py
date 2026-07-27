"""H1 notification producers + M1/M2 handler regressions (round-2 fixes).

Real PostgreSQL, nothing mocked:

* the issue module registers ``notification.fanout`` outbox events in the
  SAME transaction as assign / status / field changes (comment-inbox.md
  §5.3 I1/I3/I4, README §4.4) and seeds creator/assignee subscriptions (L2);
* the due-soon sweep producer emits one ``due_soon`` fan-out per
  issue+due-date (de-duped, terminal categories excluded);
* M1 — quiet hours suppress the toast frame but NOT the badge sync;
* M2 — 60 s aggregation never merges into an archived group.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.comment_inbox.notifications import (
    FANOUT_EVENT_TYPE,
    NotificationFanoutHandler,
    emit_due_soon_notifications,
    emit_notification_fanout,
)
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.notification import (
    IssueSubscription,
    Notification,
    NotificationPreference,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssuePatch, IssueService
from mesh.workers.due_soon_sweep import due_soon_sweep_loop

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


async def _workspace(factory) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(name="Prod WS", slug=f"prod-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
        await session.flush()
        from mesh.issue.statuses import seed_default_statuses

        await seed_default_statuses(session, workspace_id=workspace.id)
    return workspace


async def _human(factory, workspace, name: str) -> Member:
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.example",
            password_hash="x",
            display_name=name,
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user.id,
            role="member",
        )
        session.add(member)
        await session.flush()
    return member


async def _fanout_events(factory) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == FANOUT_EVENT_TYPE)
            )
        ).scalars().all()
    return list(rows)


async def _realtime_events(factory) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == REALTIME_PUBLISH)
            )
        ).scalars().all()
    return list(rows)


@pytest_asyncio.fixture
async def env(session_factory):
    workspace = await _workspace(session_factory)
    alice = await _human(session_factory, workspace, "Alice")
    bob = await _human(session_factory, workspace, "Bob")
    service = IssueService(session_factory)
    return {
        "factory": session_factory,
        "workspace": workspace,
        "alice": alice,
        "bob": bob,
        "service": service,
    }


# ---------------------------------------------------------------------------
# H1 / L2 — producers on the issue write paths
# ---------------------------------------------------------------------------


async def test_create_with_assignee_seeds_subscriptions_and_notifies(env):
    service, workspace, alice, bob = env["service"], env["workspace"], env["alice"], env["bob"]
    created = await service.create_issue(
        actor=alice,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title="ship it", assignee_id=str(bob.id)),
    )
    events = await _fanout_events(env["factory"])
    assigned = [e for e in events if e.payload.get("type") == "assigned"]
    assert len(assigned) == 1
    assert assigned[0].payload["recipient_ids"] == [str(bob.id)]
    assert assigned[0].payload["issue_id"] == created["id"]

    async with env["factory"]() as session:
        rows = (
            await session.execute(
                select(IssueSubscription).where(IssueSubscription.issue_id == uuid.UUID(created["id"]))
            )
        ).scalars().all()
    reasons = {row.subscriber_id: row.reason for row in rows}
    assert reasons[alice.id] == "creator"
    assert reasons[bob.id] == "assignee"


async def test_create_without_assignee_seeds_creator_only(env):
    service, workspace, alice = env["service"], env["workspace"], env["alice"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="solo")
    )
    events = await _fanout_events(env["factory"])
    assert not [e for e in events if e.payload.get("type") == "assigned"]
    async with env["factory"]() as session:
        rows = (
            await session.execute(
                select(IssueSubscription).where(IssueSubscription.issue_id == uuid.UUID(created["id"]))
            )
        ).scalars().all()
    assert [(r.subscriber_id, r.reason) for r in rows] == [(alice.id, "creator")]


async def test_status_change_registers_status_changed_fanout(env):
    service, workspace, alice = env["service"], env["workspace"], env["alice"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="move me")
    )
    async with env["factory"]() as session:
        from mesh.db.models.issue import IssueStatus

        target = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "in_progress",
                IssueStatus.project_id.is_(None),
            )
        )
    await service.update_issue(
        actor=alice,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=target.id),
    )
    events = await _fanout_events(env["factory"])
    status_events = [e for e in events if e.payload.get("type") == "status_changed"]
    assert len(status_events) == 1
    assert status_events[0].payload["issue_id"] == created["id"]
    # no explicit recipients — the matrix routes to subscribers/reporter
    assert status_events[0].payload["recipient_ids"] == []


async def test_field_change_registers_subscribed_update_excluding_actor(env):
    service, workspace, alice = env["service"], env["workspace"], env["alice"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="bump")
    )
    await service.update_issue(
        actor=alice,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(priority="high"),
    )
    events = await _fanout_events(env["factory"])
    updates = [e for e in events if e.payload.get("type") == "subscribed_update"]
    assert len(updates) == 1
    # the author is excluded (their own activity never notifies them)
    assert updates[0].payload["exclude_ids"] == [str(alice.id)]


async def test_assignee_change_notifies_new_assignee_only(env):
    service, workspace, alice, bob = env["service"], env["workspace"], env["alice"], env["bob"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="handoff")
    )
    await service.update_issue(
        actor=alice,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(assignee_id=str(bob.id)),
    )
    events = await _fanout_events(env["factory"])
    assigned = [e for e in events if e.payload.get("type") == "assigned"]
    assert len(assigned) == 1
    assert assigned[0].payload["recipient_ids"] == [str(bob.id)]
    # assignee-only change is NOT also a subscribed_update
    assert not [e for e in events if e.payload.get("type") == "subscribed_update"]


async def test_combined_status_and_field_change_registers_both(env):
    service, workspace, alice = env["service"], env["workspace"], env["alice"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="two birds")
    )
    async with env["factory"]() as session:
        from mesh.db.models.issue import IssueStatus

        target = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "in_progress",
                IssueStatus.project_id.is_(None),
            )
        )
    await service.update_issue(
        actor=alice,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(status_id=target.id, priority="urgent"),
    )
    events = await _fanout_events(env["factory"])
    types = sorted(e.payload.get("type") for e in events)
    assert types == ["status_changed", "subscribed_update"]


async def test_noop_patch_registers_nothing(env):
    service, workspace, alice = env["service"], env["workspace"], env["alice"]
    created = await service.create_issue(
        actor=alice, workspace_id=workspace.id, body=CreateIssueRequest(title="stable")
    )
    before = len(await _fanout_events(env["factory"]))
    await service.update_issue(
        actor=alice,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(created["id"]),
        patch=IssuePatch(title="stable"),  # identical → empty diff (§6.9)
    )
    assert len(await _fanout_events(env["factory"])) == before


# ---------------------------------------------------------------------------
# due-soon sweep producer
# ---------------------------------------------------------------------------


async def _seed_issue(factory, workspace, *, due: date | None, category: str = "todo") -> Issue:
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        from mesh.db.models.issue import IssueStatus

        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == category,
                IssueStatus.project_id.is_(None),
            )
        )
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key="inbox",
            number=uuid.uuid4().int % 1_000_000_000,
            identifier=f"WS-{uuid.uuid4().hex[:6]}",
            title="due issue",
            status_id=status.id,
            state_category=status.category,
            priority="none",
            position=0.0,
            due_date=due,
        )
        session.add(issue)
        await session.flush()
    return issue


async def test_due_soon_sweep_emits_for_issues_inside_horizon(env):
    workspace, factory = env["workspace"], env["factory"]
    today = date(2026, 7, 27)
    await _seed_issue(factory, workspace, due=today + timedelta(hours=2))
    emitted = await _run_sweep(factory, now=datetime(2026, 7, 27, 12, tzinfo=UTC))
    assert emitted == 1
    events = [e for e in await _fanout_events(factory) if e.payload.get("type") == "due_soon"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["actor_kind"] == "system"
    assert payload["actor_member_id"] is None
    assert payload["group_key"].endswith(f":due_soon:{(today + timedelta(hours=2)).isoformat()}")
    assert events[0].idempotency_key is not None


async def test_due_soon_sweep_skips_outside_horizon_terminal_and_null(env):
    workspace, factory = env["workspace"], env["factory"]
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    await _seed_issue(factory, workspace, due=date(2026, 8, 30))  # outside 24 h
    await _seed_issue(factory, workspace, due=date(2026, 7, 27), category="done")
    await _seed_issue(factory, workspace, due=date(2026, 7, 27), category="cancelled")
    await _seed_issue(factory, workspace, due=None)
    assert await _run_sweep(factory, now=now) == 0
    assert not await _fanout_events(factory)


async def test_due_soon_sweep_dedupes_via_notification_rows(env):
    workspace, factory = env["workspace"], env["factory"]
    due = date(2026, 7, 28)
    issue = await _seed_issue(factory, workspace, due=due)
    recipient = env["alice"]
    # a relay-produced notification row already exists for this group
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            Notification(
                workspace_id=workspace.id,
                recipient_id=recipient.id,
                type="due_soon",
                priority="normal",
                issue_id=issue.id,
                payload={"title": "due issue"},
                group_key=f"issue:{issue.id}:due_soon:{due.isoformat()}",
            )
        )
    assert await _run_sweep(factory) == 0


async def _run_sweep(factory, *, now: datetime | None = None) -> int:
    async with factory() as session, session.begin():
        return await emit_due_soon_notifications(
            session, horizon=timedelta(hours=24), now=now
        )


# ---------------------------------------------------------------------------
# handler regressions — M1 (quiet hours badge sync) / M2 (archived aggregation)
# ---------------------------------------------------------------------------


async def _handler_env(factory, workspace, member):
    """Issue + implicit routing is enough (reporter routing hits the member)."""
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        from mesh.db.models.issue import IssueStatus

        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == workspace.id,
                IssueStatus.category == "todo",
                IssueStatus.project_id.is_(None),
            )
        )
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key="inbox",
            number=7,
            identifier=f"WS-{uuid.uuid4().hex[:6]}",
            title="handler target",
            status_id=status.id,
            state_category=status.category,
            priority="none",
            position=0.0,
            reporter_id=member.id,
        )
        session.add(issue)
        await session.flush()
    return issue


async def _fan_out(factory, handler, workspace, event_payload_kwargs, now) -> None:
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        event = await emit_notification_fanout(session, **event_payload_kwargs)
    async with factory() as session, session.begin():
        await handler.handle(session, event)


async def test_m1_quiet_hours_suppress_toast_but_keep_badge_sync(env):
    workspace, factory, alice = env["workspace"], env["factory"], env["alice"]
    issue = await _handler_env(factory, workspace, alice)
    # quiet hours 00:00–06:00 UTC; the handler clock reads 03:00.
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        from datetime import time as dtime

        session.add(
            NotificationPreference(
                workspace_id=workspace.id,
                member_id=alice.id,
                event_type="all",
                in_app=True,
                email="none",
                quiet_hours_start=dtime(0, 0),
                quiet_hours_end=dtime(6, 0),
            )
        )
    quiet_time = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)
    handler = NotificationFanoutHandler(clock=lambda: quiet_time)
    await _fan_out(
        factory,
        handler,
        workspace,
        dict(
            workspace_id=workspace.id,
            notification_type="comment_created",
            issue_id=issue.id,
            group_key=f"issue:{issue.id}:comment_created",
            title="handler target",
        ),
        quiet_time,
    )
    # notification row persisted unread (inbox truth persists)
    async with factory() as session:
        rows = (
            await session.execute(select(Notification).where(Notification.type == "comment_created"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].read_at is None

    frames = [e.payload for e in await _realtime_events(factory)]
    badge = [f for f in frames if f.get("event") == "inbox.unread_count"]
    toast = [f for f in frames if f.get("event") == "notification.created"]
    assert len(badge) == 1 and badge[0]["data"] == {"count": 1}
    assert toast == []  # quiet hours silence the popup/toast source only


async def test_m1_outside_quiet_hours_emits_both(env):
    workspace, factory, alice = env["workspace"], env["factory"], env["alice"]
    issue = await _handler_env(factory, workspace, alice)
    open_time = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    handler = NotificationFanoutHandler(clock=lambda: open_time)
    await _fan_out(
        factory,
        handler,
        workspace,
        dict(
            workspace_id=workspace.id,
            notification_type="comment_created",
            issue_id=issue.id,
            group_key=f"issue:{issue.id}:comment_created",
            title="handler target",
        ),
        open_time,
    )
    frames = [e.payload for e in await _realtime_events(factory)]
    events = {f.get("event") for f in frames}
    assert events == {"notification.created", "inbox.unread_count"}


async def test_m2_aggregation_skips_archived_group(env):
    workspace, factory, alice = env["workspace"], env["factory"], env["alice"]
    issue = await _handler_env(factory, workspace, alice)
    group = f"issue:{issue.id}:comment_created"
    handler = NotificationFanoutHandler()
    payload = dict(
        workspace_id=workspace.id,
        notification_type="comment_created",
        issue_id=issue.id,
        group_key=group,
        title="handler target",
    )
    await _fan_out(factory, handler, workspace, payload, None)
    # archive the group, then fan out again inside the 60 s window (the second
    # clock is derived from the persisted row so the window genuinely covers it)
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        row = await session.scalar(select(Notification).where(Notification.group_key == group))
        row.archived_at = row.created_at
        later = row.created_at + timedelta(seconds=30)
    handler2 = NotificationFanoutHandler(clock=lambda: later)
    await _fan_out(factory, handler2, workspace, payload, later)

    async with factory() as session:
        rows = (
            await session.execute(select(Notification).where(Notification.group_key == group))
        ).scalars().all()
    assert len(rows) == 2  # NOT merged into the archived row
    archived = [r for r in rows if r.archived_at is not None]
    visible = [r for r in rows if r.archived_at is None]
    assert len(archived) == 1 and archived[0].payload["count"] == 1
    assert len(visible) == 1 and visible[0].payload["count"] == 1


async def test_aggregation_merges_inside_window_when_not_archived(env):
    workspace, factory, alice = env["workspace"], env["factory"], env["alice"]
    issue = await _handler_env(factory, workspace, alice)
    group = f"issue:{issue.id}:comment_created"
    handler = NotificationFanoutHandler()
    payload = dict(
        workspace_id=workspace.id,
        notification_type="comment_created",
        issue_id=issue.id,
        group_key=group,
        title="handler target",
    )
    await _fan_out(factory, handler, workspace, payload, None)
    async with factory() as session:
        row = await session.scalar(select(Notification).where(Notification.group_key == group))
        later = row.created_at + timedelta(seconds=30)
    handler2 = NotificationFanoutHandler(clock=lambda: later)
    await _fan_out(factory, handler2, workspace, payload, later)
    async with factory() as session:
        rows = (
            await session.execute(select(Notification).where(Notification.group_key == group))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["count"] == 2


# ---------------------------------------------------------------------------
# due-soon sweep loop (supervised worker) — happy path + crash recovery
# ---------------------------------------------------------------------------


async def _stop_after(stop: asyncio.Event, delay: float) -> None:
    await asyncio.sleep(delay)
    stop.set()


async def test_due_soon_sweep_loop_emits_and_stops(env):
    workspace, factory = env["workspace"], env["factory"]
    await _seed_issue(factory, workspace, due=date(2026, 7, 28))
    stop = asyncio.Event()
    stopper = asyncio.create_task(_stop_after(stop, 0.3))
    await asyncio.wait_for(
        due_soon_sweep_loop(
            factory, interval=0.05, horizon_hours=24.0, stop=stop,
            clock=lambda: datetime(2026, 7, 27, 12, tzinfo=UTC),
        ),
        timeout=10,
    )
    await stopper
    events = [e for e in await _fanout_events(factory) if e.payload.get("type") == "due_soon"]
    assert len(events) == 1


async def test_due_soon_sweep_loop_recovers_from_iteration_error(env, monkeypatch):
    # An iteration that raises (simulated: the fan-out write fails inside the
    # transaction) must NOT kill the supervised loop — the except branch rolls
    # back that transaction, logs, and the loop keeps running until stop
    # (README §2.2). After the injected failure clears, the next iteration
    # succeeds, proving recovery.
    workspace, factory = env["workspace"], env["factory"]
    await _seed_issue(factory, workspace, due=date(2026, 7, 28))

    import mesh.comment_inbox.notifications as _notes

    real = _notes.emit_notification_fanout
    calls = {"n": 0}

    async def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated fan-out failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(_notes, "emit_notification_fanout", _flaky)
    stop = asyncio.Event()
    stopper = asyncio.create_task(_stop_after(stop, 0.4))
    await asyncio.wait_for(
        due_soon_sweep_loop(
            factory, interval=0.05, horizon_hours=24.0, stop=stop,
            clock=lambda: datetime(2026, 7, 27, 12, tzinfo=UTC),
        ),
        timeout=10,
    )
    await stopper
    # the loop retried after the failure (≥2 fan-out attempts) and recovered
    assert calls["n"] >= 2
    events = [e for e in await _fanout_events(factory) if e.payload.get("type") == "due_soon"]
    assert len(events) >= 1

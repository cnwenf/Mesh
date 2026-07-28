"""Onboarding outbox consumer tests (onboarding.md §3.6 / §5.1, T34③④).

Domain events arrive as ``realtime.publish`` outbox rows; the consumer is
the relay-side chain. Tests drive ``consume_realtime_event`` with realistic
payloads against real fact rows and assert the §3.5 guard semantics:
strict trigger ownership (R4), unread-never-completes, idempotent
redelivery, single onboarding.progress / onboarding.completed emission.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.onboarding import (
    STEP_CREATE_FIRST_ISSUE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.onboarding.consumers import consume_realtime_event
from mesh.onboarding.service import ensure_seeded, seed_for_new_member
from tests.unit.onboarding_support import (
    make_agent_member,
    make_assign_activity,
    make_comment,
    make_execution,
    make_issue,
    make_mention_execution,
    make_notification,
)

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def owner_user(session_factory):
    async with session_factory() as session, session.begin():
        user = User(
            email=f"owner-{uuid.uuid4().hex[:10]}@mesh.test",
            display_name="Owner",
            password_hash="unused",
        )
        session.add(user)
    return user


def _event(workspace_id, event_name: str, data: dict) -> OutboxEvent:
    """An in-memory realtime.publish outbox row (relay shape)."""
    return OutboxEvent(
        workspace_id=workspace_id,
        event_type="realtime.publish",
        payload={
            "channel": f"workspace:{workspace_id}",
            "event": event_name,
            "data": data,
        },
    )


async def _consume(session_factory, event) -> None:
    async with session_factory() as session, session.begin():
        await consume_realtime_event(session, event)


async def _steps(session_factory, state_id) -> dict[str, OnboardingStateStep]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OnboardingStateStep).where(OnboardingStateStep.state_id == state_id)
                )
            )
            .scalars()
            .all()
        )
    return {row.step_key: row for row in rows}


async def _state(session_factory, member_id) -> OnboardingState | None:
    async with session_factory() as session:
        return await session.scalar(
            select(OnboardingState).where(OnboardingState.member_id == member_id)
        )


async def _seed(session_factory, workspace, member) -> OnboardingState:
    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
    return state


# --- member.added → step 2 --------------------------------------------------------


async def test_member_added_agent_completes_step2_batch(
    session_factory, workspace_factory, member_factory, owner_user
):
    workspace = await workspace_factory()
    m1 = await member_factory(workspace)
    m2 = await member_factory(workspace)
    s1 = await _seed(session_factory, workspace, m1)
    s2 = await _seed(session_factory, workspace, m2)
    _, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )

    await _consume(
        session_factory,
        _event(
            workspace.id,
            "member.added",
            {"member_id": str(agent_member.id), "member_type": "agent", "role": "member"},
        ),
    )

    for state in (s1, s2):
        steps = await _steps(session_factory, state.id)
        assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "completed"
        assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].completed_via == "auto"
        assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].evidence == {
            "member_added_id": str(agent_member.id)
        }


async def test_member_added_second_human_completes_step2(
    session_factory, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    m1 = await member_factory(workspace)
    s1 = await _seed(session_factory, workspace, m1)
    m2 = await member_factory(workspace)  # human #2 now in roster

    await _consume(
        session_factory,
        _event(
            workspace.id,
            "member.added",
            {"member_id": str(m2.id), "member_type": "human", "role": "member"},
        ),
    )
    steps = await _steps(session_factory, s1.id)
    assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "completed"
    assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].evidence == {"member_added_id": str(m2.id)}


async def test_member_added_first_human_noop(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    m1 = await member_factory(workspace)
    s1 = await _seed(session_factory, workspace, m1)
    await _consume(
        session_factory,
        _event(
            workspace.id,
            "member.added",
            {"member_id": str(m1.id), "member_type": "human", "role": "owner"},
        ),
    )
    steps = await _steps(session_factory, s1.id)
    assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "pending"


# --- issue.created → step 3 --------------------------------------------------------


async def test_issue_created_workspace_first_batches(
    session_factory, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    m1 = await member_factory(workspace)
    m2 = await member_factory(workspace)
    s1 = await _seed(session_factory, workspace, m1)
    s2 = await _seed(session_factory, workspace, m2)
    issue = await make_issue(session_factory, workspace, reporter_id=m1.id)

    await _consume(
        session_factory,
        _event(workspace.id, "issue.created", {"issue": {"id": str(issue.id)}}),
    )

    steps1 = await _steps(session_factory, s1.id)
    assert steps1[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert steps1[STEP_CREATE_FIRST_ISSUE].evidence == {
        "issue_id": str(issue.id),
        "reporter_member_id": str(m1.id),
    }
    steps2 = await _steps(session_factory, s2.id)
    assert steps2[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert steps2[STEP_CREATE_FIRST_ISSUE].evidence == {"issue_id": str(issue.id)}


async def test_issue_created_non_first_only_reporter(
    session_factory, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    reporter = await member_factory(workspace)
    other = await member_factory(workspace)
    s_reporter = await _seed(session_factory, workspace, reporter)
    s_other = await _seed(session_factory, workspace, other)
    await make_issue(session_factory, workspace)  # pre-existing first issue
    second = await make_issue(session_factory, workspace, reporter_id=reporter.id)

    await _consume(
        session_factory,
        _event(workspace.id, "issue.created", {"issue": {"id": str(second.id)}}),
    )

    assert (await _steps(session_factory, s_reporter.id))[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert (await _steps(session_factory, s_other.id))[STEP_CREATE_FIRST_ISSUE].status == "pending"


# --- execution.queued → step 4 (strict R4 ownership) --------------------------------


async def test_execution_queued_assign_only_trigger_member(
    session_factory, workspace_factory, member_factory, owner_user
):
    """R4: ONLY the dispatching member's checklist completes — never batched
    on the workspace's first execution; other members keep pending."""
    workspace = await workspace_factory()
    dispatcher = await member_factory(workspace)
    bystander = await member_factory(workspace)
    s_dispatcher = await _seed(session_factory, workspace, dispatcher)
    s_bystander = await _seed(session_factory, workspace, bystander)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    await make_assign_activity(
        session_factory, workspace, issue, actor_member=dispatcher, agent_member=agent_member
    )
    execution = await make_execution(
        session_factory, workspace, agent_id=agent.id, issue_id=issue.id, trigger="assign"
    )

    await _consume(
        session_factory,
        _event(
            workspace.id,
            "execution.queued",
            {
                "execution_id": str(execution.id),
                "agent_id": str(agent.id),
                "issue_id": str(issue.id),
                "trigger": "assign",
            },
        ),
    )

    d_steps = await _steps(session_factory, s_dispatcher.id)
    assert d_steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "completed"
    assert d_steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {
        "execution_id": str(execution.id),
        "trigger_member_id": str(dispatcher.id),
    }
    b_steps = await _steps(session_factory, s_bystander.id)
    assert b_steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"
    assert b_steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {}


async def test_execution_queued_mention_attributes_comment_author(
    session_factory, workspace_factory, member_factory, owner_user
):
    workspace = await workspace_factory()
    author = await member_factory(workspace)
    s_author = await _seed(session_factory, workspace, author)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    comment = await make_comment(session_factory, workspace, issue, author_member=author)
    execution, _ = await make_mention_execution(
        session_factory,
        workspace,
        comment=comment,
        mentioned_agent_member=agent_member,
        agent=agent,
        issue=issue,
        status="queued",
    )

    await _consume(
        session_factory,
        _event(
            workspace.id,
            "execution.queued",
            {
                "execution_id": str(execution.id),
                "agent_id": str(agent.id),
                "issue_id": str(issue.id),
                "trigger": "mention",
            },
        ),
    )

    steps = await _steps(session_factory, s_author.id)
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "completed"
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {
        "execution_id": str(execution.id),
        "trigger_member_id": str(author.id),
    }


async def test_execution_queued_skeleton_payload_skipped(
    session_factory, workspace_factory, member_factory
):
    """The mention writer's skeleton execution_id (an outbox event id) never
    resolves to an execution row — skipped; the materialized execution's own
    event covers the step."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    state = await _seed(session_factory, workspace, member)
    await _consume(
        session_factory,
        _event(
            workspace.id,
            "execution.queued",
            {
                "execution_id": str(uuid.uuid4()),
                "agent_member_id": str(uuid.uuid4()),
                "status": "queued",
                "trigger": "mention",
            },
        ),
    )
    steps = await _steps(session_factory, state.id)
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"


async def test_execution_queued_non_trigger_trigger_ignored(
    session_factory, workspace_factory, member_factory, owner_user
):
    """trigger ∉ (assign, mention) (e.g. autopilot) never advances step 4."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    state = await _seed(session_factory, workspace, member)
    agent, _ = await make_agent_member(session_factory, workspace, owner_user=owner_user)
    execution = await make_execution(
        session_factory, workspace, agent_id=agent.id, trigger="autopilot"
    )
    await _consume(
        session_factory,
        _event(
            workspace.id,
            "execution.queued",
            {"execution_id": str(execution.id), "trigger": "autopilot"},
        ),
    )
    steps = await _steps(session_factory, state.id)
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"


# --- notification.read → step 5 (aha, T34③④) ----------------------------------------


async def _aha_fixture(session_factory, workspace_factory, member_factory, owner_user):
    """Trigger member + bystander, agent reply on a completed triggered execution."""
    workspace = await workspace_factory()
    trigger = await member_factory(workspace)
    bystander = await member_factory(workspace)
    s_trigger = await _seed(session_factory, workspace, trigger)
    s_bystander = await _seed(session_factory, workspace, bystander)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    await make_assign_activity(
        session_factory, workspace, issue, actor_member=trigger, agent_member=agent_member
    )
    execution = await make_execution(
        session_factory, workspace, agent_id=agent.id, issue_id=issue.id, trigger="assign"
    )
    reply = await make_comment(session_factory, workspace, issue, author_member=agent_member)
    return {
        "workspace": workspace,
        "trigger": trigger,
        "bystander": bystander,
        "s_trigger": s_trigger,
        "s_bystander": s_bystander,
        "execution": execution,
        "reply": reply,
    }


async def test_notification_read_unread_never_completes(
    session_factory, workspace_factory, member_factory, owner_user
):
    """T34③: the unread notification (read_at NULL / unread event) never
    completes the final step nor sets aha."""
    fx = await _aha_fixture(session_factory, workspace_factory, member_factory, owner_user)
    notification = await make_notification(
        session_factory,
        fx["workspace"],
        recipient_member=fx["trigger"],
        comment=fx["reply"],
        execution=fx["execution"],
        read_at=None,
    )
    await _consume(
        session_factory,
        _event(
            fx["workspace"].id,
            "notification.read",
            {"id": str(notification.id), "read_at": None},
        ),
    )
    steps = await _steps(session_factory, fx["s_trigger"].id)
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    state = await _state(session_factory, fx["trigger"].id)
    assert state.aha_reached_at is None


async def test_notification_read_wrong_trigger_member_rejected(
    session_factory, workspace_factory, member_factory, owner_user
):
    """T34④: reading an agent reply on SOMEONE ELSE's triggered execution
    never completes the reader's final step."""
    fx = await _aha_fixture(session_factory, workspace_factory, member_factory, owner_user)
    notification = await make_notification(
        session_factory,
        fx["workspace"],
        recipient_member=fx["bystander"],
        comment=fx["reply"],
        execution=fx["execution"],
        read_at=datetime.now(UTC),
    )
    await _consume(
        session_factory,
        _event(
            fx["workspace"].id,
            "notification.read",
            {"id": str(notification.id), "read_at": "2026-07-29T00:00:00+00:00"},
        ),
    )
    steps = await _steps(session_factory, fx["s_bystander"].id)
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].evidence == {}
    state = await _state(session_factory, fx["bystander"].id)
    assert state.aha_reached_at is None
    # The trigger member is untouched too.
    t_steps = await _steps(session_factory, fx["s_trigger"].id)
    assert t_steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"


async def test_notification_read_trigger_member_completes_aha(
    session_factory, workspace_factory, member_factory, owner_user
):
    """T34④: the trigger member reading the reply completes the final step,
    persists the four-tuple evidence, sets aha exactly for them, and emits
    onboarding.progress + onboarding.completed through the outbox."""
    fx = await _aha_fixture(session_factory, workspace_factory, member_factory, owner_user)

    notification = await make_notification(
        session_factory,
        fx["workspace"],
        recipient_member=fx["trigger"],
        comment=fx["reply"],
        execution=fx["execution"],
        read_at=datetime.now(UTC),
    )
    await _consume(
        session_factory,
        _event(
            fx["workspace"].id,
            "notification.read",
            {"id": str(notification.id), "read_at": "2026-07-29T00:00:00+00:00"},
        ),
    )

    steps = await _steps(session_factory, fx["s_trigger"].id)
    step5 = steps[STEP_SEE_AGENT_REPLY_IN_INBOX]
    assert step5.status == "completed"
    assert step5.completed_via == "auto"
    assert step5.evidence == {
        "execution_id": str(fx["execution"].id),
        "comment_id": str(fx["reply"].id),
        "notification_id": str(notification.id),
        "trigger_member_id": str(fx["trigger"].id),
    }
    state = await _state(session_factory, fx["trigger"].id)
    assert state.aha_reached_at is not None

    # Derived realtime events queued via the outbox unique write path.
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == fx["workspace"].id,
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    names = [r.payload.get("event") for r in rows]
    assert names.count("onboarding.progress") == 1
    assert names.count("onboarding.completed") == 1
    channel = f"member:{fx['trigger'].id}:onboarding"
    assert all(r.payload["channel"] == channel for r in rows)


async def test_notification_read_redelivery_idempotent(
    session_factory, workspace_factory, member_factory, owner_user
):
    """At-least-once redelivery: second consumption is a pure no-op."""
    fx = await _aha_fixture(session_factory, workspace_factory, member_factory, owner_user)

    notification = await make_notification(
        session_factory,
        fx["workspace"],
        recipient_member=fx["trigger"],
        comment=fx["reply"],
        execution=fx["execution"],
        read_at=datetime.now(UTC),
    )
    event = _event(
        fx["workspace"].id,
        "notification.read",
        {"id": str(notification.id), "read_at": "2026-07-29T00:00:00+00:00"},
    )
    await _consume(session_factory, event)
    await _consume(session_factory, event)

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == fx["workspace"].id,
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    names = [r.payload.get("event") for r in rows]
    assert names.count("onboarding.progress") == 1
    assert names.count("onboarding.completed") == 1
    state = await _state(session_factory, fx["trigger"].id)
    steps = await _steps(session_factory, state.id)
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "completed"


async def test_human_authored_comment_notification_ignored(
    session_factory, workspace_factory, member_factory
):
    """A read notification about a HUMAN comment never touches the step."""
    workspace = await workspace_factory()
    human_a = await member_factory(workspace)
    human_b = await member_factory(workspace)
    state = await _seed(session_factory, workspace, human_b)
    issue = await make_issue(session_factory, workspace)
    comment = await make_comment(session_factory, workspace, issue, author_member=human_a)

    notification = await make_notification(
        session_factory,
        workspace,
        recipient_member=human_b,
        comment=comment,
        read_at=datetime.now(UTC),
    )
    await _consume(
        session_factory,
        _event(workspace.id, "notification.read", {"id": str(notification.id)}),
    )
    steps = await _steps(session_factory, state.id)
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"


# --- misc ---------------------------------------------------------------------------


async def test_unhandled_event_ignored(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    state = await _seed(session_factory, workspace, member)
    await _consume(session_factory, _event(workspace.id, "comment.created", {"id": "x"}))
    steps = await _steps(session_factory, state.id)
    assert all(s.status in ("pending", "completed") for s in steps.values())


async def test_consumer_reconciles_legacy_member_on_next_get(
    session_factory, workspace_factory, member_factory, owner_user
):
    """A domain event does NOT seed missing checklists (seeding belongs to
    the enrollment transaction + the GET lazy fallback); a legacy member's
    next GET seeds + reconciles and picks the workspace fact up."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)  # no checklist yet
    _, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    await _consume(
        session_factory,
        _event(
            workspace.id,
            "member.added",
            {"member_id": str(agent_member.id), "member_type": "agent", "role": "member"},
        ),
    )
    assert await _state(session_factory, member.id) is None  # consumer does not seed

    from mesh.onboarding.service import OnboardingService

    payload = await OnboardingService(session_factory).get_state(
        workspace_id=workspace.id, member_id=member.id
    )
    step_map = {s["step_key"]: s for s in payload["steps"]}
    assert step_map[STEP_INVITE_MEMBER_OR_ADD_AGENT]["status"] == "completed"
    assert seed_for_new_member is not None  # import sanity


async def test_execution_queued_assign_at_creation_attributes_reporter(
    session_factory, workspace_factory, member_factory, owner_user
):
    """Assignment AT ISSUE CREATION writes no issue_activity trail; the
    dispatching member is then the issue creator (reporter)."""
    workspace = await workspace_factory()
    creator = await member_factory(workspace)
    s_creator = await _seed(session_factory, workspace, creator)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(
        session_factory, workspace, reporter_id=creator.id, assignee_id=agent_member.id
    )
    execution = await make_execution(
        session_factory, workspace, agent_id=agent.id, issue_id=issue.id, trigger="assign"
    )

    await _consume(
        session_factory,
        _event(
            workspace.id,
            "execution.queued",
            {
                "execution_id": str(execution.id),
                "agent_id": str(agent.id),
                "issue_id": str(issue.id),
                "trigger": "assign",
            },
        ),
    )

    steps = await _steps(session_factory, s_creator.id)
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "completed"
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {
        "execution_id": str(execution.id),
        "trigger_member_id": str(creator.id),
    }

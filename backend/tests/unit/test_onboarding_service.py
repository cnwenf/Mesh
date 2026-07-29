"""Onboarding service tests — seeding, reconcile (R3/R4), guards, API methods.

onboarding.md §3 / §5.1, README §9 T34. Real PostgreSQL via the shared
fixtures; fact rows built by onboarding_support (no mocks).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.onboarding import (
    ACTIVATION_STEP_KEYS,
    STEP_CREATE_FIRST_ISSUE,
    STEP_CREATE_WORKSPACE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError, ValidationError
from mesh.onboarding.service import (
    OnboardingService,
    ensure_seeded,
    mark_aha,
    reconcile_state,
    seed_for_new_member,
)
from tests.unit.onboarding_support import (
    earlier,
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


async def _steps_by_key(session_factory, state_id) -> dict[str, OnboardingStateStep]:
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


async def _state_for(session_factory, member_id) -> OnboardingState | None:
    async with session_factory() as session:
        return await session.scalar(
            select(OnboardingState).where(OnboardingState.member_id == member_id)
        )


# --- seeding (T34①) -------------------------------------------------------------


async def test_seed_human_member_step1_completed(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    async with session_factory() as session, session.begin():
        state = await seed_for_new_member(session, workspace_id=workspace.id, member=member)
    assert state is not None
    steps = await _steps_by_key(session_factory, state.id)
    assert set(steps) == set(ACTIVATION_STEP_KEYS)
    assert steps[STEP_CREATE_WORKSPACE].status == "completed"
    assert steps[STEP_CREATE_WORKSPACE].completed_via == "auto"
    assert steps[STEP_CREATE_WORKSPACE].completed_at is not None
    for key in ACTIVATION_STEP_KEYS[1:]:
        assert steps[key].status == "pending"
        assert steps[key].completed_at is None


async def test_agent_member_not_seeded(session_factory, workspace_factory, owner_user):
    workspace = await workspace_factory()
    _, agent_member = await make_agent_member(session_factory, workspace, owner_user=owner_user)
    async with session_factory() as session, session.begin():
        result = await seed_for_new_member(session, workspace_id=workspace.id, member=agent_member)
    assert result is None
    assert await _state_for(session_factory, agent_member.id) is None


async def test_ensure_seeded_idempotent(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    async with session_factory() as session, session.begin():
        state1, created1 = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
        state2, created2 = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
    assert created1 is True
    assert created2 is False
    assert state1.id == state2.id
    steps = await _steps_by_key(session_factory, state1.id)
    assert len(steps) == 5  # no duplicate step rows


async def test_ensure_seeded_concurrent_first_visits(session_factory, workspace_factory, member_factory):
    """≥10 concurrent seeders ⇒ exactly one record + exactly five steps (§5.1)."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)

    async def _seed():
        async with session_factory() as session, session.begin():
            state, _ = await ensure_seeded(
                session, workspace_id=workspace.id, member_id=member.id
            )
            return state.id

    ids = await asyncio.gather(*[_seed() for _ in range(12)])
    assert len(set(ids)) == 1
    steps = await _steps_by_key(session_factory, ids[0])
    assert len(steps) == 5


# --- mature-workspace reconcile (T34②, R3/R4) ------------------------------------


async def test_mature_workspace_reconcile_workspace_facts_only(
    session_factory, workspace_factory, member_factory, owner_user
):
    """Invitee into a mature workspace: steps 2–3 complete from workspace
    facts; step 4 stays pending because THIS member never triggered an
    execution (R4 — never batched from others' executions, no fabricated
    evidence)."""
    workspace = await workspace_factory()
    other = await member_factory(workspace)  # human #1 (pre-existing)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace, reporter_id=other.id)
    # Other member triggered an execution historically — NOT the invitee.
    await make_assign_activity(
        session_factory, workspace, issue, actor_member=other, agent_member=agent_member
    )
    await make_execution(
        session_factory, workspace, agent_id=agent.id, issue_id=issue.id, trigger="assign"
    )

    invitee = await member_factory(workspace)
    async with session_factory() as session, session.begin():
        state = await seed_for_new_member(session, workspace_id=workspace.id, member=invitee)

    steps = await _steps_by_key(session_factory, state.id)
    assert steps[STEP_CREATE_WORKSPACE].status == "completed"
    assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "completed"
    assert steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].evidence["member_added_id"] == str(
        agent_member.id
    )
    assert steps[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert steps[STEP_CREATE_FIRST_ISSUE].evidence["issue_id"] == str(issue.id)
    # R4: the invitee never triggered anything — stays pending.
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"
    assert steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {}
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    refreshed = await _state_for(session_factory, invitee.id)
    assert refreshed.aha_reached_at is None


async def test_reconcile_step4_own_assign_history(
    session_factory, workspace_factory, member_factory, owner_user
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    activity = await make_assign_activity(
        session_factory, workspace, issue, actor_member=member, agent_member=agent_member
    )
    execution = await make_execution(
        session_factory,
        workspace,
        agent_id=agent.id,
        issue_id=issue.id,
        trigger="assign",
        queued_at=earlier(hours=0),
    )

    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
        await reconcile_state(session, workspace_id=workspace.id, state=state)

    steps = await _steps_by_key(session_factory, state.id)
    step4 = steps[STEP_DISPATCH_OR_MENTION_AGENT]
    assert step4.status == "completed"
    assert step4.evidence["execution_id"] == str(execution.id)
    assert step4.evidence["trigger_member_id"] == str(member.id)
    assert activity is not None


async def test_reconcile_step4_own_mention_history(
    session_factory, workspace_factory, member_factory, owner_user
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    comment = await make_comment(session_factory, workspace, issue, author_member=member)
    execution, _ = await make_mention_execution(
        session_factory,
        workspace,
        comment=comment,
        mentioned_agent_member=agent_member,
        agent=agent,
        issue=issue,
    )

    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
        await reconcile_state(session, workspace_id=workspace.id, state=state)

    steps = await _steps_by_key(session_factory, state.id)
    step4 = steps[STEP_DISPATCH_OR_MENTION_AGENT]
    assert step4.status == "completed"
    assert step4.evidence["execution_id"] == str(execution.id)
    assert step4.evidence["trigger_member_id"] == str(member.id)


async def test_reconcile_step5_from_historical_read(
    session_factory, workspace_factory, member_factory, owner_user
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    await make_assign_activity(
        session_factory, workspace, issue, actor_member=member, agent_member=agent_member
    )
    execution = await make_execution(
        session_factory,
        workspace,
        agent_id=agent.id,
        issue_id=issue.id,
        trigger="assign",
    )
    reply = await make_comment(session_factory, workspace, issue, author_member=agent_member)
    notification = await make_notification(
        session_factory,
        workspace,
        recipient_member=member,
        comment=reply,
        execution=execution,
        read_at=datetime.now(UTC),
    )

    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
        await reconcile_state(session, workspace_id=workspace.id, state=state)

    steps = await _steps_by_key(session_factory, state.id)
    step5 = steps[STEP_SEE_AGENT_REPLY_IN_INBOX]
    assert step5.status == "completed"
    assert step5.evidence == {
        "execution_id": str(execution.id),
        "comment_id": str(reply.id),
        "notification_id": str(notification.id),
        "trigger_member_id": str(member.id),
    }
    refreshed = await _state_for(session_factory, member.id)
    assert refreshed.aha_reached_at is not None


async def test_reconcile_step5_unread_stays_pending(
    session_factory, workspace_factory, member_factory, owner_user
):
    """T34③: an UNREAD qualifying notification never completes the step."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    issue = await make_issue(session_factory, workspace)
    await make_assign_activity(
        session_factory, workspace, issue, actor_member=member, agent_member=agent_member
    )
    execution = await make_execution(
        session_factory, workspace, agent_id=agent.id, issue_id=issue.id, trigger="assign"
    )
    reply = await make_comment(session_factory, workspace, issue, author_member=agent_member)
    await make_notification(
        session_factory,
        workspace,
        recipient_member=member,
        comment=reply,
        execution=execution,
        read_at=None,  # unread
    )

    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
        await reconcile_state(session, workspace_id=workspace.id, state=state)

    steps = await _steps_by_key(session_factory, state.id)
    assert steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    refreshed = await _state_for(session_factory, member.id)
    assert refreshed.aha_reached_at is None


# --- guards ----------------------------------------------------------------------


async def test_mark_aha_only_once(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id
        )
    async with session_factory() as session, session.begin():
        first = await mark_aha(session, state_id=state.id)
    async with session_factory() as session, session.begin():
        second = await mark_aha(session, state_id=state.id)
    assert first is True
    assert second is False


# --- service (route-owned) --------------------------------------------------------


def _service(session_factory) -> OnboardingService:
    return OnboardingService(session_factory)


async def test_get_state_lazy_seed_and_shape(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    payload = await _service(session_factory).get_state(
        workspace_id=workspace.id, member_id=member.id
    )
    assert payload["workspace_id"] == str(workspace.id)
    assert payload["member_id"] == str(member.id)
    assert payload["checklist"] == "activation"
    assert payload["aha_reached_at"] is None
    assert payload["dismissed_at"] is None
    assert payload["progress"] == {"total": 5, "completed": 1, "skipped": 0}
    assert [s["step_key"] for s in payload["steps"]] == list(ACTIVATION_STEP_KEYS)
    assert payload["steps"][0]["status"] == "completed"
    assert payload["steps"][0]["completed_via"] == "auto"
    # Second call: no reseed, identical id.
    again = await _service(session_factory).get_state(
        workspace_id=workspace.id, member_id=member.id
    )
    assert again["id"] == payload["id"]


async def test_manual_complete_and_idempotent_no_override(
    session_factory, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    step = await _service(session_factory).complete_step_manual(
        workspace_id=workspace.id,
        member_id=member.id,
        step_key=STEP_CREATE_FIRST_ISSUE,
    )
    assert step["status"] == "completed"
    assert step["completed_via"] == "manual"
    first_at = step["completed_at"]
    # Repeat = no-op: completed_via / completed_at NOT overwritten (§3.5).
    again = await _service(session_factory).complete_step_manual(
        workspace_id=workspace.id,
        member_id=member.id,
        step_key=STEP_CREATE_FIRST_ISSUE,
    )
    assert again["completed_via"] == "manual"
    assert again["completed_at"] == first_at
    state = await _state_for(session_factory, member.id)
    steps = await _steps_by_key(session_factory, state.id)
    assert steps[STEP_CREATE_FIRST_ISSUE].evidence == {"manual_by": str(member.id)}


async def test_manual_complete_invalid_step_key(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    with pytest.raises(ValidationError):
        await _service(session_factory).complete_step_manual(
            workspace_id=workspace.id, member_id=member.id, step_key="tour_step"
        )


async def test_manual_complete_while_dismissed_raises(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    service = _service(session_factory)
    await service.dismiss(workspace_id=workspace.id, member_id=member.id)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.complete_step_manual(
            workspace_id=workspace.id,
            member_id=member.id,
            step_key=STEP_INVITE_MEMBER_OR_ADD_AGENT,
        )
    assert excinfo.value.code == "checklist_completed"


async def test_dismiss_restore_idempotent(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    service = _service(session_factory)
    first = await service.dismiss(workspace_id=workspace.id, member_id=member.id)
    second = await service.dismiss(workspace_id=workspace.id, member_id=member.id)
    assert first["dismissed_at"] == second["dismissed_at"]  # first value wins
    restored = await service.restore(workspace_id=workspace.id, member_id=member.id)
    assert restored["dismissed_at"] is None
    restored_again = await service.restore(workspace_id=workspace.id, member_id=member.id)
    assert restored_again["dismissed_at"] is None


async def test_admin_reset_rebuilds(session_factory, workspace_factory, member_factory, owner_user):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    agent, agent_member = await make_agent_member(
        session_factory, workspace, owner_user=owner_user
    )
    service = _service(session_factory)
    await service.get_state(workspace_id=workspace.id, member_id=member.id)
    await service.complete_step_manual(
        workspace_id=workspace.id, member_id=member.id, step_key=STEP_CREATE_FIRST_ISSUE
    )
    old = await _state_for(session_factory, member.id)

    fresh = await service.reset(workspace_id=workspace.id, member_id=member.id)
    assert fresh["id"] != str(old.id)
    assert fresh["aha_reached_at"] is None
    assert fresh["dismissed_at"] is None
    step_map = {s["step_key"]: s for s in fresh["steps"]}
    assert step_map[STEP_CREATE_WORKSPACE]["status"] == "completed"
    # Reconcile applies: the workspace now has an agent member.
    assert step_map[STEP_INVITE_MEMBER_OR_ADD_AGENT]["status"] == "completed"
    # The manually-completed issue step is back to its reconciled truth
    # (no issue exists → pending again).
    assert step_map[STEP_CREATE_FIRST_ISSUE]["status"] == "pending"
    assert agent is not None and agent_member is not None


async def test_reset_unknown_checklist_rejected(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    with pytest.raises(ValidationError):
        await _service(session_factory).reset(
            workspace_id=workspace.id, member_id=member.id, checklist="custom"
        )


async def test_realtime_progress_emitted_on_completion(
    session_factory, workspace_factory, member_factory
):
    from mesh.db.models.outbox import OutboxEvent

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await _service(session_factory).complete_step_manual(
        workspace_id=workspace.id, member_id=member.id, step_key=STEP_CREATE_FIRST_ISSUE
    )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    progress_rows = [r for r in rows if r.payload.get("event") == "onboarding.progress"]
    assert len(progress_rows) == 1
    payload = progress_rows[0].payload
    assert payload["channel"] == f"member:{member.id}:onboarding"
    assert payload["data"]["step_key"] == STEP_CREATE_FIRST_ISSUE
    assert payload["data"]["completed_via"] == "manual"
    assert payload["data"]["progress"]["completed"] == 2  # step 1 + this one


async def test_final_step_manual_sets_aha_and_completed_event(
    session_factory, workspace_factory, member_factory
):
    from mesh.db.models.outbox import OutboxEvent

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    service = _service(session_factory)
    await service.complete_step_manual(
        workspace_id=workspace.id,
        member_id=member.id,
        step_key=STEP_SEE_AGENT_REPLY_IN_INBOX,
    )
    state = await _state_for(session_factory, member.id)
    assert state.aha_reached_at is not None
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    completed_rows = [r for r in rows if r.payload.get("event") == "onboarding.completed"]
    assert len(completed_rows) == 1
    assert completed_rows[0].payload["data"]["state_id"] == str(state.id)


async def test_ensure_seeded_clock_injection(session_factory, workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    fixed = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        state, _ = await ensure_seeded(
            session, workspace_id=workspace.id, member_id=member.id, clock=lambda: fixed
        )
    steps = await _steps_by_key(session_factory, state.id)
    assert steps[STEP_CREATE_WORKSPACE].completed_at == fixed
    assert Member is not None  # models import sanity

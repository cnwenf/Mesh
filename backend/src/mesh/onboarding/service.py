"""Onboarding service — seeding, reconcile, completion guards, rendering.

onboarding.md §3 (authoritative). The checklist is seeded in the member
ENROLLMENT transaction (§3.5 R3 main path — workspace creation / invitation
redeem / direct add) and lazily on first ``GET /onboarding/state`` (fallback
for pre-existing members). Both paths run a full historical RECONCILE in the
same transaction so an invitee entering a mature workspace gets steps 2–5
completed from their OWN history — never permanently pending, never with
fabricated evidence (R3/R4):

* step 2 — workspace roster already has an agent member or ≥ 2 humans;
* step 3 — workspace already has an issue, or the member reported one;
* step 4 — the member THEMSELVES historically triggered an assign/mention
  execution (others' executions never count — R4);
* step 5 — the member historically READ a qualifying agent-reply
  notification; unread ⇒ pending until ``notification.read`` drives it.

Completion guards are conditional single-row UPDATEs (``status='pending'``
predicate): 0 rows = already completed/skipped = no-op. This is what makes
at-least-once domain-event consumption and concurrent first visits safe.
Derived realtime events go through the transactional outbox ``emit_realtime``
(README §6.6/§6.7 unique write path — this module never writes
``realtime_events`` directly).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.constraints import violates
from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.onboarding import (
    ACTIVATION_CHECKLIST,
    ACTIVATION_STEP_KEYS,
    COMPLETED_VIA_AUTO,
    COMPLETED_VIA_MANUAL,
    STEP_CREATE_FIRST_ISSUE,
    STEP_CREATE_WORKSPACE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_PENDING,
    STEP_STATUS_SKIPPED,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ValidationError
from mesh.onboarding.channels import onboarding_channel
from mesh.outbox.service import emit_realtime

logger = logging.getLogger("mesh.onboarding")

Clock = Callable[[], datetime]

_STEP_STATE_UNIQUE = "uq_onboarding_states_ws_member_checklist"
_STEP_ROW_UNIQUE = "uq_onboarding_steps_ws_state_step"
_IDEMPOTENCY_PREFIX = "ws"
_TRIGGER_TRIGGERS = ("assign", "mention")
_ASSIGN_FIELD = "assignee_id"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _parse_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


# --- rendering ----------------------------------------------------------------


def render_step(step: OnboardingStateStep | None, step_key: str) -> dict:
    """One step object for the API (§3.2 — evidence is audit-only, not exposed)."""
    if step is None:
        return {
            "step_key": step_key,
            "status": STEP_STATUS_PENDING,
            "completed_via": None,
            "completed_at": None,
        }
    return {
        "step_key": step.step_key,
        "status": step.status,
        "completed_via": step.completed_via,
        "completed_at": _iso(step.completed_at),
    }


async def load_steps(
    session: AsyncSession, *, workspace_id: uuid.UUID, state_id: uuid.UUID
) -> dict[str, OnboardingStateStep]:
    rows = (
        (
            await session.execute(
                select(OnboardingStateStep).where(
                    OnboardingStateStep.workspace_id == workspace_id,
                    OnboardingStateStep.state_id == state_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.step_key: row for row in rows}


async def progress_snapshot(
    session: AsyncSession, *, workspace_id: uuid.UUID, state_id: uuid.UUID
) -> dict:
    steps = await load_steps(session, workspace_id=workspace_id, state_id=state_id)
    statuses = [step.status for step in steps.values()]
    return {
        "total": len(ACTIVATION_STEP_KEYS),
        "completed": statuses.count(STEP_STATUS_COMPLETED),
        "skipped": statuses.count(STEP_STATUS_SKIPPED),
    }


async def render_state(
    session: AsyncSession, state: OnboardingState
) -> dict:
    """The full GET /onboarding/state payload (§3.2)."""
    steps = await load_steps(session, workspace_id=state.workspace_id, state_id=state.id)
    statuses = [step.status for step in steps.values()]
    return {
        "id": str(state.id),
        "workspace_id": str(state.workspace_id),
        "member_id": str(state.member_id),
        "checklist": state.checklist,
        "aha_reached_at": _iso(state.aha_reached_at),
        "dismissed_at": _iso(state.dismissed_at),
        "progress": {
            "total": len(ACTIVATION_STEP_KEYS),
            "completed": statuses.count(STEP_STATUS_COMPLETED),
            "skipped": statuses.count(STEP_STATUS_SKIPPED),
        },
        "steps": [render_step(steps.get(key), key) for key in ACTIVATION_STEP_KEYS],
        "created_at": _iso(state.created_at),
        "updated_at": _iso(state.updated_at),
    }


# --- seeding (§3.5) -----------------------------------------------------------


async def _get_state(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> OnboardingState | None:
    return await session.scalar(
        select(OnboardingState).where(
            OnboardingState.workspace_id == workspace_id,
            OnboardingState.member_id == member_id,
            OnboardingState.checklist == ACTIVATION_CHECKLIST,
        )
    )


async def ensure_seeded(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    clock: Clock = _utcnow,
) -> tuple[OnboardingState, bool]:
    """Idempotently create the activation checklist + its five step rows.

    Returns ``(state, created)``. Concurrency-safe: the
    ``UNIQUE(workspace_id, member_id, checklist)`` constraint arbitrates
    racing seeders (savepoint-guarded INSERT so the outer transaction
    survives the loser's conflict); the step batch likewise relies on
    ``UNIQUE(workspace_id, state_id, step_key)``. The ``create_workspace``
    step is born completed (auto) — the workspace exists by definition
    (§1.2.1 step 1).
    """
    existing = await _get_state(session, workspace_id=workspace_id, member_id=member_id)
    if existing is not None:
        return existing, False

    now = clock()
    state: OnboardingState
    try:
        async with session.begin_nested():
            state = OnboardingState(
                workspace_id=workspace_id,
                member_id=member_id,
                checklist=ACTIVATION_CHECKLIST,
            )
            session.add(state)
            await session.flush()
    except IntegrityError as exc:
        if not violates(exc, _STEP_STATE_UNIQUE):
            raise
        # A concurrent seeder won; its row + steps are the truth.
        state = await _get_state(session, workspace_id=workspace_id, member_id=member_id)
        if state is None:  # pragma: no cover — winner vanished (deleted mid-race)
            raise
        return state, False

    steps = [
        OnboardingStateStep(
            workspace_id=workspace_id,
            state_id=state.id,
            step_key=key,
            status=STEP_STATUS_COMPLETED if key == STEP_CREATE_WORKSPACE else STEP_STATUS_PENDING,
            completed_via=COMPLETED_VIA_AUTO if key == STEP_CREATE_WORKSPACE else None,
            completed_at=now if key == STEP_CREATE_WORKSPACE else None,
        )
        for key in ACTIVATION_STEP_KEYS
    ]
    try:
        async with session.begin_nested():
            session.add_all(steps)
            await session.flush()
    except IntegrityError as exc:
        if not violates(exc, _STEP_ROW_UNIQUE):
            raise
        # Steps already seeded by a racing transaction — no-op.
    return state, True


async def seed_for_new_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member: Member,
    clock: Clock = _utcnow,
) -> OnboardingState | None:
    """Enrollment hook (onboarding.md §3.5 R3 main path, T34①).

    Called inside the member enrollment transaction (workspace creation /
    invitation redeem / direct add). Human members get the activation
    checklist seeded + fully reconciled in the SAME transaction; agent
    members get nothing (the checklist is the human member's activation
    path — T34①). Returns the state for humans, None for agents.
    """
    if member.member_type != "human":
        return None
    state, _ = await ensure_seeded(
        session, workspace_id=workspace_id, member_id=member.id, clock=clock
    )
    await reconcile_state(session, workspace_id=workspace_id, state=state, clock=clock)
    return state


# --- completion guards (§3.5) --------------------------------------------------


async def complete_step(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    state: OnboardingState,
    step_key: str,
    via: str,
    evidence: dict,
    clock: Clock = _utcnow,
) -> bool:
    """Conditional-UPDATE completion guard; True only on a real transition.

    ``pending → completed`` is one-way; a 0-row UPDATE means the step was
    already completed/skipped (no-op — the at-least-once dedup). On a real
    transition, ``onboarding.progress`` is emitted through the outbox in the
    SAME transaction; the final step additionally sets ``aha_reached_at``
    (exactly once) and emits ``onboarding.completed``.
    """
    now = clock()
    row = (
        (
            await session.execute(
                update(OnboardingStateStep)
                .where(
                    OnboardingStateStep.workspace_id == workspace_id,
                    OnboardingStateStep.state_id == state.id,
                    OnboardingStateStep.step_key == step_key,
                    OnboardingStateStep.status == STEP_STATUS_PENDING,
                )
                .values(
                    status=STEP_STATUS_COMPLETED,
                    completed_via=via,
                    completed_at=now,
                    evidence=evidence,
                    updated_at=now,
                )
                .returning(OnboardingStateStep.id)
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return False

    progress = await progress_snapshot(session, workspace_id=workspace_id, state_id=state.id)
    channel = onboarding_channel(state.member_id)
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=channel,
        event="onboarding.progress",
        data={
            "state_id": str(state.id),
            "checklist": state.checklist,
            "step_key": step_key,
            "status": STEP_STATUS_COMPLETED,
            "completed_via": via,
            "progress": progress,
        },
        idempotency_key=f"onboarding:{state.id}:{step_key}:{via}",
    )
    if step_key == STEP_SEE_AGENT_REPLY_IN_INBOX:
        aha_first = await mark_aha(session, state_id=state.id, clock=clock)
        if aha_first:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=channel,
                event="onboarding.completed",
                data={
                    "state_id": str(state.id),
                    "checklist": state.checklist,
                    "aha_reached_at": _iso(clock()),
                    "progress": progress,
                },
                idempotency_key=f"onboarding:{state.id}:completed",
            )
    return True


async def mark_aha(
    session: AsyncSession, *, state_id: uuid.UUID, clock: Clock = _utcnow
) -> bool:
    """Set ``aha_reached_at`` exactly once (conditional UPDATE guard)."""
    row = (
        (
            await session.execute(
                update(OnboardingState)
                .where(
                    OnboardingState.id == state_id,
                    OnboardingState.aha_reached_at.is_(None),
                )
                .values(aha_reached_at=clock(), updated_at=clock())
                .returning(OnboardingState.id)
            )
        )
        .scalars()
        .first()
    )
    return row is not None


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
    (member) and which predates the enqueue.
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
    return await session.scalar(
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


# --- historical reconcile (R3) --------------------------------------------------


async def _reconcile_step_invite(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    agent = await session.scalar(
        select(Member)
        .where(
            Member.workspace_id == workspace_id,
            Member.member_type == "agent",
            Member.status == "active",
        )
        .order_by(Member.created_at.asc())
        .limit(1)
    )
    evidence: dict | None = None
    if agent is not None:
        evidence = {"member_added_id": str(agent.id)}
    else:
        humans = (
            (
                await session.execute(
                    select(Member)
                    .where(
                        Member.workspace_id == workspace_id,
                        Member.member_type == "human",
                        Member.status == "active",
                    )
                    .order_by(Member.created_at.asc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if len(humans) >= 2:
            evidence = {"member_added_id": str(humans[1].id)}
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_INVITE_MEMBER_OR_ADD_AGENT,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _reconcile_step_first_issue(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    reported = await session.scalar(
        select(Issue)
        .where(
            Issue.workspace_id == workspace_id,
            Issue.reporter_id == state.member_id,
        )
        .order_by(Issue.created_at.asc())
        .limit(1)
    )
    evidence: dict | None = None
    if reported is not None:
        evidence = {"issue_id": str(reported.id), "reporter_member_id": str(state.member_id)}
    else:
        first = await session.scalar(
            select(Issue)
            .where(Issue.workspace_id == workspace_id)
            .order_by(Issue.created_at.asc(), Issue.id.asc())
            .limit(1)
        )
        if first is not None:
            evidence = {"issue_id": str(first.id)}
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_CREATE_FIRST_ISSUE,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _reconcile_step_dispatch(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    """Step 4 by the member's OWN history only (R4 — never batch from others')."""
    evidence = await _historical_mention_evidence(
        session, workspace_id=workspace_id, member_id=state.member_id
    )
    if evidence is None:
        evidence = await _historical_assign_evidence(
            session, workspace_id=workspace_id, member_id=state.member_id
        )
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_DISPATCH_OR_MENTION_AGENT,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _historical_mention_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> dict | None:
    """Earliest mention-triggered execution this member personally authored."""
    rows = (
        (
            await session.execute(
                select(CommentMention.triggered_execution_id)
                .join(
                    Comment,
                    and_(
                        Comment.id == CommentMention.comment_id,
                        Comment.workspace_id == CommentMention.workspace_id,
                    ),
                )
                .where(
                    CommentMention.workspace_id == workspace_id,
                    CommentMention.triggered_execution_id.is_not(None),
                    CommentMention.deleted_at.is_(None),
                    Comment.author_id == member_id,
                )
                .order_by(CommentMention.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for outbox_id in rows:
        scoped = await session.scalar(
            select(OutboxEvent.idempotency_key).where(
                OutboxEvent.workspace_id == workspace_id,
                OutboxEvent.id == outbox_id,
            )
        )
        if scoped is None:
            continue
        prefix = f"{_IDEMPOTENCY_PREFIX}:{workspace_id}:"
        if not scoped.startswith(prefix):
            continue
        execution = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.idempotency_key == scoped[len(prefix) :],
            )
        )
        if execution is not None and execution.trigger in _TRIGGER_TRIGGERS:
            return {"execution_id": str(execution.id), "trigger_member_id": str(member_id)}
    return None


async def _historical_assign_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> dict | None:
    """Earliest assign-triggered execution this member personally dispatched."""
    activities = (
        (
            await session.execute(
                select(IssueActivity)
                .where(
                    IssueActivity.workspace_id == workspace_id,
                    IssueActivity.actor_member_id == member_id,
                    IssueActivity.field == _ASSIGN_FIELD,
                    IssueActivity.new_value.is_not(None),
                )
                .order_by(IssueActivity.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for activity in activities:
        assigned = activity.new_value if isinstance(activity.new_value, str) else None
        assigned_id = _parse_uuid(assigned)
        if assigned_id is None:
            continue
        agent_member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == assigned_id,
                Member.member_type == "agent",
            )
        )
        if agent_member is None or agent_member.agent_id is None:
            continue
        execution = await session.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.issue_id == activity.issue_id,
                TaskExecution.agent_id == agent_member.agent_id,
                TaskExecution.trigger == "assign",
                TaskExecution.queued_at >= activity.created_at,
            )
            .order_by(TaskExecution.queued_at.asc())
            .limit(1)
        )
        if execution is not None:
            return {"execution_id": str(execution.id), "trigger_member_id": str(member_id)}
    return None


async def _reconcile_step_aha(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    """Step 5 from history: a previously READ qualifying notification only."""
    notifications = (
        (
            await session.execute(
                select(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == state.member_id,
                    Notification.read_at.is_not(None),
                    Notification.comment_id.is_not(None),
                )
                .order_by(Notification.read_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for notification in notifications:
        evidence = await evaluate_agent_reply_notification(
            session,
            workspace_id=workspace_id,
            notification=notification,
            member_id=state.member_id,
        )
        if evidence is not None:
            await complete_step(
                session,
                workspace_id=workspace_id,
                state=state,
                step_key=STEP_SEE_AGENT_REPLY_IN_INBOX,
                via=COMPLETED_VIA_AUTO,
                evidence=evidence,
                clock=clock,
            )
            return


async def reconcile_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    state: OnboardingState,
    clock: Clock = _utcnow,
) -> None:
    """Full historical reconcile (§3.5 R3) — idempotent via completion guards.

    Steps complete from the member's OWN history with real evidence; a
    member who never triggered an execution keeps step 4 pending (R4 — no
    batch completion from workspace-first-execution, no fabricated
    evidence); step 5 stays pending until a qualifying notification is
    actually read.
    """
    await _reconcile_step_invite(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_first_issue(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_dispatch(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_aha(session, workspace_id=workspace_id, state=state, clock=clock)


# --- service (route-owned transactions) ----------------------------------------


class OnboardingService:
    """Self-service + admin checklist operations (onboarding.md §3.1)."""

    def __init__(self, session_factory, clock: Clock = _utcnow) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def get_state(self, *, workspace_id: uuid.UUID, member_id: uuid.UUID) -> dict:
        """GET /onboarding/state — lazy seed+reconcile fallback (§3.5 entry 2)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            state, created = await ensure_seeded(
                session, workspace_id=workspace_id, member_id=member_id, clock=self._clock
            )
            if created:
                await reconcile_state(
                    session, workspace_id=workspace_id, state=state, clock=self._clock
                )
            return await render_state(session, state)

    async def complete_step_manual(
        self, *, workspace_id: uuid.UUID, member_id: uuid.UUID, step_key: str
    ) -> dict:
        """POST /onboarding/steps/{step_key}/complete — idempotent manual mark."""
        if step_key not in ACTIVATION_STEP_KEYS:
            raise ValidationError(
                "unknown step_key", code="validation_error", details={"step_key": step_key}
            )
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            state, _ = await ensure_seeded(
                session, workspace_id=workspace_id, member_id=member_id, clock=self._clock
            )
            if (
                state.dismissed_at is not None
                and step_key != STEP_SEE_AGENT_REPLY_IN_INBOX
            ):
                raise BusinessRuleError(
                    "checklist is dismissed; restore it before completing steps",
                    code="checklist_completed",
                )
            await complete_step(
                session,
                workspace_id=workspace_id,
                state=state,
                step_key=step_key,
                via=COMPLETED_VIA_MANUAL,
                evidence={"manual_by": str(member_id)},
                clock=self._clock,
            )
            steps = await load_steps(session, workspace_id=workspace_id, state_id=state.id)
            return render_step(steps.get(step_key), step_key)

    async def dismiss(self, *, workspace_id: uuid.UUID, member_id: uuid.UUID) -> dict:
        """POST /onboarding/dismiss — idempotent (first dismissed_at wins)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            state, _ = await ensure_seeded(
                session, workspace_id=workspace_id, member_id=member_id, clock=self._clock
            )
            await session.execute(
                update(OnboardingState)
                .where(
                    OnboardingState.id == state.id,
                    OnboardingState.dismissed_at.is_(None),
                )
                .values(dismissed_at=self._clock(), updated_at=self._clock())
            )
            await session.refresh(state)
            return {"id": str(state.id), "dismissed_at": _iso(state.dismissed_at)}

    async def restore(self, *, workspace_id: uuid.UUID, member_id: uuid.UUID) -> dict:
        """POST /onboarding/restore — clears dismissed_at (idempotent)."""
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            state, _ = await ensure_seeded(
                session, workspace_id=workspace_id, member_id=member_id, clock=self._clock
            )
            await session.execute(
                update(OnboardingState)
                .where(
                    OnboardingState.id == state.id,
                    OnboardingState.dismissed_at.is_not(None),
                )
                .values(dismissed_at=None, updated_at=self._clock())
            )
            await session.refresh(state)
            return {"id": str(state.id), "dismissed_at": _iso(state.dismissed_at)}

    async def reset(
        self,
        *,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        checklist: str = ACTIVATION_CHECKLIST,
    ) -> dict:
        """Admin reset (§3.4): DELETE + reseed + full reconcile, one transaction."""
        if checklist != ACTIVATION_CHECKLIST:
            raise ValidationError(
                "unknown checklist", code="validation_error", details={"checklist": checklist}
            )
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await session.execute(
                delete(OnboardingState).where(
                    OnboardingState.workspace_id == workspace_id,
                    OnboardingState.member_id == member_id,
                    OnboardingState.checklist == checklist,
                )
            )
            state, _ = await ensure_seeded(
                session, workspace_id=workspace_id, member_id=member_id, clock=self._clock
            )
            await reconcile_state(
                session, workspace_id=workspace_id, state=state, clock=self._clock
            )
            return await render_state(session, state)

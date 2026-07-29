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

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.constraints import violates
from mesh.db.models.member import Member
from mesh.db.models.onboarding import (
    ACTIVATION_CHECKLIST,
    ACTIVATION_STEP_KEYS,
    COMPLETED_VIA_AUTO,
    COMPLETED_VIA_MANUAL,
    STEP_CREATE_WORKSPACE,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_PENDING,
    STEP_STATUS_SKIPPED,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ValidationError
from mesh.onboarding.attribution import (  # noqa: F401 (façade re-export)
    evaluate_agent_reply_notification,
    execution_triggered_by_member,
    resolve_execution_trigger_member,
)
from mesh.onboarding.completion import (
    complete_step,
    load_steps,
    mark_aha,  # noqa: F401 (façade re-export)
    progress_snapshot,  # noqa: F401 (façade re-export)
)
from mesh.onboarding.reconcile import reconcile_state

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

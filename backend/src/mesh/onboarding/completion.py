"""完成守卫与进度聚合(onboarding.md §3.5)。

条件 UPDATE 守卫:`pending → completed` 单向、0 行即 no-op(at-least-once 重复
消费幂等);`aha_reached_at` 仅置一次;步骤完成后同事务经 outbox 唯一路径登记
`onboarding.progress` / `onboarding.completed`(README §6.6/§6.7)。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.onboarding import (
    ACTIVATION_STEP_KEYS,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_PENDING,
    STEP_STATUS_SKIPPED,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.onboarding.channels import onboarding_channel
from mesh.outbox.service import emit_realtime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


Clock = Callable[[], datetime]


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
    # Aggregate progress over the step rows (onboarding.md §3.2 progress object).
    steps = await load_steps(session, workspace_id=workspace_id, state_id=state_id)
    statuses = [step.status for step in steps.values()]
    return {
        "total": len(ACTIVATION_STEP_KEYS),
        "completed": statuses.count(STEP_STATUS_COMPLETED),
        "skipped": statuses.count(STEP_STATUS_SKIPPED),
    }



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



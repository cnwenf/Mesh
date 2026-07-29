"""Onboarding model + migration constraint tests (onboarding.md §2, README §6.2).

Covers the schema invariants the service layer relies on: the per member ×
workspace × checklist uniqueness (idempotent seed basis), the composite FK to
members (cross-tenant rejection at INSERT), the step consistency CHECK and
enum guards, and cascade behavior.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from mesh.db.models.onboarding import (
    ACTIVATION_CHECKLIST,
    ACTIVATION_STEP_KEYS,
    STEP_STATUS_COMPLETED,
    OnboardingState,
    OnboardingStateStep,
)

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def workspace_and_member(workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    return workspace, member


async def _seed_state(session, workspace, member) -> OnboardingState:
    state = OnboardingState(workspace_id=workspace.id, member_id=member.id)
    session.add(state)
    await session.flush()
    return state


async def test_state_defaults(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
    assert state.checklist == ACTIVATION_CHECKLIST
    assert state.aha_reached_at is None
    assert state.dismissed_at is None
    assert state.created_at is not None
    assert state.updated_at is not None


async def test_one_record_per_member_workspace_checklist(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        await _seed_state(db_session, workspace, member)
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await _seed_state(db_session, workspace, member)


async def test_cross_tenant_member_fk_rejected(
    db_session, workspace_factory, member_factory
):
    """README §6.2: a member_id from ANOTHER workspace is rejected at INSERT."""
    workspace_a = await workspace_factory()
    workspace_b = await workspace_factory()
    foreign_member = await member_factory(workspace_b)
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingState(workspace_id=workspace_a.id, member_id=foreign_member.id)
            )
            await db_session.flush()


async def test_unknown_member_fk_rejected(db_session, workspace_and_member):
    workspace, _ = workspace_and_member
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingState(workspace_id=workspace.id, member_id=uuid.uuid4())
            )
            await db_session.flush()


async def test_step_defaults_and_consistency(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
        step = OnboardingStateStep(
            workspace_id=workspace.id, state_id=state.id, step_key=ACTIVATION_STEP_KEYS[0]
        )
        db_session.add(step)
        await db_session.flush()
    assert step.status == "pending"
    assert step.completed_via is None
    assert step.completed_at is None
    assert step.evidence == {}


async def test_completed_requires_completed_at(db_session, workspace_and_member):
    """(status='completed') = (completed_at IS NOT NULL) — §2.3 CHECK."""
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingStateStep(
                    workspace_id=workspace.id,
                    state_id=state.id,
                    step_key=ACTIVATION_STEP_KEYS[1],
                    status=STEP_STATUS_COMPLETED,
                    completed_via="auto",
                    # completed_at intentionally missing
                )
            )
            await db_session.flush()


async def test_step_key_enum_guard(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingStateStep(
                    workspace_id=workspace.id, state_id=state.id, step_key="tour_step_x"
                )
            )
            await db_session.flush()


async def test_completed_via_enum_guard(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingStateStep(
                    workspace_id=workspace.id,
                    state_id=state.id,
                    step_key=ACTIVATION_STEP_KEYS[2],
                    completed_via="ui_click",
                )
            )
            await db_session.flush()


async def test_one_row_per_step_per_state(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
        db_session.add(
            OnboardingStateStep(
                workspace_id=workspace.id, state_id=state.id, step_key=ACTIVATION_STEP_KEYS[0]
            )
        )
        await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingStateStep(
                    workspace_id=workspace.id,
                    state_id=state.id,
                    step_key=ACTIVATION_STEP_KEYS[0],
                )
            )
            await db_session.flush()


async def test_state_delete_cascades_steps(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    async with db_session.begin():
        state = await _seed_state(db_session, workspace, member)
        for key in ACTIVATION_STEP_KEYS:
            db_session.add(
                OnboardingStateStep(workspace_id=workspace.id, state_id=state.id, step_key=key)
            )
        await db_session.flush()
        state_id = state.id
    async with db_session.begin():
        await db_session.execute(
            delete(OnboardingState).where(OnboardingState.id == state_id)
        )
    remaining = (
        await db_session.execute(
            select(OnboardingStateStep).where(OnboardingStateStep.state_id == state_id)
        )
    ).scalars().all()
    assert remaining == []


async def test_checklist_length_guard(db_session, workspace_and_member):
    workspace, member = workspace_and_member
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            db_session.add(
                OnboardingState(
                    workspace_id=workspace.id, member_id=member.id, checklist="x" * 41
                )
            )
            await db_session.flush()

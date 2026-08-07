"""Atomic claim tests (runtime.md §2.5 — R1 authoritative version).

T20 (no-match rollback, zero capacity leak), server-side label/capability
matching, default-runtime affinity, and the full claim response assembly
(attempt #1, per-attempt working branch, one-shot credential envelopes,
NEW-M1 env-name rejection).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from mesh.db.models.agent import Agent
from mesh.db.models.runtime import ExecutionAttempt, Runtime, TaskExecution
from mesh.errors import BusinessRuleError
from mesh.runtime.claim import claim_execution
from mesh.runtime.credentials import encrypt_credential_value
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
)

pytestmark = pytest.mark.unit

LEASE_SECONDS = 120


async def _claim(session_factory, runtime):
    return await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=LEASE_SECONDS,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )


async def _load(session_factory, runtime_id) -> Runtime:
    async with session_factory() as session:
        return await session.get(Runtime, runtime_id)


async def test_t20_capacity_available_but_no_match_rolls_back(session_factory):
    """R2 hard constraint: capacity + zero matching tasks → NOTHING changes."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory,
        world["ws_id"],
        status="online",
        labels={"region": "intranet"},
        capabilities=["python"],
        max_concurrent=4,
        current_load=1,
    )
    # Task requires a capability the runtime does not have.
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        capabilities=["gpu"],
    )

    result = await _claim(session_factory, runtime)

    assert result is None
    fresh = await _load(session_factory, runtime.id)
    assert fresh.current_load == 1  # untouched — no leak
    async with session_factory() as session:
        attempts = (
            await session.execute(select(ExecutionAttempt))
        ).scalars().all()
        executions = (await session.execute(select(TaskExecution))).scalars().all()
    assert attempts == []
    assert executions[0].status == "queued"  # still claimable later


async def test_claim_picks_matching_task_and_builds_attempt(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory,
        world["ws_id"],
        labels={"region": "intranet", "gpu": "true"},
        capabilities=["python", "version_control"],
        max_concurrent=2,
    )
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        labels={"region": "intranet"},
        capabilities=["python"],
    )

    result = await _claim(session_factory, runtime)

    assert result is not None
    assert result.execution["id"] == str(execution.id)
    assert result.execution["status"] == "claimed"
    assert result.attempt["attempt_number"] == 1
    assert result.attempt["lease_seq"] == 1
    assert result.attempt["working_branch"] == f"agent/{execution.id}/a1"
    fresh = await _load(session_factory, runtime.id)
    assert fresh.current_load == 1
    async with session_factory() as session:
        stored = (
            await session.execute(select(TaskExecution).where(TaskExecution.id == execution.id))
        ).scalar_one()
    assert stored.status == "claimed"


async def test_claim_freezes_provider_audit_before_terminal_result(session_factory):
    """Awaiting-approval attempts remain auditable before a result exists."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory,
        world["ws_id"],
        provider_manifest={
            "provider": "claude-code",
            "version": "2.1.218",
            "model": "runtime-default",
        },
    )
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={"provider": "claude-code", "model": "frozen-model"},
    )

    result = await _claim(session_factory, runtime)

    assert result is not None
    async with session_factory() as session:
        attempt = (await session.execute(select(ExecutionAttempt))).scalar_one()
    assert attempt.provider == "claude-code"
    assert attempt.provider_version == "2.1.218"
    assert attempt.model == "frozen-model"
    assert attempt.result is None


async def test_claim_label_containment_required(session_factory):
    """label_requirements must be contained in the runtime's labels."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], labels={"region": "intranet"}
    )
    await make_execution(
        session_factory, world["ws_id"], world["agent_id"], labels={"gpu": "true"}
    )
    assert await _claim(session_factory, runtime) is None


async def test_claim_capability_containment_required(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], capabilities=["python"]
    )
    await make_execution(
        session_factory, world["ws_id"], world["agent_id"], capabilities=["ffmpeg"]
    )
    assert await _claim(session_factory, runtime) is None


async def test_claim_respects_default_runtime_affinity(session_factory):
    """Agent with default_runtime_id: only THAT runtime may claim (§2.5)."""
    world = await seed_world(session_factory)
    other = await make_runtime(session_factory, world["ws_id"])
    async with session_factory() as session, session.begin():
        from sqlalchemy import update

        from mesh.db.models.agent import Agent

        await session.execute(
            update(Agent)
            .where(Agent.id == world["agent_id"])
            .values(default_runtime_id=other.id)
        )
    await make_execution(session_factory, world["ws_id"], world["agent_id"])

    stranger = await make_runtime(session_factory, world["ws_id"])
    assert await _claim(session_factory, stranger) is None
    result = await _claim(session_factory, other)
    assert result is not None


async def test_claim_offline_or_full_runtime_rejected(session_factory):
    world = await seed_world(session_factory)
    await make_execution(session_factory, world["ws_id"], world["agent_id"])
    offline = await make_runtime(session_factory, world["ws_id"], status="unavailable")
    full = await make_runtime(
        session_factory, world["ws_id"], max_concurrent=1, current_load=1
    )
    assert await _claim(session_factory, offline) is None
    assert await _claim(session_factory, full) is None


async def test_claim_cross_workspace_never_matches(session_factory):
    """The workspace predicate comes from the runtime row (token-derived),
    so another tenant's queued tasks are structurally invisible."""
    world_a = await seed_world(session_factory)
    world_b = await seed_world(session_factory)
    runtime_b = await make_runtime(session_factory, world_b["ws_id"])
    await make_execution(session_factory, world_a["ws_id"], world_a["agent_id"])
    assert await _claim(session_factory, runtime_b) is None


async def test_claim_delivers_one_shot_credential_envelopes(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    from mesh.db.models.runtime import RuntimeCredential

    cred = RuntimeCredential(
        workspace_id=world["ws_id"],
        name="REPO_TOKEN",
        kind="repo_token",
        encrypted_value=encrypt_credential_value("rot-secret-123", TEST_JWT_SECRET),
        env_name="REPO_TOKEN",
    )
    async with session_factory() as session, session.begin():
        session.add(cred)
        await session.flush()
        cred_id = cred.id
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        task_spec={"credential_ids": [str(cred_id)], "env_declarations": ["REPO_TOKEN"]},
    )

    result = await _claim(session_factory, runtime)

    assert result is not None
    delivered = result.attempt["credentials"]
    assert len(delivered) == 1
    assert delivered[0]["value"] == "rot-secret-123"  # plaintext ONLY here
    assert delivered[0]["env"] == "REPO_TOKEN"
    assert delivered[0]["envelope"].startswith("env-")
    from mesh.db.models.runtime import ExecutionCredential

    async with session_factory() as session:
        rows = (
            await session.execute(select(ExecutionCredential))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].revoked_at is None


async def test_claim_reserved_env_name_rejected_422(session_factory):
    """NEW-M1: env_declarations with loader-reserved names fail the claim."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        task_spec={"env_declarations": ["LD_PRELOAD"]},
    )
    with pytest.raises(BusinessRuleError) as exc:
        await _claim(session_factory, runtime)
    assert exc.value.code == "reserved_env_name"
    # Claim rolled back entirely: load untouched, task still queued.
    fresh = await _load(session_factory, runtime.id)
    assert fresh.current_load == 0


async def test_claim_priority_then_fifo_ordering(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], max_concurrent=2)
    low = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], priority=200
    )
    high = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], priority=50
    )

    first = await _claim(session_factory, runtime)
    assert first is not None
    assert first.execution["id"] == str(high.id)
    second = await _claim(session_factory, runtime)
    assert second is not None
    assert second.execution["id"] == str(low.id)
    assert second.attempt["attempt_number"] == 1  # different execution → #1


async def test_requeue_claims_attempt_number_two(session_factory):
    """After a reclaim, the next claim builds attempt #2 (audit chain, T4)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    # Seed a reclaimed first attempt (reaper's work, audited).
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO execution_attempts "
                "(workspace_id, execution_id, attempt_number, runtime_id, status, "
                " lease_seq, failure_reason, finished_at, claimed_at) "
                "VALUES (:ws, :e, 1, :r, 'reclaimed', 2, 'lease_expired', now(), now())"
            ),
            {"ws": world["ws_id"], "e": execution.id, "r": runtime.id},
        )

    result = await _claim(session_factory, runtime)

    assert result is not None
    assert result.attempt["attempt_number"] == 2
    assert result.attempt["working_branch"] == f"agent/{execution.id}/a2"


async def _set_agent_lifecycle(session_factory, agent_id, *, lifecycle=None, deleted=False):
    async with session_factory() as session, session.begin():
        agent = await session.get(Agent, agent_id)
        assert agent is not None
        if lifecycle is not None:
            agent.lifecycle_status = lifecycle
        if deleted:
            from datetime import UTC, datetime

            agent.deleted_at = datetime.now(UTC)


@pytest.mark.parametrize("lifecycle", ["paused", "disabled", "archived"])
async def test_claim_skips_non_active_agent_execution(session_factory, lifecycle):
    """HIGH-3: a queued execution whose agent is paused/disabled/archived is
    never dispatched; the claim returns None and leaves capacity untouched."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    await _set_agent_lifecycle(session_factory, world["agent_id"], lifecycle=lifecycle)

    result = await _claim(session_factory, runtime)

    assert result is None
    fresh = await _load(session_factory, runtime.id)
    assert fresh.current_load == 0
    async with session_factory() as session:
        stored = (
            await session.execute(select(TaskExecution).where(TaskExecution.id == execution.id))
        ).scalar_one()
    assert stored.status == "queued"  # not dispatched, stays queued


async def test_claim_skips_soft_deleted_agent_execution(session_factory):
    """HIGH-3: a soft-deleted agent's queued executions are never dispatched."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    await _set_agent_lifecycle(session_factory, world["agent_id"], deleted=True)

    result = await _claim(session_factory, runtime)

    assert result is None
    async with session_factory() as session:
        stored = (
            await session.execute(select(TaskExecution).where(TaskExecution.id == execution.id))
        ).scalar_one()
    assert stored.status == "queued"


async def test_claim_active_agent_execution_still_dispatched(session_factory):
    """HIGH-3 control: an active, non-deleted agent's execution claims fine."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])

    result = await _claim(session_factory, runtime)

    assert result is not None
    assert result.execution["id"] == str(execution.id)
    assert result.execution["status"] == "claimed"

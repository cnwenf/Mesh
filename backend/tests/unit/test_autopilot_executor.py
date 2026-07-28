"""autopilot.executor — dispatch pipeline + execution reconciler (§4.4 / §6.4)."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.autopilot.executor import (
    ActionError,
    _assert_public_target,
    _enqueue_idempotency_key,
    backoff_seconds,
    dispatch_run,
    reconcile_run,
)
from mesh.db.models.autopilot import AutopilotRun, AutopilotRunAttempt
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.runtime_support import seed_world


def _services(session_factory) -> dict:
    from mesh.comment_inbox.service import CommentService
    from mesh.issue.service import IssueService

    return {
        "session_factory": session_factory,
        "comment_service": CommentService(session_factory, signing_secret="x" * 40),
        "issue_service": IssueService(session_factory),
    }


APPROVAL_TTL = timedelta(hours=24)


def test_backoff_strategies() -> None:
    class _Rule:
        retry_backoff = "exponential"
        retry_base_seconds = 30
        retry_max_seconds = 1800

    rng = random.Random(42)
    fixed_rng = random.Random(42)
    d0 = backoff_seconds(_Rule(), 0, rng=rng)
    d3 = backoff_seconds(_Rule(), 3, rng=random.Random(42))
    assert 15 <= d0 <= 45  # 30 × jitter[0.5,1.5]
    assert d3 <= 1800 * 1.5
    _Rule.retry_backoff = "fixed"
    assert 15 <= backoff_seconds(_Rule(), 5, rng=fixed_rng) <= 45
    _Rule.retry_backoff = "linear"
    lin = backoff_seconds(_Rule(), 2, rng=random.Random(1))  # 30*3=90 base
    assert 45 <= lin <= 135
    # cap applies
    _Rule.retry_backoff = "exponential"
    capped = backoff_seconds(_Rule(), 20, rng=random.Random(7))
    assert capped <= 1800 * 1.5 + 1


def test_ssrf_guard_blocks_private_ranges() -> None:
    with pytest.raises(ActionError) as excinfo:
        _assert_public_target("127.0.0.1")
    assert excinfo.value.code == "private_address_forbidden"
    with pytest.raises(ActionError):
        _assert_public_target("169.254.169.254")  # cloud metadata
    with pytest.raises(ActionError):
        _assert_public_target("10.0.0.5")
    with pytest.raises(ActionError):
        _assert_public_target("unresolvable.invalid.mesh.example")
    # allowlist bypass
    _assert_public_target("127.0.0.1", allowlist=["127.0.0.1"])


async def test_dispatch_approval_gate_parks_run(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        require_approval=True,
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "waiting_approval"
    # no attempt consumed while parked
    async with session_factory() as session:
        attempts = (await session.execute(select(AutopilotRunAttempt))).scalars().all()
    assert attempts == []


async def test_dispatch_runs_notification_pipeline_to_success(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        action_config=[{"type": "send_notification", "message": "run {{run.id}} done"}],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        attempts = (await session.execute(select(AutopilotRunAttempt))).scalars().all()
        fanouts = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "succeeded"
    assert len(attempts) == 1 and attempts[0].status == "succeeded"
    types = [f.payload["type"] for f in fanouts]
    assert "autopilot_notice" in types  # action step
    assert "execution_finished" in types  # success matrix row


async def test_dispatch_agent_prompt_enqueues_and_waits(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {
                "type": "run_agent_prompt",
                "executor_agent_id": str(world["agent_id"]),
                "prompt": "diagnose {{trigger.issue.title}}",
            }
        ],
    )
    run = await make_run(
        session_factory, rule, status="pending",
        trigger_snapshot={"issue": {"id": str(uuid.uuid4()), "title": "登录报错"}},
    )
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        enqueues = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            )
            .scalars()
            .all()
        )
    # run stays running, waiting for the execution to finish
    assert row.status == "running"
    assert len(enqueues) == 1
    payload = enqueues[0].payload
    assert payload["trigger"] == "autopilot"
    assert payload["agent_id"] == str(world["agent_id"])
    assert payload["task_spec"]["kind"] == "autopilot_prompt"
    # §6.15 untrusted notice prepended to the prompt
    assert "UNTRUSTED_DATA" in payload["task_spec"]["prompt"]
    # §6.5 key shape — stable per (agent, issue, run:attempt)
    expected_key = _enqueue_idempotency_key(
        agent_id=world["agent_id"],
        issue_id=uuid.UUID(run.trigger_snapshot["issue"]["id"]),
        run=run,
        attempt_number=1,
    )
    assert payload["idempotency_key"] == expected_key

    # re-dispatching the same running run is a no-op (status guard)
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        count = len(
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            )
            .scalars()
            .all()
        )
    assert count == 1


async def _seed_execution(session_factory, world, run, *, status: str, attempt_number: int = 1):
    """Materialize a task_execution the reconciler can observe."""
    key = _enqueue_idempotency_key(
        agent_id=world["agent_id"], issue_id=None, run=run, attempt_number=attempt_number
    )
    async with session_factory() as session, session.begin():
        execution = TaskExecution(
            workspace_id=world["ws_id"],
            agent_id=world["agent_id"],
            trigger="autopilot",
            status=status,
            idempotency_key=key,
            task_spec={"kind": "autopilot_prompt"},
            label_requirements={},
            required_capabilities=[],
            config_snapshot={},
        )
        session.add(execution)
    return execution


async def test_reconcile_completed_execution_succeeds_run(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"},
            {"type": "send_notification", "message": "done: {{steps.0.output}}"},
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    execution = await _seed_execution(session_factory, world, run, status="completed")
    async with session_factory() as session, session.begin():
        ex = await session.scalar(select(TaskExecution).where(TaskExecution.id == execution.id))
        ex.result = {"output": "诊断完成", "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        attempts = (await session.execute(select(AutopilotRunAttempt))).scalars().all()
    assert row.status == "succeeded"
    assert row.execution_id == execution.id
    assert row.prompt_tokens == 100 and row.completion_tokens == 50
    assert attempts[0].execution_id == execution.id


async def test_reconcile_failed_execution_retries_then_fails(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"], max_retries=1, retry_base_seconds=1,
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"},
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    await _seed_execution(session_factory, world, run, status="failed")
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "retrying"
    assert row.retry_count == 1
    assert "retry_at" in row.error

    # backoff not yet elapsed → dispatch is a no-op
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "retrying"

    # force the deadline into the past → attempt #2 enqueued
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        error = dict(row.error)
        error["retry_at"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        row.error = error
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        attempts = (await session.execute(select(AutopilotRunAttempt))).scalars().all()
        enqueues = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "running"
    assert {a.attempt_number for a in attempts} == {1, 2}
    assert len(enqueues) == 2  # NEW execution for the retry attempt (§4.4)

    # second attempt fails → max_retries exhausted → failed + alert
    await _seed_execution(session_factory, world, run, status="failed", attempt_number=2)
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        fanouts = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "failed"
    assert any(f.payload.get("execution_status") == "failed" for f in fanouts)


async def test_reconcile_non_retryable_failure(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"},
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    execution = await _seed_execution(session_factory, world, run, status="cancelled")
    async with session_factory() as session, session.begin():
        ex = await session.scalar(select(TaskExecution).where(TaskExecution.id == execution.id))
        ex.failure_reason = "approval_rejected"
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "cancelled"


async def test_dispatch_missing_executor_fails_run(session_factory) -> None:
    world = await seed_world(session_factory)
    # rule with a run_agent_prompt action but executor agent DELETED after creation
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"},
        ],
    )
    async with session_factory() as session, session.begin():
        from mesh.db.models.agent import Agent

        agent = await session.scalar(select(Agent).where(Agent.id == world["agent_id"]))
        agent.lifecycle_status = "disabled"
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "agent_unavailable"


async def test_dispatch_deleted_rule_cancels_run(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session, session.begin():
        from mesh.db.models.autopilot import Autopilot

        row = await session.scalar(select(Autopilot).where(Autopilot.id == rule.id))
        row.deleted_at = datetime.now(UTC)
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "cancelled"
    assert row.error["code"] == "rule_deleted"


async def test_dispatch_skips_paused_rule(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], status="paused"
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=APPROVAL_TTL,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "pending"  # untouched

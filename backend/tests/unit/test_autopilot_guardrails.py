"""autopilot.guardrails — the default-ON trigger gate (autopilot.md §5.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mesh.autopilot.guardrails import (
    DEFAULT_GUARDRAILS,
    evaluate_trigger,
    merge_guardrails,
)
from mesh.db.models.autopilot import AutopilotRun
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.runtime_support import seed_world


def test_merge_guardrails_defaults_are_on() -> None:
    merged = merge_guardrails(None)
    assert merged == DEFAULT_GUARDRAILS
    assert merged["cascade_max_depth"] == 3
    assert merged["agent_loop_detection"] is True


def test_merge_guardrails_overrides_and_unknown_keys() -> None:
    merged = merge_guardrails({"cascade_max_depth": 5, "bogus_key": 1, "rate_limit_overflow": "queue"})
    assert merged["cascade_max_depth"] == 5
    assert merged["rate_limit_overflow"] == "queue"
    assert "bogus_key" not in merged
    # invalid overflow falls back to drop
    assert merge_guardrails({"rate_limit_overflow": "explode"})["rate_limit_overflow"] == "drop"


async def test_gate_allows_clean_trigger(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    async with session_factory() as session:
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is True


async def test_gate_kill_switch_denies(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        guardrails={"kill_switch_paused": True},
    )
    async with session_factory() as session:
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is False
    assert decision.reason == "kill_switch"


async def test_gate_dedup_window(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    await make_run(
        session_factory,
        rule,
        status="succeeded",
        trigger_snapshot={"dedup_key": "evt-1"},
    )
    async with session_factory() as session:
        hit = await evaluate_trigger(session, rule=rule, dedup_key="evt-1")
        miss = await evaluate_trigger(session, rule=rule, dedup_key="evt-2")
    assert hit.allowed is False and hit.reason == "deduplicated"
    assert miss.allowed is True


async def test_gate_dedup_window_expired(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        guardrails={"dedup_window_seconds": 60},
    )
    old = datetime.now(UTC) - timedelta(seconds=120)
    await make_run(
        session_factory, rule, status="succeeded", trigger_snapshot={"dedup_key": "evt-1"}, created_at=old
    )
    async with session_factory() as session:
        decision = await evaluate_trigger(session, rule=rule, dedup_key="evt-1")
    assert decision.allowed is True


async def test_gate_rate_limit_drop(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=2,
        rate_limit_window_seconds=3600,
        concurrency_limit=10,
    )
    for _ in range(2):
        await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is False
    assert decision.reason == "rate_limited"
    assert decision.http_status == 429


async def test_gate_rate_limit_alert_only_allows(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1,
        concurrency_limit=10,
        guardrails={"rate_limit_overflow": "alert_only"},
    )
    await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is True


async def test_gate_rate_limit_queue_allows(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1,
        concurrency_limit=10,
        guardrails={"rate_limit_overflow": "queue"},
    )
    await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is True


async def test_gate_concurrency_limit(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        concurrency_limit=1,
        rate_limit_max=100,
    )
    await make_run(session_factory, rule, status="running")
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is False
    assert decision.reason == "concurrency_limited"
    # terminal runs do NOT occupy a slot
    rule2 = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        concurrency_limit=1,
        rate_limit_max=100,
    )
    await make_run(session_factory, rule2, status="succeeded")
    async with session_factory() as session, session.begin():
        decision2 = await evaluate_trigger(session, rule=rule2)
    assert decision2.allowed is True


async def test_gate_agent_loop_detection(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        guardrails={"agent_loop_detection": True, "agent_loop_window_seconds": 60},
    )
    await make_run(
        session_factory, rule, status="succeeded", trigger_snapshot={"loop_target": "issue-1"}
    )
    async with session_factory() as session:
        hit = await evaluate_trigger(session, rule=rule, trigger_target_ref="issue-1")
        miss = await evaluate_trigger(session, rule=rule, trigger_target_ref="issue-2")
    assert hit.allowed is False and hit.reason == "agent_loop_detected"
    assert miss.allowed is True


async def test_gate_loop_detection_disabled(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        guardrails={"agent_loop_detection": False},
    )
    await make_run(
        session_factory, rule, status="succeeded", trigger_snapshot={"loop_target": "issue-1"}
    )
    async with session_factory() as session:
        decision = await evaluate_trigger(session, rule=rule, trigger_target_ref="issue-1")
    assert decision.allowed is True


async def test_gate_cascade_depth(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        guardrails={"cascade_max_depth": 3},
    )
    async with session_factory() as session:
        denied = await evaluate_trigger(session, rule=rule, cascade_depth=4)
        allowed = await evaluate_trigger(session, rule=rule, cascade_depth=3)
    assert denied.allowed is False and denied.reason == "cascade_depth_exceeded"
    assert denied.http_status == 422
    assert allowed.allowed is True


async def test_gate_daily_run_budget(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1000,
        rate_limit_window_seconds=1,
        concurrency_limit=100,
        guardrails={"daily_run_budget": 2, "dedup_window_seconds": 0},
    )
    for _ in range(2):
        await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is False
    assert decision.reason == "daily_run_budget"
    assert decision.alert is True


async def test_gate_daily_token_budget(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1000,
        concurrency_limit=100,
        guardrails={"daily_run_budget": 0, "daily_token_budget": 1000, "dedup_window_seconds": 0},
    )
    run = await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        run_row = await session.get(AutopilotRun, run.id)
        run_row.prompt_tokens = 900
        run_row.completion_tokens = 200
    async with session_factory() as session, session.begin():
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is False
    assert decision.reason == "daily_token_budget"


async def test_gate_test_runs_excluded_from_counts(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1,
        concurrency_limit=10,
    )
    await make_run(session_factory, rule, status="succeeded", is_test=True)
    async with session_factory() as session:
        decision = await evaluate_trigger(session, rule=rule)
    assert decision.allowed is True


async def test_rate_limited_emits_realtime_and_notification(session_factory) -> None:
    from sqlalchemy import select

    from mesh.db.models.outbox import OutboxEvent

    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        rate_limit_max=1,
        concurrency_limit=10,
    )
    await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        await evaluate_trigger(session, rule=rule)
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.workspace_id == world["ws_id"])
                )
            )
            .scalars()
            .all()
        )
    types = {event.event_type for event in events}
    assert "realtime.publish" in types  # autopilot.rate_limited
    assert "notification.fanout" in types  # critical circuit alert
    fanout = next(e for e in events if e.event_type == "notification.fanout")
    assert fanout.payload["type"] == "autopilot_alert"

"""autopilot.scheduler — atomic claim + misfire policy (§4.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mesh.autopilot.scheduler import _fire_schedule_rule
from mesh.db.models.autopilot import Autopilot, AutopilotRun
from tests.unit.autopilot_support import make_rule
from tests.unit.runtime_support import seed_world

NOW = datetime(2026, 7, 27, 9, 30, tzinfo=UTC)


async def _rule(session_factory, rule_id) -> Autopilot:
    async with session_factory() as session:
        return await session.scalar(select(Autopilot).where(Autopilot.id == rule_id))


async def _runs(session_factory) -> list[AutopilotRun]:
    async with session_factory() as session:
        return list((await session.execute(select(AutopilotRun))).scalars().all())


async def test_fire_creates_run_and_advances_next_run_at(session_factory) -> None:
    world = await seed_world(session_factory)
    due = NOW - timedelta(minutes=1)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], next_run_at=due,
    )
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    assert created == 1
    runs = await _runs(session_factory)
    assert len(runs) == 1
    assert runs[0].trigger_snapshot["scheduled_for"] == due.isoformat()
    row = await _rule(session_factory, rule.id)
    assert row.next_run_at is not None and row.next_run_at > NOW


async def test_atomic_claim_second_replica_fires_nothing(session_factory) -> None:
    world = await seed_world(session_factory)
    due = NOW - timedelta(minutes=1)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], next_run_at=due,
    )
    first = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    # a second replica still holding the OLD expected_next loses the race
    second = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    assert first == 1
    assert second == 0
    assert len(await _runs(session_factory)) == 1


async def test_misfire_skip_drops_late_slot(session_factory) -> None:
    world = await seed_world(session_factory)
    very_late = NOW - timedelta(hours=2)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], next_run_at=very_late,
        trigger_config={"cron": "0 9 * * *", "timezone": "UTC", "misfire_policy": "skip"},
    )
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=very_late, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    assert created == 0
    assert len(await _runs(session_factory)) == 0
    row = await _rule(session_factory, rule.id)
    assert row.next_run_at > NOW  # still advanced


async def test_misfire_run_all_catches_up_capped(session_factory) -> None:
    world = await seed_world(session_factory)
    three_hours_back = NOW - timedelta(hours=3, minutes=30)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        next_run_at=three_hours_back,
        trigger_config={"cron": "0 * * * *", "timezone": "UTC", "misfire_policy": "run_all"},
        rate_limit_max=100, concurrency_limit=100,
        guardrails={"dedup_window_seconds": 0, "daily_run_budget": 0, "daily_token_budget": 0},
    )
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=three_hours_back, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    # slots in (t-3.5h, now]: 3 hourly slots + the original due slot = 4
    assert created == 4
    assert len(await _runs(session_factory)) == 4


async def test_misfire_run_all_cap_truncates(session_factory) -> None:
    world = await seed_world(session_factory)
    way_back = NOW - timedelta(hours=10)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        next_run_at=way_back,
        trigger_config={"cron": "0 * * * *", "timezone": "UTC", "misfire_policy": "run_all"},
        rate_limit_max=1000, concurrency_limit=1000,
        guardrails={"dedup_window_seconds": 0, "daily_run_budget": 0, "daily_token_budget": 0},
    )
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=way_back, grace_seconds=300, run_all_cap=3, now=NOW,
    )
    assert created == 4  # original slot + cap(3) missed slots


async def test_one_time_schedule_archives(session_factory) -> None:
    world = await seed_world(session_factory)
    due = NOW - timedelta(minutes=5)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], next_run_at=due,
        trigger_config={
            "cron": "0 9 * * *", "timezone": "UTC", "misfire_policy": "run_once",
            "one_time_at": due.isoformat(),
        },
    )
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    assert created == 1
    row = await _rule(session_factory, rule.id)
    assert row.status == "archived"
    assert row.next_run_at is None


async def test_skips_inactive_or_changed_rules(session_factory) -> None:
    world = await seed_world(session_factory)
    due = NOW - timedelta(minutes=1)
    paused = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        next_run_at=due, status="paused",
    )
    assert await _fire_schedule_rule(
        session_factory, rule_id=paused.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    ) == 0
    # stale expected_next (row already advanced elsewhere) → no fire
    active = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        next_run_at=NOW + timedelta(hours=1),
    )
    assert await _fire_schedule_rule(
        session_factory, rule_id=active.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    ) == 0
    assert len(await _runs(session_factory)) == 0


async def test_guardrail_gate_drops_over_limit(session_factory) -> None:
    world = await seed_world(session_factory)
    due = NOW - timedelta(minutes=1)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        next_run_at=due, rate_limit_max=1,
    )
    # one existing run in the window → the scheduled fire is dropped
    async with session_factory() as session, session.begin():
        session.add(AutopilotRun(
            workspace_id=world["ws_id"], autopilot_id=rule.id,
            trigger_type="schedule", trigger_snapshot={"dedup_key": "x"},
            status="succeeded", created_at=NOW - timedelta(minutes=10),
            updated_at=NOW,
        ))
    created = await _fire_schedule_rule(
        session_factory, rule_id=rule.id, workspace_id=world["ws_id"],
        expected_next=due, grace_seconds=300, run_all_cap=50, now=NOW,
    )
    assert created == 0
    # next_run_at still advanced
    row = await _rule(session_factory, rule.id)
    assert row.next_run_at > NOW

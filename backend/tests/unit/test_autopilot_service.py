"""autopilot.service — console API service layer (autopilot.md §3.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.autopilot.service import AutopilotService
from mesh.db.models.autopilot import Autopilot, AutopilotRun
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from tests.unit.autopilot_support import TEST_SIGNING_SECRET, make_rule, make_run, make_secret
from tests.unit.runtime_support import seed_world


@pytest.fixture
def service(session_factory) -> AutopilotService:
    return AutopilotService(session_factory, TEST_SIGNING_SECRET)


async def _member(session_factory, world, role: str = "admin") -> Member:
    async with session_factory() as session:
        return await session.scalar(select(Member).where(Member.id == world["member_id"]))


def _schedule_payload(**overrides) -> dict:
    payload = {
        "name": "每日站会前汇总进展",
        "trigger_type": "schedule",
        "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "misfire_policy": "run_once"},
        "action_config": [
            {"type": "run_agent_prompt", "prompt": "汇总进展"},
        ],
        "executor_agent_id": None,
    }
    payload.update(overrides)
    return payload


async def test_create_schedule_rule_computes_next_run_at(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    payload = _schedule_payload()
    payload["action_config"] = [{"type": "send_notification", "message": "done"}]
    data = await service.create_rule(workspace_id=world["ws_id"], creator=member, payload=payload)
    assert data["status"] == "active"
    assert data["next_run_at"] is not None
    # guardrails defaults are ON even when unspecified
    assert data["guardrails"]["cascade_max_depth"] == 3
    assert data["guardrails"]["agent_loop_detection"] is True
    parsed = datetime.fromisoformat(data["next_run_at"])
    assert parsed > datetime.now(UTC)


async def test_create_validates_cron_and_timezone(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    with pytest.raises(ValidationError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(trigger_config={"cron": "bad cron", "timezone": "UTC"}),
        )
    assert excinfo.value.code == "invalid_cron"
    with pytest.raises(ValidationError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(
                trigger_config={"cron": "0 9 * * *", "timezone": "Mars/Olympus"}
            ),
        )
    assert excinfo.value.code == "invalid_trigger_config"
    with pytest.raises(ValidationError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(trigger_config={"cron": "0 9 * * *"}),  # no timezone
        )
    assert excinfo.value.code == "invalid_trigger_config"
    with pytest.raises(ValidationError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(
                trigger_config={"cron": "0 9 * * *", "timezone": "UTC", "misfire_policy": "bogus"}
            ),
        )
    assert excinfo.value.code == "invalid_trigger_config"


async def test_create_requires_executor_for_prompt_action(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(executor_agent_id=None),  # run_agent_prompt w/o executor
        )
    assert excinfo.value.code == "executor_required"
    # unknown agent → agent_unavailable
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(executor_agent_id=str(uuid.uuid4())),
        )
    assert excinfo.value.code == "agent_unavailable"
    # real agent passes
    data = await service.create_rule(
        workspace_id=world["ws_id"],
        creator=member,
        payload=_schedule_payload(executor_agent_id=str(world["agent_id"])),
    )
    assert data["executor_agent_id"] == str(world["agent_id"])


async def test_create_rejects_unknown_action_and_empty_actions(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    with pytest.raises(BusinessRuleError):
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(action_config=[{"type": "teleport"}]),
        )
    with pytest.raises(BusinessRuleError):
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(action_config=[]),
        )
    with pytest.raises(BusinessRuleError):
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload=_schedule_payload(action_config=[{"type": "http_request"}]),  # no url
        )


async def test_create_duplicate_name_conflict(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    payload = _schedule_payload(action_config=[{"type": "send_notification", "message": "x"}])
    await service.create_rule(workspace_id=world["ws_id"], creator=member, payload=payload)
    with pytest.raises(ConflictError):
        await service.create_rule(workspace_id=world["ws_id"], creator=member, payload=payload)


async def test_list_filter_and_stats(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], name="alpha",
        trigger_type="issue_created",
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], name="beta",
        trigger_type="comment_created", status="paused",
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    await make_run(session_factory, rule, status="succeeded")
    await make_run(session_factory, rule, status="failed")

    listing = await service.list_rules(workspace_id=world["ws_id"])
    assert len(listing["data"]) == 2
    by_trigger = await service.list_rules(workspace_id=world["ws_id"], trigger_type="issue_created")
    assert [r["name"] for r in by_trigger["data"]] == ["alpha"]
    by_status = await service.list_rules(workspace_id=world["ws_id"], status="paused")
    assert [r["name"] for r in by_status["data"]] == ["beta"]
    searched = await service.list_rules(workspace_id=world["ws_id"], search="alph")
    assert len(searched["data"]) == 1
    stats = by_trigger["data"][0]["stats"]
    assert stats["runs_30d"] == 2
    assert stats["success_rate"] == 0.5


async def test_pagination_cursor(session_factory, service) -> None:
    world = await seed_world(session_factory)
    for i in range(5):
        await make_rule(
            session_factory, world["ws_id"], created_by=world["member_id"], name=f"r{i}",
            action_config=[{"type": "send_notification", "message": "x"}],
        )
    page1 = await service.list_rules(workspace_id=world["ws_id"], limit=2)
    assert len(page1["data"]) == 2
    assert page1["next_cursor"]
    page2 = await service.list_rules(workspace_id=world["ws_id"], limit=2, cursor=page1["next_cursor"])
    assert len(page2["data"]) == 2
    assert {r["id"] for r in page1["data"]}.isdisjoint({r["id"] for r in page2["data"]})
    page3 = await service.list_rules(workspace_id=world["ws_id"], limit=2, cursor=page2["next_cursor"])
    assert len(page3["data"]) == 1
    assert page3["next_cursor"] is None


async def test_update_rule_recomputes_schedule_and_conflict(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], name="orig",
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    updated = await service.update_rule(
        workspace_id=world["ws_id"],
        rule_id=rule.id,
        patch={
            "name": "renamed",
            "trigger_config": {"cron": "30 8 * * *", "timezone": "UTC", "misfire_policy": "skip"},
            "guardrails": {"cascade_max_depth": 1},
            "max_retries": 5,
        },
    )
    assert updated["name"] == "renamed"
    assert updated["guardrails"]["cascade_max_depth"] == 1
    assert updated["guardrails"]["dedup_window_seconds"] == 300  # defaults merged
    assert updated["max_retries"] == 5
    assert updated["next_run_at"] is not None
    # duplicate rename conflict
    other = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], name="taken",
    )
    with pytest.raises(ConflictError):
        await service.update_rule(
            workspace_id=world["ws_id"], rule_id=other.id, patch={"name": "renamed"}
        )


async def test_pause_resume_lifecycle(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    paused = await service.pause_rule(workspace_id=world["ws_id"], rule_id=rule.id)
    assert paused["status"] == "paused"
    assert paused["next_run_at"] is None
    with pytest.raises(ConflictError):
        await service.pause_rule(workspace_id=world["ws_id"], rule_id=rule.id)  # already paused
    resumed = await service.resume_rule(workspace_id=world["ws_id"], rule_id=rule.id)
    assert resumed["status"] == "active"
    assert resumed["next_run_at"] is not None
    with pytest.raises(ConflictError):
        await service.resume_rule(workspace_id=world["ws_id"], rule_id=rule.id)  # already active


async def test_delete_rule_soft(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    await service.delete_rule(workspace_id=world["ws_id"], rule_id=rule.id)
    with pytest.raises(NotFoundError):
        await service.get_rule(workspace_id=world["ws_id"], rule_id=rule.id)
    # the row still exists (soft delete)
    async with session_factory() as session:
        row = await session.scalar(select(Autopilot).where(Autopilot.id == rule.id))
    assert row is not None and row.deleted_at is not None and row.status == "archived"


async def test_preview_schedule(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    preview = await service.preview_schedule(workspace_id=world["ws_id"], rule_id=rule.id, count=3)
    assert len(preview["next_runs"]) == 3
    assert preview["timezone"] == "Asia/Shanghai"
    event_rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], trigger_type="issue_created",
    )
    with pytest.raises(BusinessRuleError):
        await service.preview_schedule(workspace_id=world["ws_id"], rule_id=event_rule.id)


async def test_test_run_dry_run_and_real(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="issue_created",
        filter_config={"labels": ["bug"]},
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    status, dry = await service.test_run(
        workspace_id=world["ws_id"],
        rule_id=rule.id,
        actor=member,
        simulate_payload={"issue": {"labels": ["bug"], "title": "t"}},
        dry_run=True,
    )
    assert status == 200
    assert dry["would_run"] is True
    assert dry["matched_filters"] == {"labels": ["bug"]}
    status2, dry2 = await service.test_run(
        workspace_id=world["ws_id"],
        rule_id=rule.id,
        actor=member,
        simulate_payload={"issue": {"labels": ["feature"]}},
        dry_run=True,
    )
    assert dry2["would_run"] is False
    # real test run creates an is_test run
    status3, started = await service.test_run(
        workspace_id=world["ws_id"],
        rule_id=rule.id,
        actor=member,
        simulate_payload={"issue": {"id": str(uuid.uuid4()), "labels": ["bug"]}},
    )
    assert status3 == 202
    assert started["is_test"] is True
    async with session_factory() as session:
        run = await session.scalar(
            select(AutopilotRun).where(AutopilotRun.id == uuid.UUID(started["run_id"]))
        )
    assert run.is_test is True
    assert run.triggered_by == world["member_id"]


async def test_test_run_webhook_rule_uses_skipped_signature(session_factory, service) -> None:
    from mesh.db.models.autopilot import WebhookEvent

    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    secret_row, _token, _secret = await make_secret(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="webhook_received",
        trigger_config={"secret_id": str(secret_row.id)},
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    status, started = await service.test_run(
        workspace_id=world["ws_id"], rule_id=rule.id, actor=member,
        simulate_payload={"payload": {"k": "v"}},
    )
    assert status == 202
    async with session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.signature_status == "skipped"


async def test_runs_listing_detail_cancel_artifacts(session_factory, service) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="running")
    await make_run(session_factory, rule, status="succeeded")

    listing = await service.list_runs(workspace_id=world["ws_id"], rule_id=rule.id)
    assert len(listing["data"]) == 2
    only_running = await service.list_runs(
        workspace_id=world["ws_id"], rule_id=rule.id, status="running"
    )
    assert len(only_running["data"]) == 1

    detail = await service.get_run(workspace_id=world["ws_id"], run_id=run.id)
    assert detail["attempts"] == []
    assert detail["artifacts"] == []
    artifacts = await service.list_run_artifacts(workspace_id=world["ws_id"], run_id=run.id)
    assert artifacts["data"] == []

    cancelled = await service.cancel_run(workspace_id=world["ws_id"], run_id=run.id)
    assert cancelled["status"] == "cancelled"
    with pytest.raises(ConflictError):
        await service.cancel_run(workspace_id=world["ws_id"], run_id=run.id)


async def test_runs_cross_workspace_isolation(session_factory, service) -> None:
    world_a = await seed_world(session_factory)
    world_b = await seed_world(session_factory)
    rule_a = await make_rule(session_factory, world_a["ws_id"], created_by=world_a["member_id"])
    run_a = await make_run(session_factory, rule_a, status="running")
    # workspace B credentials see neither the rule nor the run
    with pytest.raises(NotFoundError):
        await service.get_rule(workspace_id=world_b["ws_id"], rule_id=rule_a.id)
    with pytest.raises(NotFoundError):
        await service.get_run(workspace_id=world_b["ws_id"], run_id=run_a.id)
    # the runs listing 404s on the foreign rule before returning anything
    with pytest.raises(NotFoundError):
        await service.list_runs(workspace_id=world_b["ws_id"], rule_id=rule_a.id)


async def test_kill_switch_pauses_and_restores(session_factory, service) -> None:
    world = await seed_world(session_factory)
    active = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    paused = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], status="paused"
    )
    result = await service.kill_switch(workspace_id=world["ws_id"], enabled=True, reason="stop")
    assert result["kill_switch"] is True
    assert result["paused_autopilots"] == 1
    async with session_factory() as session:
        active_row = await session.scalar(select(Autopilot).where(Autopilot.id == active.id))
        paused_row = await session.scalar(select(Autopilot).where(Autopilot.id == paused.id))
    assert active_row.status == "paused"
    assert active_row.guardrails["kill_switch_paused"] is True
    assert paused_row.status == "paused"
    assert paused_row.guardrails["kill_switch_paused"] is False  # manually paused stays as-is

    restored = await service.kill_switch(workspace_id=world["ws_id"], enabled=False, reason="go")
    assert restored["paused_autopilots"] == 1
    async with session_factory() as session:
        active_row = await session.scalar(select(Autopilot).where(Autopilot.id == active.id))
        paused_row = await session.scalar(select(Autopilot).where(Autopilot.id == paused.id))
    assert active_row.status == "active"  # restored by kill switch
    assert active_row.guardrails["kill_switch_paused"] is False
    assert paused_row.status == "paused"  # manually paused NOT force-restored


async def test_webhook_secret_endpoints(session_factory, service) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    created = await service.create_webhook_secret(
        workspace_id=world["ws_id"], member=member, label="prod"
    )
    assert "secret" in created
    rotated = await service.rotate_webhook_secret(
        workspace_id=world["ws_id"], secret_id=uuid.UUID(created["id"]), member=member
    )
    assert rotated["token"] != created["token"]
    assert rotated["id"] == created["id"]  # in-place rotation keeps the row
    listing = await service.list_webhook_secrets(workspace_id=world["ws_id"])
    assert len(listing["data"]) == 1
    for row in listing["data"]:
        assert "secret" not in row and "token" not in row


async def test_get_missing_rule_404(session_factory, service) -> None:
    world = await seed_world(session_factory)
    with pytest.raises(NotFoundError):
        await service.get_rule(workspace_id=world["ws_id"], rule_id=uuid.uuid4())
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    with pytest.raises(NotFoundError):
        await service.get_run(workspace_id=world["ws_id"], run_id=uuid.uuid4())
    del rule

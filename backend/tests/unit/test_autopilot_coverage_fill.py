"""Branch/loop coverage fill for autopilot: channels, worker loops, actions."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

import mesh.autopilot.executor as executor_mod
from mesh.autopilot import approvals as approvals_mod
from mesh.autopilot import matcher as matcher_mod
from mesh.autopilot import scheduler as scheduler_mod
from mesh.autopilot.channels import make_autopilot_channel_checker
from mesh.autopilot.executor import ActionError, _perform_http_request
from mesh.db.models.autopilot import AutopilotArtifact, AutopilotRun
from mesh.db.models.issue import Issue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import Approval
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


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------


async def test_channel_checker_membership(session_factory) -> None:
    world = await seed_world(session_factory)
    other = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])

    class _Principal:
        def __init__(self, workspace_ids):
            self.workspace_ids = workspace_ids

    check = make_autopilot_channel_checker(session_factory)
    assert await check(_Principal([world["ws_id"]]), f"autopilot:{rule.id}") is True
    assert await check(_Principal([other["ws_id"]]), f"autopilot:{rule.id}") is False
    assert await check(_Principal([world["ws_id"]]), "autopilot:not-a-uuid") is False
    assert await check(_Principal([world["ws_id"]]), "autopilot:") is False


# ---------------------------------------------------------------------------
# worker loops (supervised bodies)
# ---------------------------------------------------------------------------


async def test_executor_loop_dispatches_and_stops(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        action_config=[{"type": "send_notification", "message": "x"}],
    )
    run = await make_run(session_factory, rule, status="pending")
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.3)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            executor_mod.autopilot_executor_loop(
                session_factory,
                services=_services(session_factory),
                interval=0.1,
                stop=stop,
            ),
            _stop_soon(),
        ),
        timeout=15,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "succeeded"


async def test_scheduler_loop_fires_due_rule(session_factory) -> None:
    world = await seed_world(session_factory)
    due = datetime.now(UTC) - timedelta(seconds=5)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"], next_run_at=due
    )
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.3)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            scheduler_mod.autopilot_scheduler_loop(
                session_factory, interval=0.1, stop=stop
            ),
            _stop_soon(),
        ),
        timeout=15,
    )
    async with session_factory() as session:
        runs = (await session.execute(select(AutopilotRun))).scalars().all()
    assert len(runs) == 1


async def test_executor_loop_survives_errors(session_factory, monkeypatch) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    await make_run(session_factory, rule, status="pending")

    async def _boom(*args, **kwargs):
        raise RuntimeError("claim exploded")

    monkeypatch.setattr(executor_mod, "_claim_runs", _boom)
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.25)
        stop.set()

    # must not raise — the loop logs and retries until stop
    await asyncio.wait_for(
        asyncio.gather(
            executor_mod.autopilot_executor_loop(
                session_factory, services=_services(session_factory), interval=0.1, stop=stop
            ),
            _stop_soon(),
        ),
        timeout=15,
    )


# ---------------------------------------------------------------------------
# outbound http action (mocked transport)
# ---------------------------------------------------------------------------


async def test_perform_http_request_success_and_5xx(monkeypatch) -> None:
    calls: dict = {}

    real_client = httpx.AsyncClient

    def _client_factory(**kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            calls["url"] = str(request.url)
            calls["idem"] = request.headers.get("idempotency-key")
            if "fail" in str(request.url):
                return httpx.Response(503, text="unavailable")
            return httpx.Response(200, text='{"ok": true}')

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(executor_mod.httpx, "AsyncClient", _client_factory)
    # allowlisted host bypasses the SSRF resolution
    result = await _perform_http_request(
        {"url": "https://127.0.0.1:9999/hook", "host_allowlist": ["127.0.0.1"]}, "key-1"
    )
    assert result["status_code"] == 200
    assert calls["idem"] == "key-1"
    # 5xx is surfaced to the caller (the action step maps it to a retryable
    # ActionError); the transport call itself returns the upstream status.
    failure = await _perform_http_request(
        {"url": "https://127.0.0.1:9999/fail", "host_allowlist": ["127.0.0.1"]}, "key-2"
    )
    assert failure["status_code"] == 503
    # http scheme refused
    with pytest.raises(ActionError):
        await _perform_http_request({"url": "http://example.com/x"}, "key-3")


async def test_http_action_step_end_to_end(session_factory, monkeypatch) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        require_approval=False,
        guardrails={"approval_required_actions": []},  # no gate for the test
        action_config=[
            {"type": "http_request", "url": "https://127.0.0.1:9/hook", "host_allowlist": ["127.0.0.1"]}
        ],
    )
    run = await make_run(session_factory, rule, status="pending")

    real_client = httpx.AsyncClient

    def _client_factory(**kwargs):
        return real_client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
        )

    monkeypatch.setattr(executor_mod.httpx, "AsyncClient", _client_factory)
    from mesh.autopilot.executor import dispatch_run

    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        artifacts = (
            (
                await session.execute(
                    select(AutopilotArtifact).where(
                        AutopilotArtifact.artifact_type == "http_response"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "succeeded"
    assert artifacts and artifacts[0].summary == "HTTP 200"


# ---------------------------------------------------------------------------
# create_issue action (real IssueService)
# ---------------------------------------------------------------------------


async def test_create_issue_action(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        guardrails={"approval_required_actions": []},
        action_config=[
            {"type": "create_issue", "title": "自动建单 {{trigger.event_id}}", "description": "详情"}
        ],
    )
    run = await make_run(
        session_factory, rule, status="pending", trigger_snapshot={"event_id": "evt-x"}
    )
    from mesh.autopilot.executor import dispatch_run

    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        issues = (await session.execute(select(Issue))).scalars().all()
        artifacts = (
            (
                await session.execute(
                    select(AutopilotArtifact).where(
                        AutopilotArtifact.artifact_type == "issue"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "succeeded"
    assert len(issues) == 1
    assert "evt-x" in issues[0].title
    assert artifacts and artifacts[0].ref_table == "issues"


# ---------------------------------------------------------------------------
# add_comment action (real CommentService)
# ---------------------------------------------------------------------------


async def test_add_comment_action(session_factory) -> None:
    from mesh.db.models.issue import IssueStatus

    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        status = IssueStatus(workspace_id=world["ws_id"], name="Todo", category="todo")
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=world["ws_id"],
            identifier_namespace_key="AP",
            number=7,
            identifier="AP-7",
            title="登录报错",
            status_id=status.id,
            state_category="todo",
        )
        session.add(issue)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "add_comment", "content": "诊断结论 {{trigger.issue.title}}"}],
    )
    run = await make_run(
        session_factory, rule, status="pending",
        trigger_snapshot={"issue": {"id": str(issue.id), "title": "登录报错"}},
    )
    from mesh.autopilot.executor import dispatch_run

    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        artifacts = (
            (
                await session.execute(
                    select(AutopilotArtifact).where(
                        AutopilotArtifact.artifact_type == "comment"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "succeeded"
    assert artifacts and artifacts[0].ref_table == "comments"


async def test_add_comment_without_issue_fails(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "add_comment", "content": "x"}],
    )
    run = await make_run(session_factory, rule, status="pending", trigger_snapshot={})
    from mesh.autopilot.executor import dispatch_run

    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"


# ---------------------------------------------------------------------------
# matcher wrapper + approval expiry edges
# ---------------------------------------------------------------------------


async def test_realtime_publish_wrapper_chains_projector(session_factory) -> None:
    world = await seed_world(session_factory)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_created",
    )
    seen = []

    async def fake_projector(session, event):
        seen.append(event.id)
        return [("chan", {"op": "frame"})]

    handler = await matcher_mod.realtime_publish_with_autopilot(fake_projector)
    event = OutboxEvent(
        workspace_id=world["ws_id"],
        event_type="realtime.publish",
        payload={
            "channel": f"workspace:{world['ws_id']}:issues",
            "event": "issue.created",
            "data": {"id": str(uuid.uuid4())},
        },
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        frames = await handler(session, event)
    assert frames == [("chan", {"op": "frame"})]
    assert seen == [event.id]
    async with session_factory() as session:
        runs = (await session.execute(select(AutopilotRun))).scalars().all()
    assert len(runs) == 1  # matcher ran after the projector


async def test_realtime_publish_wrapper_swallows_matcher_errors(session_factory) -> None:
    world = await seed_world(session_factory)
    event = OutboxEvent(
        workspace_id=world["ws_id"],
        event_type="realtime.publish",
        payload={"channel": "workspace:{ws}:issues", "event": "issue.created", "data": {}},
    )

    async def fake_projector(session, ev):
        return []

    handler = await matcher_mod.realtime_publish_with_autopilot(fake_projector)
    # no session.begin() here → matcher DB writes would fail; the wrapper
    # must not propagate (projection must survive).
    async with session_factory() as session:
        frames = await handler(session, event)
    assert frames == []


async def test_expire_approval_without_run_or_waiting(session_factory) -> None:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        # run already terminal → expire is a no-op on the run
        rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
        run = await make_run(session_factory, rule, status="succeeded")
        approval = Approval(
            workspace_id=world["ws_id"],
            subject_type="autopilot_action",
            subject_run_id=run.id,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC),
        )
        session.add(approval)
        await session.flush()
        await approvals_mod.expire_run_approval(session, approval=approval)
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "succeeded"


async def test_apply_decision_missing_run(session_factory) -> None:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        # craft an approval pointing at a run that will never exist is
        # impossible (FK), so test subject_run_id=None path directly
        approval = Approval(
            workspace_id=world["ws_id"],
            subject_type="tool_call",  # subject_run_id unused for tool_call
            subject_run_id=None,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC),
        )
        # tool_call requires subject_execution_id — skip persistence and call
        # the helper with an unsaved instance carrying subject_run_id=None
        result = await approvals_mod.apply_approval_decision(
            session, approval=approval, approve=True
        )
    assert result is None


async def test_find_pending_run_approval_none(session_factory) -> None:
    world = await seed_world(session_factory)
    async with session_factory() as session:
        found = await approvals_mod.find_pending_run_approval(
            session, workspace_id=world["ws_id"], run_id=uuid.uuid4()
        )
    assert found is None

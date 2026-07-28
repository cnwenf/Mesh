"""Branch coverage for autopilot.executor edge paths (fakes where safe)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

import mesh.autopilot.executor as executor_mod
from mesh.autopilot.executor import (
    ActionError,
    _int_or_none,
    _load_step_outputs,
    _perform_http_request,
    _sum_tokens,
    _trigger_issue_id,
    dispatch_run,
    reconcile_run,
)
from mesh.db.models.agent import Agent
from mesh.db.models.autopilot import AutopilotArtifact, AutopilotRun, AutopilotRunAttempt
from mesh.db.models.runtime import TaskExecution
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.runtime_support import seed_world


def _services(session_factory, *, comment_service=None, issue_service=None) -> dict:
    from mesh.comment_inbox.service import CommentService
    from mesh.issue.service import IssueService

    return {
        "session_factory": session_factory,
        "comment_service": comment_service or CommentService(session_factory, signing_secret="x" * 40),
        "issue_service": issue_service or IssueService(session_factory),
    }


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


async def test_load_step_outputs_plain_text_fallback(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="running")
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        session.add(AutopilotArtifact(
            workspace_id=world["ws_id"], run_id=run.id, artifact_type="agent_output",
            ref_table="task_executions", ref_id=uuid.uuid4(), summary="plain text, not json",
            created_at=now,
        ))
        session.add(AutopilotArtifact(
            workspace_id=world["ws_id"], run_id=run.id, artifact_type="agent_output",
            ref_table="task_executions", ref_id=uuid.uuid4(), summary=json.dumps({"output": "ok"}),
            created_at=now + timedelta(seconds=1),
        ))
        # unrelated artifact type ignored
        session.add(AutopilotArtifact(
            workspace_id=world["ws_id"], run_id=run.id, artifact_type="comment",
            ref_table="comments", ref_id=uuid.uuid4(), summary="noise",
            created_at=now + timedelta(seconds=2),
        ))
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        steps = await _load_step_outputs(session, row)
    assert steps == [{"output": "plain text, not json"}, {"output": "ok"}]


def test_trigger_issue_id_invalid() -> None:
    assert _trigger_issue_id({"issue": {"id": "not-a-uuid"}}) is None
    assert _trigger_issue_id({}) is None
    valid = uuid.uuid4()
    assert _trigger_issue_id({"issue": {"id": str(valid)}}) == valid


def test_int_or_none_and_sum_tokens() -> None:
    assert _int_or_none("5") == 5
    assert _int_or_none("bogus") is None
    assert _int_or_none(None) is None
    assert _int_or_none(-3) is None
    assert _sum_tokens(10, None) == 10
    assert _sum_tokens(None, None) is None
    assert _sum_tokens(None, 7) == 7
    assert _sum_tokens(3, 7) == 10


# ---------------------------------------------------------------------------
# outbound http error mapping
# ---------------------------------------------------------------------------


async def test_perform_http_timeout_and_network_errors(monkeypatch) -> None:
    real_client = httpx.AsyncClient

    def _timeout_factory(**kwargs):
        def handler(request):
            raise httpx.ConnectTimeout("timed out")

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(executor_mod.httpx, "AsyncClient", _timeout_factory)
    with pytest.raises(ActionError) as excinfo:
        await _perform_http_request(
            {"url": "https://h.example/x", "host_allowlist": ["h.example"]}, "k"
        )
    assert excinfo.value.code == "timeout" and excinfo.value.retryable is True

    def _network_factory(**kwargs):
        def handler(request):
            raise httpx.ConnectError("refused")

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(executor_mod.httpx, "AsyncClient", _network_factory)
    with pytest.raises(ActionError) as excinfo:
        await _perform_http_request(
            {"url": "https://h.example/x", "host_allowlist": ["h.example"]}, "k"
        )
    assert excinfo.value.code == "transient" and excinfo.value.retryable is True


# ---------------------------------------------------------------------------
# executor resolution edge cases
# ---------------------------------------------------------------------------


async def test_dispatch_malformed_executor_id(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": "not-a-uuid", "prompt": "p"}
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "executor_required"


async def test_dispatch_agent_without_roster_member(session_factory) -> None:
    world = await seed_world(session_factory)
    # a second agent with NO roster member row
    async with session_factory() as session, session.begin():
        lonely = Agent(
            workspace_id=world["ws_id"], name="lonely",
            owner_user_id=world["user_id"], lifecycle_status="active",
        )
        session.add(lonely)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=lonely.id,
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(lonely.id), "prompt": "p"}
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "agent_unavailable"


# ---------------------------------------------------------------------------
# add_comment action error mapping (fake comment service)
# ---------------------------------------------------------------------------


class _FakeCommentService:
    def __init__(self, exc: Exception | None = None, result: dict | None = None):
        self._exc = exc
        self._result = result or {"id": str(uuid.uuid4())}

    async def create_comment(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._result


async def test_add_comment_not_found_maps_invalid_request(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "add_comment", "content": "x"}],
    )
    run = await make_run(
        session_factory, rule, status="pending",
        trigger_snapshot={"issue": {"id": str(uuid.uuid4())}},
    )
    services = _services(
        session_factory, comment_service=_FakeCommentService(Exception("issue not found"))
    )
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=services, approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "invalid_request"
    assert row.error["retryable"] is False


async def test_add_comment_transient_maps_retryable(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"], max_retries=0,
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "add_comment", "content": "x"}],
    )
    run = await make_run(
        session_factory, rule, status="pending",
        trigger_snapshot={"issue": {"id": str(uuid.uuid4())}},
    )
    services = _services(
        session_factory, comment_service=_FakeCommentService(Exception("connection reset"))
    )
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=services, approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "transient"


# ---------------------------------------------------------------------------
# create_issue action branches (fake issue service)
# ---------------------------------------------------------------------------


class _FakeIssueService:
    def __init__(self, exc: Exception | None = None):
        self._exc = exc
        self.last_body = None

    async def create_issue(self, *, actor, workspace_id, body, **kwargs):
        self.last_body = body
        if self._exc is not None:
            raise self._exc
        return {"id": str(uuid.uuid4())}


async def test_create_issue_optional_fields_and_fallback_id(session_factory) -> None:
    world = await seed_world(session_factory)
    fake = _FakeIssueService()
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        guardrails={"approval_required_actions": []},
        action_config=[
            {
                "type": "create_issue",
                "title": "t",
                "description": "d",
                "project_id": str(uuid.uuid4()),
                "priority": "high",
            }
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory, issue_service=fake),
        approval_ttl=timedelta(hours=1),
    )
    assert fake.last_body.description == "d"
    assert fake.last_body.priority == "high"
    assert fake.last_body.project_id is not None
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "succeeded"


async def test_create_issue_failure_retryable(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        max_retries=0,
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "create_issue", "title": "t"}],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory, issue_service=_FakeIssueService(Exception("db down"))),
        approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "transient"


async def test_create_issue_missing_creator(session_factory) -> None:
    # The composite FK (workspace_id, created_by) → members makes a missing
    # creator unreachable through the ORM; simulate the defensive branch by
    # calling the step with a rule whose created_by points nowhere, outside
    # any persistence path.
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        guardrails={"approval_required_actions": []},
        action_config=[{"type": "create_issue", "title": "t"}],
    )
    run = await make_run(session_factory, rule, status="pending")
    rule.created_by = uuid.uuid4()  # in-memory only; never flushed
    async with session_factory() as session:
        with pytest.raises(ActionError) as excinfo:
            await executor_mod._step_create_issue(
                session,
                run=run,
                rule=rule,
                action={"type": "create_issue", "title": "t"},
                steps=[],
                snapshot={},
                step_index=0,
                attempt_number=1,
                services=_services(session_factory),
            )
    assert excinfo.value.code == "invalid_request"


# ---------------------------------------------------------------------------
# http step 5xx → retryable failure through dispatch
# ---------------------------------------------------------------------------


async def test_http_step_5xx_retries(session_factory, monkeypatch) -> None:
    world = await seed_world(session_factory)
    real_client = httpx.AsyncClient

    def _factory(**kwargs):
        return real_client(
            transport=httpx.MockTransport(lambda req: httpx.Response(502, text="bad gateway"))
        )

    monkeypatch.setattr(executor_mod.httpx, "AsyncClient", _factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        max_retries=1, retry_base_seconds=1,
        guardrails={"approval_required_actions": []},
        action_config=[
            {"type": "http_request", "url": "https://127.0.0.1:1/h", "host_allowlist": ["127.0.0.1"]}
        ],
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "retrying"
    assert row.error["code"] == "transient"


# ---------------------------------------------------------------------------
# reconcile edge paths
# ---------------------------------------------------------------------------


async def _dispatch_prompt_run(session_factory, world, **rule_overrides):
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"}
        ],
        **rule_overrides,
    )
    run = await make_run(session_factory, rule, status="pending")
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    return rule, run


async def _seed_execution(session_factory, world, run, *, status, failure_reason=None, result=None):
    key = executor_mod._enqueue_idempotency_key(
        agent_id=world["agent_id"], issue_id=None, run=run, attempt_number=1
    )
    async with session_factory() as session, session.begin():
        execution = TaskExecution(
            workspace_id=world["ws_id"], agent_id=world["agent_id"], trigger="autopilot",
            status=status, idempotency_key=key, failure_reason=failure_reason,
            task_spec={}, label_requirements={}, required_capabilities=[], config_snapshot={},
            result=result,
        )
        session.add(execution)
    return execution


async def test_reconcile_waiting_for_queued_execution(session_factory) -> None:
    world = await seed_world(session_factory)
    _rule, run = await _dispatch_prompt_run(session_factory, world)
    await _seed_execution(session_factory, world, run, status="queued")
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "running"  # still waiting


async def test_reconcile_timeout_retryable(session_factory) -> None:
    world = await seed_world(session_factory)
    _rule, run = await _dispatch_prompt_run(session_factory, world, retry_base_seconds=1)
    await _seed_execution(session_factory, world, run, status="timeout", failure_reason="timeout")
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "retrying"
    assert row.error["code"] == "execution_failed_retryable"


async def test_reconcile_failed_non_retryable_reason(session_factory) -> None:
    world = await seed_world(session_factory)
    _rule, run = await _dispatch_prompt_run(session_factory, world)
    await _seed_execution(
        session_factory, world, run, status="failed", failure_reason="max_retries"
    )
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "execution_failed"


async def test_reconcile_no_attempt_resumes_approved_run(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    # running run with NO attempts (approved-resume shape) → reconcile starts
    # the pipeline: the send_notification action completes the run.
    run = await make_run(session_factory, rule, status="running")
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    # non-running run → no-op
    done = await make_run(session_factory, rule, status="succeeded")
    await reconcile_run(
        session_factory, run_id=done.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        row2 = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == done.id))
        attempts = (
            (
                await session.execute(
                    select(AutopilotRunAttempt).where(AutopilotRunAttempt.run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "succeeded"
    assert [a.attempt_number for a in attempts] == [1]
    assert row2.status == "succeeded"


async def test_reconcile_pipeline_error_after_completion(session_factory) -> None:
    """execution completes but the NEXT action step fails → failure handling."""
    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"], max_retries=0,
        action_config=[
            {"type": "run_agent_prompt", "executor_agent_id": str(world["agent_id"]), "prompt": "p"},
            {"type": "add_comment", "content": "x"},  # no issue in snapshot → invalid_request
        ],
    )
    run = await make_run(session_factory, rule, status="pending", trigger_snapshot={})
    await dispatch_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory), approval_ttl=timedelta(hours=1),
    )
    await _seed_execution(session_factory, world, run, status="completed")
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "failed"
    assert row.error["code"] == "invalid_request"


async def test_reconcile_missing_executor_resolves_none(session_factory) -> None:
    """agent deleted/disabled between dispatch and reconcile → no link, waits."""
    world = await seed_world(session_factory)
    _rule, run = await _dispatch_prompt_run(session_factory, world)
    # disable the agent AFTER the enqueue happened
    async with session_factory() as session, session.begin():
        agent = await session.scalar(select(Agent).where(Agent.id == world["agent_id"]))
        agent.lifecycle_status = "disabled"
    await reconcile_run(
        session_factory, run_id=run.id, workspace_id=world["ws_id"],
        services=_services(session_factory),
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "running"  # cannot resolve executor → nothing to observe


# ---------------------------------------------------------------------------
# executor loop reconcile pass
# ---------------------------------------------------------------------------


async def test_executor_loop_reconciles_running_runs(session_factory) -> None:
    world = await seed_world(session_factory)
    _rule, run = await _dispatch_prompt_run(session_factory, world)
    await _seed_execution(session_factory, world, run, status="completed")
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.6)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            executor_mod.autopilot_executor_loop(
                session_factory, services=_services(session_factory), interval=0.1, stop=stop
            ),
            _stop_soon(),
        ),
        timeout=20,
    )
    async with session_factory() as session:
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
    assert row.status == "succeeded"

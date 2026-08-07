"""Extra RuntimeService coverage: executions listing/detail rendering,
decommission, patch, filters, freeze/cancel edge paths."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.runtime import (
    ExecutionAttempt,
    RepoCheckout,
    Runtime,
    RuntimeCredential,
    TaskExecution,
)
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import NotFoundError
from mesh.runtime.approvals import decide_approval, request_tool_approval
from mesh.runtime.attempts import cancel_execution, freeze_execution, transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.credentials import encrypt_credential_value
from mesh.runtime.logs import read_execution_logs
from mesh.runtime.service import RuntimeService
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    issue_runtime_token,
    make_execution,
    make_runtime,
    make_settings,
    seed_world,
    valid_result_v1,
)

pytestmark = pytest.mark.unit


def _service(session_factory, **overrides) -> RuntimeService:
    return RuntimeService(session_factory, make_settings(**overrides))


async def _member(session_factory, member_id):
    from mesh.db.models.member import Member

    async with session_factory() as session:
        member = await session.get(Member, member_id)
        session.expunge(member)
    return member


async def test_patch_runtime_fields(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], name="old")
    patched = await service.patch_runtime(
        workspace_id=world["ws_id"],
        runtime_id=runtime.id,
        name="renamed",
        labels={"gpu": "true"},
        max_concurrent=8,
    )
    assert patched["name"] == "renamed"
    assert patched["labels"] == {"gpu": "true"}
    assert patched["max_concurrent"] == 8
    # Partial patch leaves other fields untouched.
    again = await service.patch_runtime(
        workspace_id=world["ws_id"],
        runtime_id=runtime.id,
        name=None,
        labels=None,
        max_concurrent=2,
    )
    assert again["name"] == "renamed"
    assert again["max_concurrent"] == 2


async def test_decommission_revokes_token_and_hides_runtime(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    _, _token_row = await issue_runtime_token(session_factory, runtime)
    await service.decommission_runtime(
        workspace_id=world["ws_id"], runtime_id=runtime.id
    )
    async with session_factory() as session:
        row = await session.get(Runtime, runtime.id)
    assert row.status == "decommissioned"
    assert row.deleted_at is not None
    # §2.4 S-11: token revoked by clearing the hash (no api_tokens row).
    assert row.runtime_token_hash is None
    with pytest.raises(NotFoundError):
        await service.get_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)


async def test_list_executions_filters_and_cursor(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    for _ in range(3):
        await make_execution(session_factory, world["ws_id"], world["agent_id"])
    other_agent_exec = await make_execution(session_factory, world["ws_id"], None)

    page = await service.list_executions(workspace_id=world["ws_id"], limit=2)
    assert len(page["data"]) == 2
    assert page["next_cursor"]
    rest = await service.list_executions(
        workspace_id=world["ws_id"], limit=2, cursor=page["next_cursor"]
    )
    assert len(rest["data"]) == 2
    assert rest["next_cursor"] is None

    by_agent = await service.list_executions(
        workspace_id=world["ws_id"], agent_id=world["agent_id"]
    )
    assert len(by_agent["data"]) == 3
    by_status = await service.list_executions(
        workspace_id=world["ws_id"], status="running"
    )
    assert by_status["data"] == []

    # runtime_id filter: executions touched by a given runtime's attempts.
    runtime = await make_runtime(session_factory, world["ws_id"])
    claimed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert claimed is not None
    by_runtime = await service.list_executions(
        workspace_id=world["ws_id"], runtime_id=runtime.id
    )
    assert [e["id"] for e in by_runtime["data"]] == [claimed.execution["id"]]
    assert by_runtime["data"][0]["attempts"][0]["id"] == claimed.attempt["id"]
    workspace_page = await service.list_executions(
        workspace_id=world["ws_id"], limit=10
    )
    assert all("attempts" in item for item in workspace_page["data"])
    assert other_agent_exec.agent_id is None  # sanity


async def test_runtime_operational_state_and_safe_diagnostics(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])

    await service.heartbeat(
        runtime=runtime,
        current_load=0,
        health="degraded",
        metrics={
            "inventory_hash": "sha256:" + "a" * 64,
            "token": "must-not-leak",
            "internal_path": "/srv/private/runtime",
            "thinking": "must-not-leak",
        },
        inflight=[],
        operational_state="degraded",
        diagnostics=[
            {
                "reason_code": "provider_unavailable",
                "missing_capabilities": ["python"],
                "affected_task_types": ["provider:primary"],
            }
        ],
    )
    detail = await service.get_runtime(
        workspace_id=world["ws_id"], runtime_id=runtime.id
    )
    assert detail["status"] == "unavailable"  # lifecycle stays separate
    assert detail["operational_state"] == "degraded"
    assert detail["diagnostics"] == [
        {
            "reason_code": "provider_unavailable",
            "missing_capabilities": ["python"],
            "affected_task_types": ["provider:primary"],
            "repair_command": "mesh-runtime doctor --config <config-file>",
        }
    ]
    rendered = str(detail)
    assert "must-not-leak" not in rendered
    assert "/srv/private/runtime" not in rendered
    assert detail["recent_heartbeats"][0]["metrics"] == {
        "inventory_hash": "sha256:" + "a" * 64,
        "inflight_reported": 0,
    }

    paused = await make_runtime(session_factory, world["ws_id"], status="paused")
    paused_detail = await service.get_runtime(
        workspace_id=world["ws_id"], runtime_id=paused.id
    )
    assert paused_detail["operational_state"] == "paused"
    assert paused_detail["diagnostics"] == []

    isolated = await make_runtime(session_factory, world["ws_id"])
    await service.heartbeat(
        runtime=isolated,
        current_load=0,
        health="degraded",
        metrics={},
        inflight=[],
        operational_state="isolated",
        diagnostics=[
            {
                "reason_code": "security_anomaly",
                "missing_capabilities": [],
                "affected_task_types": ["all"],
            }
        ],
    )
    isolated_detail = await service.get_runtime(
        workspace_id=world["ws_id"], runtime_id=isolated.id
    )
    assert isolated_detail["operational_state"] == "isolated"
    assert isolated_detail["diagnostics"][0]["reason_code"] == "security_anomaly"


async def test_get_execution_renders_attempts_credentials_checkout(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    cred = RuntimeCredential(
        workspace_id=world["ws_id"],
        name="DEPLOY_KEY",
        kind="ssh_key",
        encrypted_value=encrypt_credential_value("ssh-secret", TEST_JWT_SECRET),
    )
    async with session_factory() as session, session.begin():
        session.add(cred)
        await session.flush()
        cred_id = cred.id
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        task_spec={"credential_ids": [str(cred_id)]},
    )
    claimed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    attempt_id = uuid.UUID(claimed.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    # A checkout row for the attempt.
    async with session_factory() as session, session.begin():
        session.add(
            RepoCheckout(
                workspace_id=world["ws_id"],
                attempt_id=attempt_id,
                repo_url="https://code.example/t/a.git",
                base_ref="main",
                working_branch=claimed.attempt["working_branch"],
                status="ready",
            )
        )

    data = await service.get_execution(
        workspace_id=world["ws_id"], execution_id=execution.id
    )
    assert data["retry_count"] == 0
    assert data["attempts"][0]["status"] == "running"
    assert data["attempts"][0]["runtime_name"] == runtime.name
    # Credential values are NEVER rendered.
    assert data["credentials"][0]["value"] == "***"
    assert data["credentials"][0]["name"] == "DEPLOY_KEY"
    assert "ssh-secret" not in str(data)
    assert data["checkout"]["repo_url"] == "https://code.example/t/a.git"

    with pytest.raises(NotFoundError):
        await service.get_execution(
            workspace_id=world["ws_id"], execution_id=uuid.uuid4()
        )


async def test_execution_detail_renders_complete_safe_attempt_audit(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={
            "model": "frozen-model",
            "budget": {
                "max_cost_usd": "2.500000",
                "max_tokens": 10000,
                "max_turns": 25,
                "max_wall_time_seconds": 1800,
                "max_idle_time_seconds": 300,
                "secret_budget_note": "must-not-leak",
            },
        },
    )
    first = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert first is not None
    first_id = uuid.UUID(first.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=first_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    approval = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=first_id,
        lease_seq=1,
        action_summary={
            "action": "repo.write",
            "params": {
                "repository": "org/repo",
                "branch": "feature/audit",
                "token": "must-not-leak",
                "path": "/srv/private/worktree",
                "thinking": "must-not-leak",
            },
        },
        resume_context={"checkpoint_ref": "obj/private-checkpoint"},
        approval_ttl=timedelta(hours=24),
    )
    member = await _member(session_factory, world["member_id"])
    await decide_approval(
        session_factory,
        approval_id=uuid.UUID(approval["id"]),
        workspace_id=world["ws_id"],
        member=member,
        approve=True,
        comment="approved",
    )
    second = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert second is not None
    second_id = uuid.UUID(second.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=second_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    terminal_result = valid_result_v1(
        cost_usd="1.250000",
        input_tokens=100,
        cache_read_tokens=20,
        output_tokens=30,
        turns=4,
        session_id="provider-session-private",
        summary="redacted execution summary",
    )
    terminal_result["artifacts"] = {
        "checkout_id": "checkout-42",
        "diff_ref": "diff:42",
        "path": "/srv/private/worktree",
    }
    terminal_result["output"] = "must-not-leak"
    terminal_result["provider"]["credential"] = "must-not-leak"
    terminal_result["outcome"]["thinking"] = "must-not-leak"
    await transition_attempt(
        session_factory,
        attempt_id=second_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=terminal_result,
        signing_secret=TEST_JWT_SECRET,
    )

    data = await service.get_execution(
        workspace_id=world["ws_id"], execution_id=execution.id
    )
    assert data["retry_count"] == 1
    assert data["frozen_budget"] == {
        "max_cost_usd": "2.500000",
        "max_tokens": 10000,
        "max_turns": 25,
        "max_wall_time_seconds": 1800,
        "max_idle_time_seconds": 300,
    }
    assert [attempt["attempt_number"] for attempt in data["attempts"]] == [1, 2]
    completed = data["attempts"][1]
    assert completed["provider"] == "test-provider"
    assert completed["provider_version"] == "1.0.0"
    assert completed["model"] == "test-model"
    assert completed["actual_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "cache_tokens": 20,
        "total_tokens": 150,
        "cost_usd": "1.250000",
        "turns": 4,
    }
    expected_public_result = {
        "schema_version": 1,
        "provider": {
            "name": "test-provider",
            "version": "1.0.0",
            "model": "test-model",
            "session_recorded": True,
        },
        "usage": {
            "input_tokens": 100,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 20,
            "output_tokens": 30,
            "total_tokens": 150,
            "turns": 4,
            "cost_usd": "1.250000",
        },
        "outcome": {
            "exit_code": 0,
            "summary": "redacted execution summary",
            "termination": "completed",
        },
        "artifacts": {"checkout_id": "checkout-42", "diff_ref": "diff:42"},
        "redaction": {"rule_version": "test-rules", "hit_count": 0},
    }
    assert completed["result"] == expected_public_result
    assert data["result"] == expected_public_result
    assert completed["frozen_budget"] == data["frozen_budget"]
    assert [event["event"] for event in data["attempts"][0]["timeline"]] == [
        "claimed",
        "running",
        "approval_requested",
        "terminal",
        "approval_approved",
    ]
    assert data["attempts"][1]["timeline"][0] == {
        "event": "requeued",
        "at": data["approval_audits"][0]["decision"]["decided_at"],
        "reason_code": "awaiting_approval",
    }
    audit = data["approval_audits"][0]
    assert audit["request"] == {
        "action": "repo.write",
        "fields": {"repository": "org/repo", "branch": "feature/audit"},
    }
    assert audit["decision"]["status"] == "approved"
    assert audit["decision"]["decided_by_member_id"] == str(world["member_id"])
    assert audit["grant"] is None
    assert audit["result"] == {
        "attempt_id": str(second_id),
        "status": "completed",
        "termination": "completed",
    }
    rendered = str(data)
    assert "must-not-leak" not in rendered
    assert "/srv/private/worktree" not in rendered
    assert "provider-session-private" not in rendered
    assert "obj/private-checkpoint" not in rendered


async def test_execution_observability_filters_private_project_issues(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    viewer_user_id = uuid.uuid4()
    viewer_member_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws_id"])
        viewer_user = User(
            id=viewer_user_id,
            email=f"runtime-visibility-{viewer_user_id.hex[:8]}@example.com",
            display_name="Runtime viewer",
            password_hash="unused",
        )
        session.add(viewer_user)
        await session.flush()
        viewer = Member(
            id=viewer_member_id,
            workspace_id=world["ws_id"],
            member_type="human",
            user_id=viewer_user_id,
            role="member",
            status="active",
        )
        project = Project(
            workspace_id=world["ws_id"],
            name="Private runtime project",
            key=f"R{uuid.uuid4().hex[:5].upper()}",
            visibility="private",
        )
        session.add_all([viewer, project])
        await session.flush()
        status = IssueStatus(
            workspace_id=world["ws_id"],
            project_id=project.id,
            name="Todo",
            category="todo",
            is_default=True,
        )
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=world["ws_id"],
            project_id=project.id,
            identifier_namespace_key=project.key,
            number=1,
            identifier=f"{project.key}-1",
            title="Hidden runtime execution",
            status_id=status.id,
            state_category="todo",
            reporter_id=world["member_id"],
        )
        session.add(issue)
        await session.flush()
        issue_id = issue.id
        session.expunge(viewer)

    hidden = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], issue_id=issue_id
    )
    visible = await make_execution(session_factory, world["ws_id"], world["agent_id"])

    page = await service.list_executions(
        workspace_id=world["ws_id"], viewer=viewer, limit=20
    )
    assert [item["id"] for item in page["data"]] == [str(visible.id)]
    # LOW-S2 anti-oracle: execution detail/log/list-by-issue paths all reuse the
    # issue read gate (assert_member_can_view_issue), so an invisible private
    # project surfaces as 404 not_found for every member type — never 403,
    # matching GET /issues/{id} (issue.md §3.8, project.md §5.3).
    with pytest.raises(NotFoundError):
        await service.list_executions(
            workspace_id=world["ws_id"], viewer=viewer, issue_id=issue_id
        )
    with pytest.raises(NotFoundError):
        await service.get_execution(
            workspace_id=world["ws_id"], execution_id=hidden.id, viewer=viewer
        )
    with pytest.raises(NotFoundError):
        await read_execution_logs(
            session_factory,
            None,
            workspace_id=world["ws_id"],
            execution_id=hidden.id,
            viewer=viewer,
        )


async def test_freeze_without_envelopes_reports_zero(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    data = await freeze_execution(
        session_factory, workspace_id=world["ws_id"], execution_id=execution.id
    )
    assert data["revoked_envelopes"] == 0
    with pytest.raises(NotFoundError):
        await freeze_execution(
            session_factory, workspace_id=world["ws_id"], execution_id=uuid.uuid4()
        )


async def test_cancel_records_actor_and_404_foreign(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        member_id=world["member_id"],
    )
    assert data["status"] == "cancelled"
    assert data["cancel_requested_at"] is not None
    async with session_factory() as session:
        row = await session.get(ExecutionAttempt, uuid.uuid4())  # noqa: F841
        stored = await session.get(
            __import__(
                "mesh.db.models.runtime", fromlist=["TaskExecution"]
            ).TaskExecution,
            execution.id,
        )
    assert stored.cancel_requested_by == world["member_id"]
    with pytest.raises(NotFoundError):
        await cancel_execution(
            session_factory, workspace_id=world["ws_id"], execution_id=uuid.uuid4()
        )


async def test_heartbeat_on_deleted_runtime_401(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    await service.decommission_runtime(
        workspace_id=world["ws_id"], runtime_id=runtime.id
    )
    from mesh.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        await service.heartbeat(
            runtime=runtime, current_load=0, health="healthy", metrics={}, inflight=[]
        )


async def test_list_runtimes_kind_and_status_filters(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    await make_runtime(
        session_factory, world["ws_id"], kind="platform_managed", name="pm"
    )
    await make_runtime(session_factory, world["ws_id"], kind="self_hosted", name="sh")
    await make_runtime(
        session_factory,
        world["ws_id"],
        kind="self_hosted",
        name="off",
        status="unavailable",
    )
    pm = await service.list_runtimes(
        workspace_id=world["ws_id"], kind="platform_managed"
    )
    assert [r["name"] for r in pm["data"]] == ["pm"]
    online = await service.list_runtimes(workspace_id=world["ws_id"], status="online")
    assert {r["name"] for r in online["data"]} == {"pm", "sh"}


# ---------------------------------------------------------------------------
# Coverage completion: defensive branches + worker loop + approval cancel
# ---------------------------------------------------------------------------


async def test_enqueue_defensive_normalization_branches(session_factory):
    """Malformed producer payloads are normalized, not crashed on."""
    from mesh.db.models.outbox import OutboxEvent
    from mesh.runtime.enqueue import (
        _is_idempotency_conflict,
        _parse_uuid,
        enqueue_execution_handler,
    )

    world = await seed_world(session_factory)
    event = OutboxEvent(
        workspace_id=world["ws_id"],
        event_type="execution.enqueue",
        payload={
            "intent": "enqueue",
            "agent_id": None,
            "trigger": "not-a-trigger",  # → default assign
            "label_requirements": "oops",  # not a dict → {}
            "required_capabilities": {"not": "a list"},  # → []
            "config_snapshot": ["not a dict"],  # → {}
            "task_spec": "not a dict",  # → {}
            "idempotency_key": "norm-key-1",
        },
        idempotency_key="norm-key-1",
        status="pending",
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)
    async with session_factory() as session:
        stored = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.idempotency_key == "norm-key-1"
                )
            )
        ).scalar_one()
    assert stored.trigger == "assign"
    assert stored.label_requirements == {}
    assert stored.required_capabilities == []
    assert stored.config_snapshot == {}
    assert stored.task_spec == {}

    # cancel_in_flight with a malformed agent id → clean no-op.
    bad_cancel = OutboxEvent(
        workspace_id=world["ws_id"],
        event_type="execution.enqueue",
        payload={"intent": "cancel_in_flight", "agent_id": "garbage"},
        status="pending",
    )
    async with session_factory() as session, session.begin():
        session.add(bad_cancel)
        await session.flush()
        assert await enqueue_execution_handler(session, bad_cancel) is None

    # _parse_uuid failure modes.
    assert _parse_uuid("not-a-uuid") is None
    assert _parse_uuid(object()) is None
    assert _parse_uuid(None) is None

    # IntegrityError discriminator: only the idempotency index is swallowed.
    from sqlalchemy.exc import IntegrityError

    class FakeOrig:
        def __init__(self, name):
            self.constraint_name = name

        def __str__(self):
            return f'violates unique constraint "{self.constraint_name}"'

    assert _is_idempotency_conflict(
        IntegrityError("s", {}, FakeOrig("uq_task_executions_idem"))
    )
    assert not _is_idempotency_conflict(
        IntegrityError("s", {}, FakeOrig("task_executions_workspace_id_fkey"))
    )


async def test_reaper_loop_runs_a_pass_and_stops(session_factory):
    """The worker loop body: one sweep, then stop."""
    import asyncio

    from mesh.runtime.reaper import runtime_reaper_loop

    settings = make_settings(
        runtime_reaper_interval=0.05,
        runtime_heartbeat_timeout_multiplier=3,
    )
    stop = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            runtime_reaper_loop(session_factory, settings=settings, stop=stop),
            stop_soon(),
        ),
        timeout=15,
    )
    # Pre-set stop → immediate return (entry guard).
    preset = asyncio.Event()
    preset.set()
    await runtime_reaper_loop(session_factory, settings=settings, stop=preset)


async def test_cancel_awaiting_approval_execution_closes_approval(session_factory):
    """User-cancel on an awaiting_approval execution → cancelled + the pending
    approval closed (attempts.py approval branch)."""
    from mesh.db.models.runtime import Approval
    from mesh.runtime.approvals import request_tool_approval
    from mesh.runtime.attempts import cancel_execution, transition_attempt
    from mesh.runtime.claim import claim_execution

    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    attempt_id = uuid.UUID(result.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=attempt_id,
        lease_seq=1,
        action_summary={},
        resume_context={},
        approval_ttl=timedelta(hours=1),
    )

    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        member_id=world["member_id"],
    )
    assert data["status"] == "cancelled"
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
        approval = (await session.execute(select(Approval))).scalar_one()
    assert stored.status == "cancelled"
    assert approval.status == "cancelled"

    # cancel_execution on a "cancelling" execution is an idempotent return.
    exec2 = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="cancelling"
    )
    again = await cancel_execution(
        session_factory, workspace_id=world["ws_id"], execution_id=exec2.id
    )
    assert again["status"] == "cancelling"


async def test_log_read_mid_offset_and_stream_filter(session_factory, object_storage):
    """read_execution_logs: offset slicing inside a segment + stream filter."""
    from mesh.runtime.claim import claim_execution
    from mesh.runtime.logs import append_log_lines, read_execution_logs

    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    attempt_id = uuid.UUID(result.attempt["id"])
    first = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["aaaa", "bbbb"],
        signing_secret=TEST_JWT_SECRET,
    )
    await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stderr",
        start_offset=first["accepted_end_offset"],
        lines=["cccc"],
        signing_secret=TEST_JWT_SECRET,
    )
    # Mid-segment offset: skip "aaaa" (5 bytes), keep "bbbb".
    page = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        offset=5,
    )
    assert [item["line"] for item in page["lines"]] == ["bbbb", "cccc"]
    # Stream filter across both segments.
    err = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        offset=0,
        stream="stderr",
    )
    assert [item["line"] for item in err["lines"]] == ["cccc"]
    # Unknown execution → 404.
    with pytest.raises(NotFoundError):
        await read_execution_logs(
            session_factory,
            object_storage,
            workspace_id=world["ws_id"],
            execution_id=uuid.uuid4(),
            offset=0,
        )


async def test_checkout_diff_upload_to_storage(session_factory, object_storage):
    """report_checkout with a diff stores it to object storage (diff_ref)."""
    from sqlalchemy import update

    from mesh.db.models.workspace import Workspace
    from mesh.runtime.checkout import report_checkout
    from mesh.runtime.claim import claim_execution

    world = await seed_world(session_factory)
    repo = "https://code.example/team/diff.git"
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == world["ws_id"])
            .values(settings={"allowed_repos": [repo]})
        )
    runtime = await make_runtime(session_factory, world["ws_id"])
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={"repo": {"url": repo, "base_ref": "main"}},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    attempt_id = uuid.UUID(result.attempt["id"])
    data = await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="diff_ready",
        repo_url=repo,
        diff="+ added line\n- removed line\n",
        storage=object_storage,
    )
    assert data["status"] == "diff_ready"
    assert data["diff_ref"]
    raw = await object_storage.get_bytes(data["diff_ref"], max_bytes=1024)
    assert b"added line" in raw

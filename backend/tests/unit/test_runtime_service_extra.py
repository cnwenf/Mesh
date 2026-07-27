"""Extra RuntimeService coverage: executions listing/detail rendering,
decommission, patch, filters, freeze/cancel edge paths."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.api_token import ApiToken
from mesh.db.models.runtime import ExecutionAttempt, RepoCheckout, Runtime, RuntimeCredential
from mesh.errors import NotFoundError
from mesh.runtime.attempts import cancel_execution, freeze_execution, transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.credentials import encrypt_credential_value
from mesh.runtime.service import RuntimeService

from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    issue_runtime_token,
    make_execution,
    make_runtime,
    make_settings,
    seed_world,
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
        workspace_id=world["ws_id"], runtime_id=runtime.id, name=None, labels=None, max_concurrent=2
    )
    assert again["name"] == "renamed"
    assert again["max_concurrent"] == 2


async def test_decommission_revokes_token_and_hides_runtime(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], created_by=world["member_id"])
    _, token_row = await issue_runtime_token(session_factory, runtime)
    await service.decommission_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)
    async with session_factory() as session:
        row = await session.get(Runtime, runtime.id)
        revoked = await session.get(ApiToken, token_row.id)
    assert row.status == "decommissioned"
    assert row.deleted_at is not None
    assert revoked.revoked_at is not None
    with pytest.raises(NotFoundError):
        await service.get_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)


async def test_list_executions_filters_and_cursor(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    for i in range(3):
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
    by_status = await service.list_executions(workspace_id=world["ws_id"], status="running")
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
    assert other_agent_exec.agent_id is None  # sanity


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
        session_factory, attempt_id=attempt_id, runtime=runtime, lease_seq=1, new_status="running"
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

    data = await service.get_execution(workspace_id=world["ws_id"], execution_id=execution.id)
    assert data["retry_count"] == 0
    assert data["attempts"][0]["status"] == "running"
    assert data["attempts"][0]["runtime_name"] == runtime.name
    # Credential values are NEVER rendered.
    assert data["credentials"][0]["value"] == "***"
    assert data["credentials"][0]["name"] == "DEPLOY_KEY"
    assert "ssh-secret" not in str(data)
    assert data["checkout"]["repo_url"] == "https://code.example/t/a.git"

    with pytest.raises(NotFoundError):
        await service.get_execution(workspace_id=world["ws_id"], execution_id=uuid.uuid4())


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
            __import__("mesh.db.models.runtime", fromlist=["TaskExecution"]).TaskExecution,
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
    runtime = await make_runtime(session_factory, world["ws_id"], created_by=world["member_id"])
    await service.decommission_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)
    from mesh.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        await service.heartbeat(
            runtime=runtime, current_load=0, health="healthy", metrics={}, inflight=[]
        )


async def test_list_runtimes_kind_and_status_filters(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    await make_runtime(session_factory, world["ws_id"], kind="platform_managed", name="pm")
    await make_runtime(session_factory, world["ws_id"], kind="self_hosted", name="sh")
    await make_runtime(
        session_factory, world["ws_id"], kind="self_hosted", name="off", status="unavailable"
    )
    pm = await service.list_runtimes(workspace_id=world["ws_id"], kind="platform_managed")
    assert [r["name"] for r in pm["data"]] == ["pm"]
    online = await service.list_runtimes(workspace_id=world["ws_id"], status="online")
    assert {r["name"] for r in online["data"]} == {"pm", "sh"}

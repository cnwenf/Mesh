"""Task principal squad operations e2e — §2.2 S-05 current-squad-task ops.

Real HTTP tests for /api/v1/task/squad/* using mesh_task_ tokens obtained
through the REAL product flow (squad assignment → leader orchestrator
execution enqueue → daemon claim), never psql seeding. Validates:

1. Leader orchestrator token → squad roster read + decomposition submit,
   with the server verifying the caller IS the task's orchestrator;
2. Executor / non-squad tokens are refused (scope gate, fail-closed);
3. Scope widening follows the frozen squad_role — executors never receive
   squad methods;
4. Decomposition created through the task route dispatches real member
   executions (the full server-side chain, no daemon required).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import TaskExecution
from tests.e2e.test_task_routes_e2e import (
    _activated_runtime,
    _auth,
    _create_workspace,
    _daemon,
    _register_and_login,
)

ORCHESTRATOR_TIMEOUT = 30.0
EXECUTOR_TIMEOUT = 30.0


async def _make_agent_member(client, token, ws_id, name: str) -> tuple[str, str]:
    """Create an agent; return (agent_id, member_id) — agent creation
    auto-provisions the workspace member row (README §6.1)."""
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={"name": name, "system_instructions": f"You are {name}."},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()["data"]
    member_id = agent.get("member_id") or (agent.get("member") or {}).get("id")
    assert member_id, f"agent created without member: {list(agent)}"
    return agent["id"], member_id


async def _squad_world(client, suffix: str) -> dict:
    """User + workspace + leader/2 members + squad + runtime + issue.

    Returns everything the tests need; the runtime is activated (online)
    with max_concurrent=3 so leader + both member attempts can claim."""
    email = f"squad-task-{suffix}@e2e.mesh"
    token = await _register_and_login(client, email)
    ws = await _create_workspace(client, token, f"squad-task-{suffix}")
    ws_id = ws["id"]
    _leader_agent, leader_member = await _make_agent_member(client, token, ws_id, "leader")
    _a_agent, member_a = await _make_agent_member(client, token, ws_id, "worker-a")
    _b_agent, member_b = await _make_agent_member(client, token, ws_id, "worker-b")
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads",
        json={
            "name": f"squad-{suffix}",
            "members": [
                {"member_id": leader_member, "role": "leader"},
                {"member_id": member_a, "role": "member"},
                {"member_id": member_b, "role": "member"},
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    squad = resp.json()["data"]
    created, daemon_token = await _activated_runtime(client, token, ws_id)
    # Re-create with concurrency for the multi-attempt flow: activation above
    # used max_concurrent=1; bump the runtime row via console PATCH.
    resp = await client.patch(
        f"/api/v1/workspaces/{ws_id}/runtimes/{created['id']}",
        json={"max_concurrent": 3},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": f"squad e2e {suffix}", "description": "decompose me"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    issue = resp.json()["data"]
    return {
        "token": token,
        "ws_id": ws_id,
        "squad_id": squad["id"],
        "runtime_id": created["id"],
        "daemon_token": daemon_token,
        "issue_id": issue["id"],
        "leader_member": leader_member,
        "member_a": member_a,
        "member_b": member_b,
    }


async def _assign_to_squad(client, world: dict) -> str:
    """POST /squads/{id}/tasks → root squad task id; wakes the leader via
    the outbox relay (real product path)."""
    resp = await client.post(
        f"/api/v1/workspaces/{world['ws_id']}/squads/{world['squad_id']}/tasks",
        json={"issue_id": world["issue_id"]},
        headers=_auth(world["token"]),
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["id"]


async def _wait_execution_for_task(
    session_factory, ws_id: str, squad_task_id: str, role: str, timeout: float
) -> TaskExecution:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.workspace_id == uuid.UUID(ws_id)
                    )
                )
            ).scalars().all()
        for row in rows:
            spec = row.task_spec if isinstance(row.task_spec, dict) else {}
            if spec.get("squad_task_id") == squad_task_id and spec.get("squad_role") == role:
                return row
        await asyncio.sleep(0.3)
    raise AssertionError(f"{role} execution for squad task {squad_task_id} never enqueued")


async def _claim_specific_execution(
    client, world: dict, execution: TaskExecution, timeout: float = 30.0
) -> str:
    """Claim until the GIVEN execution is handed out; return its task token.

    The daemon claim endpoint picks highest-priority/oldest queued rows, so
    with multiple queued executions we claim-and-park until ours appears."""
    parked: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.post(
            f"/api/v1/daemon/runtimes/{world['runtime_id']}/executions:claim",
            json={"diagnostics": {}},
            headers=_daemon(world["daemon_token"]),
        )
        if resp.status_code == 204:
            await asyncio.sleep(0.3)
            continue
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        parked.append(data["attempt"]["task_token"])
        if data["execution"]["id"] == str(execution.id):
            return data["attempt"]["task_token"]
        await asyncio.sleep(0.1)
    raise AssertionError(f"execution {execution.id} never claimed (parked {len(parked)})")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_token_reads_squad_roster(
    api_client, runtime_worker, session_factory
):
    """① Leader orchestrator token → GET /api/v1/task/squad/members → 200,
    roster = leader + 2 members; token methods carry the squad scope."""
    world = await _squad_world(api_client, "roster")
    root_id = await _assign_to_squad(api_client, world)
    execution = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    task_token = await _claim_specific_execution(api_client, world, execution)

    resp = await api_client.get("/api/v1/task/context", headers=_auth(task_token))
    assert resp.status_code == 200, resp.text
    methods = resp.json()["data"]["methods"]
    assert "squad:task:read" in methods
    assert "squad:task:decompose" in methods

    resp = await api_client.get("/api/v1/task/squad/members", headers=_auth(task_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["squad_id"] == world["squad_id"]
    ids = {m["member_id"] for m in data["members"]}
    assert ids == {world["leader_member"], world["member_a"], world["member_b"]}
    roles = {m["member_id"]: m["role"] for m in data["members"]}
    assert roles[world["leader_member"]] == "leader"


@pytest.mark.asyncio
async def test_orchestrator_token_decomposes_and_dispatches(
    api_client, runtime_worker, session_factory
):
    """② Leader token → POST /api/v1/task/squad/subtasks → 201; children
    exist, root advances to dispatching, and BOTH members get real queued
    executions (server-side dispatch chain, no daemon)."""
    world = await _squad_world(api_client, "decompose")
    root_id = await _assign_to_squad(api_client, world)
    execution = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    task_token = await _claim_specific_execution(api_client, world, execution)

    resp = await api_client.post(
        "/api/v1/task/squad/subtasks",
        headers=_auth(task_token),
        json={
            "plan_markdown": "split into two",
            "subtasks": [
                {"title": "part A", "assignee_member_id": world["member_a"]},
                {"title": "part B", "assignee_member_id": world["member_b"]},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]["created_subtasks"]
    assert len(created) == 2

    # Root task advanced past decomposing (no plan approval on this squad).
    resp = await api_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/squads/{world['squad_id']}/tasks/{root_id}",
        headers=_auth(world["token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] in ("dispatching", "in_progress")

    # Both members received executor executions through the relay.
    for child in created:
        await _wait_execution_for_task(
            session_factory, world["ws_id"], child["id"], "executor", EXECUTOR_TIMEOUT
        )


@pytest.mark.asyncio
async def test_executor_token_has_no_squad_scope(
    api_client, runtime_worker, session_factory
):
    """③ Executor attempts never receive squad methods — scope widening is
    orchestrator-only (§2.2 S-05). Squad routes refuse them (fail-closed)."""
    world = await _squad_world(api_client, "executor")
    root_id = await _assign_to_squad(api_client, world)
    orch = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    orch_token = await _claim_specific_execution(api_client, world, orch)
    resp = await api_client.post(
        "/api/v1/task/squad/subtasks",
        headers=_auth(orch_token),
        json={
            "subtasks": [{"title": "solo", "assignee_member_id": world["member_a"]}]
        },
    )
    assert resp.status_code == 201, resp.text
    child_id = resp.json()["data"]["created_subtasks"][0]["id"]
    executor_exec = await _wait_execution_for_task(
        session_factory, world["ws_id"], child_id, "executor", EXECUTOR_TIMEOUT
    )
    executor_token = await _claim_specific_execution(api_client, world, executor_exec)

    resp = await api_client.get("/api/v1/task/context", headers=_auth(executor_token))
    assert resp.status_code == 200, resp.text
    methods = resp.json()["data"]["methods"]
    assert "squad:task:decompose" not in methods
    assert "squad:task:read" not in methods

    resp = await api_client.get("/api/v1/task/squad/members", headers=_auth(executor_token))
    assert resp.status_code == 401, resp.text
    resp = await api_client.post(
        "/api/v1/task/squad/subtasks",
        headers=_auth(executor_token),
        json={"subtasks": [{"title": "sneaky"}]},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_non_squad_token_refused_on_squad_routes(
    api_client, runtime_worker, session_factory
):
    """④ A task token from a NON-squad execution carries no squad scope —
    both squad routes fail closed with 401."""
    from tests.e2e.test_task_routes_e2e import _enqueue_and_wait, _setup_world

    token, ws_id, agent_id = await _setup_world(api_client, "nonsquad")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    await _enqueue_and_wait(session_factory, ws_id, agent_id)
    resp = await api_client.post(
        f"/api/v1/daemon/runtimes/{created['id']}/executions:claim",
        json={"diagnostics": {}},
        headers=_daemon(daemon_token),
    )
    assert resp.status_code == 200, resp.text
    task_token = resp.json()["data"]["attempt"]["task_token"]

    resp = await api_client.get("/api/v1/task/squad/members", headers=_auth(task_token))
    assert resp.status_code == 401, resp.text
    resp = await api_client.post(
        "/api/v1/task/squad/subtasks",
        headers=_auth(task_token),
        json={"subtasks": [{"title": "nope"}]},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_subtasks_assignee_must_be_squad_member(
    api_client, runtime_worker, session_factory
):
    """⑤ Decomposition with an assignee outside the squad is rejected by the
    service layer (assignee_not_member) — surfaced through the task route."""
    world = await _squad_world(api_client, "badassignee")
    root_id = await _assign_to_squad(api_client, world)
    execution = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    task_token = await _claim_specific_execution(api_client, world, execution)
    outsider = str(uuid.uuid4())
    resp = await api_client.post(
        "/api/v1/task/squad/subtasks",
        headers=_auth(task_token),
        json={"subtasks": [{"title": "x", "assignee_member_id": outsider}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "assignee_not_member"


@pytest.mark.asyncio
async def test_squad_routes_reject_forged_and_console_tokens(
    api_client, runtime_worker
):
    """⑥ Forged mesh_task_ → 401; console session token → 401."""
    resp = await api_client.get(
        "/api/v1/task/squad/members",
        headers=_auth("mesh_task_absolutely_forged"),
    )
    assert resp.status_code == 401, resp.text
    world = await _squad_world(api_client, "console")
    resp = await api_client.get(
        "/api/v1/task/squad/members", headers=_auth(world["token"])
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_task_principal_issue_read_comment_status(
    api_client, runtime_worker, session_factory
):
    """⑦ The broker's issue actions land on task-principal routes: the
    attempt's agent member reads, comments (authored as itself) and flips
    the status of its OWN pinned issue — and nothing else."""
    world = await _squad_world(api_client, "issueops")
    root_id = await _assign_to_squad(api_client, world)
    execution = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    task_token = await _claim_specific_execution(api_client, world, execution)

    # Read the current issue.
    resp = await api_client.get(
        f"/api/v1/task/issues/{world['issue_id']}", headers=_auth(task_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["id"] == world["issue_id"]

    # Comment AS the leader member (author attribution is the point).
    resp = await api_client.post(
        f"/api/v1/task/issues/{world['issue_id']}/comments",
        headers=_auth(task_token),
        json={"body": "broker-authored note"},
    )
    assert resp.status_code == 201, resp.text
    resp = await api_client.get(
        f"/api/v1/issues/{world['issue_id']}/comments", headers=_auth(world["token"])
    )
    assert resp.status_code == 200, resp.text
    listing = resp.json()["data"]
    comments = listing["items"] if isinstance(listing, dict) else listing
    mine = [cm for cm in comments if world["leader_member"] in json.dumps(cm)]
    assert mine, f"no leader-authored comment: {comments}"

    # Flip the status to done.
    resp = await api_client.patch(
        f"/api/v1/task/issues/{world['issue_id']}/status",
        headers=_auth(task_token),
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.get(
        f"/api/v1/issues/{world['issue_id']}", headers=_auth(world["token"])
    )
    status = resp.json()["data"]["status"]
    status_name = status.get("name") if isinstance(status, dict) else status
    assert str(status_name).lower().replace(" ", "_") == "done"


@pytest.mark.asyncio
async def test_task_principal_issue_routes_pin_resource_scope(
    api_client, runtime_worker, session_factory
):
    """⑧ A task token cannot touch ANY issue other than its pinned one."""
    world = await _squad_world(api_client, "scope")
    root_id = await _assign_to_squad(api_client, world)
    execution = await _wait_execution_for_task(
        session_factory, world["ws_id"], root_id, "orchestrator", ORCHESTRATOR_TIMEOUT
    )
    task_token = await _claim_specific_execution(api_client, world, execution)
    other_issue_id = str(uuid.uuid4())
    resp = await api_client.get(
        f"/api/v1/task/issues/{other_issue_id}", headers=_auth(task_token)
    )
    assert resp.status_code == 401, resp.text
    resp = await api_client.post(
        f"/api/v1/task/issues/{other_issue_id}/comments",
        headers=_auth(task_token),
        json={"body": "x"},
    )
    assert resp.status_code == 401, resp.text
    resp = await api_client.patch(
        f"/api/v1/task/issues/{other_issue_id}/status",
        headers=_auth(task_token),
        json={"status": "done"},
    )
    assert resp.status_code == 401, resp.text

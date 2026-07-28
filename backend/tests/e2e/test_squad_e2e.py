"""Squad e2e — REAL server processes, REAL API calls, REAL worker relay.

Covers the red-line acceptance paths end-to-end (squad.md §5, README §9):
* T23 unique-active-assignment: S1→S2 reassignment (same leader) cascades S1's
  root and is NEVER a no-op; duplicate dispatch to the same squad IS a no-op.
* §6.10 unified plan approval: approve over HTTP → the outbox relay applies the
  decision onto the root task (awaiting_plan_approval → dispatching).
* §2.6 DAG cycle rejection over HTTP (dependency_cycle).
* Cross-workspace access → 404 (existence must not leak).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.squad import IssueSquadAssignment, SquadTask

pytestmark = pytest.mark.e2e

PASSWORD = "e2e-password-123"
WORKER_READY_WAIT_SECONDS = 2.5


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="module")
async def squad_worker(provision_database):
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_BUCKET"] = os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e")
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    await asyncio.sleep(WORKER_READY_WAIT_SECONDS)
    assert process.poll() is None, "worker died during startup"
    yield process
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


async def _register_and_login(client, email: str, name: str = "Squad E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token, slug) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Squad WS", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["data"]


async def _owner_id(client, token, ws_id) -> str:
    resp = await client.get(f"/api/v1/workspaces/{ws_id}/members", headers=_auth(token))
    return str(resp.json()["data"][0]["id"])


async def _make_squad(client, token, ws_id, owner_id, name, *, approval=False) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads",
        json={
            "name": name,
            "require_plan_approval": approval,
            "members": [{"member_id": owner_id, "role": "leader"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _seed_issue(session_factory, ws_id) -> uuid.UUID:
    ws_uuid = uuid.UUID(ws_id)
    async with session_factory() as session, session.begin():
        status = await session.scalar(
            select(IssueStatus).where(IssueStatus.workspace_id == ws_uuid).limit(1)
        )
        if status is None:
            status = IssueStatus(
                workspace_id=ws_uuid, name=f"st-{uuid.uuid4().hex[:6]}", category="todo", is_default=True
            )
            session.add(status)
            await session.flush()
        suffix = uuid.uuid4().hex[:6]
        issue = Issue(
            workspace_id=ws_uuid,
            identifier_namespace_key=f"ws:{ws_uuid}",
            number=abs(hash(suffix)) % 100000,
            identifier=f"WS-{suffix}",
            title="Squad e2e issue",
            status_id=status.id,
            state_category="todo",
        )
        session.add(issue)
        await session.flush()
        return issue.id


async def _task_status(session_factory, task_id) -> str:
    async with session_factory() as session:
        task = await session.scalar(select(SquadTask).where(SquadTask.id == task_id))
        return task.status


async def test_t23_reassignment_unique_active_identity(api_client, squad_worker, session_factory):
    token = await _register_and_login(api_client, "t23@corp.com")
    ws = await _create_workspace(api_client, token, f"t23-{uuid.uuid4().hex[:6]}")
    owner = await _owner_id(api_client, token, ws["id"])
    # Same leader owns two squads.
    s1 = await _make_squad(api_client, token, ws["id"], owner, "S1")
    s2 = await _make_squad(api_client, token, ws["id"], owner, "S2")
    issue_id = await _seed_issue(session_factory, ws["id"])

    # Assign to S1.
    r1 = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{s1['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert r1.status_code == 202, r1.text
    d1 = r1.json()["data"]
    root1 = uuid.UUID(d1["id"])

    # Reassign to S2 (same leader) — MUST NOT be a no-op.
    r2 = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{s2['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert r2.status_code == 202, r2.text
    d2 = r2.json()["data"]
    assert d2["noop"] is False
    assert d2["superseded_assignment_id"] == d1["assignment_id"]

    # S1's root is cascade-cancelled; exactly one active assignment (S2).
    assert await _task_status(session_factory, root1) == "cancelled"
    async with session_factory() as session:
        active = (
            await session.execute(
                select(IssueSquadAssignment).where(
                    IssueSquadAssignment.issue_id == issue_id,
                    IssueSquadAssignment.status == "active",
                )
            )
        ).scalars().all()
    assert len(active) == 1
    assert active[0].squad_id == uuid.UUID(s2["id"])

    # Duplicate dispatch to the SAME squad (S2) → no-op.
    r3 = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{s2['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert r3.json()["data"]["noop"] is True
    assert r3.json()["data"]["assignment_id"] == d2["assignment_id"]


async def test_plan_approval_flow_over_relay(api_client, squad_worker, session_factory):
    token = await _register_and_login(api_client, "planflow@corp.com")
    ws = await _create_workspace(api_client, token, f"plan-{uuid.uuid4().hex[:6]}")
    owner = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner, "PlanSquad", approval=True)
    issue_id = await _seed_issue(session_factory, ws["id"])

    assign = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    root_id = assign.json()["data"]["id"]

    sub = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"plan_markdown": "split", "subtasks": [{"title": "a"}, {"title": "b"}]},
        headers=_auth(token),
    )
    assert sub.status_code == 201, sub.text
    body = sub.json()["data"]
    assert body["awaiting_approval"] is True
    assert await _task_status(session_factory, uuid.UUID(root_id)) == "awaiting_plan_approval"

    # Approve via HTTP → relay applies the decision onto the root.
    approve = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/plan/approve",
        json={},
        headers=_auth(token),
    )
    assert approve.status_code == 200, approve.text

    # Poll for the relay to move the root out of awaiting_plan_approval.
    final = "awaiting_plan_approval"
    for _ in range(40):
        final = await _task_status(session_factory, uuid.UUID(root_id))
        if final in ("dispatching", "in_progress"):
            break
        await asyncio.sleep(0.25)
    assert final in ("dispatching", "in_progress"), f"root stuck at {final}"


async def test_dag_cycle_rejected_over_http(api_client, squad_worker, session_factory):
    token = await _register_and_login(api_client, "dag@corp.com")
    ws = await _create_workspace(api_client, token, f"dag-{uuid.uuid4().hex[:6]}")
    owner = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner, "DagSquad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    root_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]

    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={
            "subtasks": [
                {"title": "a", "depends_on": ["b"]},
                {"title": "b", "depends_on": ["a"]},
            ]
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "dependency_cycle"


async def test_cross_workspace_404(api_client, squad_worker):
    token = await _register_and_login(api_client, "xws@corp.com")
    ws = await _create_workspace(api_client, token, f"xws-{uuid.uuid4().hex[:6]}")
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{uuid.uuid4()}", headers=_auth(token)
    )
    assert resp.status_code == 404


async def _add_workspace_member(session_factory, ws_id, user_id, member_id) -> None:
    """Seat a registered user into the workspace roster (direct DB, e2e scope)."""
    from mesh.db.models.member import Member

    async with session_factory() as session, session.begin():
        session.add(
            Member(
                id=member_id,
                workspace_id=uuid.UUID(ws_id),
                member_type="human",
                user_id=user_id,
                role="member",
                status="active",
            )
        )


async def _user_id_by_email(session_factory, email) -> uuid.UUID:
    from mesh.db.models.user import User

    async with session_factory() as session:
        return await session.scalar(select(User.id).where(User.email == email))


async def test_subtasks_and_dispatch_require_orchestrator(api_client, squad_worker, session_factory):
    """B3 / §3.4 / §5.3: workspace-level ``agent:trigger`` RBAC is not enough —
    a squad member who is NOT the task's orchestrator (and not admin) gets 403
    on the leader's decompose / dispatch endpoints."""
    email = f"orch-authz-{uuid.uuid4().hex[:6]}@e2e.com"
    token = await _register_and_login(api_client, email)
    ws = await _create_workspace(api_client, token, f"sq-authz-{uuid.uuid4().hex[:6]}")
    owner_id = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner_id, "Authz Squad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    root_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]

    # A second human joins the roster (member role) and the squad.
    email2 = f"orch-member-{uuid.uuid4().hex[:6]}@e2e.com"
    token2 = await _register_and_login(api_client, email2, name="Plain Member")
    member2_id = uuid.uuid4()
    await _add_workspace_member(
        session_factory, ws["id"], await _user_id_by_email(session_factory, email2), member2_id
    )
    added = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/members",
        json={"members": [{"member_id": str(member2_id), "role": "member"}]},
        headers=_auth(token),
    )
    assert added.status_code == 200, added.text

    base = f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}"
    sub = await api_client.post(
        f"{base}/subtasks", json={"subtasks": [{"title": "sneaky"}]}, headers=_auth(token2)
    )
    assert sub.status_code == 403, sub.text
    assert sub.json()["error"]["code"] == "forbidden"
    disp = await api_client.post(f"{base}/dispatch", headers=_auth(token2))
    assert disp.status_code == 403, disp.text
    assert disp.json()["error"]["code"] == "forbidden"
    # The orchestrator (owner/leader) still can.
    ok = await api_client.post(
        f"{base}/subtasks", json={"subtasks": [{"title": "legit"}]}, headers=_auth(token)
    )
    assert ok.status_code == 201, ok.text


async def test_leader_readd_unblocks_root(api_client, squad_worker, session_factory):
    """B4 / §2.5 / §5.1⑤: removing the last leader blocks the active root
    (leader_lost); adding a leader back unblocks it in the same transaction —
    assignment leader snapshot + issue assignee propagate."""
    email = f"unblock-{uuid.uuid4().hex[:6]}@e2e.com"
    token = await _register_and_login(api_client, email)
    ws = await _create_workspace(api_client, token, f"sq-unblk-{uuid.uuid4().hex[:6]}")
    owner_id = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner_id, "Unblock Squad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    assign = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert assign.status_code == 202, assign.text
    root_id = uuid.UUID(assign.json()["data"]["id"])

    # Remove the only leader → root blocked(leader_lost).
    removed = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/members/{owner_id}",
        headers=_auth(token),
    )
    assert removed.status_code == 200, removed.text
    for _ in range(20):
        if await _task_status(session_factory, root_id) == "blocked":
            break
        await asyncio.sleep(0.25)
    assert await _task_status(session_factory, root_id) == "blocked"
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
        assert root.failure_reason == "leader_lost"

    # Re-add the leader → root unblocks, assignment + issue assignee follow.
    readd = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/members",
        json={"members": [{"member_id": owner_id, "role": "leader"}]},
        headers=_auth(token),
    )
    assert readd.status_code == 200, readd.text
    for _ in range(20):
        if await _task_status(session_factory, root_id) == "in_progress":
            break
        await asyncio.sleep(0.25)
    assert await _task_status(session_factory, root_id) == "in_progress"
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
        assert root.failure_reason is None
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.issue_id == issue_id,
                IssueSquadAssignment.status == "active",
            )
        )
        assert assignment is not None
        assert str(assignment.leader_member_id) == owner_id
        issue = await session.scalar(select(Issue).where(Issue.id == issue_id))
        assert str(issue.assignee_id) == owner_id


async def test_plan_approve_without_body(api_client, squad_worker, session_factory):
    """B13 / §6.10: the plan approve/reject body is optional — a bare POST
    (no JSON at all) approves; the relay still applies the decision."""
    email = f"nobdy-{uuid.uuid4().hex[:6]}@e2e.com"
    token = await _register_and_login(api_client, email)
    ws = await _create_workspace(api_client, token, f"sq-nobdy-{uuid.uuid4().hex[:6]}")
    owner_id = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner_id, "NoBody Squad", approval=True)
    issue_id = await _seed_issue(session_factory, ws["id"])
    root_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    sub = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"subtasks": [{"title": "work item"}]},
        headers=_auth(token),
    )
    assert sub.status_code == 201, sub.text
    assert sub.json()["data"]["awaiting_approval"] is True

    # No JSON body at all.
    approve = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/plan/approve",
        headers=_auth(token),
    )
    assert approve.status_code == 200, approve.text

    async def _root_status() -> str:
        return await _task_status(session_factory, uuid.UUID(root_id))

    for _ in range(40):
        if await _root_status() in ("dispatching", "in_progress"):
            break
        await asyncio.sleep(0.25)
    assert await _root_status() in ("dispatching", "in_progress")


async def test_human_squad_completion_writes_back_summary(api_client, squad_worker, session_factory):
    """C1 / §S8 / §4.3-7: a HUMAN-led squad completing via manual status moves
    (no execution.finished relay) must still write the leader's summary back
    to the parent issue as a comment."""
    email = f"wb-human-{uuid.uuid4().hex[:6]}@e2e.com"
    token = await _register_and_login(api_client, email)
    ws = await _create_workspace(api_client, token, f"sq-wbh-{uuid.uuid4().hex[:6]}")
    owner_id = await _owner_id(api_client, token, ws["id"])
    squad = await _make_squad(api_client, token, ws["id"], owner_id, "HumanLed Squad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    root_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    sub = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={
            "subtasks": [
                {"title": "manual child one", "assignee": {"member_id": owner_id}, "stage": 1},
                {"title": "manual child two", "assignee": {"member_id": owner_id}, "stage": 1},
            ]
        },
        headers=_auth(token),
    )
    assert sub.status_code == 201, sub.text
    sub_ids = [c["id"] for c in sub.json()["data"]["created_subtasks"]]
    # Children were auto-dispatched (in_progress) at decomposition; complete
    # them via the manual status endpoint (kanban path).
    for i, sid in enumerate(sub_ids):
        base = f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{sid}/status"
        mv = await api_client.patch(
            base, json={"status": "done", "result_summary": f"child {i} result"}, headers=_auth(token)
        )
        assert mv.status_code == 200, mv.text
    # Human-leader aggregation resolves synchronously → root done on return.
    assert await _task_status(session_factory, uuid.UUID(root_id)) == "done"
    # The leader's aggregate summary was written back to the parent issue.
    comments = await api_client.get(
        f"/api/v1/issues/{issue_id}/comments", headers=_auth(token)
    )
    assert comments.status_code == 200, comments.text
    bodies = [c.get("body_markdown", "") for c in comments.json()["data"]]
    assert len(bodies) == 1, bodies
    assert "child 0 result" in bodies[0] and "child 1 result" in bodies[0]


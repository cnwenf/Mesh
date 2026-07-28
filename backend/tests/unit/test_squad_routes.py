"""Extended in-process squad route coverage — update / archive / membership /
dispatch / cancel / plan-reject / status / read-authz helper. Complements
test_squad_api.py to reach the ≥90% new-code coverage bar."""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import func, select

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.squad import SquadTask
from mesh.errors import ForbiddenError
from mesh.squad.routes import _assert_squad_read

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-squad-routes-signing-secret-00",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture(autouse=True)
async def _flush_redis(redis_url):
    c = aioredis.from_url(redis_url, decode_responses=True)
    await c.flushdb()
    yield
    await c.flushdb()
    await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client, slug):
    email = f"{slug}@corp.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": slug},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    token = login.json()["data"]["access_token"]
    ws = (
        await client.post("/api/v1/workspaces", json={"name": "T", "slug": slug}, headers=_auth(token))
    ).json()["data"]
    owner = str(
        (
            await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(token))
        ).json()["data"][0]["id"]
    )
    return token, ws["id"], owner


async def _squad(client, token, ws_id, owner, name, **extra):
    body = {"name": name, "members": [{"member_id": owner, "role": "leader"}], **extra}
    return (
        await client.post(f"/api/v1/workspaces/{ws_id}/squads", json=body, headers=_auth(token))
    ).json()["data"]


async def _seed_issue(session_factory, ws_id) -> uuid.UUID:
    ws_uuid = uuid.UUID(ws_id)
    async with session_factory() as session, session.begin():
        status = await session.scalar(select(IssueStatus).where(IssueStatus.workspace_id == ws_uuid).limit(1))
        if status is None:
            status = IssueStatus(
                workspace_id=ws_uuid, name=f"st-{uuid.uuid4().hex[:6]}", category="todo", is_default=True
            )
            session.add(status)
            await session.flush()
        suffix = uuid.uuid4().hex[:6]
        issue = Issue(
            workspace_id=ws_uuid, identifier_namespace_key=f"ws:{ws_uuid}",
            number=abs(hash(suffix)) % 100000, identifier=f"WS-{suffix}",
            title="issue", status_id=status.id, state_category="todo",
        )
        session.add(issue)
        await session.flush()
        return issue.id


async def test_update_patch_archive_restore(client):
    token, ws_id, owner = await _setup(client, "upd-arc")
    squad = await _squad(client, token, ws_id, owner, "UpdSquad")
    upd = await client.patch(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}",
        json={"name": "UpdSquad2", "max_decompose_depth": 3},
        headers=_auth(token),
    )
    assert upd.status_code == 200
    assert upd.json()["data"]["name"] == "UpdSquad2"
    # No-change patch is fine too.
    upd2 = await client.patch(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}", json={"name": "UpdSquad2"}, headers=_auth(token)
    )
    assert upd2.status_code == 200

    arc = await client.post(f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/archive", headers=_auth(token))
    assert arc.status_code == 200 and arc.json()["data"]["status"] == "archived"
    res = await client.post(f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/restore", headers=_auth(token))
    assert res.status_code == 200 and res.json()["data"]["status"] == "active"


async def test_membership_routes(client):
    token, ws_id, owner = await _setup(client, "mem-routes")
    squad = await _squad(client, token, ws_id, owner, "MemSquad")
    # Register a second human in the same workspace is non-trivial; exercise the
    # owner's own membership listing + role change guards instead.
    members = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/members", headers=_auth(token)
    )
    assert members.status_code == 200
    assert len(members.json()["data"]) == 1
    # Demoting the only leader → 422 no_leader.
    demote = await client.patch(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/members/{owner}",
        json={"role": "member"},
        headers=_auth(token),
    )
    assert demote.status_code == 422
    assert demote.json()["error"]["code"] == "no_leader"
    # Role change to leader (no-op) is fine.
    role = await client.patch(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/members/{owner}",
        json={"role": "leader"},
        headers=_auth(token),
    )
    assert role.status_code == 200


async def test_dispatch_cancel_status_tasks_routes(client, session_factory):
    token, ws_id, owner = await _setup(client, "disp-cancel")
    squad = await _squad(client, token, ws_id, owner, "DispSquad")
    issue_id = await _seed_issue(session_factory, ws_id)
    root_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    # Add a subtask.
    await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"subtasks": [{"title": "a"}, {"title": "b"}]},
        headers=_auth(token),
    )
    # Tasks list.
    tasks = await client.get(f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks", headers=_auth(token))
    assert tasks.status_code == 200
    assert len(tasks.json()["data"]) >= 1
    # Single task + status.
    got = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}", headers=_auth(token)
    )
    assert got.status_code == 200
    status = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/status", headers=_auth(token)
    )
    assert status.status_code == 200
    assert status.json()["data"]["task_id"] == root_id
    # Dispatch (idempotent, ready deps).
    disp = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/dispatch", headers=_auth(token)
    )
    assert disp.status_code == 200
    # Cancel the root → cascades.
    cancel = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/cancel",
        json={"reason": "stop"},
        headers=_auth(token),
    )
    assert cancel.status_code == 200
    assert (await _status_of(session_factory, root_id)) == "cancelled"


async def _status_of(session_factory, task_id) -> str:
    async with session_factory() as session:
        return (await session.scalar(select(SquadTask.status).where(SquadTask.id == uuid.UUID(task_id))))


async def test_plan_reject_route(client, session_factory):
    token, ws_id, owner = await _setup(client, "reject-route")
    squad = await _squad(client, token, ws_id, owner, "RejSquad", require_plan_approval=True)
    issue_id = await _seed_issue(session_factory, ws_id)
    root_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"subtasks": [{"title": "a"}]},
        headers=_auth(token),
    )
    rej = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/plan/reject",
        json={"comment": "redo"},
        headers=_auth(token),
    )
    assert rej.status_code == 200, rej.text
    # Rejecting again with no pending approval → approval_expired business error.
    rej2 = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/plan/reject",
        json={},
        headers=_auth(token),
    )
    assert rej2.status_code == 422


async def test_plan_approve_without_body(client, session_factory):
    """B13 / §6.10: the decision body is optional — a bare POST approves."""
    token, ws_id, owner = await _setup(client, "nobody-route")
    squad = await _squad(client, token, ws_id, owner, "NoBodySquad", require_plan_approval=True)
    issue_id = await _seed_issue(session_factory, ws_id)
    root_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    sub = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"subtasks": [{"title": "a"}]},
        headers=_auth(token),
    )
    assert sub.status_code == 201
    # No JSON body at all (httpx sends an empty request body).
    ok = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/plan/approve",
        headers=_auth(token),
    )
    assert ok.status_code == 200, ok.text


async def test_plan_reject_optional_body(client, session_factory):
    """B13: plan/reject with NO request body succeeds — the PlanDecisionRequest
    payload (comment) is optional."""
    token, ws_id, owner = await _setup(client, "reject-nobody")
    squad = await _squad(client, token, ws_id, owner, "RejNoBody", require_plan_approval=True)
    issue_id = await _seed_issue(session_factory, ws_id)
    root_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/subtasks",
        json={"subtasks": [{"title": "a"}]},
        headers=_auth(token),
    )
    # Omit json= entirely → optional body → still 200.
    rej = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/plan/reject",
        headers=_auth(token),
    )
    assert rej.status_code == 200, rej.text


async def test_sse_last_event_id_and_activity(client, session_factory):
    token, ws_id, owner = await _setup(client, "sse-lei")
    squad = await _squad(client, token, ws_id, owner, "SseSquad")
    issue_id = await _seed_issue(session_factory, ws_id)
    root_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks",
            json={"issue_id": str(issue_id)},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    # Real orchestration action → persisted task.status frames (§3.5).
    cancel = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/cancel",
        json={},
        headers=_auth(token),
    )
    assert cancel.status_code == 200
    from mesh.db.models.realtime import RealtimeEvent
    from tests.unit.squad_support import project_pending_realtime

    await project_pending_realtime(session_factory)
    # Provide Last-Event-ID to exercise the header parse branch: seq 0 replays
    # every persisted frame, so the cancelled frame must appear.
    async with client.stream(
        "GET",
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/stream",
        headers={**_auth(token), "Last-Event-ID": "0"},
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
            if '"cancelled"' in body:
                break
    assert "event: task.status" in body
    # No-loss/no-dup: resuming from the channel's max seq yields nothing new
    # (the stream terminates on the terminal, fully-replayed task).
    async with session_factory() as session:
        max_seq = await session.scalar(
            select(func.max(RealtimeEvent.seq)).where(
                RealtimeEvent.channel == f"squad_task:{root_id}"
            )
        )
    assert max_seq is not None
    async with client.stream(
        "GET",
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/tasks/{root_id}/stream",
        headers={**_auth(token), "Last-Event-ID": str(max_seq)},
    ) as resp:
        assert resp.status_code == 200
        replay = ""
        async for chunk in resp.aiter_text():
            replay += chunk
    assert "event: task.status" not in replay
    # Activity endpoint with action filter.
    act = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad['id']}/activity?action=squad_created", headers=_auth(token)
    )
    assert act.status_code == 200


async def test_read_authz_helper(session_factory, workspace_factory):
    from tests.unit.squad_support import add_member, make_agent_member, make_human_member, make_squad

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    member = await make_human_member(session_factory, ws, role="member")
    admin = await make_human_member(session_factory, ws, role="admin")
    outsider = await make_human_member(session_factory, ws, role="member")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, member, role="member")

    # Admin passes without membership.
    await _assert_squad_read(session_factory, workspace_id=ws.id, squad_id=squad.id, member=admin)
    # Member passes.
    await _assert_squad_read(session_factory, workspace_id=ws.id, squad_id=squad.id, member=member)
    # Outsider (member of ws, not of squad) → Forbidden.
    with pytest.raises(ForbiddenError):
        await _assert_squad_read(session_factory, workspace_id=ws.id, squad_id=squad.id, member=outsider)


async def test_path_uuid_malformed_404(client):
    token, ws_id, owner = await _setup(client, "baduuid")
    resp = await client.get(f"/api/v1/workspaces/{ws_id}/squads/not-a-uuid", headers=_auth(token))
    assert resp.status_code == 404

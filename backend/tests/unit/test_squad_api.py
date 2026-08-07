"""In-process squad API tests — route layer, envelopes, error codes, SSE.

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the squad.md §3.1 endpoint surface, §6.14 envelopes/error codes and the
SSE orchestration stream (§3.5).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.squad import Squad

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-squad-test-signing-secret-000",
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


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    return resp.json()["data"]


async def _owner_member_id(client, token, ws_id) -> str:
    resp = await client.get(f"/api/v1/workspaces/{ws_id}/members", headers=_auth(token))
    return str(resp.json()["data"][0]["id"])


async def _make_squad(client, token, ws_id, owner_id, name) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/squads",
        json={"name": name, "members": [{"member_id": owner_id, "role": "leader"}]},
        headers=_auth(token),
    )
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
            title="Squad API issue",
            status_id=status.id,
            state_category="todo",
        )
        session.add(issue)
        await session.flush()
        return issue.id


async def test_create_squad_and_get(client):
    token = await _register_and_login(client, "squad-owner@corp.com")
    ws = await _create_workspace(client, token, "squad-ws")
    owner_id = await _owner_member_id(client, token, ws["id"])
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads",
        json={"name": "API Squad", "members": [{"member_id": owner_id, "role": "leader"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    squad_id = resp.json()["data"]["id"]

    got = await client.get(f"/api/v1/workspaces/{ws['id']}/squads/{squad_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["data"]["id"] == squad_id

    listed = await client.get(f"/api/v1/workspaces/{ws['id']}/squads", headers=_auth(token))
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1


async def test_assign_no_leader_422(client, session_factory):
    token = await _register_and_login(client, "squad-noleader@corp.com")
    ws = await _create_workspace(client, token, "squad-nolead")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad_id = uuid.uuid4()
    from mesh.db.models.member import Member

    async with session_factory() as session, session.begin():
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == uuid.UUID(ws["id"]), Member.id == uuid.UUID(owner_id)
            )
        )
        session.add(
            Squad(
                id=squad_id,
                workspace_id=uuid.UUID(ws["id"]),
                name="nolead",
                creator_id=member.id,
                primary_leader_id=None,
            )
        )
    issue_id = await _seed_issue(session_factory, ws["id"])
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad_id}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "squad_no_leader"


async def test_assign_task_and_tree(client, session_factory):
    token = await _register_and_login(client, "squad-assign@corp.com")
    ws = await _create_workspace(client, token, "squad-assign")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "Assign Squad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["assignment_id"]
    root_id = data["id"]
    assert data["status_url"].endswith(f"/tasks/{root_id}/status")

    tree = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/tree",
        headers=_auth(token),
    )
    assert tree.status_code == 200
    assert tree.json()["data"]["id"] == root_id


async def test_messages_roundtrip(client):
    token = await _register_and_login(client, "squad-msg@corp.com")
    ws = await _create_workspace(client, token, "squad-msg")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "Msg Squad")
    sent = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/messages",
        json={"kind": "chat", "body_markdown": "hello"},
        headers=_auth(token),
    )
    assert sent.status_code == 201, sent.text
    listed = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/messages", headers=_auth(token)
    )
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1


async def test_activity_endpoint(client):
    token = await _register_and_login(client, "squad-act@corp.com")
    ws = await _create_workspace(client, token, "squad-act")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "Act Squad")
    act = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/activity", headers=_auth(token)
    )
    assert act.status_code == 200
    assert "squad_created" in [a["action"] for a in act.json()["data"]]


async def test_cross_workspace_not_found(client):
    token = await _register_and_login(client, "squad-xws@corp.com")
    ws = await _create_workspace(client, token, "squad-xws")
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{uuid.uuid4()}", headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_export_markdown_archive(client):
    """L486: GET /squads/{id}/export returns the task-message + timeline
    archive as a downloadable markdown document."""
    token = await _register_and_login(client, "squad-exp@corp.com")
    ws = await _create_workspace(client, token, "squad-exp")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "Exp Squad")
    sent = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/messages",
        json={"kind": "instruction", "body_markdown": "先做幂等校验"},
        headers=_auth(token),
    )
    assert sent.status_code == 201, sent.text

    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/export", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.text
    assert "# 小队归档：Exp Squad" in body
    assert "【指令】" in body
    assert "先做幂等校验" in body
    assert "## 时间线" in body


async def test_export_denied_for_outsider(client):
    """A principal outside the workspace cannot reach the export (the read
    gate 404s — no squad/workspace existence oracle)."""
    token = await _register_and_login(client, "squad-exp2@corp.com")
    ws = await _create_workspace(client, token, "squad-exp2")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "Closed Squad")

    outsider = await _register_and_login(client, "squad-exp2-out@corp.com")
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/export",
        headers=_auth(outsider),
    )
    assert resp.status_code == 404


async def test_sse_stream_terminal(client, session_factory):
    token = await _register_and_login(client, "squad-sse@corp.com")
    ws = await _create_workspace(client, token, "squad-sse")
    owner_id = await _owner_member_id(client, token, ws["id"])
    squad = await _make_squad(client, token, ws["id"], owner_id, "SSE Squad")
    issue_id = await _seed_issue(session_factory, ws["id"])
    assign_resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
        json={"issue_id": str(issue_id)},
        headers=_auth(token),
    )
    root_id = assign_resp.json()["data"]["id"]
    # A real orchestration action: cancel emits task.status frames through the
    # outbox. The stream replays PERSISTED frames (§3.5), so project them —
    # the unit suite runs no projector loop.
    cancel = await client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/cancel",
        json={"reason": "sse-terminal-test"},
        headers=_auth(token),
    )
    assert cancel.status_code == 200
    from tests.unit.squad_support import project_pending_realtime

    projected = await project_pending_realtime(session_factory)
    assert projected > 0
    async with client.stream(
        "GET",
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks/{root_id}/stream",
        headers=_auth(token),
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
            if '"cancelled"' in body:
                break
    assert "event: task.status" in body
    assert '"cancelled"' in body

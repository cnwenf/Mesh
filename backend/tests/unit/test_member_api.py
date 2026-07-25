"""In-process member roster API tests (route layer: auth chain, envelopes, codes).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the member.md §3.1 endpoint surface, the §3.4 auth matrix (member read,
admin write, self display_override), error codes and the single-entry roster
projections. member.md §3 / §5.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.member import Member

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-member-test-signing-secret-00000",
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
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _me_id(client, token: str) -> str:
    resp = await client.get("/api/v1/me", headers=_auth(token))
    return resp.json()["data"]["id"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token, ws_id, email, role="member") -> str:
    """Invite + accept; returns the new member's id."""
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    return accepted.json()["data"]["member"]["id"], joiner


# --- roster query ---------------------------------------------------------------


async def test_list_members_requires_membership(client):
    owner = await _register_and_login(client, "owner-l@corp.com")
    ws = await _create_workspace(client, owner, "mem-l")
    outsider = await _register_and_login(client, "out-l@corp.com")

    ok = await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    assert ok.status_code == 200
    assert len(ok.json()["data"]) == 1  # owner only

    # A non-member gets the same 404 as an unknown workspace (§5.3 no leak).
    denied = await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(outsider))
    assert denied.status_code == 404


async def test_list_and_agent_projection(client, session_factory):
    owner = await _register_and_login(client, "owner-p@corp.com")
    ws = await _create_workspace(client, owner, "mem-p")
    member_id, _joiner = await _invite_accept(client, owner, ws["id"], "joiner-p@corp.com")
    # Insert an agent roster row directly (agents table is deferred).
    async with session_factory() as session, session.begin():
        session.add(
            Member(
                workspace_id=uuid.UUID(ws["id"]),
                member_type="agent",
                agent_id=uuid.uuid4(),
                role="member",
            )
        )

    all_resp = await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    assert all_resp.status_code == 200
    all_types = {m["member_type"] for m in all_resp.json()["data"]}
    assert all_types == {"human", "agent"}

    agent_resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members?member_type=agent", headers=_auth(owner)
    )
    assert agent_resp.status_code == 200
    agent_rows = agent_resp.json()["data"]
    assert len(agent_rows) == 1
    assert agent_rows[0]["member_type"] == "agent"
    assert member_id  # the human member is excluded from the agent projection


async def test_get_member_detail_and_not_found(client):
    owner = await _register_and_login(client, "owner-d@corp.com")
    ws = await _create_workspace(client, owner, "mem-d")
    member_id, _ = await _invite_accept(client, owner, ws["id"], "joiner-d@corp.com")

    detail = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}", headers=_auth(owner)
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["counts"] == {"open_issues_assigned": 0}

    missing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/{uuid.uuid4()}", headers=_auth(owner)
    )
    assert missing.status_code == 404
    bad = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/not-a-uuid", headers=_auth(owner)
    )
    assert bad.status_code == 404


# --- add member -----------------------------------------------------------------


async def test_add_human_member(client):
    owner = await _register_and_login(client, "owner-a@corp.com")
    ws = await _create_workspace(client, owner, "mem-a")
    third = await _register_and_login(client, "third-a@corp.com")
    third_id = await _me_id(client, third)

    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "human", "user_id": third_id, "role": "member"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["role"] == "member"

    # Duplicate → 409 already_member
    dup = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "human", "user_id": third_id},
        headers=_auth(owner),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "already_member"


async def test_add_agent_not_available(client):
    owner = await _register_and_login(client, "owner-ag@corp.com")
    ws = await _create_workspace(client, owner, "mem-ag")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "agent", "agent_id": str(uuid.uuid4())},
        headers=_auth(owner),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "agents_not_available"


async def test_add_requires_admin(client):
    owner = await _register_and_login(client, "owner-adm@corp.com")
    ws = await _create_workspace(client, owner, "mem-adm")
    member_id, joiner = await _invite_accept(client, owner, ws["id"], "plain-adm@corp.com")
    third_id = await _me_id(client, await _register_and_login(client, "t-adm@corp.com"))
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "human", "user_id": third_id},
        headers=_auth(joiner),
    )
    assert resp.status_code == 403


# --- update member --------------------------------------------------------------


async def test_patch_self_display_override(client):
    owner = await _register_and_login(client, "owner-sd@corp.com")
    ws = await _create_workspace(client, owner, "mem-sd")
    member_id, joiner = await _invite_accept(client, owner, ws["id"], "plain-sd@corp.com")

    resp = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"display_override": "小李"},
        headers=_auth(joiner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["display_name"] == "小李"


async def test_patch_role_non_admin_forbidden(client):
    owner = await _register_and_login(client, "owner-rf@corp.com")
    ws = await _create_workspace(client, owner, "mem-rf")
    member_id, joiner = await _invite_accept(client, owner, ws["id"], "plain-rf@corp.com")
    resp = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(joiner),
    )
    assert resp.status_code == 403


async def test_patch_role_and_status_by_admin(client):
    owner = await _register_and_login(client, "owner-rs@corp.com")
    ws = await _create_workspace(client, owner, "mem-rs")
    member_id, _joiner = await _invite_accept(client, owner, ws["id"], "plain-rs@corp.com")

    role = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(owner),
    )
    assert role.status_code == 200
    assert role.json()["data"]["role"] == "admin"

    disabled = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"

    removed = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"status": "removed"},
        headers=_auth(owner),
    )
    assert removed.status_code == 400  # removal is DELETE-only


# --- remove member --------------------------------------------------------------


async def test_remove_member_with_reassign(client):
    owner = await _register_and_login(client, "owner-rm@corp.com")
    ws = await _create_workspace(client, owner, "mem-rm")
    member_id, _joiner = await _invite_accept(client, owner, ws["id"], "plain-rm@corp.com")
    # find owner's member id (a valid reassign target)
    roster = (await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))).json()["data"]
    owner_member = next(m for m in roster if m["role"] == "owner")["id"]

    bad = await client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}?reassign_to={uuid.uuid4()}",
        headers=_auth(owner),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "reassign_target_invalid"

    ok = await client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}?reassign_to={owner_member}",
        headers=_auth(owner),
    )
    assert ok.status_code == 200
    assert ok.json()["data"] == {"removed": True, "reassigned_issues": 0}


async def test_remove_last_owner_conflict(client):
    owner = await _register_and_login(client, "owner-lo@corp.com")
    ws = await _create_workspace(client, owner, "mem-lo")
    roster = (await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))).json()["data"]
    owner_member = next(m for m in roster if m["role"] == "owner")["id"]
    resp = await client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{owner_member}", headers=_auth(owner)
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_owner"


# --- reassign endpoint + available agents --------------------------------------


async def test_reassign_endpoint(client):
    owner = await _register_and_login(client, "owner-re@corp.com")
    ws = await _create_workspace(client, owner, "mem-re")
    member_id, _joiner = await _invite_accept(client, owner, ws["id"], "plain-re@corp.com")
    roster = (await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))).json()["data"]
    owner_member = next(m for m in roster if m["role"] == "owner")["id"]
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members/reassign",
        json={"from_member_id": member_id, "to_member_id": owner_member},
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"reassigned_issues": 0}


async def test_available_agents_empty_and_admin_only(client):
    owner = await _register_and_login(client, "owner-av@corp.com")
    ws = await _create_workspace(client, owner, "mem-av")
    member_id, joiner = await _invite_accept(client, owner, ws["id"], "plain-av@corp.com")
    ok = await client.get(f"/api/v1/workspaces/{ws['id']}/agents/available", headers=_auth(owner))
    assert ok.status_code == 200
    assert ok.json()["data"] == []
    denied = await client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/available", headers=_auth(joiner)
    )
    assert denied.status_code == 403


# --- project access (guest) -----------------------------------------------------


async def test_project_access_flow(client):
    owner = await _register_and_login(client, "owner-pa@corp.com")
    ws = await _create_workspace(client, owner, "mem-pa")
    guest_id, _guest = await _invite_accept(client, owner, ws["id"], "guest-pa@corp.com", role="guest")
    project_id = str(uuid.uuid4())

    granted = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members/{guest_id}/project-access",
        json={"project_id": project_id, "permission": "read"},
        headers=_auth(owner),
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["data"]["permission"] == "read"

    listed = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/{guest_id}/project-access", headers=_auth(owner)
    )
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    revoked = await client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{guest_id}/project-access/{project_id}",
        headers=_auth(owner),
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"] == {"revoked": True}


async def test_project_access_non_guest_rejected(client):
    owner = await _register_and_login(client, "owner-pg@corp.com")
    ws = await _create_workspace(client, owner, "mem-pg")
    member_id, _joiner = await _invite_accept(client, owner, ws["id"], "plain-pg@corp.com")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}/project-access",
        json={"project_id": str(uuid.uuid4()), "permission": "read"},
        headers=_auth(owner),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_guest_member"


# --- /users/me ------------------------------------------------------------------


async def test_users_me_includes_memberships(client):
    owner = await _register_and_login(client, "owner-me@corp.com")
    ws = await _create_workspace(client, owner, "mem-me")
    resp = await client.get("/api/v1/users/me", headers=_auth(owner))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user"]["email"] == "owner-me@corp.com"
    assert [str(m["workspace_id"]) for m in data["memberships"]] == [str(ws["id"])]
    assert data["memberships"][0]["role"] == "owner"


async def test_rate_limit_headers_on_member_write(client):
    owner = await _register_and_login(client, "owner-rl@corp.com")
    ws = await _create_workspace(client, owner, "mem-rl")
    third_id = await _me_id(client, await _register_and_login(client, "t-rl@corp.com"))
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "human", "user_id": third_id},
        headers=_auth(owner),
    )
    assert resp.headers.get("X-RateLimit-Limit") == "120"

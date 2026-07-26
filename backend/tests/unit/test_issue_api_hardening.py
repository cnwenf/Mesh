"""MES-54 route-level hardening: M-1 (move version) and M-4 (byte caps).

Runs the real create_app() via ASGITransport against real PostgreSQL +
Redis (same shape as test_issue_api.py) and asserts the §6.14 envelopes
clients actually receive: 422 ``move_version_required`` for a confirmed
move without ``version``, and 422 ``field_too_large`` for oversize
long-text / JSON bodies.
"""

from __future__ import annotations

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.config import load_settings

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-hardening-test-signing-secret-0",
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
    resp = await client.post("/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token))
    return resp.json()["data"]


async def _create_project(client, token, ws_id, key, **fields) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"P {key}", "key": key, **fields},
        headers=_auth(token),
    )
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, **fields) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": "hardening", **fields},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# M-1: confirmed move without version → 422 move_version_required
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_move_confirmed_without_version_422_move_version_required(client):
    owner = await _register_and_login(client, "m1-move@corp.com")
    ws = await _create_workspace(client, owner, "m1-move")
    source = await _create_project(client, owner, ws["id"], "M1S")
    target = await _create_project(client, owner, ws["id"], "M1T")
    issue = await _create_issue(client, owner, ws["id"], project_id=source["id"])

    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"], "confirm": True},
        headers=_auth(owner),
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "move_version_required"
    assert error["details"]["field"] == "version"

    # stale version → 409 conflict (the OCC the field protects)
    stale = await client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"], "confirm": True, "version": 999},
        headers=_auth(owner),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"

    # current version → 200, version+1
    ok = await client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={
            "target_project_id": target["id"],
            "confirm": True,
            "version": issue["version"],
        },
        headers=_auth(owner),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["version"] == issue["version"] + 1


@pytest.mark.unit
async def test_move_unconfirmed_without_version_still_returns_preview(client):
    # The §3.8 fallback contract (auth-first 422 preview) must survive the
    # schema-tightening: confirm defaulted away stays version-free.
    owner = await _register_and_login(client, "m1-preview@corp.com")
    ws = await _create_workspace(client, owner, "m1-preview")
    source = await _create_project(client, owner, ws["id"], "M1P")
    target = await _create_project(client, owner, ws["id"], "M1Q")
    issue = await _create_issue(client, owner, ws["id"], project_id=source["id"])

    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"]},
        headers=_auth(owner),
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "move_confirmation_required"
    assert error["details"]["preview"]["version"] == issue["version"]


# ---------------------------------------------------------------------------
# M-4: long-text / JSON byte caps → 422 field_too_large
# ---------------------------------------------------------------------------

_LIMIT = 1_048_576


@pytest.mark.unit
async def test_create_issue_oversize_description_422_field_too_large(client):
    owner = await _register_and_login(client, "m4-issue@corp.com")
    ws = await _create_workspace(client, owner, "m4-issue")

    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "too long", "description": "x" * (_LIMIT + 1)},
        headers=_auth(owner),
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "field_too_large"
    assert error["details"] == {"field": "description", "max_bytes": _LIMIT}

    # at the limit → 201
    ok = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "at limit", "description": "x" * _LIMIT},
        headers=_auth(owner),
    )
    assert ok.status_code == 201, ok.text


@pytest.mark.unit
async def test_update_issue_oversize_description_422_field_too_large(client):
    owner = await _register_and_login(client, "m4-patch@corp.com")
    ws = await _create_workspace(client, owner, "m4-patch")
    issue = await _create_issue(client, owner, ws["id"])

    resp = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"description": "y" * (_LIMIT + 1)},
        headers=_auth(owner),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "field_too_large"


@pytest.mark.unit
async def test_template_oversize_body_and_description_422(client):
    owner = await _register_and_login(client, "m4-template@corp.com")
    ws = await _create_workspace(client, owner, "m4-template")

    oversize_body = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issue-templates",
        json={"name": "big body", "template_body": {"blob": "z" * _LIMIT}},
        headers=_auth(owner),
    )
    assert oversize_body.status_code == 422, oversize_body.text
    error = oversize_body.json()["error"]
    assert error["code"] == "field_too_large"
    assert error["details"]["field"] == "template_body"

    oversize_description = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issue-templates",
        json={"name": "big desc", "description": "d" * (_LIMIT + 1)},
        headers=_auth(owner),
    )
    assert oversize_description.status_code == 422
    assert oversize_description.json()["error"]["details"]["field"] == "description"

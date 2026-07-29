"""Device-code HTTP routes (auth.md §3.1.1 / §3.6 / cli.md §3.2).

In-process real app: full code → approve → poll exchange over HTTP, the four
polling error branches as named envelope codes, the dual-dimension poll
throttle (slow_down with Retry-After), and confirmation-page access control.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.config import load_settings

pytestmark = pytest.mark.unit

EMAIL = "devroute@corp.com"
PASSWORD = "a-strong-passw0rd"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="device-routes-test-signing-secret",
        device_code_pepper="device-routes-test-pepper-0123456789",
        session_cookie_secure=False,
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


async def _web_login(client, email=EMAIL) -> tuple[str, str]:
    """Register + login; returns (access_token, sid-bound access)."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "DR"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return resp.json()["data"]["access_token"]


async def _workspace(client, access) -> dict:
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "DR WS", "slug": f"dr-{uuid.uuid4().hex[:8]}"},
        headers=_auth(access),
    )
    return resp.json()["data"]


async def _issue(client) -> dict:
    resp = await client.post(
        "/api/v1/auth/device/code",
        json={"client_id": "mesh-cli", "scope": "issue:read issue:write"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestCodeIssuance:
    async def test_code_response_shape(self, client):
        data = await _issue(client)
        assert data["device_code"]
        assert "-" in data["user_code"]
        assert data["verification_uri"] == "/device"
        assert data["user_code"] in data["verification_uri_complete"]
        assert data["expires_in"] == 900
        assert data["interval"] == 5
        assert "_id" not in data  # internal key never leaks

    async def test_code_ip_rate_limit(self, client):
        for _ in range(10):
            ok = await client.post(
                "/api/v1/auth/device/code", json={"client_id": "mesh-cli"}
            )
            assert ok.status_code == 200
        over = await client.post("/api/v1/auth/device/code", json={"client_id": "mesh-cli"})
        assert over.status_code == 429
        assert over.json()["error"]["code"] == "rate_limited"


class TestPollingBranches:
    async def test_pending_then_approved_exchange(self, client):
        access = await _web_login(client)
        ws = await _workspace(client, access)
        issued = await _issue(client)

        # Pending → 400 authorization_pending (named code the CLI branches on).
        pending = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": GRANT_TYPE, "device_code": issued["device_code"]},
        )
        assert pending.status_code == 400
        assert pending.json()["error"]["code"] == "authorization_pending"

        # Approve via the web session.
        ap = await client.post(
            "/api/v1/auth/device/approve",
            json={"user_code": issued["user_code"], "workspace_id": ws["id"]},
            headers=_auth(access),
        )
        assert ap.status_code == 200, ap.text
        assert ap.json()["data"]["status"] == "approved"
        # Server-enforced intersection echoed (member role: both read+write).
        assert ap.json()["data"]["granted_scopes"] == ["issue:read", "issue:write"]

        # Exchange (respecting the interval would be polite; the limiter window
        # already elapsed one hit above — flush is per-test, and the per-code
        # window is 5s, so wait-free re-poll hits slow_down first; assert the
        # slow_down contract, then the success on a fresh code path).
        fast = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": GRANT_TYPE, "device_code": issued["device_code"]},
        )
        assert fast.status_code == 429
        assert fast.json()["error"]["code"] == "slow_down"
        assert "Retry-After" in fast.headers

    async def test_wrong_grant_type_400(self, client):
        issued = await _issue(client)
        resp = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": "password", "device_code": issued["device_code"]},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"

    async def test_unknown_device_code_invalid_grant(self, client):
        resp = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": GRANT_TYPE, "device_code": "mesh-never-issued"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_grant"

    async def test_denied_maps_to_access_denied(self, client):
        access = await _web_login(client)
        await _workspace(client, access)
        issued = await _issue(client)
        dn = await client.post(
            "/api/v1/auth/device/deny",
            json={"user_code": issued["user_code"]},
            headers=_auth(access),
        )
        assert dn.status_code == 200
        assert dn.json()["data"]["status"] == "denied"
        denied = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": GRANT_TYPE, "device_code": issued["device_code"]},
        )
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "access_denied"


class TestConfirmationPage:
    async def test_confirm_data_requires_auth(self, client):
        issued = await _issue(client)
        resp = await client.get("/api/v1/auth/device", params={"user_code": issued["user_code"]})
        assert resp.status_code == 401

    async def test_confirm_data_shape_and_404_uniform(self, client):
        access = await _web_login(client)
        ws = await _workspace(client, access)
        issued = await _issue(client)
        ok = await client.get(
            "/api/v1/auth/device",
            params={"user_code": issued["user_code"]},
            headers=_auth(access),
        )
        assert ok.status_code == 200
        data = ok.json()["data"]
        assert data["client_name"] == "Mesh CLI"
        assert [s["scope"] for s in data["requested_scopes"]] == ["issue:read", "issue:write"]
        assert any(w["id"] == ws["id"] for w in data["workspaces"])

        # Unknown code → uniform 404 (no state oracle).
        miss = await client.get(
            "/api/v1/auth/device", params={"user_code": "ZZZZ-ZZZZ"}, headers=_auth(access)
        )
        assert miss.status_code == 404

    async def test_approve_requires_workspace_membership(self, client):
        access = await _web_login(client)
        issued = await _issue(client)
        foreign_ws = uuid.uuid4()
        resp = await client.post(
            "/api/v1/auth/device/approve",
            json={"user_code": issued["user_code"], "workspace_id": str(foreign_ws)},
            headers=_auth(access),
        )
        assert resp.status_code == 403


class TestFullExchangeOverHttp:
    async def test_exchange_returns_bound_session_credentials(self, client):
        """End-to-end over HTTP: issue → approve → single poll → cli
        credentials carrying the approved workspace binding (cli.md §4.2)."""
        access = await _web_login(client)
        ws = await _workspace(client, access)
        issued = await _issue(client)
        await client.post(
            "/api/v1/auth/device/approve",
            json={"user_code": issued["user_code"], "workspace_id": ws["id"]},
            headers=_auth(access),
        )
        # Exactly one poll for this device_code — inside the 1/interval quota.
        ok = await client.post(
            "/api/v1/auth/device/token",
            json={"grant_type": GRANT_TYPE, "device_code": issued["device_code"]},
        )
        assert ok.status_code == 200, ok.text
        data = ok.json()["data"]
        assert data["token_type"] == "Bearer"
        assert data["refresh_token"].startswith("mesh_rft_")
        assert data["workspace"]["id"] == ws["id"]
        assert data["workspace"]["slug"] == ws["slug"]
        assert set(data["scope"].split()) == {"issue:read", "issue:write"}
        # The cli access token authenticates a regular route (unified Bearer).
        me = await client.get("/api/v1/me", headers=_auth(data["access_token"]))
        assert me.status_code == 200

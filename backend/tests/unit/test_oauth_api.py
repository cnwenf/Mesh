"""In-process OAuth route tests (auth.md §3.1).

Runs the real ``create_app()`` via ASGITransport (dev mode → mock provider
registered) so the route layer (302 start, callback, identities, bind/unbind,
error paths) is coverage-measured in-process, complementing the subprocess e2e.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.auth.oauth import encode_mock_code
from mesh.config import load_settings

JWT_SECRET = "inprocess-oauth-test-secret"
PASSWORD = "a-strong-passw0rd"
CALLBACK = "http://api.test/api/v1/auth/oauth/mock/callback"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret=JWT_SECRET,
        # M1: the dev mock provider only accepts this exact callback redirect URI.
        oauth_mock_redirect_uris=CALLBACK,
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t", follow_redirects=False
    ) as c:
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


def _state(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "U"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _login_via_oauth(client, *, sub: str, email: str) -> dict:
    start = await client.get(
        "/api/v1/auth/oauth/mock/start", params={"redirect_uri": CALLBACK}
    )
    assert start.status_code == 302
    state = _state(start.headers["location"])
    # H1: auto-register/link by email requires a provider-verified email.
    code = encode_mock_code(sub=sub, email=email, email_verified=True)
    cb = await client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": state}
    )
    assert cb.status_code == 200
    return cb.json()["data"]


async def test_start_302_and_callback_issues_tokens(client):
    data = await _login_via_oauth(client, sub="s1", email="u1@corp.com")
    assert data["access_token"]
    me = await client.get("/api/v1/me", headers=_auth(data["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "u1@corp.com"


async def test_start_missing_redirect_uri_400(client):
    resp = await client.get("/api/v1/auth/oauth/mock/start")
    assert resp.status_code == 400


async def test_unknown_provider_404(client):
    resp = await client.get(
        "/api/v1/auth/oauth/nope/start", params={"redirect_uri": CALLBACK}
    )
    assert resp.status_code == 404


async def test_callback_missing_code_or_state_400(client):
    resp = await client.get("/api/v1/auth/oauth/mock/callback")
    assert resp.status_code == 400


async def test_callback_invalid_state_400(client):
    code = encode_mock_code(sub="s", email="e@corp.com")
    resp = await client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": "bad"}
    )
    assert resp.status_code == 400


async def test_bind_flow_and_identities_and_unbind(client):
    token = await _register_and_login(client, "binder@corp.com")
    start = await client.get(
        "/api/v1/auth/oauth/mock/bind",
        params={"redirect_uri": CALLBACK},
        headers=_auth(token),
    )
    assert start.status_code == 302
    state = _state(start.headers["location"])
    code = encode_mock_code(sub="bind-sub", email="b@corp.com", email_verified=True)
    cb = await client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": state}
    )
    assert cb.json()["data"]["status"] == "bound"

    ids = await client.get("/api/v1/auth/oauth/identities", headers=_auth(token))
    assert [i["provider"] for i in ids.json()["data"]] == ["mock"]

    unbind = await client.delete("/api/v1/auth/oauth/mock", headers=_auth(token))
    assert unbind.status_code == 200


async def test_bind_requires_auth(client):
    resp = await client.get(
        "/api/v1/auth/oauth/mock/bind", params={"redirect_uri": CALLBACK}
    )
    assert resp.status_code == 401


async def test_identities_requires_auth(client):
    resp = await client.get("/api/v1/auth/oauth/identities")
    assert resp.status_code == 401


async def test_unbind_last_method_422(client):
    data = await _login_via_oauth(client, sub="only-sub", email="only@corp.com")
    resp = await client.delete(
        "/api/v1/auth/oauth/mock", headers=_auth(data["access_token"])
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "last_login_method"

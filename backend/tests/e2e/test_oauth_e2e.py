"""REAL end-to-end OAuth round-trip (auth.md §1.2 A5/A6, §4.5).

Real uvicorn subprocess + real PG/Redis. The dev-mode in-process ``mock``
provider stands in for a third-party vendor, so the full authorization-code +
PKCE flow (start → 302 → callback → tokens) is exercised over HTTP with no
external service and no vendor coupling.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from mesh.auth.oauth import encode_mock_code

PASSWORD = "a-strong-passw0rd"
CALLBACK = "http://api.test/api/v1/auth/oauth/mock/callback"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _state_from_location(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


async def _register_and_login(client, email: str, name: str = "E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _oauth_login(client, *, sub: str, email: str, name: str = "OAuth User") -> dict:
    """Drive the full mock round-trip; returns the callback ``data`` payload."""
    start = await client.get(
        "/api/v1/auth/oauth/mock/start", params={"redirect_uri": CALLBACK}
    )
    assert start.status_code == 302, start.text
    state = _state_from_location(start.headers["location"])
    # H1: the provider must have verified the email for auto-register/link.
    code = encode_mock_code(sub=sub, email=email, name=name, email_verified=True)
    cb = await client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": state}
    )
    assert cb.status_code == 200, cb.text
    return cb.json()["data"]


async def test_oauth_login_round_trip_issues_working_tokens(api_client):
    data = await _oauth_login(api_client, sub="e2e-sub-1", email="oauth1@corp.com")
    assert data["access_token"] and data["refresh_token"]
    # The minted access token authenticates and resolves the auto-registered user.
    me = await api_client.get("/api/v1/me", headers=_auth(data["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "oauth1@corp.com"
    assert me.json()["data"]["email_verified"] is True


async def test_oauth_second_login_reuses_account(api_client):
    first = await _oauth_login(api_client, sub="e2e-sub-2", email="oauth2@corp.com")
    second = await _oauth_login(api_client, sub="e2e-sub-2", email="oauth2@corp.com")
    me1 = await api_client.get("/api/v1/me", headers=_auth(first["access_token"]))
    me2 = await api_client.get("/api/v1/me", headers=_auth(second["access_token"]))
    assert me1.json()["data"]["id"] == me2.json()["data"]["id"]


async def test_oauth_bind_and_unbind_with_password_account(api_client):
    token = await _register_and_login(api_client, "binder@corp.com")
    # Bind start (authenticated) → 302 → callback in bind mode.
    start = await api_client.get(
        "/api/v1/auth/oauth/mock/bind",
        params={"redirect_uri": CALLBACK},
        headers=_auth(token),
    )
    assert start.status_code == 302
    state = _state_from_location(start.headers["location"])
    code = encode_mock_code(sub="e2e-bind", email="binder-oauth@corp.com")
    cb = await api_client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": state}
    )
    assert cb.status_code == 200
    assert cb.json()["data"]["status"] == "bound"

    identities = await api_client.get(
        "/api/v1/auth/oauth/identities", headers=_auth(token)
    )
    assert [i["provider"] for i in identities.json()["data"]] == ["mock"]

    # Unbind OK — the password remains as a login method.
    unbind = await api_client.delete("/api/v1/auth/oauth/mock", headers=_auth(token))
    assert unbind.status_code == 200
    after = await api_client.get("/api/v1/auth/oauth/identities", headers=_auth(token))
    assert after.json()["data"] == []


async def test_oauth_unbind_last_method_refused(api_client):
    # OAuth-only account: the binding is the sole login method.
    data = await _oauth_login(api_client, sub="e2e-only", email="only@corp.com")
    token = data["access_token"]
    unbind = await api_client.delete("/api/v1/auth/oauth/mock", headers=_auth(token))
    assert unbind.status_code == 422
    assert unbind.json()["error"]["code"] == "last_login_method"


async def test_oauth_unknown_provider_404(api_client):
    resp = await api_client.get(
        "/api/v1/auth/oauth/nope/start", params={"redirect_uri": CALLBACK}
    )
    assert resp.status_code == 404


async def test_oauth_callback_bad_state_400(api_client):
    code = encode_mock_code(sub="x", email="x@corp.com")
    resp = await api_client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": "bogus"}
    )
    assert resp.status_code == 400


async def test_oauth_start_requires_redirect_uri(api_client):
    resp = await api_client.get("/api/v1/auth/oauth/mock/start")
    assert resp.status_code == 400


async def test_unverified_email_cannot_take_over_account(api_client):
    """H1: an unverified email must not link to / register an account."""
    # Register a password account first.
    await api_client.post(
        "/api/v1/auth/register",
        json={"email": "victim@corp.com", "password": "a-strong-passw0rd", "display_name": "V"},
    )
    start = await api_client.get(
        "/api/v1/auth/oauth/mock/start", params={"redirect_uri": CALLBACK}
    )
    state = _state_from_location(start.headers["location"])
    # Attacker holds an UNVERIFIED victim@corp.com on the provider.
    code = encode_mock_code(sub="attacker", email="victim@corp.com", email_verified=False)
    cb = await api_client.get(
        "/api/v1/auth/oauth/mock/callback", params={"code": code, "state": state}
    )
    assert cb.status_code == 422
    assert cb.json()["error"]["code"] == "oauth_email_not_verified"


async def test_redirect_uri_outside_allowlist_rejected(api_client):
    """M1: a redirect_uri outside the exact-match allowlist is rejected (422)."""
    resp = await api_client.get(
        "/api/v1/auth/oauth/mock/start", params={"redirect_uri": "http://evil.example/cb"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "redirect_uri_not_allowed"

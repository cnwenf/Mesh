"""In-process auth API tests (route layer: validation, envelope, error mapping).

Complements the subprocess e2e suite by running the real ``create_app()`` via
ASGITransport so route-level code is exercised (and coverage-measured)
in-process against the real PostgreSQL + Redis. auth.md §3.1–§3.3.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pyotp
import pytest
import redis.asyncio as aioredis
from sqlalchemy import update

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.user import User

EMAIL = "api@corp.com"
PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-auth-test-signing-secret",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    # The app builds its own engine/redis in create_app; dispose them (no lifespan).
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture(autouse=True)
async def _flush_redis_each_test(redis_url):
    """Reset rate-limit/dev-mailer state between in-process tests."""
    c = aioredis.from_url(redis_url, decode_responses=True)
    await c.flushdb()
    yield
    await c.flushdb()
    await c.aclose()


@pytest.fixture
async def redis(redis_url):
    c = aioredis.from_url(redis_url, decode_responses=True)
    yield c
    await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    return resp.json()["data"]


async def test_register_login_me_inprocess(client):
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    assert reg.status_code == 201
    tokens = await _login(client)
    me = await client.get("/api/v1/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["email"] == EMAIL


async def _login(client, email=EMAIL, password=PASSWORD):
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return resp.json()["data"]


async def test_update_me_validation_errors_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    tz = await client.patch("/api/v1/users/me", headers=h, json={"timezone": "Bad/Zone"})
    assert tz.status_code == 422 and tz.json()["error"]["code"] == "invalid_timezone"
    loc = await client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"locale": "de-DE"}}
    )
    assert loc.status_code == 422 and loc.json()["error"]["code"] == "unsupported_locale"
    theme = await client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"theme": "neon"}}
    )
    # auth.md §3.1/§5.1 + README §9 T32: invalid theme → 422 validation_error.
    assert theme.status_code == 422
    assert theme.json()["error"]["code"] == "validation_error"
    unknown = await client.patch("/api/v1/users/me", headers=h, json={"nope": 1})
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "validation_error"


async def test_update_me_explicit_null_clears_settings_key_inprocess(client):
    """Explicit null in settings.locale/theme pops the key (MES-24 清除语义)."""
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])

    # Set locale and theme.
    set_resp = await client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"locale": "zh-CN", "theme": "dark"}}
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["data"]["settings"] == {"locale": "zh-CN", "theme": "dark"}

    # Clear locale with explicit null → key popped, theme preserved.
    clear_locale = await client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"locale": None}}
    )
    assert clear_locale.status_code == 200
    after = clear_locale.json()["data"]["settings"]
    assert "locale" not in after
    assert after["theme"] == "dark"

    # Clear theme with explicit null → both keys gone.
    clear_theme = await client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"theme": None}}
    )
    assert clear_theme.status_code == 200
    assert clear_theme.json()["data"]["settings"] == {}


async def test_sessions_and_logout_all_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    await _login(client)  # second session
    sessions = await client.get("/api/v1/sessions", headers=h)
    rows = sessions.json()["data"]
    assert len(rows) == 2
    bad_id = await client.delete("/api/v1/sessions/not-a-uuid", headers=h)
    assert bad_id.status_code == 400
    deleted = await client.delete(f"/api/v1/sessions/{rows[0]['id']}", headers=h)
    assert deleted.status_code == 200  # idempotent: revoking again is still ok
    missing = await client.delete(f"/api/v1/sessions/{uuid.uuid4()}", headers=h)
    assert missing.status_code == 404
    lo = await client.post("/api/v1/auth/logout-all", headers=h)
    assert lo.status_code == 200


async def test_reset_and_verify_inprocess(client, redis):
    tokens = await _register_and_login(client)
    forgot = await client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    assert forgot.status_code == 200
    reset_token = await redis.get(f"mesh:devmail:password_reset:{EMAIL}")
    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "a-new-passw0rd"},
    )
    assert reset.status_code == 200
    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401
    # invalid reset token
    bad = await client.post(
        "/api/v1/auth/reset-password", json={"token": "x", "new_password": "a-new-passw0rd"}
    )
    assert bad.status_code == 401


async def test_logout_specific_and_refresh_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    lo = await client.post(
        "/api/v1/auth/logout", headers=h, json={"refresh_token": tokens["refresh_token"]}
    )
    assert lo.status_code == 200
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "bogus"}
    )
    assert refreshed.status_code == 401


async def test_mfa_flow_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    setup = await client.post("/api/v1/auth/mfa/setup", headers=h)
    secret = setup.json()["data"]["secret"]
    # enable with a wrong code → 422
    bad = await client.post("/api/v1/auth/mfa/enable", headers=h, json={"code": "000000"})
    assert bad.status_code == 422
    ok = await client.post(
        "/api/v1/auth/mfa/enable", headers=h, json={"code": pyotp.TOTP(secret).now()}
    )
    assert ok.status_code == 200
    login = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    ticket = login.json()["data"]["mfa_ticket"]
    verify = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
    )
    assert verify.status_code == 200
    # disable MFA
    dis = await client.post(
        "/api/v1/auth/mfa/disable", headers=h, json={"code": pyotp.TOTP(secret).now()}
    )
    assert dis.status_code == 200
    # mfa_setup on a missing user is covered elsewhere; setup-when-not-started enable:
    not_setup = await client.post("/api/v1/auth/mfa/enable", headers=h, json={"code": "1"})
    assert not_setup.status_code == 422


async def test_verify_email_endpoint_inprocess(client, redis):
    await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    token = await redis.get(f"mesh:devmail:email_verification:{EMAIL}")
    ok = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert ok.status_code == 200
    bad = await client.post("/api/v1/auth/verify-email", json={"token": "nope"})
    assert bad.status_code == 401


async def test_me_without_token_401(client):
    resp = await client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_duplicate_register_409_inprocess(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    dup = await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    assert dup.status_code == 409


async def _set_status(app, status: str) -> None:
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(User).where(User.email == EMAIL).values(status=status)
        )


@pytest.mark.parametrize("status", ["disabled", "deleted"])
async def test_non_active_user_with_valid_jwt_is_rejected(app, client, status):
    """deps.py: a valid (unexpired, well-signed) access JWT whose owner is no
    longer an active user must be rejected with 401 — the per-request gate does
    not trust the token alone, it re-checks user status (B2 security branch)."""
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    # Sanity: the token works while the account is active.
    assert (await client.get("/api/v1/me", headers=h)).status_code == 200

    await _set_status(app, status)

    resp = await client.get("/api/v1/me", headers=h)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


# Direct dependency-level tests (exercise get_current_user in-process so the
# non-active rejection branch is coverage-measured, not just ASGI-observable).


async def _make_user(app, *, status: str) -> tuple:
    from mesh.auth import jwt as jwt_mod
    from mesh.auth.security import hash_password

    uid = uuid.uuid4()
    async with app.state.session_factory() as session, session.begin():
        session.add(
            User(
                id=uid,
                email=f"{status}@corp.com",
                display_name="G",
                password_hash=hash_password("a-strong-passw0rd"),
                status=status,
            )
        )
    token, _ = jwt_mod.encode_access_token(
        subject=uid,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
        ttl=timedelta(minutes=5),
    )
    return uid, token


def _request_for(app, token: str):
    from types import SimpleNamespace

    return SimpleNamespace(app=app, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize("status", ["disabled", "deleted"])
async def test_get_current_user_rejects_non_active_directly(app, status):
    from mesh.auth.deps import get_current_user
    from mesh.errors import UnauthorizedError

    _uid, token = await _make_user(app, status=status)
    async with app.state.session_factory() as session:
        with pytest.raises(UnauthorizedError):
            await get_current_user(_request_for(app, token), session=session)


async def test_get_current_user_returns_active_user_directly(app):
    from mesh.auth.deps import get_current_user

    uid, token = await _make_user(app, status="active")
    async with app.state.session_factory() as session:
        user = await get_current_user(_request_for(app, token), session=session)
    assert user.id == uid


# --- change password (auth.md §3.1/§4.2: 已登录态修改密码) ---------------------


async def test_change_password_success_keeps_current_session_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    other = await _login(client)  # a second session (other device)

    changed = await client.post(
        "/api/v1/auth/change-password",
        headers=h,
        json={
            "old_password": PASSWORD,
            "new_password": "a-new-passw0rd",
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["status"] == "ok"

    # The presenting session survives; the other session's refresh is dead.
    # (Alive-first: presenting a revoked token triggers replay detection,
    # which revokes the whole family — so the dead check goes last.)
    alive = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert alive.status_code == 200
    dead = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": other["refresh_token"]}
    )
    assert dead.status_code == 401

    # Old password rejected with the uniform named code; the new one logs in.
    old = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert old.status_code == 422
    assert old.json()["error"]["code"] == "invalid_credentials"
    new = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "a-new-passw0rd"}
    )
    assert new.status_code == 200


async def test_change_password_wrong_old_422_inprocess(client):
    tokens = await _register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers=_auth(tokens["access_token"]),
        json={"old_password": "wrong-pass-1", "new_password": "a-new-passw0rd"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_change_password_weak_new_400_three_reasons_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    cases = [
        ("short1", "too_short"),
        ("lettersonlyx", "needs_letter_and_digit"),
        ("password123", "too_common"),
    ]
    for weak, reason in cases:
        resp = await client.post(
            "/api/v1/auth/change-password",
            headers=h,
            json={"old_password": PASSWORD, "new_password": weak},
        )
        assert resp.status_code == 400
        error = resp.json()["error"]
        assert error["code"] == "weak_password"
        assert error["details"]["reason"] == reason


async def test_change_password_requires_auth_401_inprocess(client):
    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "a-new-passw0rd"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_change_password_rate_limited_on_ip_email_inprocess(client):
    """§3.6: the password-verifying endpoint shares the login-class throttle."""
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    for _ in range(5):  # CHANGE_PASSWORD_LIMIT = 5/min on (IP, email)
        wrong = await client.post(
            "/api/v1/auth/change-password",
            headers=h,
            json={"old_password": "wrong-pass-1", "new_password": "a-new-passw0rd"},
        )
        assert wrong.status_code == 422  # wrong old password, not throttled yet
    sixth = await client.post(
        "/api/v1/auth/change-password",
        headers=h,
        json={"old_password": PASSWORD, "new_password": "a-new-passw0rd"},
    )
    assert sixth.status_code == 429
    assert sixth.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in sixth.headers


# --- C6: register/reset rate limiting is keyed on the (IP, email) tuple ------
# (auth.md §3.6, consistent with the login lockout dimension). Regression guard:
# exhausting one email's bucket must NOT rate-limit a different email from the
# same IP — which is exactly what pure-IP keying would do.


async def _register(client, email: str):
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "RL"},
    )


async def test_register_rate_limit_is_per_ip_email_tuple(client):
    a, b = "rl-a@corp.com", "rl-b@corp.com"
    for _ in range(5):  # REGISTER_LIMIT = 5
        await _register(client, a)
    # 6th register of A → 429 (bucket exhausted).
    assert (await _register(client, a)).status_code == 429
    # A different email from the SAME IP is a distinct (IP,email) bucket → not 429.
    other = await _register(client, b)
    assert other.status_code != 429
    assert other.status_code == 201


async def test_reset_rate_limit_is_per_ip_email_tuple(client):
    a, b = "rl-reset-a@corp.com", "rl-reset-b@corp.com"
    for _ in range(5):
        r = await client.post("/api/v1/auth/forgot-password", json={"email": a})
        assert r.status_code == 200  # anti-enumeration: always ok until limited
    # 6th reset of A → 429.
    assert (
        await client.post("/api/v1/auth/forgot-password", json={"email": a})
    ).status_code == 429
    # Different email, same IP → independent bucket, still 200.
    assert (
        await client.post("/api/v1/auth/forgot-password", json={"email": b})
    ).status_code == 200

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
        app_database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-auth-test-signing-secret",
        # Plaintext loopback test transport — the Secure attribute is relaxed
        # exactly like MESH_DAEMON_TLS_REQUIRED in e2e (production stays true).
        session_cookie_secure=False,
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


SITE = "http://t"  # matches the client fixture's base_url (Host header)
ORIGIN = {"Origin": SITE}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _refresh(client):
    """Cookie-transport refresh (R4-H1): the refresh rides the mesh_session
    cookie; the body carries nothing; Origin must be same-site."""
    return await client.post("/api/v1/auth/refresh", headers=ORIGIN)


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
    # R4-H1: refresh delivered via HttpOnly cookie, not the body.
    assert client.cookies.get("mesh_session", "").startswith("mesh_rft_")
    me = await client.get("/api/v1/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["email"] == EMAIL


async def test_login_issues_httponly_session_cookie(client):
    # auth.md §5.5 / theme.md §2.3 ①: login sets the mesh_session HttpOnly
    # cookie — the SOLE web refresh transport (R4-H1); the HTML entry reads it
    # server-side for first-frame theme injection.
    await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "API"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    set_cookies = resp.headers.get_list("set-cookie")
    session = [c for c in set_cookies if c.startswith("mesh_session=")]
    assert session, "login must Set-Cookie mesh_session"
    header = session[0].lower()
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "path=/" in header
    # dev auth_mode → secure omitted (http loopback); production would set it.
    # R4-H1: the body carries access ONLY — refresh never appears in plaintext.
    assert "refresh_token" not in resp.json()["data"]


async def test_me_returns_updated_at_for_pending_conflict_strategy(client):
    # theme.md §4.5: /me must expose updated_at so the pending-queue conflict
    # strategy can detect a newer server write.
    tokens = await _register_and_login(client)
    me = await client.get("/api/v1/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["updated_at"] is not None


async def test_logout_clears_session_cookie(client):
    # R4-H1 logout: the session is located via the presented mesh_session
    # cookie (kept in the client jar since login) — no body, no Bearer.
    await _register_and_login(client)
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    cleared = [c for c in resp.headers.get_list("set-cookie") if c.startswith("mesh_session=")]
    assert cleared and "max-age=0" in cleared[0].lower()


async def _login(client, email=EMAIL, password=PASSWORD):
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    data = resp.json()["data"]
    # R4-H1: the body NEVER carries a refresh — it rides the HttpOnly cookie.
    if "access_token" in data:
        assert "refresh_token" not in data
    return data


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
    # theme.md §3.3: invalid theme → 422 invalid_theme_mode (named code).
    assert theme.status_code == 422
    assert theme.json()["error"]["code"] == "invalid_theme_mode"
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


async def test_update_me_explicit_null_clears_avatar_inprocess(client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    set_avatar = await client.patch(
        "/api/v1/users/me", headers=h, json={"avatar_url": "https://cdn.example/avatar.png"}
    )
    assert set_avatar.status_code == 200
    renamed = await client.patch(
        "/api/v1/users/me", headers=h, json={"display_name": "Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["avatar_url"] == "https://cdn.example/avatar.png"
    cleared = await client.patch("/api/v1/users/me", headers=h, json={"avatar_url": None})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["avatar_url"] is None


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
    await _register_and_login(client)
    forgot = await client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    assert forgot.status_code == 200
    reset_token = await redis.get(f"mesh:devmail:password_reset:{EMAIL}")
    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "a-new-passw0rd"},
    )
    assert reset.status_code == 200
    # The reset revoked every session — the cookie refresh is dead.
    reused = await _refresh(client)
    assert reused.status_code == 401
    # invalid reset token
    bad = await client.post(
        "/api/v1/auth/reset-password", json={"token": "x", "new_password": "a-new-passw0rd"}
    )
    assert bad.status_code == 401


async def test_logout_specific_and_refresh_inprocess(client):
    await _register_and_login(client)
    # Cookie-transport logout: the mesh_session cookie names the session.
    lo = await client.post("/api/v1/auth/logout")
    assert lo.status_code == 200
    # The cookie was cleared; a bogus cookie refresh is 401.
    client.cookies.set("mesh_session", "mesh_rft_bogus", domain="t")
    refreshed = await _refresh(client)
    assert refreshed.status_code == 401


async def test_refresh_cross_origin_cookie_rejected_inprocess(client):
    """R4-H1 CSRF shield: a cookie refresh without a same-site Origin → 403."""
    await _register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/refresh", headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    # Missing Origin/Referer entirely — also denied (fail closed).
    resp2 = await client.post("/api/v1/auth/refresh")
    assert resp2.status_code == 403


async def test_refresh_bearer_rft_transport_inprocess(client):
    """CLI/device transport: Bearer mesh_rft_ rotates, body carries the new
    refresh (no Set-Cookie), and the rotated-out token grace-yields access."""
    tokens = await _register_and_login(client)
    refresh = client.cookies.get("mesh_session")
    client.cookies.clear()  # prove the Bearer path does not use the cookie
    winner = await client.post(
        "/api/v1/auth/refresh", headers=_auth(refresh)
    )
    assert winner.status_code == 200
    body = winner.json()["data"]
    assert body["refresh_token"].startswith("mesh_rft_")
    assert "set-cookie" not in {k.lower() for k in winner.headers.keys()}
    # Grace: the old token still yields access ONLY (never a refresh).
    grace = await client.post("/api/v1/auth/refresh", headers=_auth(refresh))
    assert grace.status_code == 200
    assert "refresh_token" not in grace.json()["data"]
    # A non-refresh Bearer on this endpoint is a protocol violation → 401.
    bad = await client.post(
        "/api/v1/auth/refresh", headers=_auth(tokens["access_token"])
    )
    assert bad.status_code == 401


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
    from mesh.auth.deps import get_current_principal, get_current_user
    from mesh.errors import UnauthorizedError

    _uid, token = await _make_user(app, status=status)
    async with app.state.session_factory() as session:
        principal = await get_current_principal(_request_for(app, token), session=session)
        with pytest.raises(UnauthorizedError):
            await get_current_user(
                _request_for(app, token), principal=principal, session=session
            )


async def test_get_current_user_returns_active_user_directly(app):
    from mesh.auth.deps import get_current_principal, get_current_user

    uid, token = await _make_user(app, status="active")
    async with app.state.session_factory() as session:
        principal = await get_current_principal(_request_for(app, token), session=session)
        user = await get_current_user(
            _request_for(app, token), principal=principal, session=session
        )
    assert user.id == uid


# --- change password (auth.md §3.1/§4.2: 已登录态修改密码) ---------------------


async def test_change_password_success_keeps_current_session_inprocess(app, client):
    tokens = await _register_and_login(client)
    h = _auth(tokens["access_token"])
    # A second session needs its own cookie jar (a second "device").
    other_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=other_transport, base_url=SITE) as other_client:
        await _login(other_client)

        changed = await client.post(
            "/api/v1/auth/change-password",
            headers=h,
            json={"old_password": PASSWORD, "new_password": "a-new-passw0rd"},
        )
        assert changed.status_code == 200
        assert changed.json()["data"]["status"] == "ok"

        # The initiating session (identified by its access JWT sid) survives.
        alive = await _refresh(client)
        assert alive.status_code == 200
        # The other device's session is revoked.
        dead = await _refresh(other_client)
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
    # 专属用户 + 每跑唯一邮箱:桶 (ip,email) 与全套其他用例及并行运行隔离,
    # 测试与顺序/共享 Redis 残留键无关(MES-60 验收 R2b)。
    uniq = uuid.uuid4().hex[:10]
    await client.post(
        "/api/v1/auth/register",
        json={"email": f"rl-change-{uniq}@corp.com", "password": PASSWORD, "display_name": "RL"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": f"rl-change-{uniq}@corp.com", "password": PASSWORD}
    )
    tokens = resp.json()["data"]
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
    _uniq = uuid.uuid4().hex[:10]
    a, b = f"rl-a-{_uniq}@corp.com", f"rl-b-{_uniq}@corp.com"
    for _ in range(5):  # REGISTER_LIMIT = 5
        await _register(client, a)
    # 6th register of A → 429 (bucket exhausted).
    assert (await _register(client, a)).status_code == 429
    # A different email from the SAME IP is a distinct (IP,email) bucket → not 429.
    other = await _register(client, b)
    assert other.status_code != 429
    assert other.status_code == 201


async def test_reset_rate_limit_is_per_ip_email_tuple(client):
    _uniq = uuid.uuid4().hex[:10]
    a, b = f"rl-reset-a-{_uniq}@corp.com", f"rl-reset-b-{_uniq}@corp.com"
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

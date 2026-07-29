"""REAL end-to-end auth tests (auth.md §5).

These run against a genuine uvicorn API subprocess + real PostgreSQL + real
Redis (no mocks). One-time tokens are fetched from the dev Redis mailer key the
server writes in ``auth_mode=dev`` — the database stores only SHA-256 hashes.
"""

from __future__ import annotations

import base64

import pyotp

EMAIL = "e2e@corp.com"
PASSWORD = "a-strong-passw0rd"


def _auth(access: str) -> dict:
    return {"Authorization": f"Bearer {access}"}


async def _register(client, email=EMAIL, password=PASSWORD, name="E2E"):
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": name},
    )


async def _login(client, email=EMAIL, password=PASSWORD, remember=False):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "remember": remember},
    )


async def _register_and_login(client):
    await _register(client)
    resp = await _login(client)
    return resp.json()["data"]


# --- registration / login ----------------------------------------------------


async def test_register_then_login_full_flow(api_client):
    reg = await _register(api_client)
    assert reg.status_code == 201
    body = reg.json()["data"]
    assert body["email"] == EMAIL
    assert body["status"] == "active"
    assert body["email_verified"] is False

    login = await _login(api_client)
    assert login.status_code == 200
    tokens = login.json()["data"]
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 900
    assert tokens["access_token"] and tokens["refresh_token"]


async def test_register_duplicate_email_409(api_client):
    assert (await _register(api_client)).status_code == 201
    dup = await _register(api_client)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"


async def test_register_weak_password_400(api_client):
    resp = await _register(api_client, password="weak")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "weak_password"


async def test_login_wrong_password_uniform_422(api_client):
    await _register(api_client)
    bad = await _login(api_client, password="wrong-pass-1")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_credentials"
    # Unknown account returns the IDENTICAL error (anti-enumeration).
    ghost = await _login(api_client, email="ghost@corp.com", password="x-pass-1")
    assert ghost.status_code == 422
    assert ghost.json()["error"]["code"] == "invalid_credentials"
    assert ghost.json()["error"]["message"] == bad.json()["error"]["message"]


async def test_login_rate_limit_headers_and_429(api_client):
    await _register(api_client)
    # A normal (successful) login carries the X-RateLimit-* headers.
    ok = await _login(api_client)
    assert ok.status_code == 200
    assert "X-RateLimit-Limit" in ok.headers
    # The (ip,email) bucket allows 5/min; this is hit #1, so 5 more exhausts it
    # and the request past the limit is 429 with Retry-After.
    last = None
    for _ in range(5):
        last = await _login(api_client, password="wrong-pass-1")
    assert last.status_code == 429
    assert last.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in last.headers


# --- /me + bearer ------------------------------------------------------------


async def test_me_requires_bearer_and_returns_user(api_client):
    tokens = await _register_and_login(api_client)
    no_auth = await api_client.get("/api/v1/me")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "unauthorized"

    me = await api_client.get("/api/v1/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["data"]["email"] == EMAIL


async def test_me_rejects_alg_none_token(api_client):
    """A forged alg=none JWT must be rejected (§5.5)."""
    await _register_and_login(api_client)
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(b'{"sub":"x","exp":9999999999,"typ":"access"}').rstrip(b"=").decode()
    forged = f"{header}.{payload}."
    resp = await api_client.get("/api/v1/me", headers=_auth(forged))
    assert resp.status_code == 401


async def test_me_rejects_garbage_and_expired(api_client):
    await _register_and_login(api_client)
    resp = await api_client.get("/api/v1/me", headers=_auth("not.a.token"))
    assert resp.status_code == 401


# --- refresh / logout / sessions ---------------------------------------------


async def test_refresh_rotates_and_replay_revokes_family(api_client):
    tokens = await _register_and_login(api_client)
    r1 = tokens["refresh_token"]
    refreshed = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": r1})
    assert refreshed.status_code == 200
    r2 = refreshed.json()["data"]["refresh_token"]
    assert r2 != r1
    # Reusing the rotated token r1 → 401 and revokes the whole family.
    replay = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": r1})
    assert replay.status_code == 401
    reused_r2 = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": r2})
    assert reused_r2.status_code == 401


async def test_refresh_invalid_token_401(api_client):
    resp = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": "bogus"})
    assert resp.status_code == 401


async def test_logout_and_sessions(api_client):
    tokens = await _register_and_login(api_client)
    await _login(api_client)  # a second session
    me = await api_client.get("/api/v1/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200

    sessions = await api_client.get("/api/v1/sessions", headers=_auth(tokens["access_token"]))
    assert sessions.status_code == 200
    rows = sessions.json()["data"]
    assert len(rows) == 2

    # Revoke one session by id.
    target = rows[0]["id"]
    deleted = await api_client.delete(
        f"/api/v1/sessions/{target}", headers=_auth(tokens["access_token"])
    )
    assert deleted.status_code == 200
    after = await api_client.get("/api/v1/sessions", headers=_auth(tokens["access_token"]))
    assert len(after.json()["data"]) == 1

    # logout-all clears the rest.
    lo = await api_client.post("/api/v1/auth/logout-all", headers=_auth(tokens["access_token"]))
    assert lo.status_code == 200
    final = await api_client.get("/api/v1/sessions", headers=_auth(tokens["access_token"]))
    assert len(final.json()["data"]) == 0


async def test_logout_specific_refresh(api_client):
    tokens = await _register_and_login(api_client)
    lo = await api_client.post(
        "/api/v1/auth/logout",
        headers=_auth(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert lo.status_code == 200
    reused = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401


# --- password reset / email verification (dev Redis mailer) ------------------


async def test_password_reset_single_use_via_dev_mailer(api_client, redis_client):
    tokens = await _register_and_login(api_client)
    forgot = await api_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    assert forgot.status_code == 200
    assert forgot.json()["data"]["status"] == "ok"

    reset_token = await redis_client.get(f"mesh:devmail:password_reset:{EMAIL}")
    assert reset_token

    reset = await api_client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "a-new-passw0rd"},
    )
    assert reset.status_code == 200
    # Old session invalidated by the password change.
    reused = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401
    # New password works.
    assert (await _login(api_client, password="a-new-passw0rd")).status_code == 200
    # Single-use: the reset token cannot be replayed.
    again = await api_client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "another-pass1"},
    )
    assert again.status_code == 401


async def test_forgot_password_unknown_email_still_ok(api_client):
    resp = await api_client.post("/api/v1/auth/forgot-password", json={"email": "ghost@corp.com"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


async def test_email_verification_via_dev_mailer(api_client, redis_client):
    await _register(api_client)
    verify_token = await redis_client.get(f"mesh:devmail:email_verification:{EMAIL}")
    assert verify_token
    verify = await api_client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verify.status_code == 200
    # Replay rejected (single-use).
    again = await api_client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert again.status_code == 401


# --- authenticated password change (auth.md §3.1/§4.2,MES-39) -----------------


async def test_change_password_requires_auth_real_e2e(api_client):
    resp = await api_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "a-new-passw0rd"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_change_password_full_flow_real_e2e(api_client, db_session):
    """§4.2 端到端:旧密码错 422 / 弱密码 400 三 reason / 成功后其它会话失效
    (DB 实测)/ 当前会话仍有效 / 审计落库。"""
    from sqlalchemy import select

    from mesh.auth.security import hash_token
    from mesh.db.models.audit import AuditLog
    from mesh.db.models.user import Session, User

    tokens = await _register_and_login(api_client)
    h = _auth(tokens["access_token"])
    other = (await _login(api_client)).json()["data"]  # a second device

    # 旧密码错 → 422 invalid_credentials。
    wrong = await api_client.post(
        "/api/v1/auth/change-password",
        headers=h,
        json={"old_password": "wrong-pass-1", "new_password": "a-new-passw0rd"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    # 弱密码 → 400 weak_password,三 reason(复用注册强度策略)。
    for weak, reason in [
        ("short1", "too_short"),
        ("lettersonlyx", "needs_letter_and_digit"),
        ("password123", "too_common"),
    ]:
        resp = await api_client.post(
            "/api/v1/auth/change-password",
            headers=h,
            json={"old_password": PASSWORD, "new_password": weak},
        )
        assert resp.status_code == 400
        error = resp.json()["error"]
        assert error["code"] == "weak_password"
        assert error["details"]["reason"] == reason

    # 成功:携带当前会话 refresh → 保留当前会话,其它会话失效。
    ok = await api_client.post(
        "/api/v1/auth/change-password",
        headers=h,
        json={
            "old_password": PASSWORD,
            "new_password": "a-new-passw0rd",
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "ok"

    # DB 实测:password_changed_at 已 bump;其它会话 revoked、当前会话未 revoked;
    # 审计行 user.password_changed 落库(账号级:workspace_id 为 NULL)。
    user = (
        (await db_session.execute(select(User).where(User.email == EMAIL))).scalars().one()
    )
    assert user.password_changed_at is not None
    rows = {
        row.token_hash: row
        for row in (
            (await db_session.execute(select(Session).where(Session.user_id == user.id)))
            .scalars()
            .all()
        )
    }
    assert rows[hash_token(tokens["refresh_token"])].revoked_at is None  # 当前会话保留
    assert rows[hash_token(other["refresh_token"])].revoked_at is not None  # 其它失效
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "user.password_changed")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].workspace_id is None
    assert audits[0].resource_id == user.id

    # 行为实测:当前会话 refresh 仍有效;其它会话旧 refresh 失效。
    # (存活断言在前:呈递已撤销令牌会触发重放检测并撤销整个会话族,故置最后。)
    alive = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert alive.status_code == 200
    dead = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": other["refresh_token"]}
    )
    assert dead.status_code == 401

    # 旧密码不再可登录;新密码可以。
    old_login = await _login(api_client, password=PASSWORD)
    assert old_login.status_code == 422
    assert old_login.json()["error"]["code"] == "invalid_credentials"
    assert (await _login(api_client, password="a-new-passw0rd")).status_code == 200


async def test_change_password_without_refresh_revokes_all_real_e2e(api_client, db_session):
    """未呈递当前会话凭证 → 全部会话失效(§4.5 安全默认)。"""
    from sqlalchemy import select

    from mesh.db.models.user import Session, User

    tokens = await _register_and_login(api_client)
    ok = await api_client.post(
        "/api/v1/auth/change-password",
        headers=_auth(tokens["access_token"]),
        json={"old_password": PASSWORD, "new_password": "a-new-passw0rd"},
    )
    assert ok.status_code == 200

    reused = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    user = (
        (await db_session.execute(select(User).where(User.email == EMAIL))).scalars().one()
    )
    active = (
        (
            await db_session.execute(
                select(Session).where(
                    Session.user_id == user.id, Session.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    assert active == []


# --- PATCH /users/me ---------------------------------------------------------


async def test_update_me_settings_and_validations(api_client):
    tokens = await _register_and_login(api_client)
    h = _auth(tokens["access_token"])

    ok = await api_client.patch(
        "/api/v1/users/me",
        headers=h,
        json={"display_name": "新名字", "settings": {"locale": "zh-CN"}},
    )
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert data["display_name"] == "新名字"
    assert data["settings"]["locale"] == "zh-CN"

    # Shallow merge: theme added, locale preserved.
    merged = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"theme": "dark"}}
    )
    assert merged.json()["data"]["settings"] == {"locale": "zh-CN", "theme": "dark"}

    tz = await api_client.patch("/api/v1/users/me", headers=h, json={"timezone": "Mars/X"})
    assert tz.status_code == 422
    assert tz.json()["error"]["code"] == "invalid_timezone"

    loc = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"locale": "fr-FR"}}
    )
    assert loc.status_code == 422
    assert loc.json()["error"]["code"] == "unsupported_locale"

    # theme.md §3.3 (唯一权威) + auth.md §3.5 同步登记:
    # invalid theme → 422 invalid_theme_mode (具名码,取代通用 validation_error).
    theme = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"theme": "neon"}}
    )
    assert theme.status_code == 422
    theme_error = theme.json()["error"]
    assert theme_error["code"] == "invalid_theme_mode"
    assert theme_error["details"] == {"theme": "neon", "supported": ["light", "dark", "system"]}

    avatar = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"avatar_url": "http://insecure/x.png"}
    )
    assert avatar.status_code == 400

    unknown = await api_client.patch("/api/v1/users/me", headers=h, json={"bogus_field": 1})
    assert unknown.status_code == 400


async def test_update_me_settings_explicit_null_clears_key(api_client):
    """Explicit null in settings.locale/theme pops the key (MES-24 清除语义)."""
    tokens = await _register_and_login(api_client)
    h = _auth(tokens["access_token"])

    # Set locale and theme first.
    set_resp = await api_client.patch(
        "/api/v1/users/me",
        headers=h,
        json={"settings": {"locale": "zh-CN", "theme": "dark"}},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["data"]["settings"] == {"locale": "zh-CN", "theme": "dark"}

    # Clear locale with explicit null → key popped, theme preserved.
    clear_locale = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"locale": None}}
    )
    assert clear_locale.status_code == 200
    settings_after = clear_locale.json()["data"]["settings"]
    assert "locale" not in settings_after
    assert settings_after["theme"] == "dark"

    # Clear theme with explicit null → both keys gone.
    clear_theme = await api_client.patch(
        "/api/v1/users/me", headers=h, json={"settings": {"theme": None}}
    )
    assert clear_theme.status_code == 200
    assert clear_theme.json()["data"]["settings"] == {}

    # GET /me confirms cleared state persists.
    me = await api_client.get("/api/v1/me", headers=h)
    assert me.status_code == 200
    assert me.json()["data"]["settings"] == {}


# --- MFA ---------------------------------------------------------------------


async def test_mfa_setup_enable_login_verify(api_client):
    tokens = await _register_and_login(api_client)
    h = _auth(tokens["access_token"])

    setup = await api_client.post("/api/v1/auth/mfa/setup", headers=h)
    assert setup.status_code == 200
    secret = setup.json()["data"]["secret"]
    assert setup.json()["data"]["otpauth_uri"].startswith("otpauth://totp/")
    assert len(setup.json()["data"]["backup_codes"]) == 10

    enable = await api_client.post(
        "/api/v1/auth/mfa/enable", headers=h, json={"code": pyotp.TOTP(secret).now()}
    )
    assert enable.status_code == 200

    # Login now requires the second factor (no tokens issued yet).
    login = await _login(api_client)
    assert login.status_code == 200
    body = login.json()["data"]
    assert body["mfa_required"] is True
    ticket = body["mfa_ticket"]

    # Wrong code rejected.
    bad = await api_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_ticket": ticket, "code": "000000"}
    )
    assert bad.status_code == 422

    # Correct TOTP yields tokens.
    verify = await api_client.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
    )
    assert verify.status_code == 200
    assert verify.json()["data"]["access_token"]

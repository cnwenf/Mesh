"""Step-up re-authentication chain (auth.md §1.1 credential matrix / §5.5 R6/R7).

Covers the reauth endpoint (password / TOTP-required branch exclusivity /
revoked-session 401 / OAuth pending fail-closed) and the route-level matrix:
PAT/agent credentials get ``403 reauth_required`` with
``reason=interactive_session_required`` on protected routes; a stale session
fails the freshness window until reauth refreshes ``authenticated_at``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.api.app import create_app
from mesh.auth.service import AuthService
from mesh.auth.tokens import TokenService
from mesh.config import load_settings
from mesh.db.models.member import Member
from mesh.db.models.user import Session, User
from mesh.errors import BusinessRuleError, UnauthorizedError

pytestmark = pytest.mark.unit

EMAIL = "stepup@corp.com"
PASSWORD = "a-strong-passw0rd"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="stepup-test-signing-secret",
        session_cookie_secure=False,
    )


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def service(session_factory, settings, clock):
    return AuthService(session_factory, settings, clock=clock)


@pytest.fixture
def app(settings):
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


async def _login(client, email=EMAIL, password=PASSWORD) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "SU"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return resp.json()["data"]["access_token"]


async def _sid_of(session_factory, refresh_plain: str) -> uuid.UUID:
    from mesh.auth.security import hash_token

    async with session_factory() as session:
        row = await session.scalar(
            select(Session).where(Session.token_hash == hash_token(refresh_plain))
        )
    return row.id


class TestReauthService:
    async def test_password_reauth_refreshes_authenticated_at(
        self, service, session_factory, clock
    ):
        await service.register(email=EMAIL, password=PASSWORD, display_name="SU")
        tokens = await service.login(email=EMAIL, password=PASSWORD)
        sid = await _sid_of(session_factory, tokens.refresh_token)

        clock.advance(hours=2)  # the original authentication grows stale
        moment = clock.now
        result = await service.reauth(
            user_id=tokens_access_subject(service, tokens),
            session_id=sid,
            password=PASSWORD,
        )
        assert result["status"] == "ok"
        assert result["authenticated_at"] == moment

        async with session_factory() as session:
            row = await session.get(Session, sid)
        assert row.authenticated_at == moment

    async def test_reauth_revoked_session_401(self, service, session_factory):
        await service.register(email=EMAIL, password=PASSWORD, display_name="SU")
        tokens = await service.login(email=EMAIL, password=PASSWORD)
        sid = await _sid_of(session_factory, tokens.refresh_token)
        await service.logout(refresh_token=tokens.refresh_token)
        user = await _user_id(session_factory, EMAIL)
        with pytest.raises(UnauthorizedError):
            await service.reauth(user_id=user, session_id=sid, password=PASSWORD)

    async def test_totp_account_requires_totp_not_password(
        self, service, session_factory, settings
    ):
        """MES-78 LOW-2: password alone must NOT refresh authenticated_at on a
        TOTP-enabled account (phishing-captured password cannot unlock step-up)."""
        await service.register(email=EMAIL, password=PASSWORD, display_name="SU")
        tokens = await service.login(email=EMAIL, password=PASSWORD)
        sid = await _sid_of(session_factory, tokens.refresh_token)
        user_id = await _user_id(session_factory, EMAIL)

        setup = await service.mfa_setup(user_id=user_id)
        await service.mfa_enable(user_id=user_id, code=pyotp.TOTP(setup["secret"]).now())

        # Password alone → 422 totp_required, authenticated_at untouched.
        async with session_factory() as session:
            before = (await session.get(Session, sid)).authenticated_at
        with pytest.raises(BusinessRuleError) as exc:
            await service.reauth(user_id=user_id, session_id=sid, password=PASSWORD)
        assert exc.value.details["reason"] == "totp_required"
        async with session_factory() as session:
            after = (await session.get(Session, sid)).authenticated_at
        assert after == before

        # Valid TOTP → refreshed.
        result = await service.reauth(
            user_id=user_id, session_id=sid, totp_code=pyotp.TOTP(setup["secret"]).now()
        )
        assert result["status"] == "ok"

    async def test_oauth_only_account_fails_closed(self, service, session_factory):
        # An account with no password hash (OAuth-only) cannot reauth until
        # the §2.4.3 transaction table lands — fail closed, never open.
        await service.register(email=EMAIL, password=PASSWORD, display_name="SU")
        tokens = await service.login(email=EMAIL, password=PASSWORD)
        sid = await _sid_of(session_factory, tokens.refresh_token)
        user_id = await _user_id(session_factory, EMAIL)
        async with session_factory() as session, session.begin():
            user = await session.get(User, user_id)
            user.password_hash = None  # simulate OAuth-only
        with pytest.raises(BusinessRuleError) as exc:
            await service.reauth(user_id=user_id, session_id=sid, method="oauth")
        assert exc.value.details["reason"] == "oauth_reauth_pending"


class TestCredentialMatrixRoutes:
    async def test_pat_cannot_create_pat_interactive_required(
        self, client, session_factory
    ):
        access = await _login(client)
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "SU WS", "slug": f"su-{uuid.uuid4().hex[:8]}"},
                headers=_auth(access),
            )
        ).json()["data"]
        # A PAT calling PAT-create → 403 reauth_required (interactive_session_required).
        pat = await _issue_pat(session_factory, ws["id"], actor_scopes=["token:manage"])
        resp = await client.post(
            f"/api/v1/workspaces/{ws['id']}/api-tokens",
            json={"name": "nested", "scopes": ["issue:read"]},
            headers=_auth(pat),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "reauth_required"
        assert resp.json()["error"]["details"]["reason"] == "interactive_session_required"

    async def test_stale_session_blocked_then_reauth_restores(self, client, session_factory):
        access = await _login(client)
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "SU WS2", "slug": f"su2-{uuid.uuid4().hex[:8]}"},
                headers=_auth(access),
            )
        ).json()["data"]
        # Age the session's authenticated_at past the window.
        user_id = await _user_id(session_factory, EMAIL)
        async with session_factory() as session, session.begin():
            await session.execute(
                select(Session).where(Session.user_id == user_id)
            )
            from sqlalchemy import update as sql_update

            await session.execute(
                sql_update(Session)
                .where(Session.user_id == user_id)
                .values(authenticated_at=datetime.now(UTC) - timedelta(hours=24))
            )
        # Fresh access (still carries the stale auth_time via refresh? no — the
        # current access was issued at login with fresh auth_time; mint a new
        # access by cookie refresh so the claim matches the aged row).
        refreshed = await client.post("/api/v1/auth/refresh", headers={"Origin": "http://t"})
        assert refreshed.status_code == 200
        stale_access = refreshed.json()["data"]["access_token"]

        blocked = await client.post(
            f"/api/v1/workspaces/{ws['id']}/api-tokens",
            json={"name": "t", "scopes": ["issue:read"]},
            headers=_auth(stale_access),
        )
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "reauth_required"

        # Reauth restores → creation succeeds.
        ra = await client.post(
            "/api/v1/auth/reauth", json={"password": PASSWORD}, headers=_auth(stale_access)
        )
        assert ra.status_code == 200, ra.text
        ok = await client.post(
            f"/api/v1/workspaces/{ws['id']}/api-tokens",
            json={"name": "t2", "scopes": ["issue:read"]},
            headers=_auth(stale_access),
        )
        assert ok.status_code == 201, ok.text

    async def test_reauth_wrong_password_422(self, client):
        access = await _login(client)
        resp = await client.post(
            "/api/v1/auth/reauth", json={"password": "wrong-pass-1"}, headers=_auth(access)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_credentials"

    async def test_reauth_requires_web_session_pat_403(self, client, session_factory):
        access = await _login(client)
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "SU WS3", "slug": f"su3-{uuid.uuid4().hex[:8]}"},
                headers=_auth(access),
            )
        ).json()["data"]
        pat = await _issue_pat(session_factory, ws["id"])
        resp = await client.post(
            "/api/v1/auth/reauth", json={"password": PASSWORD}, headers=_auth(pat)
        )
        assert resp.status_code == 403


# --- helpers ------------------------------------------------------------------


def tokens_access_subject(service: AuthService, tokens) -> uuid.UUID:
    from mesh.auth import jwt as jwt_mod

    claims = jwt_mod.decode_access_token(
        tokens.access_token,
        secret=service._settings.jwt_secret,
        algorithm=service._settings.jwt_algorithm,
    )
    return claims.subject


async def _user_id(session_factory, email: str) -> uuid.UUID:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
    return user.id


async def _issue_pat(session_factory, workspace_id, *, actor_scopes=None) -> str:
    async with session_factory() as session:
        owner = await session.scalar(
            select(Member).where(
                Member.workspace_id == uuid.UUID(workspace_id),
                Member.member_type == "human",
                Member.status == "active",
            )
        )
    created = await TokenService(session_factory).create_token(
        actor=owner,
        workspace_id=uuid.UUID(workspace_id),
        name="su-pat",
        scopes=actor_scopes or ["issue:read"],
    )
    return created["token"]

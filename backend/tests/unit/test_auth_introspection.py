"""Bearer self-introspection + self-revocation (auth.md §3.1, review H7).

``GET /api/v1/auth/token`` returns the CURRENT credential's metadata (never a
plaintext fragment) — what powers ``mesh auth status``. ``DELETE`` revokes the
presented credential itself (PAT → immediate 401; session → refresh dies at
once, access expires with its TTL) without needing the token's id.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.auth.tokens import TokenService
from mesh.config import load_settings
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.user import User

pytestmark = pytest.mark.unit

EMAIL = "introspect@corp.com"
PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="introspection-test-signing-secret",
        session_cookie_secure=False,
        # Device-code issuance fails closed without the HMAC pepper — the
        # workspace-bound session regression test exercises the device flow.
        device_code_pepper="introspection-test-device-pepper",
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


async def _login(client) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "Intro"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    return resp.json()["data"]["access_token"]


async def _pat(client, access: str, session_factory, *, scopes=("issue:read",)) -> str:
    """Create a workspace + PAT via the API/owner flow; returns the plaintext."""
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": "Intro WS", "slug": f"intro-{uuid.uuid4().hex[:8]}"},
            headers=_auth(access),
        )
    ).json()["data"]
    from sqlalchemy import select

    async with session_factory() as session:
        owner = await session.scalar(
            select(Member).where(
                Member.workspace_id == uuid.UUID(ws["id"]),
                Member.member_type == "human",
                Member.status == "active",
            )
        )
    created = await TokenService(session_factory).create_token(
        actor=owner,
        workspace_id=uuid.UUID(ws["id"]),
        name="intro-pat",
        scopes=list(scopes),
    )
    return created["token"]


class TestIntrospection:
    async def test_session_introspection_shape_and_no_plaintext(self, client):
        access = await _login(client)
        resp = await client.get("/api/v1/auth/token", headers=_auth(access))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["kind"] == "session"
        assert data["token_id"]
        assert data["expires_at"]
        # No plaintext fragment of ANY credential leaks.
        body = resp.text
        assert "mesh_rft_" not in body
        assert access not in body

    async def test_session_introspection_sets_tenant_context_for_workspace_session(
        self, client, monkeypatch
    ):
        """Regression: a workspace-bound cli (device) session's introspection
        MUST set the tenant GUC before the RLS-protected roster read — under
        the restricted app role the policy casts the (otherwise unset) GUC to
        uuid and the request dies with a 500 (caught by the real-stack e2e;
        owner-role CI never trips it). Asserted here via a recording spy so
        the invariant holds regardless of which DB role runs the suite."""
        access = await _login(client)
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "Intro WS", "slug": f"intro-{uuid.uuid4().hex[:8]}"},
                headers=_auth(access),
            )
        ).json()["data"]
        # Device flow → workspace-bound cli session credential.
        code = (
            await client.post(
                "/api/v1/auth/device/code",
                json={"client_id": "mesh-cli", "scope": "issue:read"},
            )
        ).json()["data"]
        approved = await client.post(
            "/api/v1/auth/device/approve",
            json={"user_code": code["user_code"], "workspace_id": ws["id"]},
            headers=_auth(access),
        )
        assert approved.status_code == 200, approved.text
        tok = (
            await client.post(
                "/api/v1/auth/device/token",
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": code["device_code"],
                    "client_id": "mesh-cli",
                },
            )
        ).json()["data"]

        import mesh.auth.service as service_mod

        recorded: list = []
        original = service_mod.set_tenant_context

        async def spy(conn, workspace_id):
            recorded.append(workspace_id)
            return await original(conn, workspace_id)

        monkeypatch.setattr(service_mod, "set_tenant_context", spy)

        resp = await client.get("/api/v1/auth/token", headers=_auth(tok["access_token"]))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["kind"] == "session"
        assert data["workspace_id"] == ws["id"]
        assert data["member_id"]  # roster resolved under the tenant context
        assert data["scopes"] == ["issue:read"]
        assert any(str(w) == ws["id"] for w in recorded), (
            "introspection read the RLS-protected roster without setting the "
            "tenant GUC — 500 under the restricted app role"
        )

    async def test_pat_introspection_masks_and_describes(self, client, session_factory):
        access = await _login(client)
        pat = await _pat(client, access, session_factory, scopes=("issue:read", "issue:write"))
        resp = await client.get("/api/v1/auth/token", headers=_auth(pat))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["kind"] == "pat"
        assert data["name"] == "intro-pat"
        assert data["prefix"].startswith("mesh_pat_")
        assert set(data["scopes"]) == {"issue:read", "issue:write"}
        assert data["workspace_id"] and data["member_id"]
        assert data["last_used_at"]  # resolve_pat touched it
        assert pat not in resp.text  # plaintext never echoed

    async def test_agent_introspection(self, client, session_factory):
        access = await _login(client)
        ws = (
            await client.post(
                "/api/v1/workspaces",
                json={"name": "A WS", "slug": f"aintro-{uuid.uuid4().hex[:8]}"},
                headers=_auth(access),
            )
        ).json()["data"]
        from sqlalchemy import select

        async with session_factory() as session, session.begin():
            me = await session.scalar(
                select(User).where(User.email == EMAIL)
            )
            agent = Agent(workspace_id=uuid.UUID(ws["id"]), name="intro-agent", owner_user_id=me.id)
            session.add(agent)
            await session.flush()
            agent_member = Member(
                workspace_id=uuid.UUID(ws["id"]),
                member_type="agent",
                agent_id=agent.id,
                role="member",
                status="active",
            )
            session.add(agent_member)
            await session.flush()
            owner = await session.scalar(
                select(Member).where(
                    Member.workspace_id == uuid.UUID(ws["id"]),
                    Member.member_type == "human",
                    Member.status == "active",
                )
            )
        created = await TokenService(session_factory).create_token(
            actor=owner,
            workspace_id=uuid.UUID(ws["id"]),
            name="intro-agent-token",
            scopes=["issue:read"],
            owner_member_id=agent_member.id,
        )
        resp = await client.get("/api/v1/auth/token", headers=_auth(created["token"]))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["kind"] == "agent"
        assert data["prefix"].startswith("mesh_agt_")

    async def test_introspection_requires_credential(self, client):
        resp = await client.get("/api/v1/auth/token")
        assert resp.status_code == 401


class TestSelfRevocation:
    async def test_pat_self_revoke_immediate_401(self, client, session_factory):
        access = await _login(client)
        pat = await _pat(client, access, session_factory)
        # Works before…
        ok = await client.get("/api/v1/auth/token", headers=_auth(pat))
        assert ok.status_code == 200
        # …revoke via the credential itself (no token id needed)…
        rev = await client.delete("/api/v1/auth/token", headers=_auth(pat))
        assert rev.status_code == 200
        assert rev.json()["data"]["status"] == "ok"
        # …immediate 401 afterwards (revoked_at checked per request).
        dead = await client.get("/api/v1/auth/token", headers=_auth(pat))
        assert dead.status_code == 401

    async def test_session_self_revoke_kills_refresh(self, client):
        access = await _login(client)
        refresh_cookie = client.cookies.get("mesh_session")
        rev = await client.delete("/api/v1/auth/token", headers=_auth(access))
        assert rev.status_code == 200
        # The refresh token is revoked at once.
        client.cookies.set("mesh_session", refresh_cookie, domain=client.base_url.host)
        refreshed = await client.post(
            "/api/v1/auth/refresh", headers={"Origin": "http://t"}
        )
        assert refreshed.status_code == 401

    async def test_self_revoke_requires_credential(self, client):
        resp = await client.delete("/api/v1/auth/token")
        assert resp.status_code == 401

"""In-process (ASGI) coverage for favorites routes (MES-67 M1).

e2e runs in uninstrumented subprocesses, so the route *branches* (validation,
resolver-None idempotent DELETE, cross-workspace 404, bad cursor) must be
exercised in-process to count toward coverage. Drives the real create_app.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.unit


def _settings(db_url: str, redis_url: str) -> dict:
    return {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "fav-routes-signing-secret-000000000000",
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", ""),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", ""),
        "storage_bucket": "mesh-fav-routes-test",
    }


@pytest_asyncio.fixture
async def client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings(db_url, redis_url)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Fav-Routes-123", "display_name": email.split("@")[0]},
    )
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "Fav-Routes-123"})
    return r.json()["data"]["access_token"]


async def _ws(client, token: str, slug: str) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": slug, "slug": slug}, headers=_h(token))
    return r.json()["data"]["id"]


async def _agent(client, token: str, ws: str) -> str:
    r = await client.post(f"/api/v1/workspaces/{ws}/agents", json={"name": "a"}, headers=_h(token))
    return r.json()["data"]["id"]


async def _session(client, token: str, ws: str, agent: str) -> str:
    r = await client.post(
        f"/api/v1/workspaces/{ws}/chat-sessions", json={"agent_id": agent}, headers=_h(token)
    )
    return r.json()["data"]["id"]


@pytest.mark.asyncio
async def test_favorites_routes_branches(client):
    bogus = str(uuid.uuid4())
    t1 = await _login(client, f"fav1-{uuid.uuid4().hex[:6]}@e2e.mesh")
    ws1 = await _ws(client, t1, f"fav1-{uuid.uuid4().hex[:6]}")
    ag1 = await _agent(client, t1, ws1)
    sid1 = await _session(client, t1, ws1, ag1)

    # Validation branches (invalid target_type) on PUT + GET.
    assert (await client.put(f"/api/v1/favorites/bogus_type/{bogus}", headers=_h(t1))).status_code == 400
    assert (
        await client.get(f"/api/v1/favorites?workspace_id={ws1}&target_type=bogus_type", headers=_h(t1))
    ).status_code == 400
    # Bad cursor → 400 invalid_cursor.
    assert (
        await client.get(f"/api/v1/favorites?workspace_id={ws1}&cursor=!!!not-b64!!!", headers=_h(t1))
    ).status_code == 400
    # Malformed target id → _path_uuid ValueError → 404.
    put_bad = await client.put("/api/v1/favorites/chat_session/not-a-uuid", headers=_h(t1))
    del_bad = await client.delete("/api/v1/favorites/chat_session/not-a-uuid", headers=_h(t1))
    assert put_bad.status_code == 404 and del_bad.status_code == 404
    # PUT unknown id (valid type) → resolver None → 404.
    put_missing = await client.put(f"/api/v1/favorites/chat_session/{bogus}", headers=_h(t1))
    assert put_missing.status_code == 404
    # DELETE unknown id → resolver None → idempotent 204.
    del_missing = await client.delete(f"/api/v1/favorites/chat_session/{bogus}", headers=_h(t1))
    assert del_missing.status_code == 204

    # A second user in their own workspace is NOT a member of ws1 → cross-tenant
    # PUT/DELETE must not leak existence (404 / 204, not 403).
    t2 = await _login(client, f"fav2-{uuid.uuid4().hex[:6]}@e2e.mesh")
    await _ws(client, t2, f"fav2-{uuid.uuid4().hex[:6]}")
    assert (await client.put(f"/api/v1/favorites/chat_session/{sid1}", headers=_h(t2))).status_code == 404
    assert (await client.delete(f"/api/v1/favorites/chat_session/{sid1}", headers=_h(t2))).status_code == 204

    # Happy path + idempotent DELETE (second delete on now-absent row → 204).
    assert (await client.put(f"/api/v1/favorites/chat_session/{sid1}", headers=_h(t1))).status_code == 201
    d1 = await client.delete(f"/api/v1/favorites/chat_session/{sid1}", headers=_h(t1))
    d2 = await client.delete(f"/api/v1/favorites/chat_session/{sid1}", headers=_h(t1))
    assert d1.status_code == 204 and d2.status_code == 204


@pytest.mark.asyncio
async def test_favorites_resolver_helper_branches(client, session_factory):
    """Direct helper calls cover the resolver None / cross-tenant branches."""
    from mesh.auth.deps import AuthenticatedPrincipal
    from mesh.db.models.user import User
    from mesh.db.tenant import set_tenant_context
    from mesh.errors import NotFoundError, ValidationError
    from mesh.favorites.routes import _path_uuid, _resolve_context, _validate_target_type

    with pytest.raises(ValidationError):
        _validate_target_type("bogus_type")
    assert _validate_target_type("chat_session") == "chat_session"
    with pytest.raises(NotFoundError):
        _path_uuid("not-a-uuid", message="x")

    t1 = await _login(client, f"favh-{uuid.uuid4().hex[:6]}@e2e.mesh")
    ws1 = await _ws(client, t1, f"favh-{uuid.uuid4().hex[:6]}")
    ag1 = await _agent(client, t1, ws1)
    sid1 = await _session(client, t1, ws1, ag1)
    foreign_user = User(id=uuid.uuid4(), email="foreign@x.io", display_name="F")
    foreign = AuthenticatedPrincipal(
        kind="session", user_id=foreign_user.id, subject=foreign_user.id
    )

    # Resolver-None branch (target does not exist).
    async with session_factory() as session:
        await set_tenant_context(session, ws1)
        assert await _resolve_context(session, foreign, "chat_session", uuid.uuid4()) is None
    # Cross-tenant branch (target exists but user is not a member of its ws).
    async with session_factory() as session:
        await set_tenant_context(session, ws1)
        assert (
            await _resolve_context(session, foreign, "chat_session", uuid.UUID(sid1)) is None
        )

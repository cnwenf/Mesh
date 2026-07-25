"""In-process API-token route tests (auth.md §3.2/§3.3).

Complements the subprocess e2e suite by running the real ``create_app()`` via
ASGITransport so the route layer (gating, validation, envelope, rate-limit
headers) is coverage-measured in-process. Users/members are seeded directly and
authenticated with freshly minted access JWTs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from mesh.api.app import create_app
from mesh.auth import jwt as jwt_mod
from mesh.config import load_settings

JWT_SECRET = "inprocess-token-test-signing-secret"
PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret=JWT_SECRET,
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


def _access_token(user_id: uuid.UUID) -> str:
    token, _ = jwt_mod.encode_access_token(
        subject=user_id, secret=JWT_SECRET, algorithm="HS256", ttl=timedelta(minutes=15)
    )
    return token


async def _seed_workspace(app) -> uuid.UUID:
    async with app.state.session_factory() as session, session.begin():
        return (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('W', :s) RETURNING id"),
                {"s": f"ws-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()


async def _seed_human(app, ws: uuid.UUID, role: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a user + member; return (user_id, member_id)."""
    async with app.state.session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": f"{uuid.uuid4().hex[:12]}@corp.com"},
            )
        ).scalar_one()
        member_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, :role, 'active') RETURNING id"
                ),
                {"ws": ws, "u": user_id, "role": role},
            )
        ).scalar_one()
    return user_id, member_id


@pytest.fixture
async def ctx(app):
    """A workspace with an owner and a plain member; returns auth tokens + ids."""
    ws = await _seed_workspace(app)
    owner_user, owner_member = await _seed_human(app, ws, "owner")
    member_user, member_member = await _seed_human(app, ws, "member")
    return {
        "ws": ws,
        "owner_token": _access_token(owner_user),
        "member_token": _access_token(member_user),
        "owner_member": owner_member,
        "member_member": member_member,
    }


# --- create / list / revoke --------------------------------------------------


async def test_create_list_revoke_inprocess(client, ctx):
    created = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "ci", "scopes": ["issue:read"]},
        headers=_auth(ctx["owner_token"]),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["token"].startswith("mesh_pat_")

    listed = await client.get(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens", headers=_auth(ctx["owner_token"])
    )
    assert listed.status_code == 200
    assert "token" not in listed.json()["data"][0]

    rev = await client.delete(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens/{data['id']}",
        headers=_auth(ctx["owner_token"]),
    )
    assert rev.status_code == 200


async def test_whoami_with_pat_and_rejects_non_pat(client, ctx):
    created = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "cli", "scopes": ["issue:read"]},
        headers=_auth(ctx["owner_token"]),
    )
    plaintext = created.json()["data"]["token"]
    who = await client.get("/api/v1/api-tokens/whoami", headers=_auth(plaintext))
    assert who.status_code == 200
    assert who.json()["data"]["role"] == "owner"
    # The access JWT is not a PAT → rejected by the PAT-only gate.
    bad = await client.get("/api/v1/api-tokens/whoami", headers=_auth(ctx["owner_token"]))
    assert bad.status_code == 401
    # No bearer at all → 401.
    assert (await client.get("/api/v1/api-tokens/whoami")).status_code == 401


async def test_create_invalid_name_400(client, ctx):
    resp = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "   "},
        headers=_auth(ctx["owner_token"]),
    )
    assert resp.status_code == 400


async def test_role_override_too_high_422(client, ctx):
    resp = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "x", "role_override": "owner"},
        headers=_auth(ctx["member_token"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "role_override_too_high"


async def test_member_create_for_other_403(client, ctx):
    resp = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "x", "owner_member_id": str(ctx["owner_member"])},
        headers=_auth(ctx["member_token"]),
    )
    assert resp.status_code == 403


async def test_member_create_for_self_ok(client, ctx):
    resp = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "mine", "owner_member_id": str(ctx["member_member"])},
        headers=_auth(ctx["member_token"]),
    )
    assert resp.status_code == 201


async def test_invalid_owner_member_id_400(client, ctx):
    resp = await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "x", "owner_member_id": "not-a-uuid"},
        headers=_auth(ctx["owner_token"]),
    )
    assert resp.status_code == 400


async def test_revoke_unknown_404_and_bad_path_404(client, ctx):
    resp = await client.delete(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens/{uuid.uuid4()}",
        headers=_auth(ctx["owner_token"]),
    )
    assert resp.status_code == 404
    bad_path = await client.delete(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens/not-a-uuid",
        headers=_auth(ctx["owner_token"]),
    )
    assert bad_path.status_code == 404


# --- audit log query (§3.3) --------------------------------------------------


async def test_audit_logs_admin_only_inprocess(client, ctx):
    # Seed an audit row via a token create.
    await client.post(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        json={"name": "ci"},
        headers=_auth(ctx["owner_token"]),
    )
    ok = await client.get(
        f"/api/v1/workspaces/{ctx['ws']}/audit-logs",
        params={"action": "token.created"},
        headers=_auth(ctx["owner_token"]),
    )
    assert ok.status_code == 200
    assert ok.json()["data"]

    forbidden = await client.get(
        f"/api/v1/workspaces/{ctx['ws']}/audit-logs",
        headers=_auth(ctx["member_token"]),
    )
    assert forbidden.status_code == 403


async def test_audit_logs_invalid_actor_400(client, ctx):
    resp = await client.get(
        f"/api/v1/workspaces/{ctx['ws']}/audit-logs",
        params={"actor_member_id": "nope"},
        headers=_auth(ctx["owner_token"]),
    )
    assert resp.status_code == 400


async def _seed_audit_rows(app, ws, *, actor_member_id):
    """Three audit rows at distinct, known timestamps for time-range tests."""
    from datetime import datetime

    from mesh.db.models.audit import AuditLog

    moments = [
        datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 3, 12, 0, 0, tzinfo=UTC),
    ]
    async with app.state.session_factory() as session, session.begin():
        for i, m in enumerate(moments):
            session.add(
                AuditLog(
                    workspace_id=ws,
                    actor_member_id=actor_member_id,
                    actor_kind="member",
                    action=f"test.event_{i}",
                    created_at=m,
                )
            )
    return moments


async def test_audit_logs_time_range_before_after(app, client, ctx):
    moments = await _seed_audit_rows(app, ctx["ws"], actor_member_id=ctx["owner_member"])
    h = _auth(ctx["owner_token"])
    base = f"/api/v1/workspaces/{ctx['ws']}/audit-logs"

    # after 2026-01-01T12:00 → the two later rows (strictly greater).
    after = await client.get(
        base, params={"after": "2026-01-01T12:00:00Z"}, headers=h
    )
    assert after.status_code == 200
    assert {r["action"] for r in after.json()["data"]} == {"test.event_1", "test.event_2"}

    # before 2026-01-03T12:00 → the two earlier rows (strictly less).
    before = await client.get(
        base, params={"before": "2026-01-03T12:00:00Z"}, headers=h
    )
    assert {r["action"] for r in before.json()["data"]} == {"test.event_0", "test.event_1"}

    # bounded window (after day1, before day3) → only the middle row.
    window = await client.get(
        base,
        params={"after": "2026-01-01T12:00:00Z", "before": "2026-01-03T12:00:00Z"},
        headers=h,
    )
    assert {r["action"] for r in window.json()["data"]} == {"test.event_1"}
    assert moments  # referenced


async def test_audit_logs_invalid_timestamp_400(client, ctx):
    h = _auth(ctx["owner_token"])
    base = f"/api/v1/workspaces/{ctx['ws']}/audit-logs"
    bad_before = await client.get(base, params={"before": "not-a-date"}, headers=h)
    assert bad_before.status_code == 400
    assert bad_before.json()["error"]["code"] == "validation_error"
    bad_after = await client.get(base, params={"after": "2026-99-99"}, headers=h)
    assert bad_after.status_code == 400


# --- workspace gating --------------------------------------------------------


async def test_non_member_gets_404(app, client, ctx):
    # A user with NO roster entry in the workspace → 404 (existence must not leak).
    async with app.state.session_factory() as session, session.begin():
        outsider = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'O') RETURNING id"),
                {"e": f"{uuid.uuid4().hex[:12]}@corp.com"},
            )
        ).scalar_one()
    resp = await client.get(
        f"/api/v1/workspaces/{ctx['ws']}/api-tokens",
        headers=_auth(_access_token(outsider)),
    )
    assert resp.status_code == 404


async def test_unknown_workspace_404(client, ctx):
    resp = await client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}/api-tokens",
        headers=_auth(ctx["owner_token"]),
    )
    assert resp.status_code == 404

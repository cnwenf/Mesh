"""In-process workspace API tests (route layer: auth chain, envelopes, codes).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis,
complementing the subprocess e2e suite. workspace.md §3 / §5.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, text

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.audit import AuditLog

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-workspace-test-signing-secret-0000",
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


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str, name: str = "Acme Team") -> dict:
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": name, "slug": slug},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# --- auth chain + CRUD ------------------------------------------------------------


async def test_workspace_crud_happy_path(client, session_factory):
    token = await _register_and_login(client, "crud@corp.com")

    created = await _create_workspace(client, token, "acme", name="Acme Team")
    assert created["my_role"] == "owner"
    assert created["settings"] == {"default_locale": "en"}
    assert "default_language" not in created

    got = await client.get(f"/api/v1/workspaces/{created['id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["data"]["slug"] == "acme"

    listed = await client.get("/api/v1/workspaces", headers=_auth(token))
    assert listed.status_code == 200
    body = listed.json()
    assert [w["slug"] for w in body["data"]] == ["acme"]
    assert body["data"][0]["my_role"] == "owner"
    assert body["next_cursor"] is None

    # Rename + slug redirect.
    patched = await client.patch(
        f"/api/v1/workspaces/{created['id']}",
        json={"slug": "acme-corp"},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["slug"] == "acme-corp"

    by_new = await client.get("/api/v1/workspaces/by-slug/acme-corp", headers=_auth(token))
    assert by_new.status_code == 200
    assert by_new.json()["data"]["id"] == created["id"]
    # Old slug resolves through workspace_slug_history.
    by_old = await client.get("/api/v1/workspaces/by-slug/acme", headers=_auth(token))
    assert by_old.status_code == 200
    assert by_old.json()["data"]["id"] == created["id"]


async def test_workspace_requires_auth(client):
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_create_validation_errors(client):
    token = await _register_and_login(client, "val@corp.com")
    bad_slug = await client.post(
        "/api/v1/workspaces",
        json={"name": "X", "slug": "UPPER"},
        headers=_auth(token),
    )
    assert bad_slug.status_code == 400
    assert bad_slug.json()["error"]["code"] == "validation_error"

    bad_tz = await client.post(
        "/api/v1/workspaces",
        json={"name": "X", "slug": "ok-slug", "timezone": "Bad/Zone"},
        headers=_auth(token),
    )
    assert bad_tz.status_code == 422
    assert bad_tz.json()["error"]["code"] == "invalid_timezone"

    bad_locale = await client.post(
        "/api/v1/workspaces",
        json={"name": "X", "slug": "ok-slug2", "settings": {"default_locale": "fr"}},
        headers=_auth(token),
    )
    assert bad_locale.status_code == 422
    assert bad_locale.json()["error"]["code"] == "unsupported_locale"


async def test_slug_taken_409(client):
    token = await _register_and_login(client, "taken@corp.com")
    await _create_workspace(client, token, "mine-slug")
    dup = await client.post(
        "/api/v1/workspaces",
        json={"name": "Y", "slug": "mine-slug"},
        headers=_auth(token),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "slug_taken"


# --- cross-tenant 404 / permission tiers -------------------------------------------


async def test_non_member_gets_404_identical_to_missing(client):
    owner = await _register_and_login(client, "owner-t@corp.com")
    outsider = await _register_and_login(client, "outsider-t@corp.com")
    created = await _create_workspace(client, owner, "private-ws")

    foreign = await client.get(
        f"/api/v1/workspaces/{created['id']}", headers=_auth(outsider)
    )
    assert foreign.status_code == 404
    missing = await client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}", headers=_auth(outsider)
    )
    assert missing.status_code == 404
    # Same envelope — existence must not leak.
    assert foreign.json() == missing.json()

    # Not listed for the outsider either.
    listed = await client.get("/api/v1/workspaces", headers=_auth(outsider))
    assert listed.json()["data"] == []


async def test_permission_tiers(client):
    owner = await _register_and_login(client, "owner-p@corp.com")
    created = await _create_workspace(client, owner, "perm-ws")

    # Invite a member.
    inv = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"role": "member"},
        headers=_auth(owner),
    )
    assert inv.status_code == 201
    token_plain = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]

    member_user = await _register_and_login(client, "member-p@corp.com")
    accepted = await client.post(
        "/api/v1/invitations/accept",
        json={"token": token_plain},
        headers=_auth(member_user),
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["member"]["role"] == "member"

    # Member can READ…
    read = await client.get(
        f"/api/v1/workspaces/{created['id']}", headers=_auth(member_user)
    )
    assert read.status_code == 200
    assert read.json()["data"]["my_role"] == "member"
    # …but not PATCH settings (403)…
    patch = await client.patch(
        f"/api/v1/workspaces/{created['id']}",
        json={"name": "Hacked"},
        headers=_auth(member_user),
    )
    assert patch.status_code == 403
    # …nor DELETE (owner-only → 403).
    delete = await client.request(
        "DELETE",
        f"/api/v1/workspaces/{created['id']}",
        json={"confirm_slug": "perm-ws"},
        headers=_auth(member_user),
    )
    assert delete.status_code == 403


# --- delete / restore ---------------------------------------------------------------


async def test_delete_requires_confirm_and_owner_then_restore(client):
    owner = await _register_and_login(client, "owner-d@corp.com")
    created = await _create_workspace(client, owner, "del-ws")

    wrong = await client.request(
        "DELETE",
        f"/api/v1/workspaces/{created['id']}",
        json={"confirm_slug": "nope"},
        headers=_auth(owner),
    )
    assert wrong.status_code == 400

    ok = await client.request(
        "DELETE",
        f"/api/v1/workspaces/{created['id']}",
        json={"confirm_slug": "del-ws"},
        headers=_auth(owner),
    )
    assert ok.status_code == 200

    # Gone from list + reads.
    listed = await client.get("/api/v1/workspaces", headers=_auth(owner))
    assert listed.json()["data"] == []
    get = await client.get(f"/api/v1/workspaces/{created['id']}", headers=_auth(owner))
    assert get.status_code == 404

    restored = await client.post(
        f"/api/v1/workspaces/{created['id']}/restore", headers=_auth(owner)
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["slug"] == "del-ws"
    listed = await client.get("/api/v1/workspaces", headers=_auth(owner))
    assert [w["slug"] for w in listed.json()["data"]] == ["del-ws"]


# --- invitations over HTTP ------------------------------------------------------------


async def test_invitation_lifecycle_over_http(client, session_factory):
    owner = await _register_and_login(client, "owner-i@corp.com")
    created = await _create_workspace(client, owner, "inv-ws")

    # Caps hardening: explicit over-cap → 422.
    over = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"max_uses": 101},
        headers=_auth(owner),
    )
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "invitation_limits_exceeded"

    # Link mode create.
    link_resp = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"role": "member", "max_uses": 1},
        headers=_auth(owner),
    )
    assert link_resp.status_code == 201
    link = link_resp.json()["data"][0]
    assert link["invite_link"].startswith("/invite/invtk_")
    token = link["invite_link"].rsplit("/", 1)[1]

    # Preview is public (no auth header).
    preview = await client.get("/api/v1/invitations/preview", params={"token": token})
    assert preview.status_code == 200
    assert preview.json()["data"]["valid"] is True
    assert preview.json()["data"]["workspace_name"] == "Acme Team"
    preview_bad = await client.get(
        "/api/v1/invitations/preview", params={"token": "invtk_ghost"}
    )
    assert preview_bad.json()["data"] == {"valid": False, "reason": "not_found"}

    # Accept by a second user.
    joiner = await _register_and_login(client, "joiner-i@corp.com")
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["workspace"]["slug"] == "inv-ws"

    # Idempotent re-accept (same member, no 422).
    again = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert again.status_code == 200
    assert again.json()["data"]["member"]["id"] == accepted.json()["data"]["member"]["id"]

    # max_uses=1 → exhausted for a third user.
    third = await _register_and_login(client, "third-i@corp.com")
    exhausted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(third)
    )
    assert exhausted.status_code == 422
    assert exhausted.json()["error"]["code"] == "invitation_invalid"
    assert exhausted.json()["error"]["details"] == {"reason": "exhausted"}

    preview_exhausted = await client.get(
        "/api/v1/invitations/preview", params={"token": token}
    )
    assert preview_exhausted.json()["data"] == {"valid": False, "reason": "exhausted"}

    # Email batch + duplicate active email → 409.
    batch = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"emails": ["jane@acme.com", "JOHN@acme.com"]},
        headers=_auth(owner),
    )
    assert batch.status_code == 201
    assert [r["email"] for r in batch.json()["data"]] == ["jane@acme.com", "john@acme.com"]
    dup = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"emails": ["jane@acme.com"]},
        headers=_auth(owner),
    )
    assert dup.status_code == 409

    # List shows no token material.
    listing = await client.get(
        f"/api/v1/workspaces/{created['id']}/invitations", headers=_auth(owner)
    )
    assert listing.status_code == 200
    for row in listing.json()["data"]:
        assert "invite_link" not in row
        assert "token_hash" not in row

    # Revoke one invitation → immediate invalidity.
    rev_target = batch.json()["data"][0]
    revoked = await client.delete(
        f"/api/v1/workspaces/{created['id']}/invitations/{rev_target['id']}",
        headers=_auth(owner),
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"
    rev_again = await client.delete(
        f"/api/v1/workspaces/{created['id']}/invitations/{rev_target['id']}",
        headers=_auth(owner),
    )
    assert rev_again.status_code == 409


# --- role changes over HTTP ------------------------------------------------------------


async def test_role_change_endpoint_with_audit(client, session_factory):
    owner = await _register_and_login(client, "owner-r@corp.com")
    created = await _create_workspace(client, owner, "role-ws")
    inv = await client.post(
        f"/api/v1/workspaces/{created['id']}/invitations",
        json={"role": "member"},
        headers=_auth(owner),
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    plain = await _register_and_login(client, "plain-r@corp.com")
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(plain)
    )
    member_id = accepted.json()["data"]["member"]["id"]

    # The member cannot change roles (403).
    own = await client.patch(
        f"/api/v1/workspaces/{created['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(plain),
    )
    assert own.status_code == 403

    # The owner can.
    changed = await client.patch(
        f"/api/v1/workspaces/{created['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(owner),
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["role"] == "admin"

    # Audit trail written.
    async with session_factory() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "member.role_changed")
            )
        ).scalars().all()
    assert len(audits) == 1
    assert audits[0].metadata_["new_role"] == "admin"

    # Last owner protection over HTTP.
    async with session_factory() as session:
        owner_member = (
            await session.execute(
                text(
                    "SELECT id FROM members WHERE workspace_id = :ws AND role = 'owner'"
                ),
                {"ws": created["id"]},
            )
        ).scalar_one()
    last = await client.patch(
        f"/api/v1/workspaces/{created['id']}/members/{owner_member}",
        json={"role": "member"},
        headers=_auth(owner),
    )
    assert last.status_code == 409
    assert last.json()["error"]["code"] == "last_owner"


async def test_rate_limit_headers_on_workspace_create(client):
    token = await _register_and_login(client, "rl@corp.com")
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "RL", "slug": "rl-ws"},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.headers["X-RateLimit-Limit"] == "120"
    assert "X-RateLimit-Remaining" in resp.headers

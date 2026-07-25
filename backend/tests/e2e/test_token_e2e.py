"""REAL end-to-end API-token tests (auth.md §2.5/§3.2/§3.3/§5.2/§5.5).

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
PostgreSQL RLS is LIVE) + real PostgreSQL + real Redis. Exercises the PAT
lifecycle over HTTP: create (plaintext once) → list (no plaintext) → whoami
(the PAT authenticates) → revoke (PAT dies) → audit trail, plus role_override
and admin-gating negatives.
"""

from __future__ import annotations

from sqlalchemy import select

from mesh.db.models.audit import AuditLog

PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str, name: str = "E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token, ws_id, email, role="member"):
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    return accepted.json()["data"]["member"]["id"], joiner


# --- PAT lifecycle -----------------------------------------------------------


async def test_pat_create_returns_plaintext_once_then_list_hides_it(api_client):
    owner = await _register_and_login(api_client, "owner@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-1")
    ws_id = ws["id"]

    created = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens",
        json={"name": "ci", "scopes": ["issue:read", "comment:write"]},
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    plaintext = data["token"]
    assert plaintext.startswith("mesh_pat_")
    assert data["prefix"] == plaintext[:12]

    # Listing never exposes the plaintext (hash-only at rest, §2.5).
    listed = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/api-tokens", headers=_auth(owner)
    )
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert rows[0]["prefix"] == data["prefix"]


async def test_pat_authenticates_whoami_then_revoke_kills_it(api_client):
    owner = await _register_and_login(api_client, "owner2@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-2")
    ws_id = ws["id"]
    created = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens",
        json={"name": "cli", "scopes": ["issue:read"]},
        headers=_auth(owner),
    )
    plaintext = created.json()["data"]["token"]
    token_id = created.json()["data"]["id"]

    # The PAT itself authenticates and resolves to the owner's principal.
    who = await api_client.get("/api/v1/api-tokens/whoami", headers=_auth(plaintext))
    assert who.status_code == 200, who.text
    body = who.json()["data"]
    assert body["workspace_id"] == ws_id
    assert body["role"] == "owner"
    assert body["member_type"] == "human"
    assert "issue:read" in body["scopes"]

    # A non-PAT bearer (the access JWT) is rejected by the PAT-only gate.
    not_pat = await api_client.get("/api/v1/api-tokens/whoami", headers=_auth(owner))
    assert not_pat.status_code == 401

    # Revoke → the PAT no longer authenticates.
    rev = await api_client.delete(
        f"/api/v1/workspaces/{ws_id}/api-tokens/{token_id}", headers=_auth(owner)
    )
    assert rev.status_code == 200
    dead = await api_client.get("/api/v1/api-tokens/whoami", headers=_auth(plaintext))
    assert dead.status_code == 401


async def test_token_create_and_revoke_write_audit_and_query_endpoint(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner3@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-3")
    ws_id = ws["id"]
    created = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens",
        json={"name": "ci", "scopes": ["issue:read"]},
        headers=_auth(owner),
    )
    token_id = created.json()["data"]["id"]
    await api_client.delete(
        f"/api/v1/workspaces/{ws_id}/api-tokens/{token_id}", headers=_auth(owner)
    )

    # Audit trail recorded both lifecycle events (append-only table).
    async with session_factory() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.workspace_id == ws_id,
                        AuditLog.action.in_(["token.created", "token.revoked"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "token.created" in actions and "token.revoked" in actions

    # The admin query endpoint returns them, filterable by action.
    logs = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/audit-logs",
        params={"action": "token.created"},
        headers=_auth(owner),
    )
    assert logs.status_code == 200
    items = logs.json()["data"]
    assert items and all(i["action"] == "token.created" for i in items)


# --- RBAC / gating negatives -------------------------------------------------


async def test_role_override_above_holder_role_is_422(api_client):
    owner = await _register_and_login(api_client, "owner4@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-4")
    ws_id = ws["id"]
    _, member_token = await _invite_accept(
        api_client, owner, ws_id, "member4@corp.com", role="member"
    )
    # A member overriding up to admin is rejected (422, §5.5).
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens",
        json={"name": "x", "role_override": "admin"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "role_override_too_high"


async def test_member_cannot_create_token_for_other_member(api_client):
    owner = await _register_and_login(api_client, "owner5@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-5")
    ws_id = ws["id"]
    _self_id, member_token = await _invite_accept(
        api_client, owner, ws_id, "member5@corp.com", role="member"
    )
    other_id, _other_token = await _invite_accept(
        api_client, owner, ws_id, "other5@corp.com", role="member"
    )
    # owner_member_id != self → admin required → 403 for a plain member.
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens",
        json={"name": "x", "owner_member_id": other_id},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403


async def test_member_sees_only_own_tokens_admin_sees_all(api_client):
    owner = await _register_and_login(api_client, "owner6@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-6")
    ws_id = ws["id"]
    _, member_token = await _invite_accept(
        api_client, owner, ws_id, "member6@corp.com", role="member"
    )
    await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens", json={"name": "owner-tok"},
        headers=_auth(owner),
    )
    await api_client.post(
        f"/api/v1/workspaces/{ws_id}/api-tokens", json={"name": "member-tok"},
        headers=_auth(member_token),
    )
    own = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/api-tokens", headers=_auth(member_token)
    )
    assert [t["name"] for t in own.json()["data"]] == ["member-tok"]
    all_ = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/api-tokens", headers=_auth(owner)
    )
    assert {t["name"] for t in all_.json()["data"]} == {"owner-tok", "member-tok"}


async def test_non_admin_cannot_read_audit_logs(api_client):
    owner = await _register_and_login(api_client, "owner7@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-7")
    ws_id = ws["id"]
    _, member_token = await _invite_accept(
        api_client, owner, ws_id, "member7@corp.com", role="member"
    )
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/audit-logs", headers=_auth(member_token)
    )
    assert resp.status_code == 403


async def test_revoke_unknown_token_404(api_client):
    import uuid

    owner = await _register_and_login(api_client, "owner8@corp.com")
    ws = await _create_workspace(api_client, owner, "tok-ws-8")
    resp = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/api-tokens/{uuid.uuid4()}", headers=_auth(owner)
    )
    assert resp.status_code == 404

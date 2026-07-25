"""REAL end-to-end workspace tests (workspace.md §5).

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
PostgreSQL RLS is LIVE on every request) + real PostgreSQL + real Redis.
No mocks: every flow is exercised over HTTP and verified against the database.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select, text

from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.workspace import (
    WorkspaceInvitation,
    WorkspaceInvitationRedemption,
    WorkspaceSlugHistory,
)

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


async def _create_workspace(client, token, slug: str, name: str = "Acme Team") -> dict:
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": name, "slug": slug},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# --- CRUD + slug redirect + settings (W1-W6, T32) --------------------------------


async def test_workspace_full_crud_and_slug_redirect(api_client, session_factory):
    token = await _register_and_login(api_client, "crud-e2e@corp.com")
    workspace = await _create_workspace(api_client, token, "acme-e2e")
    assert workspace["my_role"] == "owner"

    # Owner membership + inbox prefix seeded in the REAL database.
    async with session_factory() as session:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace["id"], Member.role == "owner"
            )
        )
        assert member is not None
        prefix = (
            await session.execute(
                text(
                    "SELECT key, kind FROM identifier_prefix_registry "
                    "WHERE workspace_id = :ws"
                ),
                {"ws": workspace["id"]},
            )
        ).all()
    assert prefix == [("WS", "inbox")]

    # Settings: locale single source round-trip (T32).
    patched = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"name": "Acme Corp", "settings": {"default_locale": "zh-CN", "seat_limit": 50}},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    got = await api_client.get(f"/api/v1/workspaces/{workspace['id']}", headers=_auth(token))
    data = got.json()["data"]
    assert data["settings"]["default_locale"] == "zh-CN"
    assert data["settings"]["seat_limit"] == 50
    assert "default_language" not in data

    # 422 canonical codes over the wire.
    bad_locale = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"settings": {"default_locale": "fr"}},
        headers=_auth(token),
    )
    assert bad_locale.status_code == 422
    assert bad_locale.json()["error"]["code"] == "unsupported_locale"
    bad_tz = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"timezone": "Moon/Base"},
        headers=_auth(token),
    )
    assert bad_tz.status_code == 422
    assert bad_tz.json()["error"]["code"] == "invalid_timezone"
    bad_logo = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"logo_url": "javascript:alert(1)"},
        headers=_auth(token),
    )
    assert bad_logo.status_code == 400

    # Rename → slug history redirect (W6).
    renamed = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"slug": "acme-corp-e2e"},
        headers=_auth(token),
    )
    assert renamed.status_code == 200
    by_old = await api_client.get(
        "/api/v1/workspaces/by-slug/acme-e2e", headers=_auth(token)
    )
    assert by_old.status_code == 200
    assert by_old.json()["data"]["slug"] == "acme-corp-e2e"
    async with session_factory() as session:
        history = (
            await session.execute(
                select(WorkspaceSlugHistory.old_slug).where(
                    WorkspaceSlugHistory.workspace_id == workspace["id"]
                )
            )
        ).scalars().all()
    assert history == ["acme-e2e"]

    # Delete requires the typed slug; then restore.
    no_confirm = await api_client.request(
        "DELETE",
        f"/api/v1/workspaces/{workspace['id']}",
        json={"confirm_slug": "wrong"},
        headers=_auth(token),
    )
    assert no_confirm.status_code == 400
    deleted = await api_client.request(
        "DELETE",
        f"/api/v1/workspaces/{workspace['id']}",
        json={"confirm_slug": "acme-corp-e2e"},
        headers=_auth(token),
    )
    assert deleted.status_code == 200
    gone = await api_client.get(
        f"/api/v1/workspaces/{workspace['id']}", headers=_auth(token)
    )
    assert gone.status_code == 404
    restored = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/restore", headers=_auth(token)
    )
    assert restored.status_code == 200
    back = await api_client.get(
        f"/api/v1/workspaces/{workspace['id']}", headers=_auth(token)
    )
    assert back.status_code == 200


# --- cross-tenant isolation (T1 / §5.3) -------------------------------------------


async def test_cross_tenant_404_no_existence_leak(api_client):
    owner = await _register_and_login(api_client, "a-owner@corp.com")
    outsider = await _register_and_login(api_client, "b-outsider@corp.com")
    workspace = await _create_workspace(api_client, owner, "secret-ws")

    # Guessed UUID of another tenant → identical 404 to a nonexistent id.
    guessed = await api_client.get(
        f"/api/v1/workspaces/{workspace['id']}", headers=_auth(outsider)
    )
    missing = await api_client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}", headers=_auth(outsider)
    )
    assert guessed.status_code == 404
    assert guessed.json() == missing.json()

    # Invitations endpoints also 404 for non-members.
    inv = await api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/invitations", headers=_auth(outsider)
    )
    assert inv.status_code == 404
    # And the workspace never appears in the outsider's list.
    listed = await api_client.get("/api/v1/workspaces", headers=_auth(outsider))
    assert listed.json()["data"] == []


# --- invitation lifecycle (W7-W9, T11, LOW-2) --------------------------------------


async def test_invitation_full_lifecycle(api_client, session_factory):
    owner = await _register_and_login(api_client, "inv-owner@corp.com")
    workspace = await _create_workspace(api_client, owner, "inv-lifecycle")

    # Create → accept → exhaust (max_uses=2).
    created = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"role": "member", "max_uses": 2, "expires_in_hours": 48},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    link_row = created.json()["data"][0]
    token = link_row["invite_link"].rsplit("/", 1)[1]

    # Token stored as SHA-256 ONLY — plaintext nowhere in the row (MES-4).
    async with session_factory() as session:
        row = await session.scalar(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.id == link_row["id"]
            )
        )
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert not token.startswith(row.token_prefix) or len(token) > len(row.token_prefix)

    joiner1 = await _register_and_login(api_client, "joiner1@corp.com")
    joiner2 = await _register_and_login(api_client, "joiner2@corp.com")
    joiner3 = await _register_and_login(api_client, "joiner3@corp.com")

    accept1 = await api_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner1)
    )
    assert accept1.status_code == 200
    assert accept1.json()["data"]["workspace"]["id"] == workspace["id"]
    member1_id = accept1.json()["data"]["member"]["id"]

    # Redemption row persisted with same-tenant composite linkage (the table
    # is truncated per test, so exactly one row exists at this point).
    async with session_factory() as session:
        redemptions = (
            await session.execute(select(WorkspaceInvitationRedemption))
        ).scalars().all()
    assert len(redemptions) == 1
    redemption = redemptions[0]
    assert redemption.invitation_id == uuid.UUID(link_row["id"])
    assert redemption.member_id == uuid.UUID(member1_id)
    assert redemption.workspace_id == uuid.UUID(workspace["id"])

    accept2 = await api_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner2)
    )
    assert accept2.status_code == 200
    async with session_factory() as session:
        status = await session.scalar(
            select(WorkspaceInvitation.status).where(
                WorkspaceInvitation.id == link_row["id"]
            )
        )
    assert status == "exhausted"  # 2/2 uses

    accept3 = await api_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner3)
    )
    assert accept3.status_code == 422
    assert accept3.json()["error"]["details"] == {"reason": "exhausted"}

    # invitation.redeemed + member.added flowed through the outbox (§6.6).
    async with session_factory() as session:
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    realtime = [e for e in events if e.event_type == "realtime.publish"]
    names = [e.payload["event"] for e in realtime]
    assert names.count("invitation.redeemed") == 2
    assert names.count("member.added") >= 2
    for event in realtime:
        assert event.payload["channel"] == f"workspace:{workspace['id']}"

    # Expire flow: backdate a fresh link, accept → expired; preview reason.
    expired_link = (
        await api_client.post(
            f"/api/v1/workspaces/{workspace['id']}/invitations",
            json={"role": "member"},
            headers=_auth(owner),
        )
    ).json()["data"][0]
    expired_token = expired_link["invite_link"].rsplit("/", 1)[1]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": expired_link["id"]},
        )
    accept_expired = await api_client.post(
        "/api/v1/invitations/accept",
        json={"token": expired_token},
        headers=_auth(joiner3),
    )
    assert accept_expired.status_code == 422
    assert accept_expired.json()["error"]["details"] == {"reason": "expired"}
    preview = await api_client.get(
        "/api/v1/invitations/preview", params={"token": expired_token}
    )
    assert preview.json()["data"] == {"valid": False, "reason": "expired"}

    # Revoke flow: immediate invalidation.
    revoked_link = (
        await api_client.post(
            f"/api/v1/workspaces/{workspace['id']}/invitations",
            json={"role": "admin"},
            headers=_auth(owner),
        )
    ).json()["data"][0]
    revoked_token = revoked_link["invite_link"].rsplit("/", 1)[1]
    revoked = await api_client.delete(
        f"/api/v1/workspaces/{workspace['id']}/invitations/{revoked_link['id']}",
        headers=_auth(owner),
    )
    assert revoked.status_code == 200
    accept_revoked = await api_client.post(
        "/api/v1/invitations/accept",
        json={"token": revoked_token},
        headers=_auth(joiner3),
    )
    assert accept_revoked.status_code == 422
    assert accept_revoked.json()["error"]["details"] == {"reason": "revoked"}


async def test_invitation_caps_and_batch_conflicts(api_client):
    owner = await _register_and_login(api_client, "caps-owner@corp.com")
    workspace = await _create_workspace(api_client, owner, "caps-ws")

    over_uses = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"max_uses": 101},
        headers=_auth(owner),
    )
    assert over_uses.status_code == 422
    assert over_uses.json()["error"]["code"] == "invitation_limits_exceeded"
    over_hours = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"expires_in_hours": 721},
        headers=_auth(owner),
    )
    assert over_hours.status_code == 422

    batch = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"emails": ["x@acme.com", "y@acme.com"]},
        headers=_auth(owner),
    )
    assert batch.status_code == 201
    dup = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"emails": ["X@acme.com"]},  # case-insensitive duplicate
        headers=_auth(owner),
    )
    assert dup.status_code == 409

    # Role cannot be owner.
    owner_role = await api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"role": "owner"},
        headers=_auth(owner),
    )
    assert owner_role.status_code == 400


# --- role changes + audit (scope 4) --------------------------------------------------


async def test_role_change_with_audit_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "rc-owner@corp.com")
    workspace = await _create_workspace(api_client, owner, "rc-ws")
    token = (
        await api_client.post(
            f"/api/v1/workspaces/{workspace['id']}/invitations",
            json={"role": "member"},
            headers=_auth(owner),
        )
    ).json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(api_client, "rc-joiner@corp.com")
    accepted = await api_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    member_id = accepted.json()["data"]["member"]["id"]

    changed = await api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(owner),
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["role"] == "admin"

    # The change is durable + audited + evented in the real database.
    async with session_factory() as session:
        role = await session.scalar(
            select(Member.role).where(Member.id == uuid.UUID(member_id))
        )
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "member.role_changed")
            )
        ).scalars().all()
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    assert role == "admin"
    assert len(audits) == 1
    assert audits[0].metadata_["old_role"] == "member"
    assert any(
        e.event_type == "realtime.publish"
        and e.payload["event"] == "member.role_changed"
        for e in events
    )

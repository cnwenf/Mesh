"""REAL end-to-end device-code authorization (auth.md §2.4.2 / §3.1.1 / §3.8,
cli.md §3.2/§4.2, review H7).

Real uvicorn subprocess + real PostgreSQL + real Redis. Covers the full chain,
the four polling branches, the single-consumption race under TRUE parallelism,
the consume ↔ member-removal linearization on the roster row lock (MES-78
LOW-4), the §3.8 refresh race assertion list, prefix misrouting, and the
unified Bearer representative endpoints over the wire.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, text

from mesh.db.models.user import DeviceAuthorization, Session
from tests.conftest import get_test_redis_url

EMAIL = "device-e2e@corp.com"
PASSWORD = "a-strong-passw0rd"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
# Mirrors the effective MESH_DEVICE_CODE_PEPPER inherited by the real e2e
# server. Isolated Compose runs supply a fresh strong value; the local fallback
# matches tests/e2e/conftest.py for direct developer runs.
_E2E_PEPPER = os.environ.get(
    "MESH_DEVICE_CODE_PEPPER", "e2e-device-code-pepper-0123456789"
)

pytestmark = pytest.mark.e2e


async def _clear_poll_limit(device_code: str) -> None:
    """Drop the per-code polling limiter key so tests may poll back-to-back
    (the limiter protects production pacing, not the assertions below)."""
    c = aioredis.from_url(get_test_redis_url(), decode_responses=True)
    try:
        await c.delete(f"mesh:ratelimit:device-poll-code:{device_code[:128]}")
    finally:
        await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _origin(client) -> dict:
    return {"Origin": str(client.base_url).rstrip("/")}


def _refresh_cookie(client) -> str:
    return client.cookies.get("mesh_session") or ""


async def _register_and_login(client, email=EMAIL) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "DE2E"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "refresh_token" not in data  # R4-H1
    return data["access_token"]


async def _make_workspace(client, access, slug=None) -> dict:
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "DE2E WS", "slug": slug or f"de2e-{uuid.uuid4().hex[:8]}"},
        headers=_auth(access),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _issue(client, scope="issue:read issue:write") -> dict:
    resp = await client.post(
        "/api/v1/auth/device/code", json={"client_id": "mesh-cli", "scope": scope}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _approve(client, access, user_code, workspace_id) -> dict:
    resp = await client.post(
        "/api/v1/auth/device/approve",
        json={"user_code": user_code, "workspace_id": workspace_id},
        headers=_auth(access),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _poll(client, device_code) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/device/token",
        json={"grant_type": GRANT_TYPE, "device_code": device_code},
    )


class TestFullChain:
    async def test_issue_approve_exchange_bound_workspace(self, api_client, db_session):
        """Golden path: issue → confirm data → approve (workspace bound on the
        approval page, §3.1.1) → pending poll → 200 with bound workspace;
        the cli session row carries the binding + inherited auth moment."""
        access = await _register_and_login(api_client)
        ws = await _make_workspace(api_client, access)
        issued = await _issue(api_client)

        # Confirmation page data (web login state).
        cd = await api_client.get(
            "/api/v1/auth/device",
            params={"user_code": issued["user_code"]},
            headers=_auth(access),
        )
        assert cd.status_code == 200
        scopes = [s["scope"] for s in cd.json()["data"]["requested_scopes"]]
        assert scopes == ["issue:read", "issue:write"]
        assert [w["id"] for w in cd.json()["data"]["workspaces"]] == [ws["id"]]

        # Pending before approval.
        pending = await _poll(api_client, issued["device_code"])
        assert pending.status_code == 400
        assert pending.json()["error"]["code"] == "authorization_pending"

        # Approve binds the workspace chosen on the page.
        approved = await _approve(api_client, access, issued["user_code"], ws["id"])
        assert approved["status"] == "approved"

        await _clear_poll_limit(issued["device_code"])
        ok = await _poll(api_client, issued["device_code"])
        assert ok.status_code == 200, ok.text
        data = ok.json()["data"]
        assert data["workspace"] == {"id": ws["id"], "slug": ws["slug"]}
        assert data["refresh_token"].startswith("mesh_rft_")
        assert set(data["scope"].split()) == {"issue:read", "issue:write"}

        # DB: exactly one cli session, bound, with device grant linkage and an
        # inherited authenticated_at (never the consumption moment, R6-H3).
        async with db_session:
            cli = (
                (
                    await db_session.execute(select(Session).where(Session.type == "cli"))
                ).scalars().one()
            )
            authz = (
                (
                    await db_session.execute(
                        select(DeviceAuthorization).where(
                            DeviceAuthorization.id == cli.device_authorization_id
                        )
                    )
                ).scalars().one()
            )
        assert cli.workspace_id == uuid.UUID(ws["id"])
        assert sorted(cli.granted_scopes) == ["issue:read", "issue:write"]
        assert authz.status == "consumed"
        assert cli.authenticated_at == authz.approved_authenticated_at

        # The issued access works on regular routes (unified Bearer)…
        me = await api_client.get("/api/v1/me", headers=_auth(data["access_token"]))
        assert me.status_code == 200
        # …and the refresh rotates via the Bearer transport (clear the web
        # cookie first — one transport per request).
        api_client.cookies.clear()
        rotated = await api_client.post(
            "/api/v1/auth/refresh", headers=_auth(data["refresh_token"])
        )
        assert rotated.status_code == 200
        assert rotated.json()["data"]["refresh_token"].startswith("mesh_rft_")

    async def test_device_session_cannot_address_other_workspace(self, api_client):
        """cli.md §4.2 R2-H1: a device session names another workspace → 403."""
        access = await _register_and_login(api_client)
        ws_bound = await _make_workspace(api_client, access)
        ws_other = await _make_workspace(api_client, access)
        issued = await _issue(api_client)
        await _approve(api_client, access, issued["user_code"], ws_bound["id"])
        ok = await _poll(api_client, issued["device_code"])
        cli_access = ok.json()["data"]["access_token"]

        bound = await api_client.get(
            f"/api/v1/workspaces/{ws_bound['id']}/issues", headers=_auth(cli_access)
        )
        assert bound.status_code == 200
        foreign = await api_client.get(
            f"/api/v1/workspaces/{ws_other['id']}/issues", headers=_auth(cli_access)
        )
        assert foreign.status_code == 403

    async def test_scope_intersection_server_enforced(self, api_client):
        """Server-enforced intersection: scopes outside the matrix (or beyond
        the role) are stripped — the CLI can never grant itself permissions
        the approver does not hold."""
        access = await _register_and_login(api_client)
        ws = await _make_workspace(api_client, access)
        # bogus:scope is not a permission anywhere → stripped by the ∩ with
        # the role matrix; issue:read survives.
        issued = await _issue(api_client, scope="issue:read bogus:scope")
        approved = await _approve(api_client, access, issued["user_code"], ws["id"])
        assert approved["granted_scopes"] == ["issue:read"]
        await _clear_poll_limit(issued["device_code"])
        ok = await _poll(api_client, issued["device_code"])
        assert ok.json()["data"]["scope"] == "issue:read"


class TestPollingBranches:
    async def test_denied_expired_invalid_grant(self, api_client, db_session):
        access = await _register_and_login(api_client)
        ws = await _make_workspace(api_client, access)

        # denied → access_denied
        d = await _issue(api_client)
        deny = await api_client.post(
            "/api/v1/auth/device/deny",
            json={"user_code": d["user_code"]},
            headers=_auth(access),
        )
        assert deny.status_code == 200
        await _clear_poll_limit(d["device_code"])
        denied = await _poll(api_client, d["device_code"])
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "access_denied"

        # expired → expired_token (backdate THIS grant past its TTL)
        e = await _issue(api_client)
        from mesh.auth.security import hmac_token

        async with db_session:
            await db_session.execute(
                text(
                    "UPDATE device_authorizations"
                    " SET expires_at = now() - interval '1 minute'"
                    " WHERE user_code_hash = :h"
                ),
                {"h": hmac_token(e["user_code"], _E2E_PEPPER)},
            )
            await db_session.commit()
        await _clear_poll_limit(e["device_code"])
        expired = await _poll(api_client, e["device_code"])
        assert expired.status_code == 400
        assert expired.json()["error"]["code"] == "expired_token"

        # consumed → invalid_grant on replay
        c = await _issue(api_client)
        await _approve(api_client, access, c["user_code"], ws["id"])
        await _clear_poll_limit(c["device_code"])
        first = await _poll(api_client, c["device_code"])
        assert first.status_code == 200
        await _clear_poll_limit(c["device_code"])
        replay = await _poll(api_client, c["device_code"])
        assert replay.status_code == 400
        assert replay.json()["error"]["code"] == "invalid_grant"

        # unknown → invalid_grant
        unknown = await _poll(api_client, "mesh-never-issued")
        assert unknown.status_code == 400
        assert unknown.json()["error"]["code"] == "invalid_grant"

    async def test_slow_down_on_fast_polling(self, api_client):
        await _register_and_login(api_client)
        issued = await _issue(api_client)
        first = await _poll(api_client, issued["device_code"])
        assert first.status_code == 400  # authorization_pending
        # Second poll inside the 5s interval window → slow_down + Retry-After.
        second = await _poll(api_client, issued["device_code"])
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "slow_down"
        assert int(second.headers["Retry-After"]) >= 5


class TestSingleConsumptionRace:
    async def test_concurrent_consumption_exactly_once(self, api_client, api_server, db_session):
        """TRUE parallel consumption: N concurrent exchanges on one approved
        grant → exactly ONE 200; every other response is a legitimate refusal
        (invalid_grant when it lost the consumption race, slow_down when it
        also tripped the per-code poll shield); exactly ONE cli session."""
        access = await _register_and_login(api_client)
        ws = await _make_workspace(api_client, access)
        issued = await _issue(api_client)
        await _approve(api_client, access, issued["user_code"], ws["id"])
        await _clear_poll_limit(issued["device_code"])

        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as c2:
            results = await asyncio.gather(
                _poll(api_client, issued["device_code"]),
                _poll(c2, issued["device_code"]),
                _poll(api_client, issued["device_code"]),
            )
        statuses = [r.status_code for r in results]
        assert statuses.count(200) == 1, [r.text for r in results]
        for r in results:
            if r.status_code != 200:
                assert r.status_code in (400, 429), r.text
                if r.status_code == 400:
                    assert r.json()["error"]["code"] == "invalid_grant"
                else:
                    assert r.json()["error"]["code"] == "slow_down"

        async with db_session:
            n = (
                (
                    await db_session.execute(
                        select(Session.id).where(Session.type == "cli")
                    )
                ).all()
            )
        assert len(n) == 1

    async def test_consume_vs_member_removal_linearizes(self, api_client, api_server, db_session):
        """consume ↔ remove race (MES-78 LOW-4): both transactions lock the
        SAME roster row — the outcome is one of exactly two valid states,
        never a stale-active issuance. A removable (non-owner) member is the
        approver so the removal path can actually commit."""
        owner_access = await _register_and_login(api_client, email=EMAIL)
        ws = await _make_workspace(api_client, owner_access)

        # Second human, added as a plain member, approves the grant.
        approver_email = f"approver-{uuid.uuid4().hex[:8]}@corp.com"
        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as approver_client:
            approver_access = await _register_and_login(approver_client, email=approver_email)
        # Resolve the approver's user id via /me (owner's token adds them).
        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as tmp:
            me = await tmp.get("/api/v1/me", headers=_auth(approver_access))
        approver_user_id = me.json()["data"]["id"]
        added = await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/members",
            json={"member_type": "human", "user_id": approver_user_id, "role": "member"},
            headers=_auth(owner_access),
        )
        assert added.status_code == 201, added.text
        approver_member_id = added.json()["data"]["id"]

        issued = await _issue(api_client)
        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as approver_client:
            ap = await approver_client.post(
                "/api/v1/auth/device/approve",
                json={"user_code": issued["user_code"], "workspace_id": ws["id"]},
                headers=_auth(approver_access),
            )
            assert ap.status_code == 200, ap.text
        await _clear_poll_limit(issued["device_code"])

        # RACE: token exchange vs removal of the approver's membership.
        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as c2:
            exchange_resp, remove_resp = await asyncio.gather(
                _poll(c2, issued["device_code"]),
                api_client.delete(
                    f"/api/v1/workspaces/{ws['id']}/members/{approver_member_id}",
                    headers=_auth(owner_access),
                ),
            )
        assert remove_resp.status_code in (200, 204), remove_resp.text

        async with db_session:
            authz = (
                (await db_session.execute(select(DeviceAuthorization))).scalars().all()
            )
            latest = sorted(authz, key=lambda r: r.created_at)[-1]
            cli_rows = (
                (
                    await db_session.execute(
                        select(Session).where(Session.type == "cli")
                    )
                ).scalars().all()
            )

        if exchange_resp.status_code == 200:
            # Consume held the roster lock first: the session exists and was
            # issued with the role read UNDER THE LOCK (member at that moment).
            assert latest.status == "consumed"
            assert len(cli_rows) == 1
        elif exchange_resp.status_code == 400 and exchange_resp.json()["error"]["code"] == "access_denied":
            # Removal committed first: grant voided, NO session minted.
            assert latest.status == "invalidated"
            assert len(cli_rows) == 0
        else:
            # slow_down would mask the race — not acceptable here since the
            # limiter key was cleared immediately before the gather.
            raise AssertionError(f"unexpected exchange outcome: {exchange_resp.text}")


class TestRefreshRace:
    async def test_concurrent_refresh_winner_and_loser(self, api_client, api_server, db_session):
        """§3.8 ①②⑤⑥ over real HTTP: two concurrent refreshes with the SAME
        refresh token → both 200, exactly one carries the new refresh (the
        winner), the loser gets access ONLY; the DB holds a single current
        token_hash equal to the winner's credential; the grace path wrote
        nothing (no second rotation)."""
        await _register_and_login(api_client)
        refresh = _refresh_cookie(api_client)
        # Bearer-only from here: the cookie would make the transport ambiguous.
        api_client.cookies.clear()

        async def bearer_refresh(client):
            return await client.post("/api/v1/auth/refresh", headers=_auth(refresh))

        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as c2:
            r1, r2 = await asyncio.gather(bearer_refresh(api_client), bearer_refresh(c2))

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        bodies = [r1.json()["data"], r2.json()["data"]]
        winners = [b for b in bodies if "refresh_token" in b]
        losers = [b for b in bodies if "refresh_token" not in b]
        assert len(winners) == 1 and len(losers) == 1  # winner-takes-all
        assert all(b["access_token"] for b in bodies)

        # ⑥ Exactly one current credential row; its hash is the winner's.
        from mesh.auth.security import hash_token

        async with db_session:
            rows = (
                (
                    await db_session.execute(
                        select(Session).where(Session.type == "web")
                    )
                ).scalars().all()
            )
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token(winners[0]["refresh_token"])
        assert rows[0].previous_token_hash == hash_token(refresh)

        # The winner's new refresh is the live credential; the original is a
        # grace-only relic (access within the window, 401 after).
        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=15) as c3:
            live = await c3.post(
                "/api/v1/auth/refresh", headers=_auth(winners[0]["refresh_token"])
            )
        assert live.status_code == 200

    async def test_revoked_session_refresh_rejected(self, api_client):
        access = await _register_and_login(api_client)
        refresh = _refresh_cookie(api_client)
        # Self-revoke the session (DELETE /auth/token).
        rev = await api_client.delete("/api/v1/auth/token", headers=_auth(access))
        assert rev.status_code == 200
        api_client.cookies.clear()
        dead = await api_client.post("/api/v1/auth/refresh", headers=_auth(refresh))
        assert dead.status_code == 401


class TestUnifiedBearerOverTheWire:
    async def test_pat_representative_endpoints(self, api_client, db_session):
        """H7: a human PAT works on the representative read/write/comment/me
        endpoints over real HTTP; a scope-narrowed PAT is 403 on uncovered
        permissions."""
        access = await _register_and_login(api_client)
        ws = await _make_workspace(api_client, access)
        # Create a PAT through the API (step-up passes: fresh web session).
        created = await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/api-tokens",
            json={"name": "e2e-pat", "scopes": ["issue:read", "issue:write", "comment:write"]},
            headers=_auth(access),
        )
        assert created.status_code == 201, created.text
        pat = created.json()["data"]["token"]
        assert pat.startswith("mesh_pat_")

        h = _auth(pat)
        listed = await api_client.get(f"/api/v1/workspaces/{ws['id']}/issues", headers=h)
        assert listed.status_code == 200
        made = await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": "via pat e2e"},
            headers=h,
        )
        assert made.status_code == 201, made.text
        issue_id = made.json()["data"]["id"]
        commented = await api_client.post(
            f"/api/v1/issues/{issue_id}/comments",
            json={"body_markdown": "pat comment"},
            headers=h,
        )
        assert commented.status_code == 201, commented.text
        me = await api_client.get("/api/v1/me", headers=h)
        assert me.status_code == 200

        # Introspection + self-revocation over the wire.
        intro = await api_client.get("/api/v1/auth/token", headers=h)
        assert intro.status_code == 200
        assert intro.json()["data"]["kind"] == "pat"
        assert pat not in intro.text
        rev = await api_client.delete("/api/v1/auth/token", headers=h)
        assert rev.status_code == 200
        dead = await api_client.get(f"/api/v1/workspaces/{ws['id']}/issues", headers=h)
        assert dead.status_code == 401  # immediate

    async def test_misrouted_prefixes_rejected(self, api_client):
        """mesh_rt_ / mesh_rft_ have no business on regular routes → 401."""
        await _register_and_login(api_client)
        rt = await api_client.get("/api/v1/me", headers=_auth("mesh_rt_whatever"))
        assert rt.status_code == 401
        rft = await api_client.get("/api/v1/me", headers=_auth("mesh_rft_whatever"))
        assert rft.status_code == 401

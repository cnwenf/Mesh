"""REAL end-to-end member roster tests (member.md §5, README §9).

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
PostgreSQL RLS is LIVE on every request) + real PostgreSQL + real Redis. No
mocks: every roster flow is exercised over HTTP and verified against the
database — including cross-workspace isolation, CHECK-constraint negatives and
real physical-DELETE behavior (T18-style).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User

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


async def _me_id(client, token: str) -> str:
    resp = await client.get("/api/v1/me", headers=_auth(token))
    return resp.json()["data"]["id"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token, ws_id, email, role="member") -> str:
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


async def _outbox_events(session_factory, name):
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows
        if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


async def _find_member_by_email(session_factory, workspace_id, email: str):
    """Resolve the members.id for a human roster row by users.email."""
    async with session_factory() as session:
        return await session.scalar(
            select(Member.id)
            .join(User, Member.user_id == User.id)
            .where(Member.workspace_id == workspace_id, User.email == email)
        )


# --- roster query + durable membership ----------------------------------------


async def test_roster_list_and_durable_membership(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-list")
    member_id, _joiner = await _invite_accept(api_client, owner, ws["id"], "ro-joiner@corp.com")

    # member.added is durably in the outbox for the invited member.
    added = await _outbox_events(session_factory, "member.added")
    assert [e for e in added if e.payload["data"]["member_id"] == member_id]

    resp = await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert {m["id"] for m in rows} >= {member_id}
    assert all(m["display_name"] for m in rows)  # server-resolved, never empty


async def test_agent_filter_projection_is_same_endpoint(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-proj@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-proj")
    await _invite_accept(api_client, owner, ws["id"], "ro-proj-h@corp.com")
    async with session_factory() as session, session.begin():
        from mesh.db.models.agent import Agent
        from mesh.db.models.user import User

        agent_owner = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            password_hash="x",
            display_name="Agent Owner",
        )
        session.add(agent_owner)
        await session.flush()
        agent_row = Agent(
            workspace_id=uuid.UUID(ws["id"]),
            name="投影助手",
            owner_user_id=agent_owner.id,
        )
        session.add(agent_row)
        await session.flush()
        session.add(
            Member(
                workspace_id=uuid.UUID(ws["id"]),
                member_type="agent",
                agent_id=agent_row.id,
                role="member",
            )
        )
    all_rows = (
        await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    ).json()["data"]
    agent_rows = (
        await api_client.get(
            f"/api/v1/workspaces/{ws['id']}/members?member_type=agent", headers=_auth(owner)
        )
    ).json()["data"]
    assert {m["member_type"] for m in all_rows} == {"human", "agent"}
    assert len(agent_rows) == 1
    assert agent_rows[0]["member_type"] == "agent"
    # Display name resolves from agents.name (README §6.1 order).
    assert agent_rows[0]["display_name"] == "投影助手"


# --- role / status change durability + gating ---------------------------------


async def test_role_change_audited_evented_and_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-role@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-role")
    member_id, _joiner = await _invite_accept(api_client, owner, ws["id"], "ro-role-h@corp.com")

    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"role": "admin"},
        headers=_auth(owner),
    )
    assert resp.status_code == 200

    async with session_factory() as session:
        role = await session.scalar(select(Member.role).where(Member.id == uuid.UUID(member_id)))
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "member.role_changed")
            )
        ).scalars().all()
    assert role == "admin"
    assert len(audits) == 1
    assert len(await _outbox_events(session_factory, "member.role_changed")) == 1


async def test_disable_blocks_access_and_enable_restores(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-dis@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-dis")
    member_id, joiner = await _invite_accept(api_client, owner, ws["id"], "ro-dis-h@corp.com")

    # Member can read the roster while active.
    assert (
        await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(joiner))
    ).status_code == 200

    disabled = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert disabled.status_code == 200
    assert len(await _outbox_events(session_factory, "member.updated")) == 1

    # Disabled member is gated out (membership gate → same 404 as invisible).
    assert (
        await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(joiner))
    ).status_code == 404

    # Re-enable restores access.
    await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}",
        json={"status": "active"},
        headers=_auth(owner),
    )
    assert (
        await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(joiner))
    ).status_code == 200


# --- removal + reassignment ----------------------------------------------------


async def test_remove_invalid_reassign_target_and_soft_removal(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-rm@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-rm")
    member_id, _joiner = await _invite_accept(api_client, owner, ws["id"], "ro-rm-h@corp.com")
    roster = (
        await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    ).json()["data"]
    owner_member = next(m for m in roster if m["role"] == "owner")["id"]

    bad = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}?reassign_to={uuid.uuid4()}",
        headers=_auth(owner),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "reassign_target_invalid"

    ok = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}?reassign_to={owner_member}",
        headers=_auth(owner),
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["removed"] is True

    # Soft removal: the row persists with status='removed' + event + audit.
    async with session_factory() as session:
        status = await session.scalar(
            select(Member.status).where(Member.id == uuid.UUID(member_id))
        )
    assert status == "removed"
    assert len(await _outbox_events(session_factory, "member.removed")) == 1


async def test_add_agent_not_available_over_http(api_client):
    owner = await _register_and_login(api_client, "ro-ag@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-ag")
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        json={"member_type": "agent", "agent_id": str(uuid.uuid4())},
        headers=_auth(owner),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "agents_not_available"


# --- cross-workspace isolation (RLS live on the app path) ---------------------


async def test_cross_workspace_member_invisible(api_client):
    owner_a = await _register_and_login(api_client, "ro-xa@corp.com")
    ws_a = await _create_workspace(api_client, owner_a, "ro-xa")
    owner_b = await _register_and_login(api_client, "ro-xb@corp.com")
    ws_b = await _create_workspace(api_client, owner_b, "ro-xb")
    member_b, _joiner = await _invite_accept(api_client, owner_b, ws_b["id"], "ro-xb-h@corp.com")

    # A's owner cannot read or mutate B's member (composite scope + RLS).
    get_b = await api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/members/{member_b}", headers=_auth(owner_a)
    )
    assert get_b.status_code == 404
    patch_b = await api_client.patch(
        f"/api/v1/workspaces/{ws_a['id']}/members/{member_b}",
        json={"role": "member"},
        headers=_auth(owner_a),
    )
    assert patch_b.status_code == 404


# --- database-level constraint behavior (T1 / T18) ----------------------------


async def test_identity_check_constraint_negatives(session_factory):
    """The polymorphic CHECK admits exactly one identity pointer (member.md §2.2)."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="C", slug=f"c-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
        await session.flush()
        ws_id = workspace.id
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e,'X') RETURNING id"),
                {"e": f"chk-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()

    # both pointers set → rejected
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, agent_id) "
                    "VALUES (:ws,'human',:u,:a)"
                ),
                {"ws": ws_id, "u": user_id, "a": uuid.uuid4()},
            )
    # agent as owner → rejected
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, agent_id, role) "
                    "VALUES (:ws,'agent',:a,'owner')"
                ),
                {"ws": ws_id, "a": uuid.uuid4()},
            )


async def test_physical_delete_behavior(session_factory):
    """T18-style: soft removal is the norm; a physical DELETE of a member who is
    an audit actor is RESTRICTed (NO ACTION), while guest project-access rows
    cascade away on physical delete (member.md §2.6 / README §6.2 rule 6)."""
    from mesh.db.models.workspace import Workspace

    async def _new_user(session, tag: str) -> uuid.UUID:
        return (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e,:d) RETURNING id"),
                {"e": f"del-{tag}-{uuid.uuid4().hex[:8]}@corp.com", "d": tag},
            )
        ).scalar_one()

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="D", slug=f"d-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
        await session.flush()
        ws_id = workspace.id

        actor = Member(
            workspace_id=ws_id, member_type="human", user_id=await _new_user(session, "actor"),
            role="owner",
        )
        guest = Member(
            workspace_id=ws_id, member_type="human", user_id=await _new_user(session, "guest"),
            role="guest",
        )
        session.add_all([actor, guest])
        await session.flush()
        actor_id, guest_id = actor.id, guest.id

        session.add(
            AuditLog(
                workspace_id=ws_id, actor_member_id=actor_id, actor_kind="member",
                action="member.role_changed", resource_type="member", resource_id=guest_id,
            )
        )
        project_id = (
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key) "
                    "VALUES (:ws, 'Del', :k) RETURNING id"
                ),
                {"ws": ws_id, "k": f"DL{uuid.uuid4().hex[:4].upper()}"},
            )
        ).scalar_one()
        session.add(
            MemberProjectAccess(
                workspace_id=ws_id, member_id=guest_id, project_id=project_id,
                permission="read",
            )
        )

    # Physical delete of the audit actor is blocked (RESTRICT / NO ACTION).
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(text("DELETE FROM members WHERE id=:id"), {"id": actor_id})

    # Physical delete of the guest cascades its project-access rows away.
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM members WHERE id=:id"), {"id": guest_id})
    async with session_factory() as session:
        remaining = (
            await session.execute(
                select(MemberProjectAccess).where(MemberProjectAccess.member_id == guest_id)
            )
        ).scalars().all()
    assert remaining == []


# --- guest project access over HTTP + /users/me -------------------------------


async def test_guest_project_access_and_users_me(api_client, session_factory):
    owner = await _register_and_login(api_client, "ro-pa@corp.com")
    ws = await _create_workspace(api_client, owner, "ro-pa")
    guest_id, _guest = await _invite_accept(api_client, owner, ws["id"], "ro-pa-g@corp.com", role="guest")
    created = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Shared", "key": "ROPA"},
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["data"]["id"]

    granted = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/members/{guest_id}/project-access",
        json={"project_id": project_id, "permission": "write"},
        headers=_auth(owner),
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["data"]["permission"] == "write"

    # Durable in the DB.
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MemberProjectAccess).where(
                    MemberProjectAccess.member_id == uuid.UUID(guest_id)
                )
            )
        ).scalars().all()
    assert len(rows) == 1 and rows[0].permission == "write"

    revoked = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{guest_id}/project-access/{project_id}",
        headers=_auth(owner),
    )
    assert revoked.json()["data"] == {"revoked": True}

    # /users/me aggregates memberships across workspaces.
    me = (await api_client.get("/api/v1/users/me", headers=_auth(owner))).json()["data"]
    assert [str(m["workspace_id"]) for m in me["memberships"]] == [str(ws["id"])]


# --- owner invariant hardening (MES-35 MB-M1/MB-M2) ---------------------------


async def test_disable_last_active_owner_conflicts_over_http(api_client, session_factory):
    """MB-M1: PATCH status=disabled on the only active owner → 409 last_owner,
    and the roster row stays untouched in the database."""
    owner = await _register_and_login(api_client, "oi-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-disable")
    owner_member_id = await _find_member_by_email(
        session_factory, ws["id"], "oi-owner@corp.com"
    )

    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{owner_member_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "last_owner"

    async with session_factory() as session:
        row = await session.scalar(select(Member).where(Member.id == owner_member_id))
    assert row.status == "active"
    assert row.role == "owner"
    assert row.disabled_at is None


async def test_disable_owner_allowed_when_second_owner_exists_over_http(
    api_client, session_factory
):
    owner = await _register_and_login(api_client, "oi-two@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-two")
    second_id, _second = await _invite_accept(
        api_client, owner, ws["id"], "oi-second@corp.com", role="admin"
    )
    # Promote the second member to owner, then disable the first: allowed.
    promote = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
        json={"role": "owner"},
        headers=_auth(owner),
    )
    assert promote.status_code == 200, promote.text
    first_id = await _find_member_by_email(session_factory, ws["id"], "oi-two@corp.com")

    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{first_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "disabled"
    async with session_factory() as session:
        row = await session.scalar(select(Member).where(Member.id == first_id))
    assert row.status == "disabled"
    assert row.disabled_at is not None


async def test_concurrent_disable_and_remove_keep_one_active_owner_over_http(
    api_client, session_factory
):
    """MB-M2 over real HTTP: racing disable + remove on the two owners yields
    exactly one 409 last_owner and the database keeps exactly one active owner."""
    owner = await _register_and_login(api_client, "oi-race@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-race")
    second_id, _second = await _invite_accept(
        api_client, owner, ws["id"], "oi-race-2@corp.com", role="admin"
    )
    promote = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
        json={"role": "owner"},
        headers=_auth(owner),
    )
    assert promote.status_code == 200, promote.text
    first_id = await _find_member_by_email(session_factory, ws["id"], "oi-race@corp.com")

    barrier = asyncio.Barrier(2)

    async def disable_first():
        await barrier.wait()
        return await api_client.patch(
            f"/api/v1/workspaces/{ws['id']}/members/{first_id}",
            json={"status": "disabled"},
            headers=_auth(owner),
        )

    async def remove_second():
        await barrier.wait()
        return await api_client.delete(
            f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
            headers=_auth(owner),
        )

    resp_disable, resp_remove = await asyncio.gather(disable_first(), remove_second())
    codes = sorted([resp_disable.status_code, resp_remove.status_code])
    assert codes == [200, 409], (resp_disable.text, resp_remove.text)
    conflict = resp_disable if resp_disable.status_code == 409 else resp_remove
    assert conflict.json()["error"]["code"] == "last_owner"

    async with session_factory() as session:
        active_owners = await session.scalar(
            select(func.count(Member.id)).where(
                Member.workspace_id == ws["id"],
                Member.role == "owner",
                Member.status == "active",
            )
        )
    assert active_owners == 1

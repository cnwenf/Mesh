"""Agent REST API e2e — REAL server + REAL API calls + REAL database.

Covers the agent.md §3 HTTP surface over the wire: creation atomicity
(asserted in PostgreSQL), configuration versions + rollback, the §4.8
lifecycle verbs, §3.5 visibility/authz, §3.4 error codes, transfer and
soft delete. Nothing on the contract path is mocked.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.agent import Agent, AgentConfigVersion
from mesh.db.models.member import Member
from mesh.db.models.runtime import TaskExecution

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str, name: str = "E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Agent E2E", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token: str, ws_id: str, email: str, role="member") -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    assert inv.status_code in (200, 201), inv.text
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accepted.status_code == 200, accepted.text
    return joiner


async def _create_agent(client, token: str, ws_id: str, **overrides) -> dict:
    body = {
        "name": "小测",
        "role_tag": "测试工程师",
        "bio": "负责回归测试",
        "system_instructions": "你是测试工程师。",
        "model_config": {"model_tier": "balanced", "temperature": 0.2, "max_tokens": 8192},
    }
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# --- creation --------------------------------------------------------------------


async def test_create_agent_over_http_writes_all_three_tables(api_client, session_factory):
    owner = await _register_and_login(api_client, "agent-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-c")

    created = await _create_agent(api_client, owner, ws["id"])

    agent_id = uuid.UUID(created["id"])
    async with session_factory() as session:
        agent = await session.get(Agent, agent_id)
        member = await session.scalar(
            select(Member).where(Member.workspace_id == uuid.UUID(ws["id"]), Member.agent_id == agent_id)
        )
        version_count = await session.scalar(
            select(AgentConfigVersion.id)
            .where(AgentConfigVersion.agent_id == agent_id)
            .limit(1)
        )
    assert agent is not None and agent.lifecycle_status == "active"
    assert member is not None and member.member_type == "agent"
    assert version_count is not None
    assert created["active_config_version_id"]
    assert created["member"]["member_type"] == "agent"
    assert created["badge_kind"] == "ai"
    assert created["capacity"] == {
        "running": 0,
        "queued": 0,
        "awaiting_approval": 0,
    }

    # Appears on the members roster (?member_type=agent is a projection, §4.2).
    roster = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/members?member_type=agent", headers=_auth(owner)
    )
    assert roster.status_code == 200
    roster_agent = next(
        m for m in roster.json()["data"] if m["id"] == created["member"]["id"]
    )
    assert roster_agent["profile"]["capacity"] == created["capacity"]


async def test_create_agent_validation_and_scheme_errors(api_client):
    owner = await _register_and_login(api_client, "agent-val@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-v")

    bad_temp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "X", "model_config": {"temperature": 3}},
        headers=_auth(owner),
    )
    assert bad_temp.status_code == 422
    error = bad_temp.json()["error"]
    assert error["code"] == "validation_error"
    assert {"field": "model_config.temperature", "issue": "out_of_range"} in error["details"]["fields"]

    bad_avatar = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "X", "avatar_url": "javascript:alert(1)"},
        headers=_auth(owner),
    )
    assert bad_avatar.status_code == 422  # M-F4: business validation = 422, not 400
    assert bad_avatar.json()["error"]["code"] == "validation_error"

    # Reserved/deferred surfaces reject non-empty values with 422-ish codes.
    runtime = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "X", "default_runtime_id": str(uuid.uuid4())},
        headers=_auth(owner),
    )
    assert runtime.status_code == 400
    skills = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "X", "skill_ids": ["s1"]},
        headers=_auth(owner),
    )
    assert skills.status_code == 400


async def test_create_agent_is_member_self_service_guest_denied(api_client):
    """§4.4/§4.5/F7 over HTTP: member-role creates (201); guest denied (403)."""
    owner = await _register_and_login(api_client, "agent-auth@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-a")
    plain = await _invite_accept(api_client, owner, ws["id"], "agent-plain@corp.com")
    guest = await _invite_accept(api_client, owner, ws["id"], "agent-guest@corp.com", role="guest")

    # A plain member may create (becoming the owner).
    ok = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "自建"},
        headers=_auth(plain),
    )
    assert ok.status_code == 201, ok.text

    denied = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "X"},
        headers=_auth(guest),
    )
    assert denied.status_code == 403


# --- config versions + rollback ----------------------------------------------------


async def test_config_update_versions_and_rollback_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "agent-cfg@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-cfg")
    created = await _create_agent(api_client, owner, ws["id"])
    agent_id = created["id"]
    v1 = created["active_config_version_id"]

    updated = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/config",
        json={"model_config": {"temperature": 0.7}, "system_instructions": "新的说明书"},
        headers=_auth(owner),
    )
    assert updated.status_code == 200
    data = updated.json()["data"]
    v2 = data["active_config_version_id"]
    assert v2 != v1
    assert data["model_config"]["temperature"] == 0.7
    assert data["model_config"]["max_tokens"] == 8192  # merged, not replaced
    assert data["system_instructions"] == "新的说明书"

    history = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/config-versions", headers=_auth(owner)
    )
    assert history.status_code == 200
    versions = history.json()["data"]
    assert len(versions) == 2
    assert versions[0]["id"] == v2  # newest first

    rolled = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/config-versions/{v1}:rollback",
        headers=_auth(owner),
    )
    assert rolled.status_code == 200
    rolled_data = rolled.json()["data"]
    assert rolled_data["model_config"]["temperature"] == 0.2
    assert rolled_data["system_instructions"] == "你是测试工程师。"
    assert rolled_data["active_config_version_id"] not in (v1, v2)

    # Immutable: still exactly 3 version rows after the rollback.
    async with session_factory() as session:
        count = len(
            (
                await session.execute(
                    select(AgentConfigVersion).where(
                        AgentConfigVersion.agent_id == uuid.UUID(agent_id)
                    )
                )
            ).scalars().all()
        )
    assert count == 3


# --- lifecycle (§4.8) -----------------------------------------------------------------


async def test_lifecycle_verbs_and_illegal_transition_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "agent-life@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-life")
    created = await _create_agent(api_client, owner, ws["id"])
    agent_id = created["id"]
    member_id = created["member"]["id"]

    async with session_factory() as session, session.begin():
        session.add_all(
            [
                TaskExecution(
                    workspace_id=uuid.UUID(ws["id"]),
                    agent_id=uuid.UUID(agent_id),
                    status="queued",
                ),
                TaskExecution(
                    workspace_id=uuid.UUID(ws["id"]),
                    agent_id=uuid.UUID(agent_id),
                    status="running",
                ),
            ]
        )

    detail = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(owner)
    )
    assert detail.json()["data"]["capacity"] == {
        "running": 1,
        "queued": 1,
        "awaiting_approval": 0,
    }

    paused = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:pause",
        json={"reason": "维护", "in_flight_policy": "cancel_current"},
        headers=_auth(owner),
    )
    assert paused.status_code == 200
    paused_data = paused.json()["data"]
    assert paused_data["lifecycle_status"] == "paused"
    assert paused_data["affected_executions"] == 2
    assert paused_data["capacity"] == {
        "running": 1,
        "queued": 0,
        "awaiting_approval": 0,
    }

    # archived → pause is illegal from paused? resume first, archive, then pause.
    await api_client.post(f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:resume", headers=_auth(owner))
    await api_client.post(f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:archive", headers=_auth(owner))
    default_agents = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents", headers=_auth(owner)
    )
    all_agents = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?status=all", headers=_auth(owner)
    )
    default_roster = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner)
    )
    all_roster = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/members?status=all", headers=_auth(owner)
    )
    assert agent_id not in {row["id"] for row in default_agents.json()["data"]}
    assert agent_id in {row["id"] for row in all_agents.json()["data"]}
    assert member_id not in {row["id"] for row in default_roster.json()["data"]}
    assert member_id in {row["id"] for row in all_roster.json()["data"]}
    illegal = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:pause", headers=_auth(owner)
    )
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "conflict"  # §3.4 (L1)

    # disable ↔ members.status linkage (§4.8)
    await api_client.post(f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:restore", headers=_auth(owner))
    await api_client.post(f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:disable", headers=_auth(owner))
    async with session_factory() as session:
        member = await session.get(Member, uuid.UUID(member_id))
        assert member.status == "disabled"
    await api_client.post(f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:enable", headers=_auth(owner))
    async with session_factory() as session:
        member = await session.get(Member, uuid.UUID(member_id))
        assert member.status == "active"


# --- visibility (§3.5) -------------------------------------------------------------------


async def test_private_agent_visibility_over_http(api_client):
    owner = await _register_and_login(api_client, "agent-vis@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-vis")
    plain = await _invite_accept(api_client, owner, ws["id"], "agent-vis-p@corp.com")
    private_agent = await _create_agent(api_client, owner, ws["id"], visibility="private")
    agent_id = private_agent["id"]

    # Owner reads it.
    mine = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(owner)
    )
    assert mine.status_code == 200

    # Plain member: 404 (existence must not leak).
    denied = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(plain)
    )
    assert denied.status_code == 404

    # And it is absent from their list.
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents", headers=_auth(plain)
    )
    assert all(a["id"] != agent_id for a in listing.json()["data"])

    # Cross-workspace isolation (T1 shape over HTTP).
    outsider = await _register_and_login(api_client, "agent-outsider@corp.com")
    cross = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(outsider)
    )
    assert cross.status_code == 404


async def test_private_filter_cannot_enumerate_others_private_agents(api_client):
    """C2: ``?visibility=private`` must NOT bypass the owner-or-admin gate.

    Regression for the elif-bypass where an explicit visibility filter
    skipped the non-admin restriction, leaking other members' private ids.
    """
    owner = await _register_and_login(api_client, "agent-c2o@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-c2")
    alice = await _invite_accept(api_client, owner, ws["id"], "alice@corp.com")
    bob = await _invite_accept(api_client, owner, ws["id"], "bob@corp.com")

    # Owner's private agent + Alice's own private agent.
    owner_priv = await _create_agent(api_client, owner, ws["id"], visibility="private", name="OwnerPriv")
    alice_priv = await _create_agent(
        api_client, alice, ws["id"], visibility="private", name="AlicePriv"
    )

    # Bob asks for the private projection: he must see NONE of them.
    bob_private = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?visibility=private", headers=_auth(bob)
    )
    assert bob_private.status_code == 200
    bob_ids = {a["id"] for a in bob_private.json()["data"]}
    assert owner_priv["id"] not in bob_ids
    assert alice_priv["id"] not in bob_ids

    # Alice sees her OWN private agent in the private projection, not the owner's.
    alice_private = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?visibility=private", headers=_auth(alice)
    )
    alice_ids = {a["id"] for a in alice_private.json()["data"]}
    assert alice_ids == {alice_priv["id"]}

    # Owner (admin) sees both via the private projection.
    owner_private = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?visibility=private", headers=_auth(owner)
    )
    owner_ids = {a["id"] for a in owner_private.json()["data"]}
    assert {owner_priv["id"], alice_priv["id"]} <= owner_ids


async def test_owner_member_can_manage_own_agent_other_member_cannot(api_client):
    """M1 (§3.5): a member-role owner manages their own agent; another member 403s."""
    owner = await _register_and_login(api_client, "agent-m1o@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-m1")
    alice = await _invite_accept(api_client, owner, ws["id"], "m1-alice@corp.com")
    bob = await _invite_accept(api_client, owner, ws["id"], "m1-bob@corp.com")

    # Alice (member role) creates → she is the owner.
    created = await _create_agent(api_client, alice, ws["id"], name="AliceBot")
    agent_id = created["id"]

    # Alice can update her own agent's config (new version).
    ok = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/config",
        json={"system_instructions": "Alice 改的"},
        headers=_auth(alice),
    )
    assert ok.status_code == 200, ok.text
    # Alice can pause her own agent.
    pause = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:pause",
        json={"in_flight_policy": "finish_current"},
        headers=_auth(alice),
    )
    assert pause.status_code == 200, pause.text

    # Bob (member, not owner, not admin) is denied on the same agent.
    denied = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/config",
        json={"system_instructions": "Bob 想改"},
        headers=_auth(bob),
    )
    assert denied.status_code == 403


# --- transfer / delete ---------------------------------------------------------------------


async def test_transfer_and_soft_delete_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "agent-tr@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-tr")
    joiner = await _invite_accept(api_client, owner, ws["id"], "agent-tr-j@corp.com")
    created = await _create_agent(api_client, owner, ws["id"])
    agent_id = created["id"]

    # Resolve the joiner's user id from /users/me.
    me = await api_client.get("/api/v1/users/me", headers=_auth(joiner))
    joiner_user_id = me.json()["data"]["user"]["id"]

    transferred = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}:transfer",
        json={"new_owner_user_id": joiner_user_id},
        headers=_auth(owner),
    )
    assert transferred.status_code == 200
    assert transferred.json()["data"]["owner_user_id"] == joiner_user_id

    deleted = await api_client.delete(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(owner)
    )
    assert deleted.status_code == 204
    gone = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}", headers=_auth(owner)
    )
    assert gone.status_code == 404
    async with session_factory() as session:
        agent = await session.get(Agent, uuid.UUID(agent_id))
        assert agent.deleted_at is not None


async def test_list_agents_pagination_and_filters_over_http(api_client):
    owner = await _register_and_login(api_client, "agent-list@corp.com")
    ws = await _create_workspace(api_client, owner, "agent-e2e-list")
    for i in range(3):
        await _create_agent(api_client, owner, ws["id"], name=f"Agent {i}")

    page1 = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?limit=2", headers=_auth(owner)
    )
    assert page1.status_code == 200
    body = page1.json()
    assert len(body["data"]) == 2
    assert body["next_cursor"]

    page2 = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?limit=2&cursor={body['next_cursor']}",
        headers=_auth(owner),
    )
    page2_body = page2.json()
    assert len(page2_body["data"]) == 1
    assert page2_body["next_cursor"] is None
    ids = {a["id"] for a in body["data"]} | {a["id"] for a in page2_body["data"]}
    assert len(ids) == 3

    searched = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/agents?q=Agent 1", headers=_auth(owner)
    )
    assert [a["name"] for a in searched.json()["data"]] == ["Agent 1"]

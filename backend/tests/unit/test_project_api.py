"""In-process project API tests (route layer: auth chain, envelopes, codes).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the project.md §3.1 endpoint surface, §3.4 auth matrix, §6.14
envelopes (data / next_cursor), If-Match optimistic concurrency and the
module error codes (project_key_taken / project_archived / conflict).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.outbox import OutboxEvent

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-project-test-signing-secret-000",
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
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
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


async def _create_project(client, token, ws_id, **overrides) -> dict:
    body = {"name": "Site Revamp", "key": "WEB"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# --- create -----------------------------------------------------------------


async def test_create_project_envelope(client):
    owner = await _register_and_login(client, "owner-c@corp.com")
    ws = await _create_workspace(client, owner, "prj-c")
    created = await _create_project(client, owner, ws["id"], target_date="2026-08-31")
    assert created["key"] == "WEB"
    assert created["status"] == "planning"
    assert created["issue_seq"] == 0
    assert created["progress"] == 0.0
    assert created["my_role"] == "lead"
    # Malformed key → 400 validation_error.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "X", "key": "bad-key"},
        headers=_auth(owner),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"
    # Duplicate key → 409 project_key_taken.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Other", "key": "WEB"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_key_taken"


async def test_project_color_rejects_css_at_create_and_patch_boundaries(client):
    owner = await _register_and_login(client, "owner-color@corp.com")
    ws = await _create_workspace(client, owner, "prj-color")
    malicious = "url(https://attacker.invalid/pixel)"

    rejected_create = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Unsafe", "key": "UNS", "color": malicious},
        headers=_auth(owner),
    )
    assert rejected_create.status_code == 400
    assert rejected_create.json()["error"]["code"] == "validation_error"

    created = await _create_project(client, owner, ws["id"], color="#a1b2c3")
    assert created["color"] == "#A1B2C3"
    rejected_patch = await client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"color": malicious},
        headers=_auth(owner),
    )
    assert rejected_patch.status_code == 400
    assert rejected_patch.json()["error"]["code"] == "validation_error"


async def test_create_project_requires_membership(client):
    owner = await _register_and_login(client, "owner-m@corp.com")
    ws = await _create_workspace(client, owner, "prj-m")
    outsider = await _register_and_login(client, "out-m@corp.com")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "X", "key": "ABC"},
        headers=_auth(outsider),
    )
    assert resp.status_code == 404  # workspace invisible to non-members


async def test_create_project_guest_forbidden(client):
    owner = await _register_and_login(client, "owner-g@corp.com")
    ws = await _create_workspace(client, owner, "prj-g")
    _member_id, guest = await _invite_accept(client, owner, ws["id"], "guest-g@corp.com", "guest")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "X", "key": "GUE"},
        headers=_auth(guest),
    )
    assert resp.status_code == 403


# --- list / detail ----------------------------------------------------------


async def test_list_projects_pagination_envelope(client):
    owner = await _register_and_login(client, "owner-l@corp.com")
    ws = await _create_workspace(client, owner, "prj-l")
    for index in range(3):
        await _create_project(client, owner, ws["id"], name=f"P{index}", key=f"KEY{index}")
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/projects?limit=2", headers=_auth(owner)
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["data"]) == 2
    assert payload["next_cursor"] is not None
    resp2 = await client.get(
        f"/api/v1/workspaces/{ws['id']}/projects?limit=2&cursor={payload['next_cursor']}",
        headers=_auth(owner),
    )
    payload2 = resp2.json()
    assert len(payload2["data"]) == 1
    assert payload2["next_cursor"] is None
    # Invalid cursor → 400 invalid_cursor.
    bad = await client.get(
        f"/api/v1/workspaces/{ws['id']}/projects?cursor=garbage", headers=_auth(owner)
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cursor"


async def test_get_project_workspaceless_path(client):
    owner = await _register_and_login(client, "owner-d@corp.com")
    ws = await _create_workspace(client, owner, "prj-d")
    created = await _create_project(client, owner, ws["id"])
    resp = await client.get(f"/api/v1/projects/{created['id']}", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["data"]["milestones"] == []
    # Malformed id → 404 (never leak id shape).
    resp = await client.get("/api/v1/projects/not-a-uuid", headers=_auth(owner))
    assert resp.status_code == 404
    # Unknown id → 404.
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=_auth(owner))
    assert resp.status_code == 404


async def test_cross_workspace_access_is_404(client):
    owner_a = await _register_and_login(client, "owner-a@corp.com")
    ws_a = await _create_workspace(client, owner_a, "prj-a")
    created = await _create_project(client, owner_a, ws_a["id"])
    owner_b = await _register_and_login(client, "owner-b@corp.com")
    await _create_workspace(client, owner_b, "prj-b")
    resp = await client.get(f"/api/v1/projects/{created['id']}", headers=_auth(owner_b))
    assert resp.status_code == 404
    resp = await client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Hacked"},
        headers=_auth(owner_b),
    )
    assert resp.status_code == 404


async def test_private_project_403_for_other_members(client):
    owner = await _register_and_login(client, "owner-p@corp.com")
    ws = await _create_workspace(client, owner, "prj-p")
    created = await _create_project(client, owner, ws["id"], visibility="private")
    _member_id, member = await _invite_accept(client, owner, ws["id"], "member-p@corp.com")
    resp = await client.get(f"/api/v1/projects/{created['id']}", headers=_auth(member))
    assert resp.status_code == 403
    # Admin can see it.
    admin_id, admin = await _invite_accept(client, owner, ws["id"], "admin-p@corp.com", "admin")
    assert admin_id
    resp = await client.get(f"/api/v1/projects/{created['id']}", headers=_auth(admin))
    assert resp.status_code == 200


# --- PATCH + optimistic concurrency -----------------------------------------


async def test_patch_project_if_match_concurrency(client):
    owner = await _register_and_login(client, "owner-v@corp.com")
    ws = await _create_workspace(client, owner, "prj-v")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    # Stale If-Match → 409 conflict.
    resp = await client.patch(
        f"/api/v1/projects/{pid}",
        json={"name": "Renamed"},
        headers={**_auth(owner), "If-Match": "2020-01-01T00:00:00Z"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # Correct version succeeds.
    current = created["updated_at"]  # RFC3339 as returned by the API
    resp = await client.patch(
        f"/api/v1/projects/{pid}",
        json={"status": "active"},
        headers={**_auth(owner), "If-Match": current},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "active"
    # Without If-Match it just applies.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"health": "on_track"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["health"] == "on_track"


async def test_patch_project_tri_state_and_validation(client):
    owner = await _register_and_login(client, "owner-t@corp.com")
    ws = await _create_workspace(client, owner, "prj-t")
    created = await _create_project(
        client, owner, ws["id"], description="has desc", start_date="2026-08-01"
    )
    pid = created["id"]
    # Explicit null clears the description.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"description": None}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["description"] is None
    # Omitted field untouched.
    assert resp.json()["data"]["start_date"] == "2026-08-01"
    # Invalid enum → 400.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"status": "bogus"}, headers=_auth(owner)
    )
    assert resp.status_code == 400
    # target before start → 400.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"target_date": "2026-07-01"}, headers=_auth(owner)
    )
    assert resp.status_code == 400


async def test_patch_project_lead_change_requires_lead_or_admin(client):
    """PJ-H1: PATCH lead_member_id is gated to lead/admin — no self-escalation."""
    owner = await _register_and_login(client, "owner-h1@corp.com")
    ws = await _create_workspace(client, owner, "prj-h1")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    member_id, member = await _invite_accept(client, owner, ws["id"], "mem-h1@corp.com")
    resp = await client.post(
        f"/api/v1/projects/{pid}/members",
        json={"member_id": member_id, "role": "member"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    # The creator's own member id comes from the project roster (role=lead).
    roster = (await client.get(f"/api/v1/projects/{pid}/members", headers=_auth(owner))).json()[
        "data"
    ]
    owner_member_id = next(entry["member_id"] for entry in roster if entry["role"] == "lead")

    # Lead sets the initial lead → 200.
    resp = await client.patch(
        f"/api/v1/projects/{pid}",
        json={"lead_member_id": owner_member_id},
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["lead_member_id"] == owner_member_id

    # Member self-assignment → 403.
    resp = await client.patch(
        f"/api/v1/projects/{pid}",
        json={"lead_member_id": member_id},
        headers=_auth(member),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    # Member clearing the lead → 403.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": None}, headers=_auth(member)
    )
    assert resp.status_code == 403
    # Escalation regression: the failed self-assignment buys no delete power.
    resp = await client.delete(f"/api/v1/projects/{pid}", headers=_auth(member))
    assert resp.status_code == 403
    # Lead unchanged on the server.
    detail = (await client.get(f"/api/v1/projects/{pid}", headers=_auth(owner))).json()["data"]
    assert detail["lead_member_id"] == owner_member_id

    # Lead may reassign → 200; the new lead may then clear it → 200.
    resp = await client.patch(
        f"/api/v1/projects/{pid}",
        json={"lead_member_id": member_id},
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["lead_member_id"] == member_id
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": None}, headers=_auth(member)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["lead_member_id"] is None


# --- archive / delete -------------------------------------------------------


async def test_archive_lifecycle_and_readonly(client):
    owner = await _register_and_login(client, "owner-ar@corp.com")
    ws = await _create_workspace(client, owner, "prj-ar")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    resp = await client.post(f"/api/v1/projects/{pid}/archive", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["data"]["archived"] is True
    # Writes → 422 project_archived.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"name": "X"}, headers=_auth(owner)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "project_archived"
    resp = await client.post(
        f"/api/v1/projects/{pid}/updates", json={"message": "x"}, headers=_auth(owner)
    )
    assert resp.status_code == 422
    # Unarchive restores.
    resp = await client.post(f"/api/v1/projects/{pid}/unarchive", headers=_auth(owner))
    assert resp.json()["data"]["archived"] is False
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"name": "Writable"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    # Archived projects show up with the archived filter.
    await client.post(f"/api/v1/projects/{pid}/archive", headers=_auth(owner))
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/projects?archived=true", headers=_auth(owner)
    )
    assert [item["id"] for item in resp.json()["data"]] == [pid]
    resp = await client.get(f"/api/v1/workspaces/{ws['id']}/projects", headers=_auth(owner))
    assert resp.json()["data"] == []


async def test_delete_project_soft(client):
    owner = await _register_and_login(client, "owner-del@corp.com")
    ws = await _create_workspace(client, owner, "prj-del")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    # A regular member (not lead) cannot delete.
    _member_id, member = await _invite_accept(client, owner, ws["id"], "member-del@corp.com")
    resp = await client.delete(f"/api/v1/projects/{pid}", headers=_auth(member))
    assert resp.status_code == 403
    # Lead/creator can.
    resp = await client.delete(f"/api/v1/projects/{pid}", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": pid, "deleted": True}
    resp = await client.get(f"/api/v1/projects/{pid}", headers=_auth(owner))
    assert resp.status_code == 404
    # Prefix permanently reserved — reuse → 409.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Again", "key": "WEB"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_key_taken"


# --- updates trail ----------------------------------------------------------


async def test_project_updates_endpoints(client):
    owner = await _register_and_login(client, "owner-u@corp.com")
    ws = await _create_workspace(client, owner, "prj-u")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    resp = await client.post(
        f"/api/v1/projects/{pid}/updates",
        json={"health": "at_risk", "message": "risk"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    update = resp.json()["data"]
    assert update["health"] == "at_risk"
    assert update["author"]["name"]
    # Health written back to the project.
    resp = await client.get(f"/api/v1/projects/{pid}", headers=_auth(owner))
    assert resp.json()["data"]["health"] == "at_risk"
    # Empty update → 400.
    resp = await client.post(
        f"/api/v1/projects/{pid}/updates", json={}, headers=_auth(owner)
    )
    assert resp.status_code == 400
    # History list.
    resp = await client.get(f"/api/v1/projects/{pid}/updates", headers=_auth(owner))
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


# --- milestones / cycles ----------------------------------------------------


async def test_milestone_endpoints(client):
    owner = await _register_and_login(client, "owner-ms@corp.com")
    ws = await _create_workspace(client, owner, "prj-ms")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    resp = await client.post(
        f"/api/v1/projects/{pid}/milestones",
        json={"title": "v1.0", "target_date": "2026-01-01"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    milestone = resp.json()["data"]
    assert milestone["overdue"] is True  # 2026-01-01 < today
    mid = milestone["id"]
    resp = await client.patch(
        f"/api/v1/milestones/{mid}", json={"state": "closed"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["state"] == "closed"
    resp = await client.get(f"/api/v1/projects/{pid}/milestones", headers=_auth(owner))
    assert len(resp.json()["data"]) == 1
    resp = await client.delete(f"/api/v1/milestones/{mid}", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True
    # Unknown milestone → 404.
    resp = await client.patch(
        f"/api/v1/milestones/{uuid.uuid4()}", json={"state": "closed"}, headers=_auth(owner)
    )
    assert resp.status_code == 404


async def test_cycle_endpoints_and_auto_roll(client):
    owner = await _register_and_login(client, "owner-cy@corp.com")
    ws = await _create_workspace(client, owner, "prj-cy")
    created = await _create_project(client, owner, ws["id"])
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/cycles",
        json={
            "name": "Sprint 1",
            "starts_at": "2026-08-01",
            "ends_at": "2026-08-14",
            "project_id": created["id"],
            "auto_roll": True,
        },
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    cycle = resp.json()["data"]
    cid = cycle["id"]
    # ends < starts → 400.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/cycles",
        json={"name": "Bad", "starts_at": "2026-08-14", "ends_at": "2026-08-01"},
        headers=_auth(owner),
    )
    assert resp.status_code == 400
    # Complete → auto-roll returns next_cycle.
    resp = await client.patch(
        f"/api/v1/cycles/{cid}", json={"state": "completed"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["state"] == "completed"
    assert data["next_cycle"]["starts_at"] == "2026-08-15"
    # List + state filter.
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/cycles?state=planned", headers=_auth(owner)
    )
    assert len(resp.json()["data"]) == 1
    resp = await client.get(f"/api/v1/workspaces/{ws['id']}/cycles", headers=_auth(owner))
    assert len(resp.json()["data"]) == 2
    # Unknown cycle → 404.
    resp = await client.patch(
        f"/api/v1/cycles/{uuid.uuid4()}", json={"state": "active"}, headers=_auth(owner)
    )
    assert resp.status_code == 404


# --- project members --------------------------------------------------------


async def test_project_member_endpoints(client):
    owner = await _register_and_login(client, "owner-pm@corp.com")
    ws = await _create_workspace(client, owner, "prj-pm")
    created = await _create_project(client, owner, ws["id"])
    pid = created["id"]
    member_id, member = await _invite_accept(client, owner, ws["id"], "member-pm@corp.com")
    # Add member.
    resp = await client.post(
        f"/api/v1/projects/{pid}/members",
        json={"member_id": member_id, "role": "member"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["role"] == "member"
    # Duplicate → 409.
    resp = await client.post(
        f"/api/v1/projects/{pid}/members",
        json={"member_id": member_id},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_member_exists"
    # Member can now write the project (project membership grants writes).
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"description": "by member"}, headers=_auth(member)
    )
    assert resp.status_code == 200
    # But cannot manage members (lead-only).
    resp = await client.delete(
        f"/api/v1/projects/{pid}/members/{member_id}", headers=_auth(member)
    )
    assert resp.status_code == 403
    # Role change.
    resp = await client.patch(
        f"/api/v1/projects/{pid}/members/{member_id}",
        json={"role": "viewer"},
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "viewer"
    # Viewer loses write access.
    resp = await client.patch(
        f"/api/v1/projects/{pid}", json={"description": "nope"}, headers=_auth(member)
    )
    assert resp.status_code == 403
    # List + remove.
    resp = await client.get(f"/api/v1/projects/{pid}/members", headers=_auth(owner))
    assert len(resp.json()["data"]) == 2
    resp = await client.delete(
        f"/api/v1/projects/{pid}/members/{member_id}", headers=_auth(owner)
    )
    assert resp.status_code == 200
    # Invalid member id in body → 400.
    resp = await client.post(
        f"/api/v1/projects/{pid}/members",
        json={"member_id": "not-a-uuid"},
        headers=_auth(owner),
    )
    assert resp.status_code == 400


# --- templates --------------------------------------------------------------


async def test_template_endpoints_and_instantiate(client):
    owner = await _register_and_login(client, "owner-tp@corp.com")
    ws = await _create_workspace(client, owner, "prj-tp")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/project-templates",
        json={
            "name": "Standard",
            "template_body": {
                "description": "tpl",
                "initial_milestones": [{"title": "GA", "target_date": "2026-08-31"}],
                "initial_cycles": [
                    {"name": "S1", "starts_at": "2026-08-01", "ends_at": "2026-08-14"}
                ],
                "status_set_seed": ["todo"],
            },
        },
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    template = resp.json()["data"]
    tid = template["id"]
    # Duplicate name → 409.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/project-templates",
        json={"name": "Standard", "template_body": {}},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "template_name_taken"
    # Instantiate.
    resp = await client.post(
        f"/api/v1/project-templates/{tid}/instantiate",
        json={"name": "From Template", "key": "TPL"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["key"] == "TPL"
    assert len(data["milestone_ids"]) == 1
    assert len(data["cycle_ids"]) == 1
    assert data["skipped"] == ["status_set_seed:issue_module_pending"]
    # Key conflict via registry on instantiate.
    resp = await client.post(
        f"/api/v1/project-templates/{tid}/instantiate",
        json={"name": "Dup", "key": "TPL"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    # Update + delete template.
    resp = await client.patch(
        f"/api/v1/project-templates/{tid}", json={"name": "Renamed"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "Renamed"
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/project-templates", headers=_auth(owner)
    )
    assert len(resp.json()["data"]) == 1
    resp = await client.delete(f"/api/v1/project-templates/{tid}", headers=_auth(owner))
    assert resp.status_code == 200
    resp = await client.delete(f"/api/v1/project-templates/{tid}", headers=_auth(owner))
    assert resp.status_code == 404


# --- realtime channel behavior ----------------------------------------------


async def test_private_project_events_only_detail_channel(client, session_factory):
    owner = await _register_and_login(client, "owner-rt@corp.com")
    ws = await _create_workspace(client, owner, "prj-rt")
    created = await _create_project(client, owner, ws["id"], visibility="private")
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    created_events = [r for r in rows if r.payload.get("event") == "project.created"]
    assert len(created_events) == 1
    assert created_events[0].payload["channel"] == f"project:{created['id']}"
    # Public project hits both channels.
    public = await _create_project(client, owner, ws["id"], name="Pub", key="PUB")
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    public_events = [
        r
        for r in rows
        if r.payload.get("event") == "project.created"
        and r.payload["data"]["project"]["id"] == public["id"]
    ]
    assert {r.payload["channel"] for r in public_events} == {
        f"project:{public['id']}",
        f"workspace:{ws['id']}:projects",
    }


# --- 404 coverage on workspace-less paths --------------------------------------


async def test_unknown_ids_return_404_on_every_workspaceless_path(client):
    owner = await _register_and_login(client, "owner-404@corp.com")
    ws = await _create_workspace(client, owner, "prj-404")
    missing = str(uuid.uuid4())
    member_like = str(uuid.uuid4())
    endpoints = [
        ("patch", f"/api/v1/projects/{missing}", {"name": "X"}),
        ("delete", f"/api/v1/projects/{missing}", None),
        ("post", f"/api/v1/projects/{missing}/archive", None),
        ("post", f"/api/v1/projects/{missing}/unarchive", None),
        ("post", f"/api/v1/projects/{missing}/updates", {"message": "x"}),
        ("get", f"/api/v1/projects/{missing}/updates", None),
        ("get", f"/api/v1/projects/{missing}/milestones", None),
        ("post", f"/api/v1/projects/{missing}/milestones", {"title": "x"}),
        ("get", f"/api/v1/projects/{missing}/members", None),
        ("post", f"/api/v1/projects/{missing}/members", {"member_id": member_like}),
        ("patch", f"/api/v1/projects/{missing}/members/{member_like}", {"role": "member"}),
        ("delete", f"/api/v1/projects/{missing}/members/{member_like}", None),
        ("delete", f"/api/v1/milestones/{missing}", None),
        ("patch", f"/api/v1/cycles/{missing}", {"state": "active"}),
        ("patch", f"/api/v1/project-templates/{missing}", {"name": "x"}),
        ("delete", f"/api/v1/project-templates/{missing}", None),
        ("post", f"/api/v1/project-templates/{missing}/instantiate",
         {"name": "x", "key": "ZZZ"}),
    ]
    for method, url, body in endpoints:
        kwargs = {"headers": _auth(owner)}
        if body is not None:
            kwargs["json"] = body
        resp = await client.request(method.upper(), url, **kwargs)
        assert resp.status_code == 404, f"{method} {url} → {resp.status_code}"
        assert resp.json()["error"]["code"] == "not_found"
    # The resolver is tenant-scoped: a milestone from workspace A is invisible
    # even with a valid id when asked from workspace B's credentials.
    created = await _create_project(client, owner, ws["id"])
    milestone = await client.post(
        f"/api/v1/projects/{created['id']}/milestones",
        json={"title": "M"},
        headers=_auth(owner),
    )
    mid = milestone.json()["data"]["id"]
    other = await _register_and_login(client, "other-404@corp.com")
    await _create_workspace(client, other, "prj-404b")
    resp = await client.patch(
        f"/api/v1/milestones/{mid}", json={"state": "closed"}, headers=_auth(other)
    )
    # The milestone's workspace is not accessible to `other` → the
    # membership gate 404 is rewritten to the resource message, exactly
    # like an unknown id (no existence oracle, §5.3).
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "milestone not found"


async def test_malformed_query_uuids_are_400(client):
    owner = await _register_and_login(client, "owner-q@corp.com")
    ws = await _create_workspace(client, owner, "prj-q")
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/projects?lead_member_id=garbage",
        headers=_auth(owner),
    )
    assert resp.status_code == 400
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/cycles?project_id=garbage", headers=_auth(owner)
    )
    assert resp.status_code == 400


async def test_prefixless_endpoints_uniform_404_message(client):
    """L3 product-wide parity (workspace.md §5.3): the project module's
    workspace-less paths return the SAME 404 message for "unknown id" and
    "exists in another tenant" across all four resource types, killing the
    existence oracle exactly like the issue module does."""
    owner_a = await _register_and_login(client, "l3p-a@corp.com")
    owner_b = await _register_and_login(client, "l3p-b@corp.com")
    await _create_workspace(client, owner_a, "l3p-a")
    ws_b = await _create_workspace(client, owner_b, "l3p-b")

    project_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/projects",
            json={"name": "Secret", "key": "SEC"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    milestone_b = (
        await client.post(
            f"/api/v1/projects/{project_b['id']}/milestones",
            json={"title": "M1"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    cycle_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/cycles",
            json={"name": "C1", "starts_at": "2026-08-01", "ends_at": "2026-08-14"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    template_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/project-templates",
            json={"name": "Tmpl", "template_body": {}},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    random_id = str(uuid.uuid4())

    probes = (
        # (existing-id probe, existing id, resource message)
        (
            lambda target: client.get(f"/api/v1/projects/{target}", headers=_auth(owner_a)),
            project_b["id"],
            "project not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/milestones/{target}", json={"title": "x"}, headers=_auth(owner_a)
            ),
            milestone_b["id"],
            "milestone not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/cycles/{target}", json={"name": "x"}, headers=_auth(owner_a)
            ),
            cycle_b["id"],
            "cycle not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/project-templates/{target}",
                json={"name": "x"},
                headers=_auth(owner_a),
            ),
            template_b["id"],
            "project template not found",
        ),
    )
    for call, existing_id, message in probes:
        existing = await call(existing_id)  # exists, owner_a is NOT a member
        missing = await call(random_id)  # does not exist anywhere
        assert existing.status_code == 404, existing.text
        assert missing.status_code == 404, missing.text
        # Both states are indistinguishable and carry the resource message.
        assert existing.json()["error"]["message"] == message
        assert missing.json()["error"]["message"] == message

    # Soft-deleted + non-member → same message (whichever layer answers,
    # the contract is one 404 text).
    await client.delete(f"/api/v1/projects/{project_b['id']}", headers=_auth(owner_b))
    deleted_probe = await client.get(
        f"/api/v1/projects/{project_b['id']}", headers=_auth(owner_a)
    )
    assert deleted_probe.status_code == 404
    assert deleted_probe.json()["error"]["message"] == "project not found"

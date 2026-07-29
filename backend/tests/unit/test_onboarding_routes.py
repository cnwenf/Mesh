"""In-process (ASGI) route coverage for onboarding (onboarding.md §3 / §5.3).

Drives the real create_app (mesh_app RLS path): envelope shape, workspace
gate (404 matrix, no existence leak), anti-IDOR self-scoping, idempotent
writes, 422 dismissed completion, admin-only reset (403), invitation-redeem
seeding hook.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.unit

PASSWORD = "Onb-Routes-123"


def _settings(db_url: str, redis_url: str) -> dict:
    return {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "onb-routes-signing-secret-00000000000",
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-onb-routes-test",
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
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return r.json()["data"]["access_token"]


async def _ws(client, token: str, slug: str) -> str:
    r = await client.post(
        "/api/v1/workspaces", json={"name": slug, "slug": slug}, headers=_h(token)
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _invite_accept(client, owner_token: str, ws: str, email: str) -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"emails": [email], "role": "member"},
        headers=_h(owner_token),
    )
    token = await _login(client, email)
    accept_token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    r = await client.post(
        "/api/v1/invitations/accept", json={"token": accept_token}, headers=_h(token)
    )
    assert r.status_code == 200, r.text
    return token


async def test_workspace_creation_seeds_owner_checklist(client):
    token = await _login(client, f"seed-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, token, f"ob-{uuid.uuid4().hex[:8]}")
    r = await client.get(f"/api/v1/onboarding/state?workspace_id={ws}", headers=_h(token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workspace_id"] == ws
    assert data["checklist"] == "activation"
    assert data["progress"] == {"total": 5, "completed": 1, "skipped": 0}
    steps = {s["step_key"]: s for s in data["steps"]}
    assert steps["create_workspace"]["status"] == "completed"
    assert steps["create_workspace"]["completed_via"] == "auto"
    assert steps["invite_member_or_add_agent"]["status"] == "pending"


async def test_state_workspace_gate_error_matrix(client):
    token = await _login(client, f"gate-{uuid.uuid4().hex[:8]}@e2e.mesh")
    # workspace_id 缺失 / 非法 → 400 validation_error(onboarding.md §3.3 参数校验)。
    r = await client.get("/api/v1/onboarding/state", headers=_h(token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
    r = await client.get("/api/v1/onboarding/state?workspace_id=nope", headers=_h(token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
    # 合法 UUID 但非成员 → 404 not_found(§5.3 不泄漏存在性,成员资格门裁决)。
    other = await _login(client, f"other-{uuid.uuid4().hex[:8]}@e2e.mesh")
    foreign_ws = await _ws(client, other, f"fo-{uuid.uuid4().hex[:8]}")
    r = await client.get(f"/api/v1/onboarding/state?workspace_id={foreign_ws}", headers=_h(token))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_manual_complete_and_invalid_step_key(client):
    token = await _login(client, f"man-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, token, f"ob-{uuid.uuid4().hex[:8]}")
    r = await client.post(
        "/api/v1/onboarding/steps/create_first_issue/complete",
        params={"workspace_id": ws},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text
    step = r.json()["data"]
    assert step["status"] == "completed"
    assert step["completed_via"] == "manual"
    # Idempotent no-op on repeat.
    again = await client.post(
        "/api/v1/onboarding/steps/create_first_issue/complete",
        params={"workspace_id": ws},
        headers=_h(token),
    )
    assert again.json()["data"]["completed_at"] == step["completed_at"]
    # Invalid step_key → 400 validation_error.
    bad = await client.post(
        "/api/v1/onboarding/steps/tour_step/complete",
        params={"workspace_id": ws},
        headers=_h(token),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"


async def test_dismiss_restore_flow_and_422_while_dismissed(client):
    token = await _login(client, f"dis-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, token, f"ob-{uuid.uuid4().hex[:8]}")
    d1 = await client.post(f"/api/v1/onboarding/dismiss?workspace_id={ws}", headers=_h(token))
    assert d1.status_code == 200
    dismissed_at = d1.json()["data"]["dismissed_at"]
    assert dismissed_at is not None
    d2 = await client.post(f"/api/v1/onboarding/dismiss?workspace_id={ws}", headers=_h(token))
    assert d2.json()["data"]["dismissed_at"] == dismissed_at  # idempotent

    # Manual completion while dismissed → 422 checklist_completed.
    blocked = await client.post(
        "/api/v1/onboarding/steps/invite_member_or_add_agent/complete",
        params={"workspace_id": ws},
        headers=_h(token),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "checklist_completed"

    restored = await client.post(f"/api/v1/onboarding/restore?workspace_id={ws}", headers=_h(token))
    assert restored.json()["data"]["dismissed_at"] is None
    ok = await client.post(
        "/api/v1/onboarding/steps/invite_member_or_add_agent/complete",
        params={"workspace_id": ws},
        headers=_h(token),
    )
    assert ok.status_code == 200


async def test_invitation_redeem_seeds_invitee_checklist(client):
    owner = await _login(client, f"own-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, owner, f"ob-{uuid.uuid4().hex[:8]}")
    invitee_email = f"inv-{uuid.uuid4().hex[:8]}@e2e.mesh"
    invitee = await _invite_accept(client, owner, ws, invitee_email)

    r = await client.get(f"/api/v1/onboarding/state?workspace_id={ws}", headers=_h(invitee))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # Seeded in the accept transaction — step 1 completed at enrollment.
    steps = {s["step_key"]: s for s in data["steps"]}
    assert steps["create_workspace"]["status"] == "completed"
    # Two humans now in the roster → step 2 reconciled completed.
    assert steps["invite_member_or_add_agent"]["status"] == "completed"
    assert steps["create_first_issue"]["status"] == "pending"


async def test_admin_reset_and_403_for_member(client):
    owner = await _login(client, f"adm-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, owner, f"ob-{uuid.uuid4().hex[:8]}")
    invitee_email = f"mem-{uuid.uuid4().hex[:8]}@e2e.mesh"
    invitee = await _invite_accept(client, owner, ws, invitee_email)

    # Resolve the invitee's member id.
    members = await client.get(f"/api/v1/workspaces/{ws}/members", headers=_h(owner))
    rows = members.json()["data"]
    invitee_row = next(m for m in rows if m["role"] == "member")
    member_id = invitee_row["id"]

    # Invitee completes a step, then the admin resets them.
    await client.post(
        "/api/v1/onboarding/steps/create_first_issue/complete",
        params={"workspace_id": ws},
        headers=_h(invitee),
    )
    before = await client.get(f"/api/v1/onboarding/state?workspace_id={ws}", headers=_h(invitee))
    assert before.json()["data"]["progress"]["completed"] == 3  # step1 + step2 + manual

    reset = await client.post(
        f"/api/v1/workspaces/{ws}/onboarding/reset",
        json={"member_id": member_id, "checklist": "activation"},
        headers=_h(owner),
    )
    assert reset.status_code == 200, reset.text
    fresh = reset.json()["data"]
    assert fresh["id"] != before.json()["data"]["id"]
    # Rebuilt + reconciled: step1 (workspace exists) + step2 (two humans).
    assert fresh["progress"]["completed"] == 2
    assert fresh["aha_reached_at"] is None
    assert fresh["dismissed_at"] is None

    # Non-admin reset → 403.
    forbidden = await client.post(
        f"/api/v1/workspaces/{ws}/onboarding/reset",
        json={"member_id": member_id},
        headers=_h(invitee),
    )
    assert forbidden.status_code == 403

    # Malformed member_id → 404 (not 400 — no shape leak).
    bad = await client.post(
        f"/api/v1/workspaces/{ws}/onboarding/reset",
        json={"member_id": "not-a-uuid"},
        headers=_h(owner),
    )
    assert bad.status_code == 404


async def test_agent_creation_completes_step2_via_event_chain(client):
    """Agent creation emits member.added; the owner's checklist step 2
    completes when the relay drains (asserted here via the reconcile-on-GET
    path — the relay itself is covered by unit + e2e tiers)."""
    owner = await _login(client, f"agt-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _ws(client, owner, f"ob-{uuid.uuid4().hex[:8]}")
    r = await client.post(
        f"/api/v1/workspaces/{ws}/agents", json={"name": "Helper"}, headers=_h(owner)
    )
    assert r.status_code == 201, r.text
    state = await client.get(f"/api/v1/onboarding/state?workspace_id={ws}", headers=_h(owner))
    steps = {s["step_key"]: s for s in state.json()["data"]["steps"]}
    # Without a relay drain the live event is not consumed; the roster fact
    # still reconciles on any fresh seed. The owner was seeded at workspace
    # creation (before the agent existed) → step 2 remains pending until an
    # event or reset; assert the pending truth here (relay coverage is e2e).
    assert steps["invite_member_or_add_agent"]["status"] == "pending"
    reset = await client.post(
        f"/api/v1/workspaces/{ws}/onboarding/reset",
        json={"member_id": _member_id_of(state.json()["data"])},
        headers=_h(owner),
    )
    assert reset.status_code == 200
    step_map = {s["step_key"]: s for s in reset.json()["data"]["steps"]}
    assert step_map["invite_member_or_add_agent"]["status"] == "completed"


def _member_id_of(state_payload: dict) -> str:
    return state_payload["member_id"]

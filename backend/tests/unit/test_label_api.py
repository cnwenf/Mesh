"""In-process label-property definition-layer API tests (route layer).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the label-property.md §3.1 definition-layer endpoints over HTTP:
§6.14 envelopes (data / next_cursor), the auth chain (401 / 404 workspace
leak / 403 role matrix), module error codes (label_name_taken /
field_key_taken / invalid_field_config / field_inactive / conflict),
If-Match optimistic concurrency and write-endpoint rate-limit headers.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.config import load_settings

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-label-test-signing-secret-0000",
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
    body = {"name": "Site Revamp", "key": f"K{uuid.uuid4().hex[:4].upper()}"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# labels over HTTP
# ---------------------------------------------------------------------------


async def test_label_full_crud_flow_envelopes(client):
    owner = await _register_and_login(client, "owner@corp.com")
    ws = await _create_workspace(client, owner, "lbl-flow")
    # Create (workspace scope) → 201 + data envelope.
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "bug", "color": "#e5484d", "description": "defects"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201, resp.text
    label = resp.json()["data"]
    assert label["scope"] == "workspace"
    assert label["color"] == "#e5484d"
    # Rate-limit headers on writes.
    assert resp.headers["x-ratelimit-limit"] == "120"
    # List → list envelope with next_cursor=null (last page).
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(owner)
    )
    assert listing.status_code == 200
    body = listing.json()
    assert [item["name"] for item in body["data"]] == ["bug"]
    assert body["next_cursor"] is None
    # PATCH workspace-less path with If-Match.
    patched = await client.patch(
        f"/api/v1/labels/{label['id']}",
        json={"name": "defect"},
        headers={**_auth(owner), "If-Match": label["updated_at"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["name"] == "defect"
    # Stale If-Match → 409 conflict envelope.
    conflict = await client.patch(
        f"/api/v1/labels/{label['id']}",
        json={"name": "whatever"},
        headers={**_auth(owner), "If-Match": label["updated_at"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"
    # DELETE.
    deleted = await client.delete(f"/api/v1/labels/{label['id']}", headers=_auth(owner))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": label["id"], "deleted": True}
    # Gone → workspace-less paths resolve tenant first, then 404.
    gone = await client.patch(
        f"/api/v1/labels/{label['id']}", json={"name": "x"}, headers=_auth(owner)
    )
    assert gone.status_code == 404


async def test_label_project_scope_and_filters(client):
    owner = await _register_and_login(client, "owner2@corp.com")
    ws = await _create_workspace(client, owner, "lbl-scope")
    project = await _create_project(client, owner, ws["id"])
    await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "ws-tag", "color": "#111111"},
        headers=_auth(owner),
    )
    proj_resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "proj-tag", "color": "#222222", "project_id": project["id"]},
        headers=_auth(owner),
    )
    assert proj_resp.status_code == 201, proj_resp.text
    assert proj_resp.json()["data"]["scope"] == "project"
    # project_id filter includes workspace-level labels.
    filtered = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels",
        params={"project_id": project["id"]},
        headers=_auth(owner),
    )
    names = {item["name"] for item in filtered.json()["data"]}
    assert names == {"ws-tag", "proj-tag"}
    # Invalid project_id query → 400 validation_error.
    bad = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels",
        params={"project_id": "nope"},
        headers=_auth(owner),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"


async def test_label_name_taken_and_validation_codes(client):
    owner = await _register_and_login(client, "owner3@corp.com")
    ws = await _create_workspace(client, owner, "lbl-dup")
    ok = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "bug", "color": "#e5484d"},
        headers=_auth(owner),
    )
    assert ok.status_code == 201
    dup = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "bug", "color": "#000000"},
        headers=_auth(owner),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "label_name_taken"
    bad_color = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "x", "color": "chartreuse"},
        headers=_auth(owner),
    )
    assert bad_color.status_code == 400
    assert bad_color.json()["error"]["code"] == "validation_error"


async def test_label_auth_chain(client):
    owner = await _register_and_login(client, "owner4@corp.com")
    ws = await _create_workspace(client, owner, "lbl-auth")
    _, member = await _invite_accept(client, owner, ws["id"], "member4@corp.com")
    # Missing token → 401.
    anon = await client.get(f"/api/v1/workspaces/{ws['id']}/labels")
    assert anon.status_code == 401
    # Foreign workspace id → same 404 as unknown (no existence leak).
    foreign = await client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}/labels", headers=_auth(member)
    )
    assert foreign.status_code == 404
    # Member cannot write definitions.
    forbidden = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "nope", "color": "#ffffff"},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403
    # Member can read.
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(member)
    )
    assert listing.status_code == 200


async def test_label_non_uuid_paths_are_404(client):
    owner = await _register_and_login(client, "owner5@corp.com")
    _ = await _create_workspace(client, owner, "lbl-uuid")
    resp = await client.patch("/api/v1/labels/not-a-uuid", json={"name": "x"}, headers=_auth(owner))
    assert resp.status_code == 404
    resp2 = await client.delete("/api/v1/labels/not-a-uuid", headers=_auth(owner))
    assert resp2.status_code == 404


async def test_label_cross_workspace_404(client):
    owner_a = await _register_and_login(client, "ownerA@corp.com")
    owner_b = await _register_and_login(client, "ownerB@corp.com")
    ws_a = await _create_workspace(client, owner_a, "lbl-ws-a")
    await _create_workspace(client, owner_b, "lbl-ws-b")
    created = await client.post(
        f"/api/v1/workspaces/{ws_a['id']}/labels",
        json={"name": "secret", "color": "#ffffff"},
        headers=_auth(owner_a),
    )
    label_id = created.json()["data"]["id"]
    # Owner B resolves the tenant (SECURITY DEFINER), then fails the
    # membership gate — rewritten to the resource 404, byte-identical to
    # an unknown id, so label existence never leaks (§5.3).
    foreign = await client.patch(
        f"/api/v1/labels/{label_id}", json={"name": "hax"}, headers=_auth(owner_b)
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["message"] == "label not found"
    unknown = await client.patch(
        f"/api/v1/labels/{uuid.uuid4()}", json={"name": "hax"}, headers=_auth(owner_b)
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["message"] == "label not found"


# ---------------------------------------------------------------------------
# custom fields + options over HTTP
# ---------------------------------------------------------------------------


async def test_custom_field_crud_flow(client):
    owner = await _register_and_login(client, "cf@corp.com")
    ws = await _create_workspace(client, owner, "cf-flow")
    created = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={
            "name": "Severity",
            "field_key": "severity",
            "type": "single_select",
            "options": [
                {"name": "Minor", "color": "#888888"},
                {"name": "Major", "color": "#f5a623"},
            ],
        },
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    field = created.json()["data"]
    assert [option["name"] for option in field["options"]] == ["Minor", "Major"]
    # List with is_active filter.
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        params={"is_active": True},
        headers=_auth(owner),
    )
    assert [item["field_key"] for item in listing.json()["data"]] == ["severity"]
    # PATCH: deactivate.
    patched = await client.patch(
        f"/api/v1/custom-fields/{field['id']}",
        json={"is_active": False},
        headers=_auth(owner),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["is_active"] is False
    # DELETE cascades.
    deleted = await client.delete(
        f"/api/v1/custom-fields/{field['id']}", headers=_auth(owner)
    )
    assert deleted.status_code == 200
    gone = await client.get(
        f"/api/v1/workspaces/{ws['id']}/custom-fields", headers=_auth(owner)
    )
    assert gone.json()["data"] == []


async def test_custom_field_error_codes(client):
    owner = await _register_and_login(client, "cf2@corp.com")
    ws = await _create_workspace(client, owner, "cf-codes")
    # Bad field_key → 400.
    bad_key = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "X", "field_key": "Not Valid", "type": "text"},
        headers=_auth(owner),
    )
    assert bad_key.status_code == 400
    # Unsupported type → 400.
    bad_type = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "X", "field_key": "x1", "type": "formula"},
        headers=_auth(owner),
    )
    assert bad_type.status_code == 400
    # Invalid config for type → 422 invalid_field_config.
    bad_config = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "X", "field_key": "x2", "type": "text", "config": {"precision": 2}},
        headers=_auth(owner),
    )
    assert bad_config.status_code == 422
    assert bad_config.json()["error"]["code"] == "invalid_field_config"
    # Invalid default for type → 422 invalid_field_config.
    bad_default = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "X", "field_key": "x3", "type": "number", "default_value": "NaN"},
        headers=_auth(owner),
    )
    assert bad_default.status_code == 422
    assert bad_default.json()["error"]["code"] == "invalid_field_config"
    # Duplicate field_key → 409 field_key_taken.
    ok = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "A", "field_key": "impact", "type": "number"},
        headers=_auth(owner),
    )
    assert ok.status_code == 201
    dup = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "B", "field_key": "impact", "type": "text"},
        headers=_auth(owner),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "field_key_taken"
    # Extra body fields rejected (strict schemas).
    extra = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "C", "field_key": "x4", "type": "text", "surprise": 1},
        headers=_auth(owner),
    )
    assert extra.status_code == 400


async def test_custom_field_write_requires_admin(client):
    owner = await _register_and_login(client, "cf3@corp.com")
    ws = await _create_workspace(client, owner, "cf-auth")
    _, member = await _invite_accept(client, owner, ws["id"], "member-cf@corp.com")
    forbidden = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={"name": "X", "field_key": "x", "type": "text"},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403
    readable = await client.get(
        f"/api/v1/workspaces/{ws['id']}/custom-fields", headers=_auth(member)
    )
    assert readable.status_code == 200


async def test_option_endpoints_and_codes(client):
    owner = await _register_and_login(client, "opt@corp.com")
    ws = await _create_workspace(client, owner, "opt-flow")
    field = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "Severity", "field_key": "severity", "type": "single_select"},
            headers=_auth(owner),
        )
    ).json()["data"]
    fid = field["id"]
    # Create option.
    created = await client.post(
        f"/api/v1/custom-fields/{fid}/options",
        json={"name": "Critical", "color": "#e5484d"},
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    option = created.json()["data"]
    # Duplicate name → 409 conflict.
    dup = await client.post(
        f"/api/v1/custom-fields/{fid}/options", json={"name": "Critical"}, headers=_auth(owner)
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"
    # List options.
    listing = await client.get(f"/api/v1/custom-fields/{fid}/options", headers=_auth(owner))
    assert [item["name"] for item in listing.json()["data"]] == ["Critical"]
    # Update option with If-Match.
    patched = await client.patch(
        f"/api/v1/custom-fields/{fid}/options/{option['id']}",
        json={"is_active": False},
        headers={**_auth(owner), "If-Match": option["updated_at"]},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["is_active"] is False
    # Delete option.
    deleted = await client.delete(
        f"/api/v1/custom-fields/{fid}/options/{option['id']}", headers=_auth(owner)
    )
    assert deleted.status_code == 200
    # Options on a non-select field → 422 invalid_field_config.
    text_field = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "Notes", "field_key": "notes", "type": "text"},
            headers=_auth(owner),
        )
    ).json()["data"]
    rejected = await client.post(
        f"/api/v1/custom-fields/{text_field['id']}/options",
        json={"name": "nope"},
        headers=_auth(owner),
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_field_config"
    # Options on an inactive field → 422 field_inactive.
    await client.patch(
        f"/api/v1/custom-fields/{fid}", json={"is_active": False}, headers=_auth(owner)
    )
    inactive = await client.post(
        f"/api/v1/custom-fields/{fid}/options", json={"name": "Late"}, headers=_auth(owner)
    )
    assert inactive.status_code == 422
    assert inactive.json()["error"]["code"] == "field_inactive"
    # Unknown field / option ids → 404.
    missing = await client.get(
        f"/api/v1/custom-fields/{uuid.uuid4()}/options", headers=_auth(owner)
    )
    assert missing.status_code == 404
    missing_opt = await client.delete(
        f"/api/v1/custom-fields/{fid}/options/{uuid.uuid4()}", headers=_auth(owner)
    )
    assert missing_opt.status_code == 404


async def test_option_write_requires_admin(client):
    owner = await _register_and_login(client, "opt2@corp.com")
    ws = await _create_workspace(client, owner, "opt-auth")
    _, member = await _invite_accept(client, owner, ws["id"], "member-opt@corp.com")
    field = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "S", "field_key": "s", "type": "multi_select"},
            headers=_auth(owner),
        )
    ).json()["data"]
    forbidden = await client.post(
        f"/api/v1/custom-fields/{field['id']}/options",
        json={"name": "x"},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403
    readable = await client.get(
        f"/api/v1/custom-fields/{field['id']}/options", headers=_auth(member)
    )
    assert readable.status_code == 200


async def test_project_lead_can_manage_project_scoped_definitions(client):
    owner = await _register_and_login(client, "lead@corp.com")
    ws = await _create_workspace(client, owner, "lead-ws")
    lead_id, lead = await _invite_accept(client, owner, ws["id"], "lead@corp-member.com")
    project = await _create_project(client, owner, ws["id"], lead_member_id=lead_id)
    # Lead creates a project-scoped label + field.
    label = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "customer-a", "color": "#aabbcc", "project_id": project["id"]},
        headers=_auth(lead),
    )
    assert label.status_code == 201, label.text
    field = await client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={
            "name": "Impact",
            "field_key": "impact",
            "type": "number",
            "project_id": project["id"],
        },
        headers=_auth(lead),
    )
    assert field.status_code == 201, field.text
    # But not workspace-level.
    ws_level = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "global", "color": "#ffffff"},
        headers=_auth(lead),
    )
    assert ws_level.status_code == 403


async def test_label_rate_limit_headers_present(client):
    owner = await _register_and_login(client, "rl@corp.com")
    ws = await _create_workspace(client, owner, "rl-ws")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "rl", "color": "#ffffff"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    assert "x-ratelimit-remaining" in resp.headers
    assert "x-ratelimit-reset" in resp.headers


async def test_prefixless_endpoints_uniform_404_message(client):
    """L3 product-wide parity (workspace.md §5.3): label / custom-field /
    custom-field-option workspace-less paths return the SAME 404 message for
    "unknown id" and "exists in another tenant" — no existence oracle,
    matching the issue module. Option paths resolve through the parent
    field definition, so their resource message is the field's."""
    owner_a = await _register_and_login(client, "l3l-a@corp.com")
    owner_b = await _register_and_login(client, "l3l-b@corp.com")
    await _create_workspace(client, owner_a, "l3l-a")
    ws_b = await _create_workspace(client, owner_b, "l3l-b")

    label_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/labels",
            json={"name": "secret", "color": "#123456"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    field_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/custom-fields",
            json={
                "name": "Severity",
                "field_key": "severity",
                "type": "single_select",
                "options": [{"name": "Minor", "color": "#888888"}],
            },
            headers=_auth(owner_b),
        )
    ).json()["data"]
    option_b = field_b["options"][0]
    random_id = str(uuid.uuid4())

    probes = (
        # (existing-id probe, existing id, resource message)
        (
            lambda target: client.patch(
                f"/api/v1/labels/{target}", json={"name": "x"}, headers=_auth(owner_a)
            ),
            label_b["id"],
            "label not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/custom-fields/{target}",
                json={"name": "x"},
                headers=_auth(owner_a),
            ),
            field_b["id"],
            "custom field not found",
        ),
        (
            lambda target: client.get(
                f"/api/v1/custom-fields/{target}/options", headers=_auth(owner_a)
            ),
            field_b["id"],
            "custom field not found",
        ),
        (
            # option paths resolve through the parent field definition, so
            # the gate rewrite carries the field's message either way
            lambda target: client.patch(
                f"/api/v1/custom-fields/{target}/options/{option_b['id']}",
                json={"name": "x"},
                headers=_auth(owner_a),
            ),
            field_b["id"],
            "custom field not found",
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

    # Soft-deleted label + non-member → same message.
    await client.delete(f"/api/v1/labels/{label_b['id']}", headers=_auth(owner_b))
    deleted_probe = await client.patch(
        f"/api/v1/labels/{label_b['id']}", json={"name": "x"}, headers=_auth(owner_a)
    )
    assert deleted_probe.status_code == 404
    assert deleted_probe.json()["error"]["message"] == "label not found"

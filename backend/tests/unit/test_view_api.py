"""In-process views API tests (route layer: auth chain, envelopes, codes).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the kanban §3.1 independent endpoint surface, the §3.4 auth matrix
over HTTP, §6.14 envelopes (data / next_cursor), If-Match optimistic
concurrency, named validation codes and write rate-limit headers.
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
        jwt_secret="inprocess-view-test-signing-secret-00000",
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
    return joiner, accepted.json()["data"]["member"]["id"]


async def _create_view(client, token, ws_id, **overrides) -> dict:
    body = {"name": "Sprint Board"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/views", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------


async def test_views_require_authentication(client) -> None:
    resp = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}/views")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    resp = await client.get(f"/api/v1/views/{uuid.uuid4()}", headers=_auth("bogus"))
    assert resp.status_code == 401


async def test_create_get_update_delete_flow(client) -> None:
    token = await _register_and_login(client, "owner-view@corp.com")
    ws = await _create_workspace(client, token, "view-flow")

    created = await _create_view(
        client,
        token,
        ws["id"],
        visibility="shared",
        filters={"operator": "AND", "conditions": [{"field": "priority", "op": "in", "value": ["high"]}]},
        board_settings={"card_fields": ["labels"]},
    )
    assert created["name"] == "Sprint Board"
    assert created["filters"]["conditions"][0]["field"] == "priority"

    got = await client.get(f"/api/v1/views/{created['id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["data"]["id"] == created["id"]

    patched = await client.patch(
        f"/api/v1/views/{created['id']}",
        json={"name": "Renamed", "board_settings": {"wip": {"todo": {"limit": 3}}}},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()["data"]
    assert data["name"] == "Renamed"
    # Shallow merge over HTTP: card_fields survives the wip write.
    assert data["board_settings"]["card_fields"] == ["labels"]
    assert data["board_settings"]["wip"] == {"todo": {"limit": 3, "enforcement": "warn"}}

    deleted = await client.delete(f"/api/v1/views/{created['id']}", headers=_auth(token))
    assert deleted.status_code == 204
    gone = await client.get(f"/api/v1/views/{created['id']}", headers=_auth(token))
    assert gone.status_code == 404


async def test_list_views_envelope_and_pagination(client) -> None:
    token = await _register_and_login(client, "lister@corp.com")
    ws = await _create_workspace(client, token, "view-list")
    ids = []
    for index in range(3):
        view = await _create_view(client, token, ws["id"], name=f"V{index}")
        ids.append(view["id"])

    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/views?limit=2", headers=_auth(token)
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert [item["id"] for item in payload["data"]] == ids[:2]
    assert payload["next_cursor"] is not None

    resp2 = await client.get(
        f"/api/v1/workspaces/{ws['id']}/views?limit=2&cursor={payload['next_cursor']}",
        headers=_auth(token),
    )
    payload2 = resp2.json()
    assert [item["id"] for item in payload2["data"]] == ids[2:]
    assert payload2["next_cursor"] is None


async def test_get_view_unknown_and_malformed_id(client) -> None:
    token = await _register_and_login(client, "notfound@corp.com")
    resp = await client.get(f"/api/v1/views/{uuid.uuid4()}", headers=_auth(token))
    assert resp.status_code == 404
    resp = await client.get("/api/v1/views/not-a-uuid", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_if_match_conflict_and_success(client) -> None:
    token = await _register_and_login(client, "ifmatch@corp.com")
    ws = await _create_workspace(client, token, "view-match")
    created = await _create_view(client, token, ws["id"])

    stale = await client.patch(
        f"/api/v1/views/{created['id']}",
        json={"name": "Stale"},
        headers={**_auth(token), "If-Match": '"2020-01-01T00:00:00Z"'},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"

    ok = await client.patch(
        f"/api/v1/views/{created['id']}",
        json={"name": "Fresh"},
        headers={**_auth(token), "If-Match": f'"{created["updated_at"]}"'},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["name"] == "Fresh"


async def test_cross_workspace_access_is_404(client) -> None:
    owner_a = await _register_and_login(client, "ws-a@corp.com")
    ws_a = await _create_workspace(client, owner_a, "view-ws-a")
    view = await _create_view(client, owner_a, ws_a["id"])

    owner_b = await _register_and_login(client, "ws-b@corp.com")
    await _create_workspace(client, owner_b, "view-ws-b")

    foreign = await client.get(f"/api/v1/views/{view['id']}", headers=_auth(owner_b))
    assert foreign.status_code == 404
    patched = await client.patch(
        f"/api/v1/views/{view['id']}", json={"name": "Hacked"}, headers=_auth(owner_b)
    )
    assert patched.status_code == 404
    deleted = await client.delete(f"/api/v1/views/{view['id']}", headers=_auth(owner_b))
    assert deleted.status_code == 404


async def test_private_view_hidden_from_workspace_mate(client) -> None:
    owner = await _register_and_login(client, "priv-owner@corp.com")
    ws = await _create_workspace(client, owner, "view-priv")
    mate, _ = await _invite_accept(client, owner, ws["id"], "priv-mate@corp.com")

    private = await _create_view(client, owner, ws["id"], name="Mine")
    shared = await _create_view(client, owner, ws["id"], name="Shared", visibility="shared")

    mate_get = await client.get(f"/api/v1/views/{private['id']}", headers=_auth(mate))
    assert mate_get.status_code == 404

    mate_list = await client.get(f"/api/v1/workspaces/{ws['id']}/views", headers=_auth(mate))
    ids = {item["id"] for item in mate_list.json()["data"]}
    assert ids == {shared["id"]}

    mate_patch = await client.patch(
        f"/api/v1/views/{shared['id']}", json={"name": "Nope"}, headers=_auth(mate)
    )
    assert mate_patch.status_code == 403
    assert mate_patch.json()["error"]["code"] == "forbidden"


async def test_duplicate_and_wip_and_reorder_endpoints(client) -> None:
    token = await _register_and_login(client, "actions@corp.com")
    ws = await _create_workspace(client, token, "view-actions")
    first = await _create_view(client, token, ws["id"], name="Base")
    second = await _create_view(client, token, ws["id"], name="Other")

    dup = await client.post(f"/api/v1/views/{first['id']}/duplicate", headers=_auth(token))
    assert dup.status_code == 201, dup.text
    assert dup.json()["data"]["name"] == "Base (copy)"

    wip = await client.patch(
        f"/api/v1/views/{first['id']}/wip",
        json={"group_key": "in_progress", "limit": 5, "enforcement": "block"},
        headers=_auth(token),
    )
    assert wip.status_code == 200, wip.text
    assert wip.json()["data"]["board_settings"]["wip"] == {
        "in_progress": {"limit": 5, "enforcement": "block"}
    }

    reorder = await client.patch(
        f"/api/v1/workspaces/{ws['id']}/views/reorder",
        json={"view_ids": [second["id"], first["id"]]},
        headers=_auth(token),
    )
    assert reorder.status_code == 200, reorder.text
    assert [item["position"] for item in reorder.json()["data"]] == [1.0, 2.0]


async def test_invalid_config_named_codes_over_http(client) -> None:
    token = await _register_and_login(client, "invalid@corp.com")
    ws = await _create_workspace(client, token, "view-invalid")

    bad_filters = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={
            "name": "Bad",
            "filters": {"operator": "AND", "conditions": [{"field": "evil", "op": "eq", "value": "x"}]},
        },
        headers=_auth(token),
    )
    assert bad_filters.status_code == 400
    assert bad_filters.json()["error"]["code"] == "invalid_filters"

    bad_group = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={"name": "Bad2", "group_by": "severity"},
        headers=_auth(token),
    )
    assert bad_group.status_code == 400
    assert bad_group.json()["error"]["code"] == "invalid_group_by"

    too_complex = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={
            "name": "Bad3",
            "filters": {
                "operator": "AND",
                "conditions": [
                    {"field": "priority", "op": "eq", "value": "high"} for _ in range(21)
                ],
            },
        },
        headers=_auth(token),
    )
    assert too_complex.status_code == 400
    assert too_complex.json()["error"]["code"] == "filter_too_complex"

    pydantic_shape = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={"name": ""},
        headers=_auth(token),
    )
    assert pydantic_shape.status_code == 400
    assert pydantic_shape.json()["error"]["code"] == "validation_error"


async def test_duplicate_name_conflict_over_http(client) -> None:
    token = await _register_and_login(client, "conflict@corp.com")
    ws = await _create_workspace(client, token, "view-conflict")
    await _create_view(client, token, ws["id"], name="Dup")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={"name": "Dup"},
        headers=_auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "view_name_taken"


async def test_write_rate_limit_headers_present(client) -> None:
    token = await _register_and_login(client, "ratelimit@corp.com")
    ws = await _create_workspace(client, token, "view-rl")
    resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={"name": "RL"},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.headers["x-ratelimit-limit"] == "120"
    assert "x-ratelimit-remaining" in resp.headers
    assert "x-ratelimit-reset" in resp.headers


async def test_prefixless_endpoints_uniform_404_message(client) -> None:
    """L3 product-wide parity (workspace.md §5.3): /views/{id} paths return
    the SAME 404 message for "unknown id" and "exists in another tenant" —
    no view-existence oracle, matching the issue module."""
    owner_a = await _register_and_login(client, "l3v-a@corp.com")
    owner_b = await _register_and_login(client, "l3v-b@corp.com")
    await _create_workspace(client, owner_a, "l3v-a")
    ws_b = await _create_workspace(client, owner_b, "l3v-b")
    view_b = await _create_view(client, owner_b, ws_b["id"], name="Secret Board")
    random_id = str(uuid.uuid4())

    probes = (
        # (existing-id probe, existing id, resource message)
        (
            lambda target: client.get(f"/api/v1/views/{target}", headers=_auth(owner_a)),
            view_b["id"],
            "view not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/views/{target}", json={"name": "x"}, headers=_auth(owner_a)
            ),
            view_b["id"],
            "view not found",
        ),
        (
            # non-member DELETE is rejected by the gate — the view survives
            lambda target: client.delete(f"/api/v1/views/{target}", headers=_auth(owner_a)),
            view_b["id"],
            "view not found",
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

    # Soft-deleted + non-member → same message.
    await client.delete(f"/api/v1/views/{view_b['id']}", headers=_auth(owner_b))
    deleted_probe = await client.get(f"/api/v1/views/{view_b['id']}", headers=_auth(owner_a))
    assert deleted_probe.status_code == 404
    assert deleted_probe.json()["error"]["message"] == "view not found"

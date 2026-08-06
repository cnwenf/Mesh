"""In-process projection API tests — GET /views/{id}/issues over HTTP.

Real create_app() via ASGITransport against real PostgreSQL + Redis. Covers the
README §6.14 grouped/overall-cursor envelope over the wire, group_by mapping,
column_target_status, cross-workspace 404, and dynamic label grouping.
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
        jwt_secret="inprocess-projection-test-signing-secret",
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
    resp = await client.post("/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_view(client, token, ws_id, **overrides) -> dict:
    body = {"name": "Board"}
    body.update(overrides)
    resp = await client.post(f"/api/v1/workspaces/{ws_id}/views", json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_project(client, token, ws_id) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": "Projection Project", "key": f"P{uuid.uuid4().hex[:5].upper()}"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, **overrides) -> dict:
    body = {"title": f"Issue {uuid.uuid4().hex[:6]}"}
    body.update(overrides)
    resp = await client.post(f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def test_get_view_issues_grouped_envelope(client) -> None:
    token = await _register_and_login(client, "owner-proj@corp.com")
    ws = await _create_workspace(client, token, "proj-env")
    await _create_issue(client, token, ws["id"], priority="high")
    await _create_issue(client, token, ws["id"], priority="high")
    await _create_issue(client, token, ws["id"], priority="low")
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="priority")

    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # Top-level grouped contract — no "data" wrapper.
    assert payload["group_by"] == "priority"
    assert "groups" in payload and "next_cursor" in payload
    assert "data" not in payload
    by_key = {group["key"]: group for group in payload["groups"]}
    assert by_key["high"]["count"] == 2
    assert len(by_key["high"]["data"]) == 2
    assert by_key["low"]["count"] == 1
    # No per-group cursor.
    for group in payload["groups"]:
        assert "cursor" not in group


async def test_get_view_issues_state_category_column_target_status(client) -> None:
    token = await _register_and_login(client, "owner-cts@corp.com")
    ws = await _create_workspace(client, token, "proj-cts")
    project = await _create_project(client, token, ws["id"])
    issue = await _create_issue(client, token, ws["id"], project_id=project["id"])
    view = await _create_view(
        client,
        token,
        ws["id"],
        visibility="shared",
        project_id=project["id"],
        group_by="state_category",
    )

    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(token))
    payload = resp.json()
    mapping = payload["column_target_status"]
    # The created issue's status is the default for its category; the mapping
    # resolves every category to a concrete status id.
    assert mapping["todo"]
    todo = next(g for g in payload["groups"] if g["key"] == "todo")
    assert any(card["id"] == issue["id"] for card in todo["data"])


async def test_workspace_wide_view_omits_ambiguous_column_target_status(client) -> None:
    token = await _register_and_login(client, "owner-cts-wide@corp.com")
    ws = await _create_workspace(client, token, "proj-cts-wide")
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="state_category")
    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert "column_target_status" not in resp.json()


async def test_get_view_issues_pagination_overall_cursor(client) -> None:
    token = await _register_and_login(client, "owner-page@corp.com")
    ws = await _create_workspace(client, token, "proj-page")
    for _ in range(3):
        await _create_issue(client, token, ws["id"])
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="state_category")

    page1 = await client.get(f"/api/v1/views/{view['id']}/issues?limit=2", headers=_auth(token))
    payload1 = page1.json()
    assert payload1["next_cursor"] is not None
    page2 = await client.get(
        f"/api/v1/views/{view['id']}/issues?limit=2&cursor={payload1['next_cursor']}",
        headers=_auth(token),
    )
    assert page2.status_code == 200, page2.text
    assert page2.json()["next_cursor"] is None


async def test_get_view_issues_cross_workspace_not_found(client) -> None:
    token = await _register_and_login(client, "owner-xws@corp.com")
    ws = await _create_workspace(client, token, "proj-xws")
    view = await _create_view(client, token, ws["id"], visibility="shared")

    other = await _register_and_login(client, "stranger-xws@corp.com")
    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_get_view_issues_label_group_by_returns_dynamic_skeleton(client) -> None:
    token = await _register_and_login(client, "owner-label@corp.com")
    ws = await _create_workspace(client, token, "proj-label")
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="label")

    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["group_by"] == "label"
    assert payload["multi_value_axis"] is True
    assert payload["groups"] == [
        {
            "key": "__none__",
            "label": "No label",
            "count": 0,
            "wip": None,
            "data": [],
        }
    ]


async def test_get_view_issues_timeline_layout_501(client) -> None:
    token = await _register_and_login(client, "owner-timeline@corp.com")
    ws = await _create_workspace(client, token, "proj-timeline")
    view = await _create_view(client, token, ws["id"], visibility="shared", layout="timeline")

    resp = await client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(token))
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "not_implemented"


# ---------------------------------------------------------------------------
# POST /views/{id}/moves + /reorder (over HTTP)
# ---------------------------------------------------------------------------


async def test_quick_create_cell_is_idempotent_over_http(client) -> None:
    token = await _register_and_login(client, "owner-quick-create@corp.com")
    ws = await _create_workspace(client, token, "proj-quick-create")
    view = await _create_view(
        client,
        token,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        sub_group_by="priority",
    )
    headers = {
        **_auth(token),
        "Idempotency-Key": "api-quick-create-retry-1",
    }
    url = f"/api/v1/views/{view['id']}/issues"
    first = await client.post(
        url,
        json={
            "title": "Created from the board cell",
            "group_key": "in_progress",
            "sub_group_key": "urgent",
        },
        headers=headers,
    )
    replay = await client.post(
        url,
        json={
            "title": "Changed retry body",
            "group_key": "todo",
            "sub_group_key": "low",
        },
        headers=headers,
    )
    assert first.status_code == replay.status_code == 201
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    assert replay.json()["data"]["title"] == "Created from the board cell"

    board = await client.get(url, headers=_auth(token))
    payload = board.json()
    cards = [card for lane in payload["lanes"] for group in lane["groups"] for card in group["data"]]
    assert [card["id"] for card in cards].count(first.json()["data"]["id"]) == 1


async def test_move_card_cross_column(client) -> None:
    token = await _register_and_login(client, "owner-move@corp.com")
    ws = await _create_workspace(client, token, "proj-move")
    issue = await _create_issue(client, token, ws["id"])
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="state_category")

    resp = await client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": "in_progress",
            "position": 1.0,
            "version": issue["version"],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["state_category"] == "in_progress"
    assert data["version"] == issue["version"] + 1


async def test_move_card_stale_version_409(client) -> None:
    token = await _register_and_login(client, "owner-409@corp.com")
    ws = await _create_workspace(client, token, "proj-409")
    issue = await _create_issue(client, token, ws["id"])
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="state_category")

    resp = await client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": "in_progress",
            "position": 1.0,
            "version": issue["version"] + 5,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_move_card_wip_block_422(client) -> None:
    token = await _register_and_login(client, "owner-wip@corp.com")
    ws = await _create_workspace(client, token, "proj-wip")
    # Fill the in_progress column to its limit of 1 (block).
    statuses = await client.get(f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(token))
    by_category = {s["category"]: s["id"] for s in statuses.json()["data"]}
    await _create_issue(client, token, ws["id"], status_id=by_category["in_progress"])
    mover = await _create_issue(client, token, ws["id"])
    view = await _create_view(
        client,
        token,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 1, "enforcement": "block"}}},
    )
    resp = await client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": mover["id"],
            "to_group_key": "in_progress",
            "position": 1.0,
            "version": mover["version"],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "wip_limit_exceeded"
    assert err["details"]["group_key"] == "in_progress"
    assert err["details"]["limit"] == 1


async def test_reorder_card(client) -> None:
    token = await _register_and_login(client, "owner-reorder@corp.com")
    ws = await _create_workspace(client, token, "proj-reorder")
    issue = await _create_issue(client, token, ws["id"])
    view = await _create_view(client, token, ws["id"], visibility="shared", group_by="state_category")

    resp = await client.post(
        f"/api/v1/views/{view['id']}/reorder",
        json={"issue_id": issue["id"], "to_group_key": "todo", "position": 3.5},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["position"] == 3.5


async def test_get_view_issues_is_rate_limited(client, monkeypatch) -> None:
    """§5.3: the view execution read endpoint is rate-limited (429 rate_limited)."""
    from mesh.views import routes as view_routes

    monkeypatch.setattr(view_routes, "READ_LIMIT", 2)
    token = await _register_and_login(client, "owner-rl@corp.com")
    ws = await _create_workspace(client, token, "proj-rl")
    view = await _create_view(client, token, ws["id"], visibility="shared")
    url = f"/api/v1/views/{view['id']}/issues"

    r1 = await client.get(url, headers=_auth(token))
    assert r1.status_code == 200
    assert r1.headers["x-ratelimit-limit"] == "2"  # read limiter wired
    r2 = await client.get(url, headers=_auth(token))
    assert r2.status_code == 200
    r3 = await client.get(url, headers=_auth(token))
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "rate_limited"

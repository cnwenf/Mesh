"""In-process issue API tests (route layer + channel authorizer).

Runs the real create_app() via ASGITransport against real PostgreSQL + Redis.
Covers the issue.md §3.1 endpoint surface, auth gates, §6.14 envelopes/error
codes, the ``issue:{id}`` resource-level subscription authorizer (README
§6.7) and service branches not hit by the service-level unit tests.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.issue import Issue
from mesh.realtime.auth import Principal

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-issue-test-signing-secret-0000",
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
    return resp.json()["data"]


async def _create_project(client, token, ws_id, key, **fields) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"P {key}", "key": key, **fields},
        headers=_auth(token),
    )
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, **fields) -> dict:
    fields.setdefault("title", "t")
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json=fields,
        headers=_auth(token),
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
    await client.post("/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner))
    return joiner


# ---------------------------------------------------------------------------
# route surface: CRUD + validation + envelopes
# ---------------------------------------------------------------------------


async def test_create_validation_errors(client):
    owner = await _register_and_login(client, "api-val@corp.com")
    ws = await _create_workspace(client, owner, "api-val")
    bad_title = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": ""},
        headers=_auth(owner),
    )
    assert bad_title.status_code == 400  # request validation → §6.14 validation_error
    bad_priority = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "x", "priority": "yesterday"},
        headers=_auth(owner),
    )
    assert bad_priority.status_code == 400
    assert bad_priority.json()["error"]["code"] == "validation_error"
    bad_dates = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "x", "start_date": "2026-08-10", "due_date": "2026-08-01"},
        headers=_auth(owner),
    )
    assert bad_dates.status_code == 400
    bad_uuid = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "x", "project_id": "not-a-uuid"},
        headers=_auth(owner),
    )
    assert bad_uuid.status_code == 400
    # no token → 401
    assert (
        await client.post(f"/api/v1/workspaces/{ws['id']}/issues", json={"title": "x"})
    ).status_code == 401


async def test_get_unknown_and_malformed_ids_are_404(client):
    owner = await _register_and_login(client, "api-404@corp.com")
    assert (
        await client.get(f"/api/v1/issues/{uuid.uuid4()}", headers=_auth(owner))
    ).status_code == 404
    assert (
        await client.get("/api/v1/issues/not-a-uuid", headers=_auth(owner))
    ).status_code == 404
    ws = await _create_workspace(client, owner, "api-404")
    assert (
        await client.get(
            f"/api/v1/workspaces/{ws['id']}/issues/by-identifier/NOPE-9", headers=_auth(owner)
        )
    ).status_code == 404


async def test_patch_fields_and_position_and_activity(client):
    owner = await _register_and_login(client, "api-patch@corp.com")
    ws = await _create_workspace(client, owner, "api-patch")
    issue = await _create_issue(client, owner, ws["id"], estimate="3", estimate_unit="points")
    patched = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={
            "description": "now with details",
            "priority": "medium",
            "estimate": 5,
            "estimate_unit": "hours",
            "due_date": "2026-09-01",
            "start_date": "2026-08-01",
            "position": 42.0,
        },
        headers=_auth(owner),
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["estimate"] == 5 and data["estimate_unit"] == "hours"
    assert data["position"] == 42.0
    # invalid estimate unit
    bad = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"estimate_unit": "bananas"},
        headers=_auth(owner),
    )
    assert bad.status_code == 400
    # If-Match optimistic concurrency (updated_at etag)
    current = (await client.get(f"/api/v1/issues/{issue['id']}", headers=_auth(owner))).json()[
        "data"
    ]
    ok = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "via if-match"},
        headers={**_auth(owner), "If-Match": current["updated_at"]},
    )
    assert ok.status_code == 200
    stale = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "stale"},
        headers={**_auth(owner), "If-Match": "2020-01-01T00:00:00Z"},
    )
    assert stale.status_code == 409


async def test_list_filter_params_sort_and_pagination(client):
    owner = await _register_and_login(client, "api-list@corp.com")
    ws = await _create_workspace(client, owner, "api-list")
    for i in range(5):
        await _create_issue(
            client, owner, ws["id"], title=f"item {i}", priority="high" if i % 2 else "low",
            due_date=f"2026-08-{10 + i:02d}",
        )
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues",
        params={
            "priority": "high",
            "due_before": "2026-08-13",
            "due_after": "2026-08-10",
            "sort": "due_date",
            "order": "asc",
            "limit": 1,
        },
        headers=_auth(owner),
    )
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["next_cursor"]
    page2 = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues",
        params={"priority": "high", "sort": "due_date", "order": "asc", "limit": 1,
                "cursor": body["next_cursor"]},
        headers=_auth(owner),
    )
    assert len(page2.json()["data"]) == 1
    assert page2.json()["data"][0]["id"] != body["data"][0]["id"]
    # invalid sort / order / bad filters JSON
    assert (
        await client.get(
            f"/api/v1/workspaces/{ws['id']}/issues?sort=bogus", headers=_auth(owner)
        )
    ).status_code == 400
    assert (
        await client.get(
            f"/api/v1/workspaces/{ws['id']}/issues?order=sideways", headers=_auth(owner)
        )
    ).status_code == 400
    assert (
        await client.get(
            f"/api/v1/workspaces/{ws['id']}/issues",
            params={"filters": '{"and": 1}'},
            headers=_auth(owner),
        )
    ).status_code == 400
    # group_by by assignee/priority/project; HIGH-A: label is now supported
    grouped = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues?group_by=priority", headers=_auth(owner)
    )
    assert grouped.status_code == 200 and grouped.json()["groups"]
    label = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues?group_by=label", headers=_auth(owner)
    )
    assert label.status_code == 200 and "groups" in label.json()
    # structured filters: in / gte / is_null operators
    sf = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues",
        params={"filters": '{"and": [{"field": "priority", "op": "in", "value": ["high", "low"]},'
                           ' {"field": "position", "op": "gte", "value": 0},'
                           ' {"field": "parent_id", "op": "is_null", "value": true}]}'},
        headers=_auth(owner),
    )
    assert sf.status_code == 200 and len(sf.json()["data"]) == 5


async def test_filter_compiler_rejections(client, app):
    from mesh.errors import ValidationError
    from mesh.issue.filters import coerce_date, compile_filter_tree

    columns = {"priority": Issue.priority, "due_date": Issue.due_date}
    with pytest.raises(ValidationError):
        compile_filter_tree({"field": "nope", "op": "eq", "value": 1}, columns)
    with pytest.raises(ValidationError):
        compile_filter_tree({"field": "priority", "op": "weird", "value": 1}, columns)
    with pytest.raises(ValidationError):
        compile_filter_tree({"field": "priority", "op": "in", "value": "notalist"}, columns)
    with pytest.raises(ValidationError):
        compile_filter_tree(
            {"field": "due_date", "op": "gt", "value": "notadate"},
            columns,
            value_coercers={"due_date": coerce_date},
        )
    with pytest.raises(ValidationError):
        compile_filter_tree("scalar", columns)
    # is_null false branch + ne/lt/lte compile
    expr = compile_filter_tree(
        {"and": [
            {"field": "priority", "op": "ne", "value": "low"},
            {"field": "priority", "op": "lt", "value": "z"},
            {"field": "priority", "op": "lte", "value": "z"},
            {"field": "due_date", "op": "is_null", "value": False},
        ]},
        columns,
    )
    assert expr is not None


async def test_children_and_activity_endpoints(client):
    owner = await _register_and_login(client, "api-tree@corp.com")
    ws = await _create_workspace(client, owner, "api-tree")
    parent = await _create_issue(client, owner, ws["id"], title="epic")
    await _create_issue(client, owner, ws["id"], title="s1", parent_id=parent["id"])
    kids = await client.get(f"/api/v1/issues/{parent['id']}/children", headers=_auth(owner))
    assert len(kids.json()["data"]) == 1
    act = await client.get(f"/api/v1/issues/{parent['id']}/activity", headers=_auth(owner))
    assert act.status_code == 200
    assert (
        await client.get(f"/api/v1/issues/{uuid.uuid4()}/children", headers=_auth(owner))
    ).status_code == 404


# ---------------------------------------------------------------------------
# statuses endpoints
# ---------------------------------------------------------------------------


async def test_status_update_and_default_handoff(client):
    owner = await _register_and_login(client, "api-status@corp.com")
    ws = await _create_workspace(client, owner, "api-status")
    created = await client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "QA Round", "category": "in_review", "color": "#123456", "position": 3},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    sid = created.json()["data"]["id"]
    # rename + recolor + move + recategorize
    upd = await client.patch(
        f"/api/v1/statuses/{sid}",
        json={"name": "QA Round 2", "color": "#654321", "position": 9.0, "category": "done"},
        headers=_auth(owner),
    )
    assert upd.status_code == 200
    assert upd.json()["data"]["category"] == "done"
    # make it the scope default (handoff in one transaction)
    dflt = await client.patch(
        f"/api/v1/statuses/{sid}", json={"is_default": True}, headers=_auth(owner)
    )
    assert dflt.status_code == 200
    statuses = (
        await client.get(f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(owner))
    ).json()["data"]
    assert [s for s in statuses if s["is_default"]][0]["id"] == sid
    # bare unset refused
    unset = await client.patch(
        f"/api/v1/statuses/{sid}", json={"is_default": False}, headers=_auth(owner)
    )
    assert unset.status_code == 422
    assert unset.json()["error"]["code"] == "default_status_required"
    # M-6 (MES-54): the scope's LAST default cannot be deleted — doing so
    # would 422 every future issue creation in the scope
    blocked = await client.delete(f"/api/v1/statuses/{sid}", headers=_auth(owner))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "last_default_status"
    # hand the default off (same-transaction guarantee, README §6.3) →
    # the now-non-default status becomes deletable
    seeded_todo = [s for s in statuses if s["name"] == "Todo"][0]
    handoff = await client.patch(
        f"/api/v1/statuses/{seeded_todo['id']}", json={"is_default": True}, headers=_auth(owner)
    )
    assert handoff.status_code == 200
    # delete unreferenced status OK; unknown status 404
    ok = await client.delete(f"/api/v1/statuses/{sid}", headers=_auth(owner))
    assert ok.status_code == 200
    assert (
        await client.delete(f"/api/v1/statuses/{uuid.uuid4()}", headers=_auth(owner))
    ).status_code == 404


async def test_status_project_scope_and_guest(client):
    owner = await _register_and_login(client, "api-status2@corp.com")
    ws = await _create_workspace(client, owner, "api-status2")
    project = await _create_project(client, owner, ws["id"], "PSC")
    guest = await _invite_accept(client, owner, ws["id"], "api-status2-guest@corp.com", "guest")
    created = await client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "Private", "category": "todo", "project_id": project["id"]},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    # listing with project_id includes workspace + project scopes
    listing = (
        await client.get(
            f"/api/v1/workspaces/{ws['id']}/statuses?project_id={project['id']}",
            headers=_auth(owner),
        )
    ).json()["data"]
    assert any(s["id"] == created.json()["data"]["id"] for s in listing)
    assert len([s for s in listing if s["project_id"] is None]) == 7
    # guests cannot manage statuses
    denied = await client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "Nope", "category": "todo"},
        headers=_auth(guest),
    )
    assert denied.status_code == 400


async def test_status_category_change_syncs_issues(client, app):
    owner = await _register_and_login(client, "api-sync@corp.com")
    ws = await _create_workspace(client, owner, "api-sync")
    new_status = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/statuses",
            json={"name": "Custom Todo", "category": "todo"},
            headers=_auth(owner),
        )
    ).json()["data"]
    issue = await _create_issue(client, owner, ws["id"], status_id=new_status["id"])
    # recategorize the status → issues denormalized in the same transaction
    await client.patch(
        f"/api/v1/statuses/{new_status['id']}",
        json={"category": "in_progress"},
        headers=_auth(owner),
    )
    got = (await client.get(f"/api/v1/issues/{issue['id']}", headers=_auth(owner))).json()["data"]
    assert got["state_category"] == "in_progress"


# ---------------------------------------------------------------------------
# dependencies + move + bulk endpoints
# ---------------------------------------------------------------------------


async def test_dependency_validation_and_move_to_inbox(client):
    owner = await _register_and_login(client, "api-dep@corp.com")
    ws = await _create_workspace(client, owner, "api-dep")
    project = await _create_project(client, owner, ws["id"], "DEP")
    a = await _create_issue(client, owner, ws["id"], project_id=project["id"])
    b = await _create_issue(client, owner, ws["id"])
    bad_type = await client.post(
        f"/api/v1/issues/{a['id']}/dependencies",
        json={"depends_on_id": b["id"], "type": "married_to"},
        headers=_auth(owner),
    )
    assert bad_type.status_code == 400
    self_edge = await client.post(
        f"/api/v1/issues/{a['id']}/dependencies",
        json={"depends_on_id": a["id"], "type": "blocks"},
        headers=_auth(owner),
    )
    assert self_edge.status_code == 400
    # move back to inbox (target null): identifier stays
    preview = await client.post(
        f"/api/v1/issues/{a['id']}/move-preview",
        json={"target_project_id": None},
        headers=_auth(owner),
    )
    assert preview.status_code == 200
    moved = await client.post(
        f"/api/v1/issues/{a['id']}/move",
        json={"target_project_id": None, "confirm": True, "version": a["version"]},
        headers=_auth(owner),
    )
    assert moved.status_code == 200
    data = moved.json()["data"]
    assert data["project_id"] is None and data["identifier"] == "DEP-1"
    # move to the same project is a no-op (§3.8: version still mandatory)
    again = await client.post(
        f"/api/v1/issues/{a['id']}/move",
        json={"target_project_id": None, "confirm": True, "version": data["version"]},
        headers=_auth(owner),
    )
    assert again.status_code == 200
    assert again.json()["data"]["version"] == data["version"]


async def test_move_workspace_level_status_kept_and_private_leak_frame(client):
    owner = await _register_and_login(client, "api-move2@corp.com")
    ws = await _create_workspace(client, owner, "api-move2")
    source = await _create_project(client, owner, ws["id"], "PUB")
    target = await _create_project(client, owner, ws["id"], "PRV", visibility="private")
    issue = await _create_issue(client, owner, ws["id"], project_id=source["id"])
    # workspace-level default status survives the move (no mapping entry)
    preview = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/move-preview",
            json={"target_project_id": target["id"]},
            headers=_auth(owner),
        )
    ).json()["data"]
    assert preview["mapped_fields"] == []
    moved = await client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"], "confirm": True, "version": issue["version"]},
        headers=_auth(owner),
    )
    assert moved.status_code == 200
    # unknown target project → 404
    assert (
        await client.post(
            f"/api/v1/issues/{issue['id']}/move-preview",
            json={"target_project_id": str(uuid.uuid4())},
            headers=_auth(owner),
        )
    ).status_code == 404


async def test_bulk_status_change_and_invalid_ids(client):
    owner = await _register_and_login(client, "api-bulk@corp.com")
    ws = await _create_workspace(client, owner, "api-bulk")
    i1 = await _create_issue(client, owner, ws["id"])
    i2 = await _create_issue(client, owner, ws["id"])
    statuses = (
        await client.get(f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(owner))
    ).json()["data"]
    done = next(s for s in statuses if s["category"] == "done")
    partial = await client.post(
        "/api/v1/issues/bulk",
        json={
            "issue_ids": [i1["id"], i2["id"], "garbage"],
            "changes": {"status_id": done["id"]},
        },
        headers=_auth(owner),
    )
    assert partial.status_code == 422
    body = partial.json()["error"]
    assert body["details"]["succeeded"] == 2 and body["details"]["failed"] == 1
    got = (await client.get(f"/api/v1/issues/{i1['id']}", headers=_auth(owner))).json()["data"]
    assert got["state_category"] == "done" and got["completed_at"] is not None
    # changes without anything + no delete → per-item validation error
    nothing = await client.post(
        "/api/v1/issues/bulk",
        json={"issue_ids": [i1["id"]]},
        headers=_auth(owner),
    )
    assert nothing.status_code == 422


async def test_bulk_requires_workspace_resolution(client):
    owner = await _register_and_login(client, "api-bulk2@corp.com")
    resp = await client.post(
        "/api/v1/issues/bulk",
        json={"issue_ids": [str(uuid.uuid4())], "changes": {"priority": "low"}},
        headers=_auth(owner),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# templates endpoints
# ---------------------------------------------------------------------------


async def test_template_list_update_delete_and_gates(client):
    owner = await _register_and_login(client, "api-tpl@corp.com")
    ws = await _create_workspace(client, owner, "api-tpl")
    other = await _invite_accept(client, owner, ws["id"], "api-tpl-other@corp.com")
    created = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/issue-templates",
            json={"name": "Feature", "template_body": {"priority": "high"}},
            headers=_auth(owner),
        )
    ).json()["data"]
    tid = created["id"]
    # duplicate name in scope → 409
    dup = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issue-templates",
        json={"name": "Feature", "template_body": {}},
        headers=_auth(owner),
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "template_name_taken"
    # non-creator cannot manage
    forbidden = await client.patch(
        f"/api/v1/issue-templates/{tid}",
        json={"name": "Hijack"},
        headers=_auth(other),
    )
    assert forbidden.status_code == 403
    upd = await client.patch(
        f"/api/v1/issue-templates/{tid}",
        json={"name": "Feature v2", "description": "updated", "template_body": {"priority": "low"}},
        headers=_auth(owner),
    )
    assert upd.status_code == 200 and upd.json()["data"]["name"] == "Feature v2"
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issue-templates", headers=_auth(owner)
    )
    assert len(listing.json()["data"]) == 1
    # instantiate with overrides + stale label prefill degradation
    inst = await client.post(
        f"/api/v1/issue-templates/{tid}/instantiate",
        json={"title": "instantiated", "overrides": {"label_ids": ["l1"], "priority": "urgent"}},
        headers=_auth(owner),
    )
    assert inst.status_code == 201
    data = inst.json()["data"]
    assert data["priority"] == "urgent"
    assert any(s["reason"] == "label_module_pending" for s in data["skipped_fields"])
    # instantiate with state_category baseline
    inst2 = await client.post(
        f"/api/v1/issue-templates/{tid}/instantiate",
        json={"title": "cat", "overrides": {"state_category": "in_progress"}},
        headers=_auth(owner),
    )
    assert inst2.json()["data"]["state_category"] == "in_progress"
    deleted = await client.delete(f"/api/v1/issue-templates/{tid}", headers=_auth(owner))
    assert deleted.status_code == 200
    assert (
        await client.get(f"/api/v1/issues/{uuid.uuid4()}/activity", headers=_auth(owner))
    ).status_code == 404


async def test_template_project_scope_gates(client):
    owner = await _register_and_login(client, "api-tpl2@corp.com")
    ws = await _create_workspace(client, owner, "api-tpl2")
    project = await _create_project(client, owner, ws["id"], "TPLP")
    created = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issue-templates",
        json={"name": "Scoped", "template_body": {}, "project_id": project["id"]},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    # template to unknown project → 404
    assert (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/issue-templates",
            json={"name": "Ghost", "template_body": {}, "project_id": str(uuid.uuid4())},
            headers=_auth(owner),
        )
    ).status_code == 404


# ---------------------------------------------------------------------------
# issue:{id} channel authorizer (README §6.7 resource-level auth)
# ---------------------------------------------------------------------------


async def test_issue_channel_authorizer(app, client, session_factory):
    owner = await _register_and_login(client, "chan-owner@corp.com")
    ws = await _create_workspace(client, owner, "chan-ws")
    public_project = await _create_project(client, owner, ws["id"], "PUBC")
    private_project = await _create_project(client, owner, ws["id"], "PRVC", visibility="private")
    public_issue = await _create_issue(client, owner, ws["id"], project_id=public_project["id"])
    private_issue = await _create_issue(client, owner, ws["id"], project_id=private_project["id"])
    inbox_issue = await _create_issue(client, owner, ws["id"])

    # resolve the owner's user id for the principal subject
    from mesh.db.models.realtime import RealtimeChannel
    from mesh.db.models.user import User

    # Materialize the channel rows the projector would create on first
    # publish — the ownership floor resolves the workspace from these rows
    # (README §6.7: the channel row carries the authoritative workspace_id).
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == "chan-owner@corp.com"))
        for issue_id in (public_issue["id"], private_issue["id"], inbox_issue["id"]):
            session.add(
                RealtimeChannel(channel=f"issue:{issue_id}", workspace_id=uuid.UUID(ws["id"]))
            )
    principal = Principal(subject=str(user.id), workspace_ids=frozenset({uuid.UUID(ws["id"])}))
    authorizer = app.state.authorizer

    assert await authorizer.authorize(principal, f"issue:{public_issue['id']}") is not None
    assert await authorizer.authorize(principal, f"issue:{inbox_issue['id']}") is not None
    # private project: owner is workspace admin → allowed
    assert await authorizer.authorize(principal, f"issue:{private_issue['id']}") is not None
    # unknown issue / malformed / foreign workspace
    assert await authorizer.authorize(principal, f"issue:{uuid.uuid4()}") is None
    assert await authorizer.authorize(principal, "issue:not-a-uuid") is None

    # a plain member without project membership cannot subscribe to private
    await _invite_accept(client, owner, ws["id"], "chan-member@corp.com")
    from mesh.db.models.user import User as U2

    async with session_factory() as session:
        member_user = await session.scalar(select(U2).where(U2.email == "chan-member@corp.com"))
    member_principal = Principal(
        subject=str(member_user.id), workspace_ids=frozenset({uuid.UUID(ws["id"])})
    )
    assert await authorizer.authorize(member_principal, f"issue:{public_issue['id']}") is not None
    assert await authorizer.authorize(member_principal, f"issue:{private_issue['id']}") is None

    # dev principal: workspace-level access by definition
    dev_principal = Principal(subject="dev-user", workspace_ids=frozenset({uuid.UUID(ws["id"])}))
    assert await authorizer.authorize(dev_principal, f"issue:{private_issue['id']}") is not None

    # deleted issue channel → denied
    await client.delete(f"/api/v1/issues/{inbox_issue['id']}", headers=_auth(owner))
    assert await authorizer.authorize(principal, f"issue:{inbox_issue['id']}") is None


async def test_guest_issue_visibility_via_api(client):
    owner = await _register_and_login(client, "gv-owner@corp.com")
    ws = await _create_workspace(client, owner, "gv-ws")
    private_project = await _create_project(client, owner, ws["id"], "GVP", visibility="private")
    private_issue = await _create_issue(client, owner, ws["id"], project_id=private_project["id"])
    guest = await _invite_accept(client, owner, ws["id"], "gv-guest@corp.com", "guest")
    # guest cannot see private project issue (404), and cannot write inbox issues
    assert (
        await client.get(f"/api/v1/issues/{private_issue['id']}", headers=_auth(guest))
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/issues", json={"title": "g"}, headers=_auth(guest)
        )
    ).status_code == 403
    # guest PATCH on a member's inbox issue → forbidden
    inbox = await _create_issue(client, owner, ws["id"])
    assert (
        await client.patch(
            f"/api/v1/issues/{inbox['id']}", json={"title": "nope"}, headers=_auth(guest)
        )
    ).status_code == 403


# ---------------------------------------------------------------------------
# dependency happy path over HTTP (route coverage) + route 404 edges
# ---------------------------------------------------------------------------


async def test_dependency_happy_path_http(client):
    owner = await _register_and_login(client, "dep-hp@corp.com")
    ws = await _create_workspace(client, owner, "dep-hp")
    a = await _create_issue(client, owner, ws["id"], title="a")
    b = await _create_issue(client, owner, ws["id"], title="b")
    added = await client.post(
        f"/api/v1/issues/{a['id']}/dependencies",
        json={"depends_on_id": b["id"], "type": "blocks"},
        headers=_auth(owner),
    )
    assert added.status_code == 201
    dep_id = added.json()["data"]["id"]
    listing = await client.get(
        f"/api/v1/issues/{a['id']}/dependencies", headers=_auth(owner)
    )
    assert listing.status_code == 200
    assert listing.json()["data"][0]["id"] == dep_id
    removed = await client.delete(
        f"/api/v1/issues/{a['id']}/dependencies/{dep_id}", headers=_auth(owner)
    )
    assert removed.status_code == 200
    after = await client.get(f"/api/v1/issues/{a['id']}/dependencies", headers=_auth(owner))
    assert after.json()["data"] == []
    # unknown issue on dependency routes → 404
    assert (
        await client.get(f"/api/v1/issues/{uuid.uuid4()}/dependencies", headers=_auth(owner))
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/issues/{uuid.uuid4()}/dependencies",
            json={"depends_on_id": b["id"], "type": "blocks"},
            headers=_auth(owner),
        )
    ).status_code == 404
    # move-preview on unknown issue → 404
    assert (
        await client.post(
            f"/api/v1/issues/{uuid.uuid4()}/move-preview",
            json={"target_project_id": None},
            headers=_auth(owner),
        )
    ).status_code == 404
    # instantiate unknown template → 404; patch unknown status → 404
    assert (
        await client.post(
            f"/api/v1/issue-templates/{uuid.uuid4()}/instantiate",
            json={"title": "x"},
            headers=_auth(owner),
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/statuses/{uuid.uuid4()}", json={"name": "x"}, headers=_auth(owner)
        )
    ).status_code == 404


async def test_patch_with_body_version_and_bulk_bad_first_id(client):
    owner = await _register_and_login(client, "ver-body@corp.com")
    ws = await _create_workspace(client, owner, "ver-body")
    issue = await _create_issue(client, owner, ws["id"])
    patched = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "v2", "version": issue["version"]},
        headers=_auth(owner),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["version"] == 2
    # stale body version → 409 conflict
    stale = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "v3", "version": 1},
        headers=_auth(owner),
    )
    assert stale.status_code == 409
    # bulk whose first id is unknown → workspace resolution 404
    assert (
        await client.post(
            "/api/v1/issues/bulk",
            json={"issue_ids": [str(uuid.uuid4())], "changes": {"priority": "low"}},
            headers=_auth(owner),
        )
    ).status_code == 404


async def test_prefixless_endpoints_uniform_404_message(client):
    """L3 (workspace.md §5.3): /issues/{id}、/statuses/{id}、
    /issue-templates/{id} 对「不存在」与「存在但非成员」返回同一 404
    消息,消除资源存在性 oracle。"""
    owner_a = await _register_and_login(client, "l3-a@corp.com")
    owner_b = await _register_and_login(client, "l3-b@corp.com")
    await _create_workspace(client, owner_a, "l3-a")
    ws_b = await _create_workspace(client, owner_b, "l3-b")
    issue_b = await _create_issue(client, owner_b, ws_b["id"])
    status_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/statuses",
            json={"name": "Extra", "category": "todo"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    template_b = (
        await client.post(
            f"/api/v1/workspaces/{ws_b['id']}/issue-templates",
            json={"name": "Tmpl"},
            headers=_auth(owner_b),
        )
    ).json()["data"]
    random_id = str(uuid.uuid4())

    probes = (
        # (existing-id 探测, 不存在探测, 资源消息)
        (
            lambda target: client.get(f"/api/v1/issues/{target}", headers=_auth(owner_a)),
            issue_b["id"],
            "issue not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/statuses/{target}", json={}, headers=_auth(owner_a)
            ),
            status_b["id"],
            "issue status not found",
        ),
        (
            lambda target: client.patch(
                f"/api/v1/issue-templates/{target}", json={}, headers=_auth(owner_a)
            ),
            template_b["id"],
            "issue template not found",
        ),
    )
    for call, existing_id, message in probes:
        existing = await call(existing_id)  # 存在但 owner_a 非成员
        missing = await call(random_id)  # 完全不存在
        assert existing.status_code == 404, existing.text
        assert missing.status_code == 404, missing.text
        # 两态消息不可区分,且为该资源的 not-found 口径
        assert existing.json()["error"]["message"] == message
        assert missing.json()["error"]["message"] == message

    # 软删除 + 非成员 → 同一消息(解析器不过滤 deleted_at,消息统一兜住)
    await client.delete(f"/api/v1/issues/{issue_b['id']}", headers=_auth(owner_b))
    deleted_probe = await client.get(
        f"/api/v1/issues/{issue_b['id']}", headers=_auth(owner_a)
    )
    assert deleted_probe.status_code == 404
    assert deleted_probe.json()["error"]["message"] == "issue not found"

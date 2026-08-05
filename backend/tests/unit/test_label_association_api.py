"""In-process issue-association API tests (route layer, label-property §3.1).

Real create_app() over ASGITransport against real PostgreSQL + Redis.
Covers the MES-32 remainder endpoints over HTTP: issue label list / add /
remove / whole-set replace, label merge, per-issue custom-field-value
read / whole-form write — §6.14 envelopes, named 422 codes, 404 tenant
isolation on workspace-less paths, If-Match optimistic concurrency and
write-endpoint rate-limit headers.
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
        jwt_secret="inprocess-assoc-test-signing-secret-000",
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
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _label(client, token, ws_id: str, name: str, **extra) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/labels",
        json={"name": name, "color": "#e5484d", **extra},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _project(client, token, ws_id: str, name: str, key: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": name, "key": key},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _field(client, token, ws_id: str, **body) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/custom-fields",
        json=body, headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _issue(client, token, ws_id: str, title: str = "api issue") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": title}, headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# issue labels
# ---------------------------------------------------------------------------


async def test_issue_labels_full_http_flow(client):
    token = await _register_and_login(client, "owner@corp.com")
    ws = await _workspace(client, token, "assoc-ws")
    bug = await _label(client, token, ws["id"], "bug")
    ux = await _label(client, token, ws["id"], "ux")
    issue = await _issue(client, token, ws["id"])
    base = f"/api/v1/issues/{issue['id']}/labels"

    # Empty start.
    resp = await client.get(base, headers=_auth(token))
    assert resp.status_code == 200 and resp.json()["data"] == []

    # Add one — envelope {"data": {"labels": [...]}}.
    resp = await client.post(f"{base}/{bug['id']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert [label["id"] for label in resp.json()["data"]["labels"]] == [bug["id"]]
    assert "X-RateLimit-Limit" in resp.headers

    # The generic issue detail/list payloads are the fact source used by
    # issue rows and board cards. They expose the same compact, coloured
    # label snapshot instead of making each consumer refetch associations.
    detail = await client.get(f"/api/v1/issues/{issue['id']}", headers=_auth(token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["labels"] == [
        {"id": bug["id"], "name": "bug", "color": "#e5484d"}
    ]
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/issues", headers=_auth(token)
    )
    assert listing.status_code == 200, listing.text
    projected = next(row for row in listing.json()["data"] if row["id"] == issue["id"])
    assert projected["labels"] == detail.json()["data"]["labels"]

    # Whole-set replace (PUT) with de-dup.
    resp = await client.put(
        base, json={"label_ids": [bug["id"], ux["id"], ux["id"]]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert {label["id"] for label in resp.json()["data"]["labels"]} == {bug["id"], ux["id"]}

    # Remove one.
    resp = await client.delete(f"{base}/{bug['id']}", headers=_auth(token))
    assert resp.status_code == 200
    assert [label["id"] for label in resp.json()["data"]["labels"]] == [ux["id"]]

    # Remove the same again — idempotent 200, ux untouched.
    resp = await client.delete(f"{base}/{bug['id']}", headers=_auth(token))
    assert resp.status_code == 200
    assert [label["id"] for label in resp.json()["data"]["labels"]] == [ux["id"]]


async def test_issue_labels_scope_mismatch_422(client):
    token = await _register_and_login(client, "scope@corp.com")
    ws = await _workspace(client, token, "scope-ws")
    project = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/projects",
            json={"name": "P1", "key": "PX1"}, headers=_auth(token),
        )
    ).json()["data"]
    other = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/projects",
            json={"name": "P2", "key": "PX2"}, headers=_auth(token),
        )
    ).json()["data"]
    scoped = await _label(client, token, ws["id"], "client", project_id=project["id"])
    issue_resp = await client.post(
        f"/api/v1/workspaces/{ws['id']}/issues",
        json={"title": "in p2", "project_id": other["id"]}, headers=_auth(token),
    )
    issue_id = issue_resp.json()["data"]["id"]
    resp = await client.post(
        f"/api/v1/issues/{issue_id}/labels/{scoped['id']}", headers=_auth(token)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "label_scope_mismatch"


async def test_unknown_label_404_and_bad_uuid_404(client):
    token = await _register_and_login(client, "nf@corp.com")
    ws = await _workspace(client, token, "nf-ws")
    issue = await _issue(client, token, ws["id"])
    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/labels/{uuid.uuid4()}", headers=_auth(token)
    )
    assert resp.status_code == 404
    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/labels/not-a-uuid", headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_cross_tenant_issue_paths_404(client, caplog):
    import logging

    caplog.set_level(logging.ERROR)
    suffix = uuid.uuid4().hex[:10]
    owner_a = await _register_and_login(client, f"a-{suffix}@corp.com")
    owner_b = await _register_and_login(client, f"b-{suffix}@corp.com")
    ws_a = await _workspace(client, owner_a, f"tenant-a-{suffix}")
    await _workspace(client, owner_b, f"tenant-b-{suffix}")
    resp = await client.post(
        f"/api/v1/workspaces/{ws_a['id']}/issues",
        json={"title": "api issue"}, headers=_auth(owner_a),
    )
    # Surface the server-side traceback if the intermittent 500 recurs.
    assert resp.status_code == 201, f"{resp.text}\nSERVER-LOG:\n{caplog.text}"
    issue = resp.json()["data"]
    unknown = str(uuid.uuid4())
    for method, path in (
        ("GET", f"/api/v1/issues/{issue['id']}/labels"),
        ("PUT", f"/api/v1/issues/{issue['id']}/labels"),
        ("GET", f"/api/v1/issues/{issue['id']}/custom-field-values"),
        ("PUT", f"/api/v1/issues/{issue['id']}/custom-field-values"),
    ):
        body = (
            {"label_ids": []} if method == "PUT" and "labels" in path
            else {"values": []} if method == "PUT" else None
        )
        resp = await client.request(method, path, json=body, headers=_auth(owner_b))
        assert resp.status_code == 404, (method, path, resp.text)
        # L3 parity: the membership-gate 404 is rewritten to the resource
        # message — byte-identical to a totally unknown issue id, so the
        # association paths carry no existence oracle either (§5.3).
        assert resp.json()["error"]["message"] == "issue not found"
        missing = await client.request(
            method, path.replace(issue["id"], unknown), json=body, headers=_auth(owner_b)
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["message"] == "issue not found"


async def test_unauthenticated_401(client):
    resp = await client.get(f"/api/v1/issues/{uuid.uuid4()}/labels")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


async def test_merge_endpoint(client):
    token = await _register_and_login(client, "merge@corp.com")
    ws = await _workspace(client, token, "merge-ws")
    bug = await _label(client, token, ws["id"], "bug")
    defect = await _label(client, token, ws["id"], "defect")
    issue = await _issue(client, token, ws["id"])
    await client.post(
        f"/api/v1/issues/{issue['id']}/labels/{defect['id']}", headers=_auth(token)
    )
    # The settings list is also the merge-preview source: it reports the
    # number of live issues that will be migrated before the destructive
    # confirmation is accepted (label-property.md §4.1/§4.4).
    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(token)
    )
    counts = {label["id"]: label["issue_count"] for label in listing.json()["data"]}
    assert counts == {bug["id"]: 0, defect["id"]: 1}
    resp = await client.post(
        f"/api/v1/labels/{defect['id']}/merge",
        json={"target_label_id": bug["id"]}, headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["merged_issue_count"] == 1
    assert data["target_label"]["id"] == bug["id"]
    # Issue now carries the target label.
    resp = await client.get(f"/api/v1/issues/{issue['id']}/labels", headers=_auth(token))
    assert [label["id"] for label in resp.json()["data"]] == [bug["id"]]
    # Source label is gone.
    resp = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(token)
    )
    assert all(label["id"] != defect["id"] for label in resp.json()["data"])
    assert resp.json()["data"][0]["issue_count"] == 1


async def test_merge_self_conflict(client):
    token = await _register_and_login(client, "ms@corp.com")
    ws = await _workspace(client, token, "ms-ws")
    bug = await _label(client, token, ws["id"], "bug")
    resp = await client.post(
        f"/api/v1/labels/{bug['id']}/merge",
        json={"target_label_id": bug["id"]}, headers=_auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_merge_rejects_unsafe_project_targets_without_mutating_labels(client):
    token = await _register_and_login(client, "merge-scope@corp.com")
    ws = await _workspace(client, token, "merge-scope-ws")
    first = await _project(client, token, ws["id"], "First", "MG1")
    second = await _project(client, token, ws["id"], "Second", "MG2")
    workspace_source = await _label(client, token, ws["id"], "workspace source")
    first_label = await _label(
        client, token, ws["id"], "first private", project_id=first["id"]
    )
    second_label = await _label(
        client, token, ws["id"], "second private", project_id=second["id"]
    )

    for source_id, target_id in (
        (workspace_source["id"], first_label["id"]),
        (first_label["id"], second_label["id"]),
    ):
        resp = await client.post(
            f"/api/v1/labels/{source_id}/merge",
            json={"target_label_id": target_id},
            headers=_auth(token),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "label_scope_mismatch"

    listing = await client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(token)
    )
    assert listing.status_code == 200, listing.text
    assert {label["id"] for label in listing.json()["data"]} == {
        workspace_source["id"],
        first_label["id"],
        second_label["id"],
    }


async def test_merge_cross_tenant_target_is_same_404_as_unknown_target(client):
    suffix = uuid.uuid4().hex[:10]
    owner_a = await _register_and_login(client, f"merge-a-{suffix}@corp.com")
    owner_b = await _register_and_login(client, f"merge-b-{suffix}@corp.com")
    ws_a = await _workspace(client, owner_a, f"merge-a-{suffix}")
    ws_b = await _workspace(client, owner_b, f"merge-b-{suffix}")
    source = await _label(client, owner_a, ws_a["id"], "source")
    foreign_target = await _label(client, owner_b, ws_b["id"], "foreign target")

    foreign = await client.post(
        f"/api/v1/labels/{source['id']}/merge",
        json={"target_label_id": foreign_target["id"]},
        headers=_auth(owner_a),
    )
    unknown = await client.post(
        f"/api/v1/labels/{source['id']}/merge",
        json={"target_label_id": str(uuid.uuid4())},
        headers=_auth(owner_a),
    )

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"] == unknown.json()["error"]


# ---------------------------------------------------------------------------
# custom field values
# ---------------------------------------------------------------------------


async def test_field_values_http_flow_with_type_validation(client):
    token = await _register_and_login(client, "fv@corp.com")
    ws = await _workspace(client, token, "fv-ws")
    severity = await _field(
        client, token, ws["id"], name="Severity", field_key="severity",
        type="single_select",
        options=[{"name": "Major"}, {"name": "Minor"}],
    )
    users = await _field(
        client, token, ws["id"], name="Users", field_key="users", type="number",
    )
    issue = await _issue(client, token, ws["id"])
    base = f"/api/v1/issues/{issue['id']}/custom-field-values"
    major = next(o["id"] for o in severity["options"] if o["name"] == "Major")

    # Listing shows defs with null values.
    resp = await client.get(base, headers=_auth(token))
    assert resp.status_code == 200
    entries = {e["field_def"]["field_key"]: e for e in resp.json()["data"]}
    assert entries["severity"]["value"] is None

    # Set both fields.
    resp = await client.put(
        base,
        json={"values": [
            {"field_def_id": severity["id"], "value_json": major},
            {"field_def_id": users["id"], "value_number": 1500},
        ]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    entries = {e["field_def"]["field_key"]: e for e in resp.json()["data"]}
    assert entries["severity"]["value"]["value_json"] == major
    assert entries["users"]["value"]["value_number"] == 1500

    # Wrong column for the type → 422 invalid_field_value.
    resp = await client.put(
        base,
        json={"values": [{"field_def_id": users["id"], "value_text": "lots"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_field_value"
    assert error["details"]["reason"] == "wrong_value_column"

    # Unknown option → 422 invalid_field_value.
    resp = await client.put(
        base,
        json={"values": [
            {"field_def_id": severity["id"], "value_json": str(uuid.uuid4())}
        ]},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_field_value"


async def test_field_values_inactive_field_422(client):
    token = await _register_and_login(client, "inact@corp.com")
    ws = await _workspace(client, token, "inact-ws")
    field = await _field(
        client, token, ws["id"], name="X", field_key="x_key", type="text"
    )
    issue = await _issue(client, token, ws["id"])
    resp = await client.patch(
        f"/api/v1/custom-fields/{field['id']}",
        json={"is_active": False}, headers=_auth(token),
    )
    assert resp.status_code == 200
    resp = await client.put(
        f"/api/v1/issues/{issue['id']}/custom-field-values",
        json={"values": [{"field_def_id": field["id"], "value_text": "x"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "field_inactive"


async def test_field_values_if_match_conflict(client):
    token = await _register_and_login(client, "im@corp.com")
    ws = await _workspace(client, token, "im-ws")
    field = await _field(
        client, token, ws["id"], name="N", field_key="n_key", type="text"
    )
    issue = await _issue(client, token, ws["id"])
    resp = await client.put(
        f"/api/v1/issues/{issue['id']}/custom-field-values",
        json={"values": [{"field_def_id": field["id"], "value_text": "x"}]},
        headers={**_auth(token), "If-Match": '"1999-01-01T00:00:00Z"'},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    # Current updated_at → success.
    resp = await client.put(
        f"/api/v1/issues/{issue['id']}/custom-field-values",
        json={"values": [{"field_def_id": field["id"], "value_text": "x"}]},
        headers={**_auth(token), "If-Match": issue["updated_at"]},
    )
    assert resp.status_code == 200, resp.text


async def test_required_field_blocks_status_transition_over_http(client):
    """§4.5 end-to-end: required_on=[status:done] blocks the done flow."""
    token = await _register_and_login(client, "req@corp.com")
    ws = await _workspace(client, token, "req-ws")
    field = await _field(
        client, token, ws["id"], name="Acceptor", field_key="acceptor",
        type="text", is_required=True, required_on=["status:done"],
    )
    issue = await _issue(client, token, ws["id"])
    statuses = (
        await client.get(f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(token))
    ).json()["data"]
    done = next(s for s in statuses if s["category"] == "done")
    resp = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"status_id": done["id"], "version": issue["version"]},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "required_field_missing"
    assert error["details"]["missing"][0]["field_def_id"] == field["id"]
    # Fill the field, then the transition succeeds.
    await client.put(
        f"/api/v1/issues/{issue['id']}/custom-field-values",
        json={"values": [{"field_def_id": field["id"], "value_text": "me"}]},
        headers=_auth(token),
    )
    fresh = (
        await client.get(f"/api/v1/issues/{issue['id']}", headers=_auth(token))
    ).json()["data"]
    resp = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"status_id": done["id"], "version": fresh["version"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["state_category"] == "done"


async def test_association_write_rate_limit_rejects_121st(client):
    """Reject side of the write limiter: 120 allowed per 60s, 121st → 429."""
    token = await _register_and_login(client, "rl@corp.com")
    ws = await _workspace(client, token, "rl-ws")
    label = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/labels",
            json={"name": "bug", "color": "#e5484d"},
            headers=_auth(token),
        )
    ).json()["data"]
    issue = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": "rl"},
            headers=_auth(token),
        )
    ).json()["data"]
    last = None
    for _ in range(121):  # first call consumed 1 token → 120 more allowed
        last = await client.post(
            f"/api/v1/issues/{issue['id']}/labels/{label['id']}",
            headers=_auth(token),
        )
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429
    assert last.json()["error"]["code"] == "rate_limited"

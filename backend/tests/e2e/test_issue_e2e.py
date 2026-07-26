"""Issue module e2e — REAL server processes, REAL API calls, REAL database
writes (issue.md §5, README §9 T1/T9/T12/T15/T19/T22).

Runs against genuine uvicorn subprocesses (``api_client`` fixture) with the
migrated PostgreSQL test database — nothing mocked.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

_AUTH_PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _AUTH_PASSWORD, "display_name": "E2E"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": _AUTH_PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Issue E2E", "slug": slug}, headers=_auth(token)
    )
    return resp.json()["data"]


async def _create_project(client, token, ws_id: str, key: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"Project {key}", "key": key},
        headers=_auth(token),
    )
    return resp.json()["data"]


async def _create_issue(client, token, ws_id: str, **fields) -> dict:
    body = {"title": "e2e issue", **fields}
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _outbox_events(session_factory, ws_id: str, channel_substr: str | None = None):
    async with session_factory() as session:
        rows = (
            
                await session.execute(
                    text(
                        "SELECT event_type, payload FROM outbox_events "
                        "WHERE workspace_id = :ws ORDER BY created_at"
                    ),
                    {"ws": uuid.UUID(ws_id)},
                )
            
        ).all()
    if channel_substr is None:
        return rows
    return [
        r for r in rows
        if r[0] == "realtime.publish" and channel_substr in r[1].get("channel", "")
    ]


# ---------------------------------------------------------------------------
# full flow + numbering + events
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_issue_full_flow_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "issue-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-flow")
    project = await _create_project(api_client, owner, ws["id"], "WEB")

    created = await _create_issue(
        api_client,
        owner,
        ws["id"],
        project_id=project["id"],
        priority="high",
        description="repro steps",
    )
    assert created["identifier"] == "WEB-1"
    assert created["state_category"] == "todo"
    assert created["reporter"] is not None  # reporter defaults to the actor

    # GET by UUID and by identifier agree.
    got = await api_client.get(f"/api/v1/issues/{created['id']}", headers=_auth(owner))
    assert got.status_code == 200
    by_ident = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues/by-identifier/WEB-1", headers=_auth(owner)
    )
    assert by_ident.json()["data"]["id"] == created["id"]

    # PATCH with version → bumped; done category sets completed_at.
    statuses = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(owner)
    )
    done = next(
        s for s in statuses.json()["data"] if s["category"] == "done"
    )
    patched = await api_client.patch(
        f"/api/v1/issues/{created['id']}",
        json={"status_id": done["id"], "version": created["version"]},
        headers=_auth(owner),
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["version"] == 2
    assert data["completed_at"] is not None

    # List + grouped query (overall cursor contract).
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues?project_id={project['id']}",
        headers=_auth(owner),
    )
    assert listing.json()["data"][0]["id"] == created["id"]
    grouped = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues?group_by=state_category&project_id={project['id']}",
        headers=_auth(owner),
    )
    body = grouped.json()
    assert "groups" in body and "next_cursor" in body
    assert all("cursor" not in g for g in body["groups"])

    # Activity trail recorded.
    activity = await api_client.get(
        f"/api/v1/issues/{created['id']}/activity", headers=_auth(owner)
    )
    fields = {row["field"] for row in activity.json()["data"]}
    assert "status_id" in fields

    # Soft delete; identifier tombstone stays reserved (next issue = WEB-2).
    deleted = await api_client.delete(
        f"/api/v1/issues/{created['id']}", headers=_auth(owner)
    )
    assert deleted.json()["data"]["deleted"] is True
    second = await _create_issue(api_client, owner, ws["id"], project_id=project["id"])
    assert second["identifier"] == "WEB-2"

    # Outbox realtime events emitted on the unique write path.
    events = await _outbox_events(session_factory, ws["id"], "issue:")
    names = [r[1]["event"] for r in events]
    assert "issue.created" in names
    assert "issue.updated" in names
    assert "issue.deleted" in names


# ---------------------------------------------------------------------------
# T1 cross-tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_t1_cross_tenant_issue_isolation(api_client):
    a_token = await _register_and_login(api_client, "tenant-a@corp.com")
    b_token = await _register_and_login(api_client, "tenant-b@corp.com")
    ws_a = await _create_workspace(api_client, a_token, "issue-tenant-a")
    ws_b = await _create_workspace(api_client, b_token, "issue-tenant-b")
    assert ws_b["id"] != ws_a["id"]
    project_a = await _create_project(api_client, a_token, ws_a["id"], "TA")
    issue_a = await _create_issue(api_client, a_token, ws_a["id"], project_id=project_a["id"])

    # B's credentials cannot see A's issue (404, same as unknown).
    resp = await api_client.get(f"/api/v1/issues/{issue_a['id']}", headers=_auth(b_token))
    assert resp.status_code == 404
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/issues", headers=_auth(b_token)
    )
    assert listing.status_code == 404


@pytest.mark.e2e
async def test_t1_cross_tenant_composite_fk_rejected_at_db(session_factory):
    """Cross-workspace status reference is rejected by the composite FK."""
    from mesh.db.models.issue import Issue, IssueStatus
    from mesh.db.models.workspace import Workspace
    from mesh.issue.statuses import seed_default_statuses

    async with session_factory() as session, session.begin():
        ws1 = Workspace(name="FK1", slug=f"fk1-{uuid.uuid4().hex[:8]}")
        ws2 = Workspace(name="FK2", slug=f"fk2-{uuid.uuid4().hex[:8]}")
        session.add_all([ws1, ws2])
        await session.flush()
        await seed_default_statuses(session, workspace_id=ws1.id)
        await seed_default_statuses(session, workspace_id=ws2.id)
        status1 = await session.scalar(
            select(IssueStatus).where(IssueStatus.workspace_id == ws1.id)
        )
        status2 = await session.scalar(
            select(IssueStatus).where(IssueStatus.workspace_id == ws2.id)
        )
        good = Issue(
            workspace_id=ws2.id,
            identifier_namespace_key="WS",
            number=900,
            identifier="WS-900",
            title="ok",
            status_id=status2.id,
            state_category=status2.category,
        )
        session.add(good)
        await session.flush()
        bad = Issue(
            workspace_id=ws2.id,
            identifier_namespace_key="WS",
            number=901,
            identifier="WS-901",
            title="cross-tenant status",
            status_id=status1.id,  # ws1's status — composite FK must deny
            state_category=status1.category,
        )
        session.add(bad)
        with pytest.raises(Exception) as exc_info:
            await session.flush()
        assert "issues_status_id_issue_statuses" in str(exc_info.value) or "foreign key" in str(
            exc_info.value
        ).lower()


# ---------------------------------------------------------------------------
# T9 optimistic concurrency
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_t9_optimistic_conflict_converges(api_client):
    owner = await _register_and_login(api_client, "t9-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-t9")
    issue = await _create_issue(api_client, owner, ws["id"], title="race me")

    first = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "writer one", "version": issue["version"]},
        headers=_auth(owner),
    )
    assert first.status_code == 200
    second = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "writer two", "version": issue["version"]},  # stale
        headers=_auth(owner),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    # server state is writer one's; a retry with the fresh version converges.
    current = await api_client.get(f"/api/v1/issues/{issue['id']}", headers=_auth(owner))
    data = current.json()["data"]
    assert data["title"] == "writer one"
    retry = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "writer two", "version": data["version"]},
        headers=_auth(owner),
    )
    assert retry.status_code == 200


# ---------------------------------------------------------------------------
# T15 numbering concurrency over the real server
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_t15_concurrent_issue_creation_no_dupes(api_client):
    owner = await _register_and_login(api_client, "t15-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-t15")
    project = await _create_project(api_client, owner, ws["id"], "RACE")

    async def _one(i: int):
        return await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": f"concurrent {i}", "project_id": project["id"]},
            headers=_auth(owner),
        )

    responses = await asyncio.gather(*[_one(i) for i in range(12)])
    assert all(r.status_code == 201 for r in responses)
    identifiers = [r.json()["data"]["identifier"] for r in responses]
    assert len(set(identifiers)) == 12
    numbers = sorted(r.json()["data"]["number"] for r in responses)
    assert numbers == list(range(1, 13))


# ---------------------------------------------------------------------------
# T12 concurrent dependency cycle
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_t12_concurrent_dependency_cycle_exactly_one_rejected(api_client):
    owner = await _register_and_login(api_client, "t12-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-t12")
    a = await _create_issue(api_client, owner, ws["id"], title="a")
    b = await _create_issue(api_client, owner, ws["id"], title="b")

    async def _add(first, second):
        return await api_client.post(
            f"/api/v1/issues/{first['id']}/dependencies",
            json={"depends_on_id": second["id"], "type": "blocks"},
            headers=_auth(owner),
        )

    r1, r2 = await asyncio.gather(_add(a, b), _add(b, a))
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 409]
    failed = r1 if r1.status_code == 409 else r2
    assert failed.json()["error"]["code"] == "circular_dependency"


# ---------------------------------------------------------------------------
# T19/T22 cross-project move contract
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_t19_t22_move_contract(api_client, session_factory):
    owner = await _register_and_login(api_client, "t22-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-t22")
    source = await _create_project(api_client, owner, ws["id"], "SRC")
    target = await _create_project(api_client, owner, ws["id"], "APP")

    # private status + milestone on the source project
    private_status = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "联调中", "category": "in_progress", "project_id": source["id"]},
        headers=_auth(owner),
    )
    assert private_status.status_code == 201
    milestone = await api_client.post(
        f"/api/v1/projects/{source['id']}/milestones",
        json={"title": "v1"},
        headers=_auth(owner),
    )
    issue = await _create_issue(
        api_client,
        owner,
        ws["id"],
        project_id=source["id"],
        status_id=private_status.json()["data"]["id"],
        milestone_id=milestone.json()["data"]["id"],
    )
    # target project already has APP-1 → the moved SRC-1 must not collide (T19)
    await _create_issue(api_client, owner, ws["id"], project_id=target["id"])

    # preview lists mapping + clearing
    preview = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move-preview",
        json={"target_project_id": target["id"]},
        headers=_auth(owner),
    )
    assert preview.status_code == 200
    plan = preview.json()["data"]
    mapped = {m["field"]: m for m in plan["mapped_fields"]}
    assert mapped["status"]["to"]["category"] == "in_progress"
    assert {c["field"] for c in plan["cleared_fields"]} >= {"milestone_id"}

    # unconfirmed move → 422 move_confirmation_required with preview attached
    unconfirmed = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"]},
        headers=_auth(owner),
    )
    assert unconfirmed.status_code == 422
    err = unconfirmed.json()["error"]
    assert err["code"] == "move_confirmation_required"
    assert err["details"]["preview"]["issue_id"] == issue["id"]

    # stale version → 409
    stale = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={"target_project_id": target["id"], "confirm": True, "version": 99},
        headers=_auth(owner),
    )
    assert stale.status_code == 409

    # confirmed move: identifier immutable, fields mapped/cleared, event out
    moved = await api_client.post(
        f"/api/v1/issues/{issue['id']}/move",
        json={
            "target_project_id": target["id"],
            "confirm": True,
            "version": issue["version"],
        },
        headers=_auth(owner),
    )
    assert moved.status_code == 200, moved.text
    data = moved.json()["data"]
    assert data["identifier"] == "SRC-1"  # T19: unchanged
    assert data["project_id"] == target["id"]
    assert data["milestone_id"] is None
    assert data["state_category"] == "in_progress"

    rows = await _outbox_events(session_factory, ws["id"], "issue:")
    moved_events = [
        r for r in rows if r[1]["event"] == "issue.project_changed"
    ]
    assert moved_events
    payload = moved_events[0][1]["data"]
    assert payload["from_project_id"] == source["id"]
    assert payload["to_project_id"] == target["id"]
    assert payload["mapped_fields"] and payload["cleared_fields"]


# ---------------------------------------------------------------------------
# bulk operations (§5.5)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_bulk_partial_failure_envelope(api_client):
    owner = await _register_and_login(api_client, "bulk-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-bulk")
    i1 = await _create_issue(api_client, owner, ws["id"], title="b1")
    i2 = await _create_issue(api_client, owner, ws["id"], title="b2")

    ok = await api_client.post(
        "/api/v1/issues/bulk",
        json={"issue_ids": [i1["id"], i2["id"]], "changes": {"priority": "urgent"}},
        headers=_auth(owner),
    )
    assert ok.status_code == 200
    assert ok.json()["data"] == {"succeeded": 2, "failed": 0, "errors": []}

    # unknown id mixed in → 422 bulk_partial_failure with per-item errors
    partial = await api_client.post(
        "/api/v1/issues/bulk",
        json={
            "issue_ids": [i1["id"], str(uuid.uuid4())],
            "changes": {"priority": "low"},
        },
        headers=_auth(owner),
    )
    assert partial.status_code == 422
    body = partial.json()["error"]
    assert body["code"] == "bulk_partial_failure"
    assert body["details"]["succeeded"] == 1
    assert body["details"]["failed"] == 1

    # bulk delete
    deleted = await api_client.post(
        "/api/v1/issues/bulk",
        json={"issue_ids": [i1["id"], i2["id"]], "delete": True},
        headers=_auth(owner),
    )
    assert deleted.status_code == 200
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues", headers=_auth(owner)
    )
    assert listing.json()["data"] == []


# ---------------------------------------------------------------------------
# statuses + templates + dependencies over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_status_crud_and_name_conflict(api_client):
    owner = await _register_and_login(api_client, "status-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-status")
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(owner)
    )
    assert len(listing.json()["data"]) == 7  # canonical seed set
    created = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "QA", "category": "in_review"},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    conflict = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/statuses",
        json={"name": "QA", "category": "todo"},
        headers=_auth(owner),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "status_name_taken"


@pytest.mark.e2e
async def test_dependency_endpoints_and_cycle_path(api_client):
    owner = await _register_and_login(api_client, "dep-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-dep")
    a = await _create_issue(api_client, owner, ws["id"], title="a")
    b = await _create_issue(api_client, owner, ws["id"], title="b")
    added = await api_client.post(
        f"/api/v1/issues/{a['id']}/dependencies",
        json={"depends_on_id": b["id"], "type": "blocked_by"},
        headers=_auth(owner),
    )
    assert added.status_code == 201
    assert added.json()["data"]["type"] == "blocked_by"
    listing = await api_client.get(
        f"/api/v1/issues/{b['id']}/dependencies", headers=_auth(owner)
    )
    assert listing.json()["data"][0]["type"] == "blocks"  # bidirectional expansion
    cycle = await api_client.post(
        f"/api/v1/issues/{b['id']}/dependencies",
        json={"depends_on_id": a["id"], "type": "blocked_by"},
        headers=_auth(owner),
    )
    assert cycle.status_code == 409
    assert cycle.json()["error"]["code"] == "circular_dependency"
    assert cycle.json()["error"]["details"]["path"]
    removed = await api_client.delete(
        f"/api/v1/issues/{a['id']}/dependencies/{added.json()['data']['id']}",
        headers=_auth(owner),
    )
    assert removed.json()["data"]["deleted"] is True


@pytest.mark.e2e
async def test_template_crud_and_instantiate(api_client):
    owner = await _register_and_login(api_client, "tpl-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-tpl")
    project = await _create_project(api_client, owner, ws["id"], "TPL")
    created = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/issue-templates",
        json={"name": "Bug", "template_body": {"priority": "urgent", "project_id": project["id"]}},
        headers=_auth(owner),
    )
    assert created.status_code == 201
    template_id = created.json()["data"]["id"]
    instantiated = await api_client.post(
        f"/api/v1/issue-templates/{template_id}/instantiate",
        json={"title": "from template"},
        headers=_auth(owner),
    )
    assert instantiated.status_code == 201
    data = instantiated.json()["data"]
    assert data["identifier"] == "TPL-1"
    assert data["priority"] == "urgent"
    assert data["template_id"] == template_id


# ---------------------------------------------------------------------------
# filter limits (§6.14)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_filter_too_complex_over_http(api_client):
    owner = await _register_and_login(api_client, "filter-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "issue-filter")
    import json as _json

    deep = {"field": "priority", "op": "eq", "value": "low"}
    for _ in range(5):
        deep = {"and": [deep]}
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues",
        params={"filters": _json.dumps(deep)},
        headers=_auth(owner),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "filter_too_complex"

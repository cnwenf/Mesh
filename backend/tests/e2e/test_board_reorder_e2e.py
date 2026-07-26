"""Real end-to-end tests for the board per-view ordering (kanban.md §4.3, MES-33).

Real uvicorn API subprocess (restricted ``mesh_app`` role → RLS live) + real
PostgreSQL 16 + real HTTP ``POST /views/{id}/reorder`` + real DB durability
assertions on ``view_issue_positions``. Covers:

- reorder persists the per-view position over real HTTP (and does NOT touch
  ``issues.position`` — view isolation, §2.7);
- precision-exhaustion: two cards whose positions are within the float midpoint
  floor trigger a whole-column rerank to integer positions, fanning out
  ``issue.moved`` (with ``view_id``) per reranked card (§4.3);
- T1 cross-tenant: a foreign tenant's GUC cannot read another tenant's
  ``view_issue_positions`` rows (README §6.2 rule 5), and an unauthenticated
  reorder is rejected (401).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.e2e.conftest import _app_role_url

pytestmark = pytest.mark.e2e


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-strong-passw0rd", "display_name": "E2E"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "a-strong-passw0rd"}
    )
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_view(client, token, ws_id, **overrides) -> dict:
    body = {"name": "Reorder Board", "group_by": "state_category"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/views", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, **overrides) -> dict:
    body = {"title": f"Issue {uuid.uuid4().hex[:6]}"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _outbox_events(session_factory, name: str):
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT payload FROM outbox_events "
                        "WHERE event_type = 'realtime.publish'"
                    )
                )
            )
            .scalars()
            .all()
        )
    return [row for row in rows if row.get("event") == name]


async def _position(session_factory, view_id, issue_id):
    async with session_factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT position, group_key FROM view_issue_positions "
                    "WHERE view_id = :v AND issue_id = :i"
                ),
                {"v": view_id, "i": issue_id},
            )
        ).mappings().first()


async def test_reorder_persists_per_view_position_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "reorder-dur@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-reorder-dur")
    view = await _create_view(api_client, owner, ws["id"])
    issue = await _create_issue(api_client, owner, ws["id"])
    # Canonical position seed (must NOT be what the reorder writes).
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE issues SET position = 999 WHERE id = :i"), {"i": issue["id"]}
        )

    resp = await api_client.post(
        f"/api/v1/views/{view['id']}/reorder",
        json={"issue_id": issue["id"], "to_group_key": "todo", "position": 7.5},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["position"] == 7.5

    # Durability: the per-view row landed with the new position + group_key.
    row = await _position(session_factory, view["id"], issue["id"])
    assert row is not None
    assert float(row["position"]) == 7.5
    assert row["group_key"] == "todo"
    # View isolation: the canonical issues.position is untouched (§2.7).
    async with session_factory() as session:
        canon = (
            await session.execute(
                text("SELECT position FROM issues WHERE id = :i"), {"i": issue["id"]}
            )
        ).scalar_one()
    assert float(canon) == 999.0

    # Reorder fans out issue.moved carrying the view_id (§4.3).
    moved = await _outbox_events(session_factory, "issue.moved")
    assert any(
        frame["data"].get("id") == issue["id"] and frame["data"].get("view_id") == view["id"]
        for frame in moved
    )


async def test_reorder_precision_exhaustion_reranks_column(api_client, session_factory):
    owner = await _register_and_login(api_client, "reorder-rerank@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-reorder-rerank")
    view = await _create_view(api_client, owner, ws["id"])
    a = await _create_issue(api_client, owner, ws["id"])
    b = await _create_issue(api_client, owner, ws["id"])
    c = await _create_issue(api_client, owner, ws["id"])
    view_id = view["id"]
    # Seed a/b within the float midpoint floor (POSITION_EPSILON = 1e-6).
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO view_issue_positions "
                "(workspace_id, view_id, issue_id, group_key, position) VALUES "
                "(:ws, :v, :a, 'todo', 1.0), (:ws, :v, :b, 'todo', 1.0000001)"
            ),
            {"ws": ws["id"], "v": view_id, "a": a["id"], "b": b["id"]},
        )

    # Reordering c onto the exhausted column triggers a whole-column rerank.
    resp = await api_client.post(
        f"/api/v1/views/{view_id}/reorder",
        json={"issue_id": c["id"], "to_group_key": "todo", "position": 1.0},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text

    # After rerank every card sits on a distinct integer position (no collisions);
    # the exact card→slot mapping follows (position, id) order, so compare as a set.
    positions = set()
    for iid in (a["id"], b["id"], c["id"]):
        row = await _position(session_factory, view_id, iid)
        assert row is not None
        positions.add(float(row["position"]))
    assert positions == {1.0, 2.0, 3.0}

    # Rerank fans out issue.moved for the reranked cards (view_id present).
    moved = await _outbox_events(session_factory, "issue.moved")
    moved_ids = {
        frame["data"]["id"]
        for frame in moved
        if frame["data"].get("view_id") == view_id
    }
    assert {a["id"], b["id"]} <= moved_ids


async def test_reorder_rls_tenant_scoped_and_unauth_rejected(
    api_client, session_factory, db_url
):
    owner = await _register_and_login(api_client, "reorder-rls@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-reorder-rls")
    view = await _create_view(api_client, owner, ws["id"])
    issue = await _create_issue(api_client, owner, ws["id"])
    await api_client.post(
        f"/api/v1/views/{view['id']}/reorder",
        json={"issue_id": issue["id"], "to_group_key": "todo", "position": 3.0},
        headers=_auth(owner),
    )

    # Unauthenticated reorder is rejected (auth middleware → 401).
    noauth = await api_client.post(
        f"/api/v1/views/{view['id']}/reorder",
        json={"issue_id": issue["id"], "to_group_key": "todo", "position": 4.0},
    )
    assert noauth.status_code == 401

    # RLS fail-closed: app role without the tenant GUC sees no rows; a foreign
    # tenant GUC hides this tenant's view_issue_positions entirely (§6.2 rule 5).
    engine = create_async_engine(_app_role_url(db_url))
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT count(*) FROM view_issue_positions"))
            await conn.rollback()

            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, false)"),
                {"ws": ws["id"]},
            )
            own = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM view_issue_positions WHERE view_id = :v"
                    ),
                    {"v": view["id"]},
                )
            ).scalar_one()
            assert own == 1

            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, false)"),
                {"ws": str(uuid.uuid4())},
            )
            foreign = (
                await conn.execute(
                    text("SELECT count(*) FROM view_issue_positions")
                )
            ).scalar_one()
            assert foreign == 0
    finally:
        await engine.dispose()

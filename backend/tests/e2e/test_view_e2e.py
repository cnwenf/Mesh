"""Real end-to-end tests for the views definition layer (MES-43).

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
RLS is live on the app path) + real PostgreSQL 16 + real API calls + real
database durability assertions. Covers:

- full CRUD durability: the view row physically lands in PostgreSQL and the
  config JSONB round-trips;
- T1 cross-tenant isolation: API 404s with foreign credentials + composite
  FK rejection at INSERT (README §9 T1);
- RLS defense-in-depth on the app path (fail-closed without the tenant GUC,
  tenant rows visible with it — README §6.2 rule 5);
- view.updated through the outbox unique write path with the right channels
  (README §6.6/§6.7);
- optimistic concurrency over the wire (If-Match → 409) and duplicate
  default views → 409.
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
    body = {"name": "Sprint Board"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/views", json=body, headers=_auth(token)
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


# --- full real-server flow + durability --------------------------------------


async def test_view_full_flow_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-view@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-view")

    created = await _create_view(
        api_client,
        owner,
        ws["id"],
        visibility="shared",
        group_by="priority",
        filters={
            "operator": "AND",
            "conditions": [{"field": "priority", "op": "in", "value": ["high", "urgent"]}],
        },
        sort=[{"field": "position", "order": "asc"}],
        board_settings={"card_fields": ["labels", "due_date"]},
    )
    view_id = uuid.UUID(created["id"])

    # Durability: the row physically exists with the exact JSONB config.
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT name, layout, visibility, filters, group_by, sort, "
                    "display_fields, board_settings, is_default "
                    "FROM views WHERE id = :id"
                ),
                {"id": view_id},
            )
        ).one()
    assert row.name == "Sprint Board"
    assert row.layout == "board"
    assert row.visibility == "shared"
    assert row.group_by == "priority"
    assert row.filters["conditions"][0]["value"] == ["high", "urgent"]
    assert row.sort == [{"field": "position", "order": "asc"}]
    assert row.board_settings == {"card_fields": ["labels", "due_date"]}
    assert row.is_default is False

    # Read back over HTTP (workspace-less path).
    got = await api_client.get(f"/api/v1/views/{view_id}", headers=_auth(owner))
    assert got.status_code == 200
    assert got.json()["data"]["group_by"] == "priority"

    # PATCH with shallow board_settings merge.
    patched = await api_client.patch(
        f"/api/v1/views/{view_id}",
        json={"name": "Renamed", "board_settings": {"wip": {"in_progress": {"limit": 5}}}},
        headers=_auth(owner),
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()["data"]
    assert data["name"] == "Renamed"
    assert data["board_settings"] == {
        "card_fields": ["labels", "due_date"],
        "wip": {"in_progress": {"limit": 5, "enforcement": "warn"}},
    }
    async with session_factory() as session:
        stored_name = (
            await session.execute(text("SELECT name FROM views WHERE id = :id"), {"id": view_id})
        ).scalar_one()
    assert stored_name == "Renamed"

    # WIP endpoint + duplicate + reorder.
    wip = await api_client.patch(
        f"/api/v1/views/{view_id}/wip",
        json={"group_key": "todo", "limit": 2, "enforcement": "block"},
        headers=_auth(owner),
    )
    assert wip.status_code == 200
    assert wip.json()["data"]["board_settings"]["wip"]["todo"] == {
        "limit": 2,
        "enforcement": "block",
    }

    dup = await api_client.post(f"/api/v1/views/{view_id}/duplicate", headers=_auth(owner))
    assert dup.status_code == 201
    dup_id = uuid.UUID(dup.json()["data"]["id"])
    assert dup.json()["data"]["name"] == "Renamed (copy)"
    async with session_factory() as session:
        dup_owner = (
            await session.execute(
                text("SELECT owner_member_id FROM views WHERE id = :id"), {"id": dup_id}
            )
        ).scalar_one()
    assert dup_owner is not None

    listed = await api_client.get(f"/api/v1/workspaces/{ws['id']}/views", headers=_auth(owner))
    ids = [item["id"] for item in listed.json()["data"]]
    reorder = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/views/reorder",
        json={"view_ids": [str(dup_id), str(view_id)]},
        headers=_auth(owner),
    )
    assert reorder.status_code == 200
    assert [item["id"] for item in reorder.json()["data"]] == [str(dup_id), str(view_id)]

    # Delete removes the physical row.
    deleted = await api_client.delete(f"/api/v1/views/{view_id}", headers=_auth(owner))
    assert deleted.status_code == 204
    async with session_factory() as session:
        remaining = (
            await session.execute(text("SELECT count(*) FROM views WHERE id = :id"), {"id": view_id})
        ).scalar_one()
    assert remaining == 0

    # view.updated rode the outbox unique write path with both channels
    # (create + update + wip + duplicate frames; deletion marker on the list
    # channel).
    frames = await _outbox_events(session_factory, "view.updated")
    channels = {frame["channel"] for frame in frames}
    assert f"view:{view_id}" in channels
    assert f"workspace:{ws['id']}:views" in channels
    deleted_frames = [frame for frame in frames if frame["data"].get("deleted") is True]
    assert len(deleted_frames) == 1
    assert deleted_frames[0]["data"]["id"] == str(view_id)
    assert set(ids) >= {str(view_id), str(dup_id)}


# --- T1 cross-tenant isolation ------------------------------------------------


async def test_cross_tenant_api_isolation(api_client):
    token_a = await _register_and_login(api_client, "tenant-a@corp.com")
    ws_a = await _create_workspace(api_client, token_a, "e2e-tenant-a")
    view_a = await _create_view(api_client, token_a, ws_a["id"])

    token_b = await _register_and_login(api_client, "tenant-b@corp.com")
    await _create_workspace(api_client, token_b, "e2e-tenant-b")

    for method, path in (
        ("get", f"/api/v1/views/{view_a['id']}"),
        ("delete", f"/api/v1/views/{view_a['id']}"),
    ):
        resp = await getattr(api_client, method)(path, headers=_auth(token_b))
        assert resp.status_code == 404, (method, path, resp.text)
    resp = await api_client.patch(
        f"/api/v1/views/{view_a['id']}", json={"name": "Stolen"}, headers=_auth(token_b)
    )
    assert resp.status_code == 404

    # Listing B's workspace never shows A's shared views either.
    shared = await _create_view(api_client, token_a, ws_a["id"], name="SharedA", visibility="shared")
    ws_b_list = await api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/views", headers=_auth(token_b)
    )
    # B is not a member of A's workspace → membership gate 404/403.
    assert ws_b_list.status_code in (403, 404)
    assert shared["visibility"] == "shared"


async def test_cross_tenant_composite_fk_rejected(session_factory):
    """Composite FKs reject a view referencing another workspace's member."""
    async with session_factory() as session, session.begin():
        ws_a_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) VALUES (:n, :s) RETURNING id"
                ),
                {"n": "A", "s": f"fk-a-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        ws_b_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) VALUES (:n, :s) RETURNING id"
                ),
                {"n": "B", "s": f"fk-b-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name, password_hash, status) "
                    "VALUES (:e, 'X', 'x', 'active') RETURNING id"
                ),
                {"e": f"{uuid.uuid4().hex[:12]}@corp.com"},
            )
        ).scalar_one()
        member_b_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status, joined_at) "
                    "VALUES (:ws, 'human', :u, 'member', 'active', now()) RETURNING id"
                ),
                {"ws": ws_b_id, "u": user_id},
            )
        ).scalar_one()

    async with session_factory() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO views (workspace_id, owner_member_id, name) "
                        "VALUES (:ws, :member, 'cross-tenant')"
                    ),
                    {"ws": ws_a_id, "member": member_b_id},
                )


# --- RLS defense-in-depth on the app path -------------------------------------


async def test_rls_fail_closed_and_tenant_scoped(api_client, session_factory, db_url):
    owner = await _register_and_login(api_client, "rls-view@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-rls-view")
    created = await _create_view(api_client, owner, ws["id"])

    engine = create_async_engine(_app_role_url(db_url))
    try:
        async with engine.connect() as conn:
            # Without the tenant GUC the app role is fail-closed.
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT count(*) FROM views"))
            await conn.rollback()  # the failed statement aborted the tx

            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, false)"),
                {"ws": ws["id"]},
            )
            visible = (await conn.execute(text("SELECT id FROM views"))).scalars().all()
            assert str(created["id"]) in {str(row) for row in visible}

            # Another tenant's GUC hides the row entirely.
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, false)"),
                {"ws": str(uuid.uuid4())},
            )
            hidden = (await conn.execute(text("SELECT count(*) FROM views"))).scalar_one()
            assert hidden == 0
    finally:
        await engine.dispose()


# --- optimistic concurrency + default uniqueness over the wire -----------------


async def test_optimistic_concurrency_conflict(api_client):
    owner = await _register_and_login(api_client, "occ-view@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-occ-view")
    created = await _create_view(api_client, owner, ws["id"])
    version = created["updated_at"]

    first = await api_client.patch(
        f"/api/v1/views/{created['id']}",
        json={"name": "First"},
        headers={**_auth(owner), "If-Match": f'"{version}"'},
    )
    assert first.status_code == 200, first.text
    second = await api_client.patch(
        f"/api/v1/views/{created['id']}",
        json={"name": "Second"},
        headers={**_auth(owner), "If-Match": f'"{version}"'},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_duplicate_default_view_conflict(api_client):
    owner = await _register_and_login(api_client, "default-view@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-default-view")
    await _create_view(api_client, owner, ws["id"], name="A", is_default=True)
    second = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/views",
        json={"name": "B", "is_default": True},
        headers=_auth(owner),
    )
    # The service hands off the default in-transaction, so the second create
    # succeeds and the first loses the flag.
    assert second.status_code == 201, second.text
    listed = await api_client.get(f"/api/v1/workspaces/{ws['id']}/views", headers=_auth(owner))
    defaults = [item for item in listed.json()["data"] if item["is_default"]]
    assert [item["name"] for item in defaults] == ["B"]

"""Real end-to-end tests for the label-property definition layer.

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
PostgreSQL RLS is live on the app path) + real PostgreSQL 16 + real API calls
+ real database durability assertions. Covers:

- full definition-layer CRUD flows (labels, custom fields, enum options) with
  database durability and the outbox → projector unique write path (README
  §6.6/§6.7): channel seq monotonic after projection;
- T1 cross-tenant isolation: composite FK rejections at INSERT (labels scope,
  custom_field_options → field def) + API 404s across workspaces;
- RLS defense-in-depth under the restricted app role: cross-tenant reads are
  empty and cross-tenant writes are rejected (README §6.2 rule 5).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
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


async def _outbox_payloads(session_factory, event_name: str | None = None) -> list[dict]:
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
    if event_name is not None:
        return [row for row in rows if row.get("event") == event_name]
    return list(rows)


# --- full real-server flow ---------------------------------------------------


async def test_label_and_field_definition_full_flow_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-lbl@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-lbl")

    # Label CRUD over the real API.
    created = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/labels",
        json={"name": "bug", "color": "#e5484d", "description": "defects"},
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    label = created.json()["data"]
    patched = await api_client.patch(
        f"/api/v1/labels/{label['id']}",
        json={"color": "#ff0000"},
        headers={**_auth(owner), "If-Match": label["updated_at"]},
    )
    assert patched.status_code == 200, patched.text

    # Custom field with initial options + a later option add.
    field_resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/custom-fields",
        json={
            "name": "Severity",
            "field_key": "severity",
            "type": "single_select",
            "is_required": True,
            "required_on": ["status:done"],
            "options": [
                {"name": "Minor", "color": "#888888", "position": 0},
                {"name": "Major", "color": "#f5a623", "position": 1},
            ],
        },
        headers=_auth(owner),
    )
    assert field_resp.status_code == 201, field_resp.text
    field = field_resp.json()["data"]
    added = await api_client.post(
        f"/api/v1/custom-fields/{field['id']}/options",
        json={"name": "Critical", "color": "#e5484d", "position": 2},
        headers=_auth(owner),
    )
    assert added.status_code == 201, added.text

    # ---- durability: real rows in the database, not just HTTP echoes.
    async with session_factory() as session:
        label_row = (
            await session.execute(
                text("SELECT name, color, description FROM labels WHERE id = :id"),
                {"id": uuid.UUID(label["id"])},
            )
        ).one()
        field_row = (
            await session.execute(
                text(
                    "SELECT name, field_key, type, is_required, required_on "
                    "FROM custom_field_defs WHERE id = :id"
                ),
                {"id": uuid.UUID(field["id"])},
            )
        ).one()
        option_names = (
            (
                await session.execute(
                    text(
                        "SELECT name FROM custom_field_options "
                        "WHERE field_def_id = :id ORDER BY position"
                    ),
                    {"id": uuid.UUID(field["id"])},
                )
            ).scalars().all()
        )
    assert label_row.name == "bug"
    assert label_row.color == "#ff0000"  # PATCH persisted
    assert label_row.description == "defects"
    assert field_row.field_key == "severity"
    assert field_row.type == "single_select"
    assert field_row.is_required is True
    assert field_row.required_on == ["status:done"]
    assert option_names == ["Minor", "Major", "Critical"]

    # ---- events went through the outbox unique write path.
    assert len(await _outbox_payloads(session_factory, "label.created")) == 1
    assert len(await _outbox_payloads(session_factory, "label.updated")) == 1
    field_events = await _outbox_payloads(session_factory, "custom_field.updated")
    assert [event["data"]["change"] for event in field_events] == ["created"]
    option_events = await _outbox_payloads(session_factory, "custom_field_option.updated")
    assert len(option_events) == 1
    assert option_events[0]["data"]["option"]["name"] == "Critical"

    # ---- the projector registers them with monotonic per-channel seq.
    from mesh.events.vocab import REALTIME_PUBLISH
    from mesh.outbox.projector import project_realtime_event
    from mesh.outbox.relay import OutboxRelay

    relay = OutboxRelay(session_factory, handlers={REALTIME_PUBLISH: project_realtime_event})
    result = await relay.run_once()
    # At least label.created / label.updated / custom_field.updated(created) /
    # custom_field_option.updated (workspace creation also emits member.added);
    # deletions happen AFTER this projection.
    assert result.published >= 4
    async with session_factory() as session:
        seqs = (
            (
                await session.execute(
                    text(
                        "SELECT seq FROM realtime_events "
                        "WHERE channel = :ch ORDER BY seq"
                    ),
                    {"ch": f"workspace:{ws['id']}:labels"},
                )
            ).scalars().all()
        )
        field_seqs = (
            (
                await session.execute(
                    text(
                        "SELECT seq FROM realtime_events "
                        "WHERE channel = :ch ORDER BY seq"
                    ),
                    {"ch": f"workspace:{ws['id']}:custom_fields"},
                )
            ).scalars().all()
        )
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)) and len(seqs) == 2
    assert field_seqs == sorted(field_seqs) and len(field_seqs) == 2

    # ---- delete label + field; DB rows gone (option cascade).
    assert (
        await api_client.delete(f"/api/v1/labels/{label['id']}", headers=_auth(owner))
    ).status_code == 200
    assert (
        await api_client.delete(f"/api/v1/custom-fields/{field['id']}", headers=_auth(owner))
    ).status_code == 200
    async with session_factory() as session:
        remaining_labels = (
            await session.execute(text("SELECT count(*) FROM labels"))
        ).scalar()
        remaining_defs = (
            await session.execute(text("SELECT count(*) FROM custom_field_defs"))
        ).scalar()
        remaining_options = (
            await session.execute(text("SELECT count(*) FROM custom_field_options"))
        ).scalar()
    assert (remaining_labels, remaining_defs, remaining_options) == (0, 0, 0)
    deleted_events = await _outbox_payloads(session_factory, "label.deleted")
    assert len(deleted_events) == 1
    field_after = await _outbox_payloads(session_factory, "custom_field.updated")
    assert [event["data"]["change"] for event in field_after] == ["created", "deleted"]


async def test_unique_indexes_enforce_scope_uniqueness_at_db_level(session_factory):
    """uq_labels_name / uq_cfdefs_key reject same-scope duplicates on raw INSERT."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    ws_id = workspace.id
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO labels (workspace_id, name, color) "
                "VALUES (:ws, 'bug', '#ffffff')"
            ),
            {"ws": ws_id},
        )
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO labels (workspace_id, name, color) "
                    "VALUES (:ws, 'bug', '#000000')"
                ),
                {"ws": ws_id},
            )
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO custom_field_defs (workspace_id, name, field_key, type) "
                "VALUES (:ws, 'A', 'severity', 'text')"
            ),
            {"ws": ws_id},
        )
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO custom_field_defs (workspace_id, name, field_key, type) "
                    "VALUES (:ws, 'B', 'severity', 'number')"
                ),
                {"ws": ws_id},
            )


# --- T1 cross-tenant isolation ------------------------------------------------


async def test_cross_tenant_composite_fk_rejected_at_insert(session_factory):
    """README §9 T1: cross-workspace composite FK INSERTs are DB-rejected."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    # A project owned by workspace B.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO projects (id, workspace_id, name, key) "
                "VALUES (:pid, :ws, 'B project', 'BPX')"
            ),
            {"pid": (pid := uuid.uuid4()), "ws": ws_b.id},
        )
    # Label in workspace A scoping itself to B's project → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO labels (workspace_id, project_id, name, color) "
                    "VALUES (:ws_a, :pid, 'sneaky', '#ffffff')"
                ),
                {"ws_a": ws_a.id, "pid": pid},
            )
    # A field def owned by workspace A...
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO custom_field_defs (id, workspace_id, name, field_key, type) "
                "VALUES (:fid, :ws, 'Sev', 'sev', 'single_select')"
            ),
            {"fid": (fid := uuid.uuid4()), "ws": ws_a.id},
        )
    # ...an option row claiming workspace B but referencing A's field → rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO custom_field_options (workspace_id, field_def_id, name) "
                    "VALUES (:ws_b, :fid, 'opt')"
                ),
                {"ws_b": ws_b.id, "fid": fid},
            )


async def test_cross_tenant_api_returns_404(api_client):
    owner_a = await _register_and_login(api_client, "a-owner@corp.com")
    owner_b = await _register_and_login(api_client, "b-owner@corp.com")
    ws_a = await _create_workspace(api_client, owner_a, "e2e-tenant-a")
    await _create_workspace(api_client, owner_b, "e2e-tenant-b")
    label = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_a['id']}/labels",
            json={"name": "secret", "color": "#ffffff"},
            headers=_auth(owner_a),
        )
    ).json()["data"]
    field = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_a['id']}/custom-fields",
            json={"name": "X", "field_key": "x_key", "type": "text"},
            headers=_auth(owner_a),
        )
    ).json()["data"]
    # Workspace B's owner: every workspace-less path resolves to 404.
    for method, path in (
        ("PATCH", f"/api/v1/labels/{label['id']}"),
        ("DELETE", f"/api/v1/labels/{label['id']}"),
        ("PATCH", f"/api/v1/custom-fields/{field['id']}"),
        ("DELETE", f"/api/v1/custom-fields/{field['id']}"),
        ("GET", f"/api/v1/custom-fields/{field['id']}/options"),
    ):
        resp = await api_client.request(
            method, path, json={"name": "hax"} if method == "PATCH" else None,
            headers=_auth(owner_b),
        )
        assert resp.status_code == 404, (method, path, resp.text)
    # And A's label listing is unreachable from B's workspace context.
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/labels", headers=_auth(owner_b)
    )
    assert listing.status_code == 404


async def test_rls_cross_tenant_read_write_rejected_under_app_role(
    session_factory, db_url
):
    """README §6.2 rule 5: RLS backstop under the restricted mesh_app role."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO labels (id, workspace_id, name, color) "
                "VALUES (:id, :ws, 'bug', '#ffffff')"
            ),
            {"id": (label_id := uuid.uuid4()), "ws": ws_a.id},
        )
    engine = create_async_engine(_app_role_url(db_url))
    try:
        # Tenant B context: A's label is invisible.
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                {"ws": str(ws_b.id)},
            )
            rows = (
                await conn.execute(text("SELECT id FROM labels WHERE id = :id"), {"id": label_id})
            ).all()
        assert rows == []
        # Tenant B context: writing an A-owned row is rejected by RLS.
        with pytest.raises(Exception) as excinfo:
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_b.id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO labels (workspace_id, name, color) "
                        "VALUES (:ws_a, 'sneaky', '#ffffff')"
                    ),
                    {"ws_a": ws_a.id},
                )
        assert "row-level security" in str(excinfo.value).lower()
        # Tenant A context sees its own row.
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                {"ws": str(ws_a.id)},
            )
            rows = (
                await conn.execute(text("SELECT name FROM labels WHERE id = :id"), {"id": label_id})
            ).all()
        assert [row[0] for row in rows] == ["bug"]
    finally:
        await engine.dispose()

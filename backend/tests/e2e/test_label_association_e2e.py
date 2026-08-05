"""Real end-to-end tests for the label-property issue-association layer.

Real uvicorn API subprocess (restricted mesh_app role → PostgreSQL RLS is
live on the app path) + real PostgreSQL 16 + real API calls + real database
durability assertions. Covers the MES-32 remainder:

- full association flows over HTTP (issue labels add/remove/replace, merge,
  custom-field value PUT/GET) with row-level durability and the outbox →
  projector unique write path (channel seq monotonic after projection);
- per-type validation negatives with the named 422 codes over real HTTP;
- required-field status-transition gate over real HTTP;
- T1 cross-tenant isolation: composite FK rejections at raw INSERT on BOTH
  association tables + API 404s across workspaces + RLS backstop reads /
  writes under the restricted app role (README §6.2 rule 5);
- T18: physically deleting a member referenced by a member-typed value nulls
  ONLY value_member_id (PG16 column-level SET NULL), the row survives.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.e2e.conftest import _app_role_url

pytestmark = pytest.mark.e2e

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


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


async def test_issue_association_full_flow_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-assoc@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-assoc")

    # Labels + a select field + an issue, all over the real API.
    bug = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/labels",
            json={"name": "bug", "color": "#e5484d"},
            headers=_auth(owner),
        )
    ).json()["data"]
    field = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={
                "name": "Severity", "field_key": "severity", "type": "single_select",
                "options": [{"name": "Major"}, {"name": "Minor"}],
            },
            headers=_auth(owner),
        )
    ).json()["data"]
    major = next(o["id"] for o in field["options"] if o["name"] == "Major")
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": "assoc e2e"},
            headers=_auth(owner),
        )
    ).json()["data"]

    # Attach label + set value over the real API.
    resp = await api_client.post(
        f"/api/v1/issues/{issue['id']}/labels/{bug['id']}", headers=_auth(owner)
    )
    assert resp.status_code == 200, resp.text
    detail = await api_client.get(
        f"/api/v1/issues/{issue['id']}", headers=_auth(owner)
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["labels"] == [
        {"id": bug["id"], "name": "bug", "color": "#e5484d"}
    ]
    issue_page = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/issues", headers=_auth(owner)
    )
    assert issue_page.status_code == 200, issue_page.text
    listed_issue = next(
        row for row in issue_page.json()["data"] if row["id"] == issue["id"]
    )
    assert listed_issue["labels"] == detail.json()["data"]["labels"]
    resp = await api_client.put(
        f"/api/v1/issues/{issue['id']}/custom-field-values",
        json={"values": [{"field_def_id": field["id"], "value_json": major}]},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text

    # ---- durability: real rows in both association tables.
    async with session_factory() as session:
        link = (
            await session.execute(
                text(
                    "SELECT workspace_id, label_id FROM issue_labels "
                    "WHERE issue_id = :id"
                ),
                {"id": uuid.UUID(issue["id"])},
            )
        ).one()
        value = (
            await session.execute(
                text(
                    "SELECT field_def_id, value_json, value_text, value_number "
                    "FROM issue_custom_field_values WHERE issue_id = :id"
                ),
                {"id": uuid.UUID(issue["id"])},
            )
        ).one()
    assert str(link.label_id) == bug["id"]
    assert str(link.workspace_id) == ws["id"]
    assert str(value.field_def_id) == field["id"]
    assert value.value_json == major  # JSONB string scalar round-trip
    assert value.value_text is None and value.value_number is None

    # ---- events went through the outbox unique write path.
    label_events = await _outbox_payloads(session_factory, "issue.labels_changed")
    assert any(
        e["data"]["issue_id"] == issue["id"]
        and [label["id"] for label in e["data"]["labels"]] == [bug["id"]]
        for e in label_events
    )
    field_events = await _outbox_payloads(session_factory, "issue.custom_field_changed")
    assert any(
        e["data"]["issue_id"] == issue["id"]
        and e["data"]["field_def_id"] == field["id"]
        and e["data"]["value"]["value_json"] == major
        for e in field_events
    )

    # ---- the projector registers them with monotonic per-channel seq.
    from mesh.events.vocab import REALTIME_PUBLISH
    from mesh.outbox.projector import project_realtime_event
    from mesh.outbox.relay import OutboxRelay

    relay = OutboxRelay(session_factory, handlers={REALTIME_PUBLISH: project_realtime_event})
    result = await relay.run_once()
    assert result.published >= 2
    async with session_factory() as session:
        seqs = (
            (
                await session.execute(
                    text(
                        "SELECT seq FROM realtime_events "
                        "WHERE channel = :ch ORDER BY seq"
                    ),
                    {"ch": f"issue:{issue['id']}"},
                )
            )
            .scalars().all()
        )
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)) and len(seqs) >= 2

    # ---- merge over the real API migrates the durable link.
    defect = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/labels",
            json={"name": "defect", "color": "#aa0000"},
            headers=_auth(owner),
        )
    ).json()["data"]
    await api_client.post(
        f"/api/v1/issues/{issue['id']}/labels/{defect['id']}", headers=_auth(owner)
    )
    preview = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(owner)
    )
    preview_counts = {
        label["id"]: label["issue_count"] for label in preview.json()["data"]
    }
    assert preview_counts[defect["id"]] == 1
    resp = await api_client.post(
        f"/api/v1/labels/{defect['id']}/merge",
        json={"target_label_id": bug["id"]}, headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["merged_issue_count"] == 1
    async with session_factory() as session:
        remaining = (
            (
                await session.execute(
                    text("SELECT label_id FROM issue_labels WHERE issue_id = :id"),
                    {"id": uuid.UUID(issue["id"])},
                )
            ).scalars().all()
        )
    assert [str(r) for r in remaining] == [bug["id"]]
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/labels", headers=_auth(owner)
    )
    assert listing.json()["data"][0]["issue_count"] == 1


async def test_type_validation_negatives_over_http(api_client):
    owner = await _register_and_login(api_client, "neg@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-neg")
    number_field = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "Users", "field_key": "users", "type": "number",
                  "config": {"min": 0, "max": 100}},
            headers=_auth(owner),
        )
    ).json()["data"]
    url_field = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "Link", "field_key": "link", "type": "url"},
            headers=_auth(owner),
        )
    ).json()["data"]
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": "neg"}, headers=_auth(owner),
        )
    ).json()["data"]
    base = f"/api/v1/issues/{issue['id']}/custom-field-values"

    cases = [
        # wrong column for type
        ({"field_def_id": number_field["id"], "value_text": "5"}, "wrong_value_column"),
        # below configured min
        ({"field_def_id": number_field["id"], "value_number": -1}, "number_below_min"),
        # above configured max
        ({"field_def_id": number_field["id"], "value_number": 101}, "number_above_max"),
        # malformed url
        ({"field_def_id": url_field["id"], "value_text": "nope"}, "url_value_invalid"),
        # two value columns at once
        ({"field_def_id": url_field["id"], "value_text": "https://x.co",
          "value_boolean": True}, "exactly_one_value_column"),
    ]
    for values_entry, reason in cases:
        resp = await api_client.put(
            base, json={"values": [values_entry]}, headers=_auth(owner)
        )
        assert resp.status_code == 422, (values_entry, resp.text)
        error = resp.json()["error"]
        assert error["code"] == "invalid_field_value"
        assert error["details"]["reason"] == reason


async def test_required_field_blocks_done_transition(api_client, session_factory):
    owner = await _register_and_login(api_client, "req-e2e@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-req")
    field = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/custom-fields",
            json={"name": "Acceptor", "field_key": "acceptor", "type": "text",
                  "is_required": True, "required_on": ["status:done"]},
            headers=_auth(owner),
        )
    ).json()["data"]
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/issues",
            json={"title": "needs acceptor"}, headers=_auth(owner),
        )
    ).json()["data"]
    statuses = (
        await api_client.get(
            f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(owner)
        )
    ).json()["data"]
    done = next(s for s in statuses if s["category"] == "done")

    resp = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"status_id": done["id"], "version": issue["version"]},
        headers=_auth(owner),
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "required_field_missing"
    assert error["details"]["missing"] == [
        {"field_def_id": field["id"], "name": "Acceptor"}
    ]
    # The transition did NOT persist.
    async with session_factory() as session:
        category = (
            await session.execute(
                text("SELECT state_category FROM issues WHERE id = :id"),
                {"id": uuid.UUID(issue["id"])},
            )
        ).scalar()
    assert category != "done"


# --- T1 cross-tenant isolation ------------------------------------------------


async def test_cross_tenant_composite_fk_rejected_at_insert(session_factory):
    """README §9 T1: cross-workspace composite FK INSERTs are DB-rejected on
    BOTH association tables."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    # Workspace A: a status + an issue + a label + a field def.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issue_statuses (id, workspace_id, name, category, color, position) "
                "VALUES (:st, :ws, 'Todo', 'todo', '#000000', 0)"
            ),
            {"st": (st := uuid.uuid4()), "ws": ws_a.id},
        )
        await session.execute(
            text(
                "INSERT INTO issues (id, workspace_id, identifier_namespace_key, "
                "number, identifier, title, status_id, state_category) "
                "VALUES (:iss, :ws, 'ns', 1, 'T-1', 't', :st, 'todo')"
            ),
            {"iss": (issue_id := uuid.uuid4()), "ws": ws_a.id, "st": st},
        )
        await session.execute(
            text(
                "INSERT INTO labels (id, workspace_id, name, color) "
                "VALUES (:lb, :ws, 'bug', '#ffffff')"
            ),
            {"lb": (label_a := uuid.uuid4()), "ws": ws_a.id},
        )
        await session.execute(
            text(
                "INSERT INTO custom_field_defs (id, workspace_id, name, field_key, type) "
                "VALUES (:fd, :ws, 'F', 'f_key', 'text')"
            ),
            {"fd": (field_a := uuid.uuid4()), "ws": ws_a.id},
        )
    # A label owned by workspace B.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO labels (id, workspace_id, name, color) "
                "VALUES (:lb, :ws, 'sneaky', '#000000')"
            ),
            {"lb": (label_b := uuid.uuid4()), "ws": ws_b.id},
        )
    # issue_labels pairing A's issue with B's label under tenant A → rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO issue_labels (workspace_id, issue_id, label_id) "
                    "VALUES (:ws, :iss, :lb)"
                ),
                {"ws": ws_a.id, "iss": issue_id, "lb": label_b},
            )
    # Same-tenant pairing works.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issue_labels (workspace_id, issue_id, label_id) "
                "VALUES (:ws, :iss, :lb)"
            ),
            {"ws": ws_a.id, "iss": issue_id, "lb": label_a},
        )
    # issue_custom_field_values claiming tenant B but A's field def → rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO issue_custom_field_values "
                    "(workspace_id, issue_id, field_def_id, value_text) "
                    "VALUES (:ws_b, :iss, :fd, 'x')"
                ),
                {"ws_b": ws_b.id, "iss": issue_id, "fd": field_a},
            )


async def test_cross_tenant_api_returns_404(api_client):
    owner_a = await _register_and_login(api_client, "ta@corp.com")
    owner_b = await _register_and_login(api_client, "tb@corp.com")
    ws_a = await _create_workspace(api_client, owner_a, "e2e-ta")
    await _create_workspace(api_client, owner_b, "e2e-tb")
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_a['id']}/issues",
            json={"title": "secret"}, headers=_auth(owner_a),
        )
    ).json()["data"]
    for method, path, body in (
        ("GET", f"/api/v1/issues/{issue['id']}/labels", None),
        ("PUT", f"/api/v1/issues/{issue['id']}/labels", {"label_ids": []}),
        ("GET", f"/api/v1/issues/{issue['id']}/custom-field-values", None),
        ("PUT", f"/api/v1/issues/{issue['id']}/custom-field-values", {"values": []}),
    ):
        resp = await api_client.request(
            method, path, json=body, headers=_auth(owner_b)
        )
        assert resp.status_code == 404, (method, path, resp.text)


async def test_rls_cross_tenant_on_association_tables_under_app_role(
    session_factory, db_url
):
    """README §6.2 rule 5: RLS backstop on issue_labels /
    issue_custom_field_values under the restricted mesh_app role."""
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issue_statuses (id, workspace_id, name, category, color, position) "
                "VALUES (:st, :ws, 'Todo', 'todo', '#000000', 0)"
            ),
            {"st": (st := uuid.uuid4()), "ws": ws_a.id},
        )
        await session.execute(
            text(
                "INSERT INTO issues (id, workspace_id, identifier_namespace_key, "
                "number, identifier, title, status_id, state_category) "
                "VALUES (:iss, :ws, 'ns', 1, 'T-1', 't', :st, 'todo')"
            ),
            {"iss": (issue_id := uuid.uuid4()), "ws": ws_a.id, "st": st},
        )
        await session.execute(
            text(
                "INSERT INTO labels (id, workspace_id, name, color) "
                "VALUES (:lb, :ws, 'bug', '#ffffff')"
            ),
            {"lb": (label_a := uuid.uuid4()), "ws": ws_a.id},
        )
        await session.execute(
            text(
                "INSERT INTO issue_labels (workspace_id, issue_id, label_id) "
                "VALUES (:ws, :iss, :lb)"
            ),
            {"ws": ws_a.id, "iss": issue_id, "lb": label_a},
        )
    engine = create_async_engine(_app_role_url(db_url))
    try:
        # Tenant B context: A's issue_labels row is invisible.
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                {"ws": str(ws_b.id)},
            )
            rows = (
                await conn.execute(
                    text("SELECT issue_id FROM issue_labels WHERE issue_id = :id"),
                    {"id": issue_id},
                )
            ).all()
        assert rows == []
        # Tenant B context: writing into A's issue is rejected by RLS.
        with pytest.raises(Exception) as excinfo:
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_b.id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO issue_labels (workspace_id, issue_id, label_id) "
                        "VALUES (:ws_a, :iss, :lb)"
                    ),
                    {"ws_a": ws_a.id, "iss": issue_id, "lb": label_a},
                )
        assert "row-level security" in str(excinfo.value).lower()
        # Tenant A sees its own row.
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                {"ws": str(ws_a.id)},
            )
            rows = (
                await conn.execute(
                    text("SELECT label_id FROM issue_labels WHERE issue_id = :id"),
                    {"id": issue_id},
                )
            ).all()
        assert [str(row[0]) for row in rows] == [str(label_a)]
    finally:
        await engine.dispose()


# --- T18: member deletion nulls only the reference column ----------------------


async def test_member_delete_sets_null_on_member_typed_value(session_factory):
    """README §9 T18: ON DELETE SET NULL (value_member_id) — physically
    deleting a member nulls only the reference column; the value row survives
    with workspace_id intact."""
    from mesh.db.models.member import Member
    from mesh.db.models.user import User
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Doomed", password_hash="x", status="active",
        )
        session.add(user)
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id,
            role="member", status="active", joined_at=FIXED_NOW,
        )
        session.add(member)
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issue_statuses (id, workspace_id, name, category, color, position) "
                "VALUES (:st, :ws, 'Todo', 'todo', '#000000', 0)"
            ),
            {"st": (st := uuid.uuid4()), "ws": workspace.id},
        )
        await session.execute(
            text(
                "INSERT INTO issues (id, workspace_id, identifier_namespace_key, "
                "number, identifier, title, status_id, state_category) "
                "VALUES (:iss, :ws, 'ns', 1, 'M-1', 't', :st, 'todo')"
            ),
            {"iss": (issue_id := uuid.uuid4()), "ws": workspace.id, "st": st},
        )
        await session.execute(
            text(
                "INSERT INTO custom_field_defs (id, workspace_id, name, field_key, type) "
                "VALUES (:fd, :ws, 'Acceptor', 'acceptor', 'member')"
            ),
            {"fd": (field_id := uuid.uuid4()), "ws": workspace.id},
        )
        await session.execute(
            text(
                "INSERT INTO issue_custom_field_values "
                "(workspace_id, issue_id, field_def_id, value_member_id) "
                "VALUES (:ws, :iss, :fd, :mb)"
            ),
            {"ws": workspace.id, "iss": issue_id, "fd": field_id, "mb": member.id},
        )
    # Physically delete the member row.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM members WHERE id = :id"), {"id": member.id}
        )
    # The value row survives; only the reference column is nulled.
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT workspace_id, value_member_id, num_nonnulls(value_member_id) "
                    "FROM issue_custom_field_values WHERE issue_id = :id"
                ),
                {"id": issue_id},
            )
        ).one()
    assert row.workspace_id == workspace.id  # tenant column intact
    assert row.value_member_id is None
    assert row.num_nonnulls == 0

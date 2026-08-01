"""Real end-to-end tests for the project module (README §9 T1/T18/T19).

Real uvicorn API subprocess (connected as the restricted mesh_app role, so
RLS is live on the app path) + real PostgreSQL 16 + real API calls + real
database durability assertions. Covers:

- T1  cross-tenant isolation: composite FK rejections at INSERT + API 404s;
- T18 real DELETE behavior for the project module's FKs (column-level
  SET NULL on lead_member_id / prefix registry, CASCADE on owned children,
  RESTRICT on the trail author). The issues.project_id column-level SET NULL
  lands with the issue.md increment; the spec validation script exercises it
  against the identical DDL;
- T19 prefix registry exclusivity: project key vs inbox/retired prefixes and
  permanent reservation after soft delete.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
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
    return accepted.json()["data"]["member"]["id"]


async def _create_project(client, token, ws_id, **overrides) -> dict:
    body = {"name": "Site Revamp", "key": "WEB"}
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects", json=body, headers=_auth(token)
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


# --- full real-server flow ----------------------------------------------------


async def test_project_full_flow_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-flow@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-flow")
    malicious_color = "url(https://attacker.invalid/pixel)"
    rejected_create = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Unsafe", "key": "UNS", "color": malicious_color},
        headers=_auth(owner),
    )
    assert rejected_create.status_code == 400
    assert rejected_create.json()["error"]["code"] == "validation_error"

    created = await _create_project(
        api_client,
        owner,
        ws["id"],
        target_date="2026-08-31",
        color="#a1b2c3",
    )
    assert created["color"] == "#A1B2C3"
    pid = created["id"]
    # Durable in the database (real write, not just an HTTP echo).
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT name, key, status, color FROM projects WHERE id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).one()
    assert (row.name, row.key, row.status, row.color) == (
        "Site Revamp",
        "WEB",
        "planning",
        "#A1B2C3",
    )
    # Prefix registered in the same transaction.
    async with session_factory() as session:
        registry = (
            await session.execute(
                text(
                    "SELECT kind, project_id FROM identifier_prefix_registry "
                    "WHERE workspace_id = :ws AND key = 'WEB'"
                ),
                {"ws": uuid.UUID(ws["id"])},
            )
        ).one()
    assert registry.kind == "project"
    assert registry.project_id == uuid.UUID(pid)
    # Health update trail + writeback + events.
    resp = await api_client.post(
        f"/api/v1/projects/{pid}/updates",
        json={"health": "off_track", "message": "blocked"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    async with session_factory() as session:
        health = (
            await session.execute(
                text("SELECT health FROM projects WHERE id = :id"), {"id": uuid.UUID(pid)}
            )
        ).scalar_one()
        trail = (
            await session.execute(
                text("SELECT COUNT(*) FROM project_updates WHERE project_id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert health == "off_track"
    assert trail == 1
    assert len(await _outbox_events(session_factory, "project_update.added")) == 1
    assert len(await _outbox_events(session_factory, "project.created")) == 2  # both channels
    # PATCH durable + milestone durable.
    rejected_patch = await api_client.patch(
        f"/api/v1/projects/{pid}",
        json={"color": malicious_color},
        headers=_auth(owner),
    )
    assert rejected_patch.status_code == 400
    assert rejected_patch.json()["error"]["code"] == "validation_error"
    resp = await api_client.patch(
        f"/api/v1/projects/{pid}", json={"status": "active"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    resp = await api_client.post(
        f"/api/v1/projects/{pid}/milestones",
        json={"title": "GA", "target_date": "2026-08-31"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    async with session_factory() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM milestones WHERE project_id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert count == 1
    # Soft delete keeps the prefix reserved.
    resp = await api_client.delete(f"/api/v1/projects/{pid}", headers=_auth(owner))
    assert resp.status_code == 200
    async with session_factory() as session:
        deleted_at = (
            await session.execute(
                text("SELECT deleted_at FROM projects WHERE id = :id"), {"id": uuid.UUID(pid)}
            )
        ).scalar_one()
        registry_after = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM identifier_prefix_registry "
                    "WHERE workspace_id = :ws AND key = 'WEB'"
                ),
                {"ws": uuid.UUID(ws["id"])},
            )
        ).scalar_one()
    assert deleted_at is not None
    assert registry_after == 1  # prefix permanently reserved


async def test_pj_h1_lead_self_assign_forbidden_real_service(api_client, session_factory):
    """PJ-H1 over the real service: member lead self-assignment is 403 and the
    database row is untouched; lead/admin reassignment still works."""
    owner = await _register_and_login(api_client, "owner-h1@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-h1")
    created = await _create_project(api_client, owner, ws["id"])
    pid = created["id"]
    member_id = await _invite_accept(api_client, owner, ws["id"], "mem-h1@corp.com")
    member = await _register_and_login(api_client, "mem-h1@corp.com")
    resp = await api_client.post(
        f"/api/v1/projects/{pid}/members",
        json={"member_id": member_id, "role": "member"},
        headers=_auth(owner),
    )
    assert resp.status_code == 201
    resp = await api_client.get(f"/api/v1/projects/{pid}/members", headers=_auth(owner))
    roster = resp.json()["data"]
    owner_member_id = next(entry["member_id"] for entry in roster if entry["role"] == "lead")

    # Lead sets the initial lead — durable in PostgreSQL.
    resp = await api_client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": owner_member_id}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    async with session_factory() as session:
        lead = (
            await session.execute(
                text("SELECT lead_member_id FROM projects WHERE id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert lead == uuid.UUID(owner_member_id)

    # Member self-assignment → 403, and the row stays at the owner's member id.
    resp = await api_client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": member_id}, headers=_auth(member)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    async with session_factory() as session:
        lead_after = (
            await session.execute(
                text("SELECT lead_member_id FROM projects WHERE id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert lead_after == uuid.UUID(owner_member_id)
    # The failed escalation buys no delete power.
    resp = await api_client.delete(f"/api/v1/projects/{pid}", headers=_auth(member))
    assert resp.status_code == 403

    # Lead reassigns to the member — durable; the new lead may clear it.
    resp = await api_client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": member_id}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    async with session_factory() as session:
        lead_moved = (
            await session.execute(
                text("SELECT lead_member_id FROM projects WHERE id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert lead_moved == uuid.UUID(member_id)
    resp = await api_client.patch(
        f"/api/v1/projects/{pid}", json={"lead_member_id": None}, headers=_auth(member)
    )
    assert resp.status_code == 200


# --- T1: cross-tenant isolation ------------------------------------------------


async def test_t1_cross_tenant_api_isolation(api_client):
    owner_a = await _register_and_login(api_client, "owner-t1a@corp.com")
    ws_a = await _create_workspace(api_client, owner_a, "e2e-t1a")
    created = await _create_project(api_client, owner_a, ws_a["id"])
    owner_b = await _register_and_login(api_client, "owner-t1b@corp.com")
    ws_b = await _create_workspace(api_client, owner_b, "e2e-t1b")
    # B's credentials cannot read / write / list A's project — 404 everywhere.
    resp = await api_client.get(f"/api/v1/projects/{created['id']}", headers=_auth(owner_b))
    assert resp.status_code == 404
    resp = await api_client.patch(
        f"/api/v1/projects/{created['id']}", json={"name": "X"}, headers=_auth(owner_b)
    )
    assert resp.status_code == 404
    resp = await api_client.post(
        f"/api/v1/projects/{created['id']}/updates",
        json={"message": "x"},
        headers=_auth(owner_b),
    )
    assert resp.status_code == 404
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws_b['id']}/projects", headers=_auth(owner_b)
    )
    assert resp.json()["data"] == []


async def test_t1_cross_tenant_composite_fk_rejected(session_factory):
    """Composite FKs reject references into another workspace at INSERT."""
    async with session_factory() as session, session.begin():
        ws_a = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('A', :s) RETURNING id"),
                {"s": f"t1-a-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        ws_b = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('B', :s) RETURNING id"),
                {"s": f"t1-b-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        user_a = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'UA') RETURNING id"
                ),
                {"e": f"t1-{uuid.uuid4().hex[:10]}@corp.com"},
            )
        ).scalar_one()
        member_a = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:ws, 'human', :u, 'member') RETURNING id"
                ),
                {"ws": ws_a, "u": user_a},
            )
        ).scalar_one()
        project_b = (
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key) "
                    "VALUES (:ws, 'PB', :k) RETURNING id"
                ),
                {"ws": ws_b, "k": f"T1{uuid.uuid4().hex[:4].upper()}"},
            )
        ).scalar_one()

    # Milestone in ws_b referencing a ws_a project → foreign_key_violation.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO milestones (workspace_id, project_id, title) "
                    "VALUES (:ws_b, :project_b, 'X')"
                ),
                # workspace_id mismatches the project's workspace.
                {"ws_b": ws_a, "project_b": project_b},
            )
    # Project lead from another workspace → foreign_key_violation.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key, lead_member_id) "
                    "VALUES (:ws_b, 'P2', :k, :member_a)"
                ),
                {"ws_b": ws_b, "k": f"T1X{uuid.uuid4().hex[:3].upper()}", "member_a": member_a},
            )
    # Cycle bound to a project in another workspace → foreign_key_violation.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO cycles (workspace_id, project_id, name, starts_at, ends_at) "
                    "VALUES (:ws_a, :project_b, 'C', '2026-08-01', '2026-08-02')"
                ),
                {"ws_a": ws_a, "project_b": project_b},
            )


# --- T18: real DELETE behavior -------------------------------------------------


async def test_t18_delete_semantics_column_level_set_null_and_restrict(session_factory):
    """Real DELETEs — not just table creation (README §9 T18 / §6.2 rule 6)."""
    async with session_factory() as session, session.begin():
        ws = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('D', :s) RETURNING id"),
                {"s": f"t18-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'UD') RETURNING id"
                ),
                {"e": f"t18-{uuid.uuid4().hex[:10]}@corp.com"},
            )
        ).scalar_one()
        member = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:ws, 'human', :u, 'owner') RETURNING id"
                ),
                {"ws": ws, "u": user},
            )
        ).scalar_one()
        project = (
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key, lead_member_id) "
                    "VALUES (:ws, 'PD', 'T18K', :lead) RETURNING id"
                ),
                {"ws": ws, "lead": member},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind, project_id) "
                "VALUES (:ws, 'T18K', 'project', :project)"
            ),
            {"ws": ws, "project": project},
        )
        await session.execute(
            text(
                "INSERT INTO milestones (workspace_id, project_id, title) "
                "VALUES (:ws, :project, 'M')"
            ),
            {"ws": ws, "project": project},
        )
        await session.execute(
            text(
                "INSERT INTO cycles (workspace_id, project_id, name, starts_at, ends_at) "
                "VALUES (:ws, :project, 'C', '2026-08-01', '2026-08-02')"
            ),
            {"ws": ws, "project": project},
        )
        await session.execute(
            text(
                "INSERT INTO project_members (workspace_id, project_id, member_id) "
                "VALUES (:ws, :project, :member)"
            ),
            {"ws": ws, "project": project, "member": member},
        )
        await session.execute(
            text(
                "INSERT INTO member_project_access (workspace_id, member_id, project_id) "
                "VALUES (:ws, :member, :project)"
            ),
            {"ws": ws, "member": member, "project": project},
        )
        await session.execute(
            text(
                "INSERT INTO project_updates (workspace_id, project_id, author_member_id, message) "
                "VALUES (:ws, :project, :member, 'trail')"
            ),
            {"ws": ws, "project": project, "member": member},
        )

    # ① Deleting the member is RESTRICTed by the trail author (members are
    # soft-deleted in practice, so RESTRICT never blocks normal flows).
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM members WHERE id = :member"), {"member": member}
            )

    # ② Physical project delete: column-level SET NULL keeps the registry row
    # (prefix permanently reserved, project_id pointer cleared); owned children
    # cascade. The analogous issues.project_id SET NULL (identifier unchanged)
    # lands with the issue.md increment and is exercised by the spec validation
    # script against the identical DDL (T18-2).
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM projects WHERE id = :project"), {"project": project})
    async with session_factory() as session:
        registry = (
            await session.execute(
                text(
                    "SELECT project_id FROM identifier_prefix_registry "
                    "WHERE workspace_id = :ws AND key = 'T18K'"
                ),
                {"ws": ws},
            )
        ).one_or_none()
        children = (
            await session.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM milestones WHERE project_id = :p) "
                    "+ (SELECT COUNT(*) FROM cycles WHERE project_id = :p) "
                    "+ (SELECT COUNT(*) FROM project_members WHERE project_id = :p) "
                    "+ (SELECT COUNT(*) FROM project_updates WHERE project_id = :p) "
                    "+ (SELECT COUNT(*) FROM member_project_access WHERE project_id = :p)"
                ),
                {"p": project},
            )
        ).scalar_one()
    assert registry is not None  # row survives…
    assert registry.project_id is None  # …with project_id column nulled only
    assert children == 0  # everything owned cascaded


async def test_t18_lead_member_delete_column_level_set_null(session_factory):
    """Deleting a member nulls ONLY lead_member_id; workspace_id stays non-null."""
    async with session_factory() as session, session.begin():
        ws = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('L', :s) RETURNING id"),
                {"s": f"t18l-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'UL') RETURNING id"
                ),
                {"e": f"t18l-{uuid.uuid4().hex[:10]}@corp.com"},
            )
        ).scalar_one()
        member = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:ws, 'human', :u, 'member') RETURNING id"
                ),
                {"ws": ws, "u": user},
            )
        ).scalar_one()
        project = (
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key, lead_member_id) "
                    "VALUES (:ws, 'PL', :k, :lead) RETURNING id"
                ),
                {"ws": ws, "k": f"L18{uuid.uuid4().hex[:3].upper()}", "lead": member},
            )
        ).scalar_one()
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM members WHERE id = :member"), {"member": member})
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT lead_member_id IS NULL AS nulled, "
                    "workspace_id IS NOT NULL AS ws_kept "
                    "FROM projects WHERE id = :project"
                ),
                {"project": project},
            )
        ).one()
    assert row.nulled is True
    assert row.ws_kept is True


# --- T19: prefix registry exclusivity ------------------------------------------


async def test_t19_prefix_registry_exclusivity(api_client, session_factory):
    """Project keys are exclusive against every registered prefix (README §6.3)."""
    owner = await _register_and_login(api_client, "owner-t19@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-t19")
    # The workspace creation registered the inbox prefix 'WS'.
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Clash", "key": "WS"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_key_taken"
    # Rotate the inbox prefix → the old one retires, still exclusive.
    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}",
        json={"settings": {"inbox_issue_prefix": "INB"}},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Retired Clash", "key": "WS"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_key_taken"
    # The new inbox prefix is equally exclusive.
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Inbox Clash", "key": "INB"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    # Soft-deleted project keys can never be re-issued.
    created = await _create_project(api_client, owner, ws["id"], key="IMM")
    resp = await api_client.delete(f"/api/v1/projects/{created['id']}", headers=_auth(owner))
    assert resp.status_code == 200
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Reuse Attempt", "key": "IMM"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_key_taken"
    # Archived project keys are equally unavailable.
    archived = await _create_project(api_client, owner, ws["id"], key="ARC")
    resp = await api_client.post(
        f"/api/v1/projects/{archived['id']}/archive", headers=_auth(owner)
    )
    assert resp.status_code == 200
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/projects",
        json={"name": "Arc Reuse", "key": "ARC"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409


# --- RLS on the app path --------------------------------------------------------


async def test_rls_fail_closed_on_app_role(session_factory, db_url):
    """The app role sees nothing without the tenant GUC (fail-closed RLS)."""
    engine = create_async_engine(_app_role_url(db_url))
    try:
        # Seed as the owner role.
        async with session_factory() as session, session.begin():
            ws = (
                await session.execute(
                    text("INSERT INTO workspaces (name, slug) VALUES ('R', :s) RETURNING id"),
                    {"s": f"rls-{uuid.uuid4().hex[:8]}"},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO projects (workspace_id, name, key) "
                    "VALUES (:ws, 'RLS', :k)"
                ),
                {"ws": ws, "k": f"RL{uuid.uuid4().hex[:4].upper()}"},
            )
        async with engine.connect() as conn:
            # Without the GUC the policy cannot even be evaluated → error.
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT COUNT(*) FROM projects"))
            await conn.rollback()  # the failed statement aborted the transaction
            # With the GUC → exactly the tenant's row.
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, false)"), {"ws": str(ws)}
            )
            count = (
                await conn.execute(text("SELECT COUNT(*) FROM projects"))
            ).scalar_one()
            assert count == 1
    finally:
        await engine.dispose()

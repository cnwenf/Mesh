"""Skill HTTP API tests — in-process app, real PG (skill.md §3, README §6.14).

Exercises every endpoint through the FastAPI ASGI transport so the routes
layer (and the service branches reached from it) are covered by the unit
suite; the SSRF-gated import success path is covered by the real-HTTP e2e.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from mesh.config import load_settings

pytestmark = pytest.mark.unit

PASSWORD = "S3cure-passw0rd!"


@pytest.fixture
def app(db_url, redis_url, attachment_settings_kwargs):
    from mesh.api.app import create_app

    return create_app(load_settings(**attachment_settings_kwargs))


@pytest_asyncio.fixture
async def client(app, object_storage):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Skill Tester"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


async def _workspace(client, token: str, slug: str) -> str:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Skill WS", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _seed_awaiting_review(app, workspace_id: uuid.UUID, member_id: uuid.UUID) -> dict:
    """Seed a skill + draft version + awaiting-review import task via the ORM."""
    from mesh.db.models.skill import (
        Skill,
        SkillImportTask,
        SkillScript,
        SkillSource,
        SkillVersion,
    )

    async with app.state.session_factory() as session, session.begin():
        source = SkillSource(
            workspace_id=workspace_id, source_type="url", name="u",
            uri="https://reg.example.com/x.json", trust_level="untrusted",
        )
        session.add(source)
        await session.flush()
        skill = Skill(
            workspace_id=workspace_id, source_id=source.id, name="Imp",
            slug=f"imp-{uuid.uuid4().hex[:8]}", summary="s", status="draft",
            required_capabilities=["exec:shell", "net:outbound"], created_by=member_id,
        )
        session.add(skill)
        await session.flush()
        version = SkillVersion(
            workspace_id=workspace_id, skill_id=skill.id, version="1.0.0",
            instructions="do", status="draft",
            required_capabilities=["exec:shell", "net:outbound"],
            content_hash="a" * 64, created_by=member_id,
        )
        session.add(version)
        await session.flush()
        session.add(
            SkillScript(
                skill_version_id=version.id, path="s.sh", runtime="shell",
                content_ref="mem:s.sh", content_hash="b" * 64,
            )
        )
        task = SkillImportTask(
            workspace_id=workspace_id, created_by=member_id, source_type="url",
            status="awaiting_review", requires_approval=True,
            skill_id=skill.id, skill_version_id=version.id,
        )
        session.add(task)
        await session.flush()
        return {"skill_id": skill.id, "version_id": version.id, "task_id": task.id}


async def _member_id(app, workspace_id: uuid.UUID, token: str) -> uuid.UUID:
    from mesh.auth.deps import get_current_user  # noqa: F401
    from mesh.db.models.member import Member

    # Resolve the caller's member row from the workspace.
    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                Member.__table__.select().where(Member.workspace_id == workspace_id)
            )
        ).all()
    return rows[0].id


# --- CRUD ------------------------------------------------------------------------


async def test_skill_crud_and_lifecycle(client, app) -> None:
    token = await _register_login(client, f"o-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, token, f"sk-{uuid.uuid4().hex[:8]}")

    created = await client.post(
        f"/api/v1/workspaces/{ws}/skills",
        json={"name": "代码评审规范", "summary": "SOP", "slug": "code-review-sop",
              "tags": ["review"], "required_capabilities": ["read:code"]},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    skill_id = created.json()["data"]["id"]
    assert created.json()["data"]["source_type"] == "user"

    listed = await client.get(
        f"/api/v1/workspaces/{ws}/skills", params={"status": "draft", "q": "评审"},
        headers=_auth(token),
    )
    assert listed.status_code == 200
    assert any(s["id"] == skill_id for s in listed.json()["data"])

    got = await client.get(f"/api/v1/workspaces/{ws}/skills/{skill_id}", headers=_auth(token))
    assert got.json()["data"]["slug"] == "code-review-sop"

    # invalid status value → 400
    bad = await client.patch(
        f"/api/v1/workspaces/{ws}/skills/{skill_id}",
        json={"status": "exploded"}, headers=_auth(token),
    )
    assert bad.status_code == 400

    # delete a draft → 423 locked
    locked = await client.delete(
        f"/api/v1/workspaces/{ws}/skills/{skill_id}", headers=_auth(token)
    )
    assert locked.status_code == 423


async def test_version_create_publish_and_conflict(client) -> None:
    token = await _register_login(client, f"v-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, token, f"v-{uuid.uuid4().hex[:8]}")
    skill = (await client.post(
        f"/api/v1/workspaces/{ws}/skills", json={"name": "N", "summary": "S"},
        headers=_auth(token),
    )).json()["data"]["id"]

    ver = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
        json={"version": "1.0.0", "instructions": "do it",
              "references": [{"path": "r.md", "content": "# r"}],
              "triggers": [{"trigger_type": "keyword", "pattern": "deploy", "weight": 2}],
              "required_capabilities": ["read:code"], "publish": True},
        headers=_auth(token),
    )
    assert ver.status_code == 201, ver.text
    version_id = ver.json()["data"]["id"]

    # duplicate version number → 409 version_conflict
    dup = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
        json={"version": "1.0.0", "instructions": "again"}, headers=_auth(token),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "version_conflict"

    # manifest invalid → 422
    bad = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
        json={"version": "2.0.0", "instructions": "x",
              "scripts": [{"path": "a.sh", "runtime": "cobol"}]},
        headers=_auth(token),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "manifest_invalid"

    # list + get (with content)
    page = await client.get(f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
                            headers=_auth(token))
    assert page.json()["data"][0]["is_current"] is True
    detail = await client.get(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions/{version_id}",
        params={"include_content": "true"}, headers=_auth(token),
    )
    assert detail.json()["data"]["references"][0]["path"] == "r.md"
    assert detail.json()["data"]["triggers"][0]["pattern"] == "deploy"

    # not-found version path id
    nf = await client.get(f"/api/v1/workspaces/{ws}/skills/{skill}/versions/{uuid.uuid4()}",
                          headers=_auth(token))
    assert nf.status_code == 404


async def test_install_bind_unbind_rollback(client) -> None:
    token = await _register_login(client, f"i-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, token, f"i-{uuid.uuid4().hex[:8]}")
    skill = (await client.post(
        f"/api/v1/workspaces/{ws}/skills", json={"name": "N", "summary": "S"},
        headers=_auth(token),
    )).json()["data"]["id"]
    version_id = (await client.post(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
        json={"version": "1.0.0", "instructions": "do", "publish": True},
        headers=_auth(token),
    )).json()["data"]["id"]
    version2_id = (await client.post(
        f"/api/v1/workspaces/{ws}/skills/{skill}/versions",
        json={"version": "1.1.0", "instructions": "do more", "publish": True},
        headers=_auth(token),
    )).json()["data"]["id"]

    # agent scope without agent_id → 400
    no_agent = await client.post(
        f"/api/v1/workspaces/{ws}/skill-installations",
        json={"skill_id": skill, "skill_version_id": version_id, "scope": "agent"},
        headers=_auth(token),
    )
    assert no_agent.status_code == 400

    installed = await client.post(
        f"/api/v1/workspaces/{ws}/skill-installations",
        json={"skill_id": skill, "skill_version_id": version_id, "scope": "workspace"},
        headers=_auth(token),
    )
    assert installed.status_code == 201, installed.text
    installation_id = installed.json()["data"]["id"]

    # duplicate scope → 409
    dup = await client.post(
        f"/api/v1/workspaces/{ws}/skill-installations",
        json={"skill_id": skill, "skill_version_id": version_id, "scope": "workspace"},
        headers=_auth(token),
    )
    assert dup.status_code == 409

    # list filtered by skill
    listing = await client.get(
        f"/api/v1/workspaces/{ws}/skill-installations", params={"skill_id": skill},
        headers=_auth(token),
    )
    assert len(listing.json()["data"]) == 1

    # patch → disable
    disabled = await client.patch(
        f"/api/v1/workspaces/{ws}/skill-installations/{installation_id}",
        json={"install_status": "disabled"}, headers=_auth(token),
    )
    assert disabled.json()["data"]["install_status"] == "disabled"

    # upgrade to v2 then rollback to v1
    upgraded = await client.patch(
        f"/api/v1/workspaces/{ws}/skill-installations/{installation_id}",
        json={"skill_version_id": version2_id}, headers=_auth(token),
    )
    assert upgraded.json()["data"]["skill_version_id"] == version2_id
    rolled = await client.post(
        f"/api/v1/workspaces/{ws}/skill-installations/{installation_id}/rollback",
        json={"target_version_id": version_id, "reason": "regression"},
        headers=_auth(token),
    )
    assert rolled.json()["data"]["skill_version_id"] == version_id

    # bind to an agent
    agent_id = (await client.post(
        f"/api/v1/workspaces/{ws}/agents",
        json={"name": "Bot", "system_instructions": "x"}, headers=_auth(token),
    )).json()["data"]["id"]
    bound = await client.post(
        f"/api/v1/workspaces/{ws}/agents/{agent_id}/skills",
        json={"skill_installation_id": installation_id, "priority": 200},
        headers=_auth(token),
    )
    assert bound.status_code == 201, bound.text
    binding_id = bound.json()["data"]["id"]

    rows = await client.get(
        f"/api/v1/workspaces/{ws}/agents/{agent_id}/skills", headers=_auth(token)
    )
    assert rows.json()["data"][0]["priority"] == 200

    patched = await client.patch(
        f"/api/v1/workspaces/{ws}/agents/{agent_id}/skills/{binding_id}",
        json={"enabled": False, "auto_trigger": False}, headers=_auth(token),
    )
    assert patched.json()["data"]["enabled"] is False

    # invalid priority → 400
    badp = await client.patch(
        f"/api/v1/workspaces/{ws}/agents/{agent_id}/skills/{binding_id}",
        json={"priority": 9999}, headers=_auth(token),
    )
    assert badp.status_code == 400

    assert (await client.delete(
        f"/api/v1/workspaces/{ws}/agents/{agent_id}/skills/{binding_id}",
        headers=_auth(token),
    )).status_code == 204
    assert (await client.delete(
        f"/api/v1/workspaces/{ws}/skill-installations/{installation_id}",
        headers=_auth(token),
    )).status_code == 204


async def test_import_ssrf_and_validation(client) -> None:
    token = await _register_login(client, f"s-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, token, f"s-{uuid.uuid4().hex[:8]}")

    # SSRF: cloud metadata → 502 source_unreachable
    ssrf = await client.post(
        f"/api/v1/workspaces/{ws}/skills/import",
        json={"source_type": "url", "uri": "http://169.254.169.254/latest/m.json"},
        headers=_auth(token),
    )
    assert ssrf.status_code == 502
    assert ssrf.json()["error"]["code"] == "source_unreachable"

    # invalid source_type → 400
    bad = await client.post(
        f"/api/v1/workspaces/{ws}/skills/import",
        json={"source_type": "builtin", "uri": "https://x/m.json"}, headers=_auth(token),
    )
    assert bad.status_code == 400

    # unknown task id → 404
    nf = await client.get(f"/api/v1/workspaces/{ws}/skills/import/{uuid.uuid4()}",
                          headers=_auth(token))
    assert nf.status_code == 404


async def test_approve_paths(client, app) -> None:
    token = await _register_login(client, f"a-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, token, f"a-{uuid.uuid4().hex[:8]}")
    seed = await _seed_awaiting_review(app, uuid.UUID(ws), await _member_id(app, uuid.UUID(ws), token))

    # over-grant → 422 capability_not_declared
    over = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{seed['skill_id']}/approve",
        json={"task_id": str(seed["task_id"]),
              "granted_capabilities": ["exec:shell", "root:all"], "decision": "approve"},
        headers=_auth(token),
    )
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "capability_not_declared"

    # approve with a subset → 200 §3.2 approval-result shape (status published)
    ok = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{seed['skill_id']}/approve",
        json={"task_id": str(seed["task_id"]),
              "granted_capabilities": ["exec:shell"], "decision": "approve"},
        headers=_auth(token),
    )
    assert ok.status_code == 200, ok.text
    ok_data = ok.json()["data"]
    assert ok_data["status"] == "published"
    assert ok_data["skill_id"] == str(seed["skill_id"])
    assert ok_data["skill_version_id"] == str(seed["version_id"])
    assert ok_data["granted_capabilities"] == ["exec:shell"]
    assert ok_data["reviewed_by"] is not None
    assert ok_data["reviewed_at"] is not None
    # §3.2 shape: no import-task leakage fields
    assert "task_id" not in ok_data and "percent" not in ok_data and "preview" not in ok_data

    # approve again → 409 (no longer awaiting review)
    again = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{seed['skill_id']}/approve",
        json={"task_id": str(seed["task_id"]), "granted_capabilities": [],
              "decision": "approve"},
        headers=_auth(token),
    )
    assert again.status_code == 409

    # HIGH-2: malformed grant shape → 422 capability_invalid (not persisted)
    bad_shape = await _seed_awaiting_review(app, uuid.UUID(ws), await _member_id(app, uuid.UUID(ws), token))
    mal = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{bad_shape['skill_id']}/approve",
        json={"task_id": str(bad_shape["task_id"]),
              "granted_capabilities": [{"capability": "exec:shell", "permission": "bogus"}],
              "decision": "approve"},
        headers=_auth(token),
    )
    assert mal.status_code == 422
    assert mal.json()["error"]["code"] == "capability_invalid"

    # HIGH-3: permission ESCALATION (declare read_only-ish, grant write) → 422
    esc = await _seed_awaiting_review(app, uuid.UUID(ws), await _member_id(app, uuid.UUID(ws), token))
    # re-declare the version's required caps as read_only for exec:shell via a
    # fresh seed is fixed at ["exec:shell","net:outbound"] (bare=confirm_required);
    # granting write on exec:shell exceeds confirm_required → escalation.
    escal = await client.post(
        f"/api/v1/workspaces/{ws}/skills/{esc['skill_id']}/approve",
        json={"task_id": str(esc["task_id"]),
              "granted_capabilities": [{"capability": "exec:shell", "permission": "write"}],
              "decision": "approve"},
        headers=_auth(token),
    )
    assert escal.status_code == 422
    assert escal.json()["error"]["code"] == "capability_not_declared"
    assert escal.json()["error"]["details"].get("escalated")


async def test_marketplace_empty_and_member_forbidden(client) -> None:
    owner = await _register_login(client, f"mo-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    ws = await _workspace(client, owner, f"mo-{uuid.uuid4().hex[:8]}")

    empty = await client.get(f"/api/v1/workspaces/{ws}/marketplace/skills",
                             headers=_auth(owner))
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    # invite a member and assert 403 on write
    inv = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"emails": [f"mm-{uuid.uuid4().hex[:8]}@mesh-sk.com"], "role": "member"},
        headers=_auth(owner),
    )
    token_value = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    member = await _register_login(client, f"mm-{uuid.uuid4().hex[:8]}@mesh-sk.com")
    await client.post("/api/v1/invitations/accept", json={"token": token_value},
                      headers=_auth(member))
    forbidden = await client.post(
        f"/api/v1/workspaces/{ws}/skills", json={"name": "N", "summary": "S"},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403
    # member can still list
    assert (await client.get(f"/api/v1/workspaces/{ws}/skills",
                             headers=_auth(member))).status_code == 200

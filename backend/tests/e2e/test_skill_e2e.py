"""Skill REST API e2e — REAL server + REAL API calls + REAL DB + REAL HTTP fetch.

Covers skill.md over the wire: the four-layer lifecycle (definition →
immutable version → installation → binding), the SSRF-guarded import
pipeline against a genuine local HTTP source server (manifest + script +
reference bodies actually fetched), the §5.3 approval gate (422
approval_required before review, minimized grants), update_available /
rollback, and the cross-tenant 404. Nothing on the contract path is mocked.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


# --- real local source server (the import pipeline really fetches these) ------

SCRIPT_V1 = b"#!/bin/sh\necho release-check-v1\n"
SCRIPT_V101 = b"#!/bin/sh\necho release-check-v101-changed\n"
RUNBOOK = b"# Runbook\nstep by step\n"


def _manifest(version: str, script_body_marker: str) -> dict:
    return {
        "name": "发布检查清单",
        "version": version,
        "summary": "发布前的标准检查流程",
        "instructions": "## 发布前检查\n1. 运行回归测试\n2. 核对变更日志",
        "scripts": [
            {
                "path": "scripts/check.sh",
                "runtime": "shell",
                "entrypoint": True,
                "required_capabilities": ["exec:shell", "net:outbound"],
            }
        ],
        "references": [{"path": "docs/runbook.md", "media_type": "text/markdown"}],
        "triggers": [{"trigger_type": "keyword", "pattern": "发布 release", "weight": 1.5}],
        "tags": ["release"],
        "required_capabilities": ["exec:shell", "net:outbound"],
        "changelog": f"release {version} ({script_body_marker})",
    }


DOCS_ONLY_MANIFEST = {
    "name": "接口文档规范",
    "version": "2.0.0",
    "summary": "API 文档写作规范",
    "instructions": "## 文档规范\n所有接口必须有示例。",
    "tags": ["docs"],
    "required_capabilities": [],
}

# Mutable "live" source content — tests swap the served bytes in place to
# simulate an upstream repository publishing a new version at the SAME URI.
LIVE_STATE: dict[str, bytes] = {
    "manifest": json.dumps(_manifest("1.3.0", "v1")).encode(),
    "script": SCRIPT_V1,
}


class _SourceHandler(BaseHTTPRequestHandler):
    """Serves the fixture skill source; suffix-matched like a static root.

    ``/skills/live/*`` models a version-control source whose CONTENT changes
    in place between re-imports (same URI → same source → same skill, new
    version) — the real-world update-detection shape (§4.4).
    """

    def log_message(self, *_args: object) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server contract
        routes = {
            "/skills/release-checklist/manifest.json": json.dumps(
                _manifest("1.3.0", "v1")
            ).encode(),
            "/skills/release-checklist/scripts/check.sh": SCRIPT_V1,
            "/skills/release-checklist/docs/runbook.md": RUNBOOK,
            "/skills/live/manifest.json": LIVE_STATE["manifest"],
            "/skills/live/scripts/check.sh": LIVE_STATE["script"],
            "/skills/live/docs/runbook.md": RUNBOOK,
            "/skills/docs-only/manifest.json": json.dumps(DOCS_ONLY_MANIFEST).encode(),
        }
        body = routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def source_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# --- helpers ---------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str, name: str = "E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Skill E2E", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_agent(client, token: str, ws_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={"name": "发布助手", "system_instructions": "你是发布助手。"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# --- the four-layer lifecycle -------------------------------------------------------


async def test_skill_full_lifecycle_create_install_bind(api_client, session_factory) -> None:
    token = await _register_and_login(api_client, f"owner-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"sk-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    # 1. definition (draft)
    created = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills",
        json={"name": "代码评审规范", "summary": "评审 SOP", "slug": "code-review-sop",
              "tags": ["review"], "required_capabilities": ["read:code"]},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    skill = created.json()["data"]
    assert skill["status"] == "draft"
    assert skill["source_type"] == "user"
    assert skill["trust_level"] == "reviewed"

    # slug collision → 409 conflict at the service layer
    dup = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills",
        json={"name": "x", "summary": "y", "slug": "code-review-sop"},
        headers=_auth(token),
    )
    # slugify collision is auto-suffixed, but explicit duplicate slugs 409
    assert dup.status_code in (201, 409), dup.text

    # 2. immutable version + publish
    version_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/{skill['id']}/versions",
        json={
            "version": "1.0.0",
            "instructions": "## 评审\n1. 安全\n2. 质量",
            "scripts": [
                {"path": "scripts/lint.sh", "runtime": "shell", "entrypoint": True,
                 "required_capabilities": ["exec:shell"], "content": "#!/bin/sh\necho lint"}
            ],
            "references": [{"path": "docs/checklist.md", "content": "# checklist"}],
            "triggers": [{"trigger_type": "keyword", "pattern": "评审", "weight": 2}],
            "required_capabilities": ["read:code", "write:comment"],
            "publish": True,
        },
        headers=_auth(token),
    )
    assert version_resp.status_code == 201, version_resp.text
    version = version_resp.json()["data"]
    assert version["status"] == "published"

    # duplicate version number → 409 version_conflict
    dup_version = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/{skill['id']}/versions",
        json={"version": "1.0.0", "instructions": "again"},
        headers=_auth(token),
    )
    assert dup_version.status_code == 409
    assert dup_version.json()["error"]["code"] == "version_conflict"

    # skill is now published with the current pointer (same-parent FK)
    detail = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/skills/{skill['id']}", headers=_auth(token)
    )
    assert detail.json()["data"]["current_version_id"] == version["id"]
    assert detail.json()["data"]["has_scripts"] is True

    # version detail carries scripts/references/triggers + content
    v_detail = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/skills/{skill['id']}/versions/{version['id']}"
        "?include_content=true",
        headers=_auth(token),
    )
    v_body = v_detail.json()["data"]
    assert v_body["scripts"][0]["content"] == "#!/bin/sh\necho lint"
    assert v_body["triggers"][0]["pattern"] == "评审"

    # 3. installation (reviewed source → full declared grants)
    inst_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": skill["id"], "skill_version_id": version["id"],
              "scope": "workspace"},
        headers=_auth(token),
    )
    assert inst_resp.status_code == 201, inst_resp.text
    installation = inst_resp.json()["data"]
    assert set(installation["granted_capabilities"]) == {"read:code", "write:comment"}

    # agent-scope without agent_id → 400
    no_agent = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": skill["id"], "skill_version_id": version["id"], "scope": "agent"},
        headers=_auth(token),
    )
    assert no_agent.status_code == 400

    # duplicate scope → 409 conflict (DB partial unique index surfaced)
    dup_inst = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": skill["id"], "skill_version_id": version["id"],
              "scope": "workspace"},
        headers=_auth(token),
    )
    assert dup_inst.status_code == 409
    assert dup_inst.json()["error"]["code"] == "conflict"

    # 4. agent binding
    agent = await _create_agent(api_client, token, ws_id)
    bind_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents/{agent['id']}/skills",
        json={"skill_installation_id": installation["id"], "priority": 120},
        headers=_auth(token),
    )
    assert bind_resp.status_code == 201, bind_resp.text
    binding = bind_resp.json()["data"]
    assert binding["skill_version_id"] == version["id"]

    # duplicate binding → 409
    dup_bind = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents/{agent['id']}/skills",
        json={"skill_installation_id": installation["id"]},
        headers=_auth(token),
    )
    assert dup_bind.status_code == 409

    listed = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/agents/{agent['id']}/skills", headers=_auth(token)
    )
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["skill"]["name"] == "代码评审规范"
    assert rows[0]["priority"] == 120

    # §6.11 rows exist in the real database
    async with session_factory() as session:
        db_rows = (
            await session.execute(text("SELECT skill_id, skill_version_id FROM agent_skills"))
        ).all()
    assert len(db_rows) == 1

    # unbind + uninstall
    unbind = await api_client.delete(
        f"/api/v1/workspaces/{ws_id}/agents/{agent['id']}/skills/{binding['id']}",
        headers=_auth(token),
    )
    assert unbind.status_code == 204
    uninstall = await api_client.delete(
        f"/api/v1/workspaces/{ws_id}/skill-installations/{installation['id']}",
        headers=_auth(token),
    )
    assert uninstall.status_code == 204


# --- import pipeline with REAL HTTP fetch -------------------------------------------


async def test_import_approval_gate_and_install(
    api_client, session_factory, source_server
) -> None:
    token = await _register_and_login(api_client, f"imp-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"im-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    # SSRF: cloud metadata endpoint refused with the neutral 502
    ssrf = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url", "uri": "http://169.254.169.254/latest/meta-data/m.json"},
        headers=_auth(token),
    )
    assert ssrf.status_code == 502
    assert ssrf.json()["error"]["code"] == "source_unreachable"

    # real fetch: manifest + script + reference bodies from the local server
    started = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url",
              "uri": f"{source_server}/skills/release-checklist/manifest.json"},
        headers=_auth(token),
    )
    assert started.status_code == 202, started.text
    task = started.json()["data"]
    assert task["status"] == "awaiting_review"
    assert task["requires_approval"] is True
    preview = task["preview"]
    assert preview["scripts"][0]["path"] == "scripts/check.sh"
    assert set(preview["requested_capabilities"]) == {"exec:shell", "net:outbound"}

    skill_id = task["skill_id"]
    version_id = task["skill_version_id"]

    # poll endpoint sees the same state (documented fallback, §3.5)
    polled = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/skills/import/{task['task_id']}", headers=_auth(token)
    )
    assert polled.json()["data"]["status"] == "awaiting_review"

    # install BEFORE approval → 422 approval_required (the specific gate)
    premature = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": skill_id, "skill_version_id": version_id, "scope": "workspace"},
        headers=_auth(token),
    )
    assert premature.status_code == 422
    assert premature.json()["error"]["code"] == "approval_required"

    # granting an undeclared capability → 422 capability_not_declared
    overgrant = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/{skill_id}/approve",
        json={"task_id": task["task_id"],
              "granted_capabilities": ["exec:shell", "root:everything"],
              "decision": "approve"},
        headers=_auth(token),
    )
    assert overgrant.status_code == 422
    assert overgrant.json()["error"]["code"] == "capability_not_declared"

    # approve with a MINIMIZED subset (net:outbound refused)
    approved = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/{skill_id}/approve",
        json={"task_id": task["task_id"], "granted_capabilities": ["exec:shell"],
              "decision": "approve", "comment": "拒绝出站网络"},
        headers=_auth(token),
    )
    assert approved.status_code == 200, approved.text
    # §3.2 approval-result shape (M1)
    approved_data = approved.json()["data"]
    assert approved_data["status"] == "published"
    assert approved_data["granted_capabilities"] == ["exec:shell"]
    assert approved_data["reviewed_by"] is not None

    # skill + version are now published (asserted in PostgreSQL)
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT status, current_version_id FROM skills WHERE id = :i"),
                {"i": skill_id},
            )
        ).one()
        version_status = (
            await session.execute(
                text("SELECT status FROM skill_versions WHERE id = :v"), {"v": version_id}
            )
        ).scalar()
    assert row.status == "published"
    assert str(row.current_version_id) == version_id
    assert version_status == "published"

    # install carries the approved grants only
    installed = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": skill_id, "skill_version_id": version_id, "scope": "workspace"},
        headers=_auth(token),
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["data"]["granted_capabilities"] == ["exec:shell"]


async def test_import_script_change_requires_reapproval(
    api_client, source_server
) -> None:
    """§4.4 anti-bypass: ANY script change re-enters human review on upgrade."""
    token = await _register_and_login(api_client, f"re-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"re-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    # The live source starts at 1.3.0; reset in case another test swapped it.
    LIVE_STATE["manifest"] = json.dumps(_manifest("1.3.0", "v1")).encode()
    LIVE_STATE["script"] = SCRIPT_V1
    live_uri = f"{source_server}/skills/live/manifest.json"

    first = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url", "uri": live_uri},
        headers=_auth(token),
    )
    task1 = first.json()["data"]
    await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/{task1['skill_id']}/approve",
        json={"task_id": task1["task_id"], "granted_capabilities": ["exec:shell"],
              "decision": "approve"},
        headers=_auth(token),
    )
    installed = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": task1["skill_id"],
              "skill_version_id": task1["skill_version_id"], "scope": "workspace",
              "auto_update": True},
        headers=_auth(token),
    )
    installation_id = installed.json()["data"]["id"]

    # Upstream publishes 1.3.1 at the SAME URI with a changed script body.
    LIVE_STATE["manifest"] = json.dumps(_manifest("1.3.1", "v101")).encode()
    LIVE_STATE["script"] = SCRIPT_V101
    second = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url", "uri": live_uri},
        headers=_auth(token),
    )
    task2 = second.json()["data"]
    assert task2["status"] == "awaiting_review"  # script hash changed → review again
    assert task2["skill_id"] == task1["skill_id"]  # same source → same skill

    # auto_update must NOT have followed (script changed) → updated_available
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        params={"skill_id": task1["skill_id"]},
        headers=_auth(token),
    )
    assert listing.json()["data"][0]["install_status"] == "updated_available"

    # explicit switch to the unapproved new version → 422 approval_required
    switch = await api_client.patch(
        f"/api/v1/workspaces/{ws_id}/skill-installations/{installation_id}",
        json={"skill_version_id": task2["skill_version_id"]},
        headers=_auth(token),
    )
    assert switch.status_code == 422
    assert switch.json()["error"]["code"] == "approval_required"

    # rollback to the first version works anytime (history never deleted)
    rollback = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations/{installation_id}/rollback",
        json={"target_version_id": task1["skill_version_id"], "reason": "stay on 1.3.0"},
        headers=_auth(token),
    )
    assert rollback.status_code == 200
    assert rollback.json()["data"]["skill_version_id"] == task1["skill_version_id"]


async def test_instructions_only_import_needs_no_approval(api_client, source_server) -> None:
    token = await _register_and_login(api_client, f"doc-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"do-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    started = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url", "uri": f"{source_server}/skills/docs-only/manifest.json"},
        headers=_auth(token),
    )
    assert started.status_code == 202, started.text
    task = started.json()["data"]
    assert task["status"] == "ready"  # no scripts → straight through
    assert task["requires_approval"] is False

    installed = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations",
        json={"skill_id": task["skill_id"], "skill_version_id": task["skill_version_id"],
              "scope": "workspace"},
        headers=_auth(token),
    )
    assert installed.status_code == 201


async def test_import_bad_source_fails_task(api_client, source_server) -> None:
    token = await _register_and_login(api_client, f"bad-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"ba-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    started = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills/import",
        json={"source_type": "url", "uri": f"{source_server}/does-not-exist.json"},
        headers=_auth(token),
    )
    assert started.status_code == 202
    task = started.json()["data"]
    assert task["status"] == "failed"
    assert task["error"] is not None


# --- authz & tenant isolation ---------------------------------------------------------


async def test_member_cannot_manage_skills(api_client) -> None:
    owner = await _register_and_login(api_client, f"own-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, owner, f"rb-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    # invite a plain member
    member_email = f"mem-{uuid.uuid4().hex[:8]}@mesh-e2e.com"
    inv = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [member_email], "role": "member"},
        headers=_auth(owner),
    )
    token_value = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    member = await _register_and_login(api_client, member_email)
    accepted = await api_client.post(
        "/api/v1/invitations/accept", json={"token": token_value}, headers=_auth(member)
    )
    assert accepted.status_code == 200, accepted.text

    # reads are fine
    listing = await api_client.get(f"/api/v1/workspaces/{ws_id}/skills", headers=_auth(member))
    assert listing.status_code == 200

    # writes → 403
    forbidden = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skills",
        json={"name": "N", "summary": "S"},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403


async def test_cross_workspace_skill_invisible(api_client) -> None:
    token_a = await _register_and_login(api_client, f"wA-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws_a = await _create_workspace(api_client, token_a, f"wa-{uuid.uuid4().hex[:10]}")
    ws_a_id = ws_a["workspace_id"] if "workspace_id" in ws_a else ws_a["id"]
    created = await api_client.post(
        f"/api/v1/workspaces/{ws_a_id}/skills",
        json={"name": "A secret SOP", "summary": "s"},
        headers=_auth(token_a),
    )
    skill_id = created.json()["data"]["id"]

    token_b = await _register_and_login(api_client, f"wB-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws_b = await _create_workspace(api_client, token_b, f"wb-{uuid.uuid4().hex[:10]}")
    ws_b_id = ws_b["workspace_id"] if "workspace_id" in ws_b else ws_b["id"]

    # workspace B cannot read or install workspace A's skill
    get_b = await api_client.get(
        f"/api/v1/workspaces/{ws_b_id}/skills/{skill_id}", headers=_auth(token_b)
    )
    assert get_b.status_code == 404
    list_b = await api_client.get(f"/api/v1/workspaces/{ws_b_id}/skills", headers=_auth(token_b))
    assert all(item["id"] != skill_id for item in list_b.json()["data"])


async def test_marketplace_unconfigured_is_empty(api_client) -> None:
    token = await _register_and_login(api_client, f"mk-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"mk-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/marketplace/skills", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_import_redirect_into_non_allowlisted_loopback_refused(api_client) -> None:
    """CRITICAL-1 (real sockets): a validated allowlisted URL that 302-bounces
    into a NON-allowlisted loopback host must be refused at the redirect hop,
    and the secret body on the target must never be fetched.

    The e2e allowlist (conftest) contains 127.0.0.1 but NOT 127.0.0.2, so a
    302 from 127.0.0.1 → http://127.0.0.2:<port>/secret is caught by the
    per-hop re-validation. With the historical urllib auto-follow this body
    would have been returned (the verifier's 5-line reproduction).
    """
    secret = b"INTERNAL_SECRET_REACHED"
    target_hits: list[str] = []

    class _TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            target_hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(secret)))
            self.end_headers()
            self.wfile.write(secret)

    target = ThreadingHTTPServer(("127.0.0.2", 0), _TargetHandler)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    target_port = target.server_address[1]

    class _BounceHandler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.2:{target_port}/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()

    bounce = ThreadingHTTPServer(("127.0.0.1", 0), _BounceHandler)
    threading.Thread(target=bounce.serve_forever, daemon=True).start()
    bounce_port = bounce.server_address[1]

    try:
        token = await _register_and_login(api_client, f"rd-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
        ws = await _create_workspace(api_client, token, f"rd-{uuid.uuid4().hex[:10]}")
        ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

        started = await api_client.post(
            f"/api/v1/workspaces/{ws_id}/skills/import",
            json={"source_type": "url", "uri": f"http://127.0.0.1:{bounce_port}/manifest.json"},
            headers=_auth(token),
        )
        assert started.status_code == 202, started.text
        task = started.json()["data"]
        assert task["status"] == "failed"
        assert task["error"] is not None
        # The secret server was NEVER contacted — the redirect was blocked
        # before any connect to the non-allowlisted loopback target.
        assert target_hits == []
    finally:
        target.shutdown()
        target.server_close()
        bounce.shutdown()
        bounce.server_close()


async def test_rollback_changes_installation_pointer_via_real_api(
    api_client, session_factory
) -> None:
    """CRITICAL-3 regression: the real rollback endpoint moves the installation
    pointer to a historic version (asserted in PostgreSQL). Seeded via DB so the
    test needs no object storage (scripts are not required for rollback)."""
    token = await _register_and_login(api_client, f"rb-{uuid.uuid4().hex[:8]}@mesh-e2e.com")
    ws = await _create_workspace(api_client, token, f"rb-{uuid.uuid4().hex[:10]}")
    ws_id = ws["workspace_id"] if "workspace_id" in ws else ws["id"]

    skill_id = uuid.uuid4()
    v1_id = uuid.uuid4()
    v2_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        src_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO skill_sources (id, workspace_id, source_type, name, trust_level) "
                "VALUES (:s, :w, 'user', 'u', 'reviewed')"
            ),
            {"s": src_id, "w": ws_id},
        )
        member_id = (await session.execute(
            text("SELECT id FROM members WHERE workspace_id = :w LIMIT 1"), {"w": ws_id}
        )).scalar()
        # skill first with NULL current_version_id (versions don't exist yet);
        # the overlapping composite FK forbids pointing at a not-yet-inserted
        # version, so we set the pointer AFTER inserting the versions.
        await session.execute(
            text(
                "INSERT INTO skills (id, workspace_id, source_id, name, slug, summary, "
                "status, created_by) VALUES "
                "(:sk, :w, :s, 'RB', :slug, 's', 'published', :m)"
            ),
            {"sk": skill_id, "w": ws_id, "s": src_id, "slug": f"rb-{uuid.uuid4().hex[:8]}",
             "m": member_id},
        )
        for vid, ver in ((v1_id, "1.0.0"), (v2_id, "1.1.0")):
            await session.execute(
                text(
                    "INSERT INTO skill_versions (id, workspace_id, skill_id, version, "
                    "instructions, status, content_hash, created_by) VALUES "
                    "(:v, :w, :sk, :ver, 'i', 'published', :h, :m)"
                ),
                {"v": vid, "w": ws_id, "sk": skill_id, "ver": ver, "h": uuid.uuid4().hex * 2,
                 "m": member_id},
            )
        await session.execute(
            text("UPDATE skills SET current_version_id = :v2 WHERE id = :sk"),
            {"v2": v2_id, "sk": skill_id},
        )
        await session.execute(
            text(
                "INSERT INTO skill_installations (id, workspace_id, skill_id, "
                "skill_version_id, scope, install_status, installed_by) VALUES "
                "(:i, :w, :sk, :v2, 'workspace', 'installed', :m)"
            ),
            {"i": inst_id, "w": ws_id, "sk": skill_id, "v2": v2_id, "m": member_id},
        )

    # real API rollback to v1
    rb = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/skill-installations/{inst_id}/rollback",
        json={"target_version_id": str(v1_id), "reason": "regression"},
        headers=_auth(token),
    )
    assert rb.status_code == 200, rb.text
    assert rb.json()["data"]["skill_version_id"] == str(v1_id)
    assert rb.json()["data"]["previous_version_id"] == str(v2_id)

    # asserted in PostgreSQL: the pointer moved.
    async with session_factory() as session:
        row = (await session.execute(
            text("SELECT skill_version_id FROM skill_installations WHERE id = :i"),
            {"i": inst_id},
        )).one()
    assert str(row.skill_version_id) == str(v1_id)

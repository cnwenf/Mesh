"""In-process (ASGI) coverage for the search endpoint (search-command-palette.md §5.1).

Drives the real create_app over ASGI so every route/service branch counts
toward coverage: six object types, identifier fast path, cursor security,
visibility negatives (private project/agent, chat isolation, guest),
highlight code points, rename liveness, validation, rate limiting, and the
no-query-in-logs contract.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.unit


def _settings(db_url: str, redis_url: str) -> dict:
    return {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "search-routes-signing-secret-000000000000",
        "search_cursor_secret": "search-cursor-unit-secret",
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-search-routes-test",
    }


@pytest_asyncio.fixture
async def client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings(db_url, redis_url)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_login(client, name: str, display: str | None = None) -> tuple[str, str]:
    email = f"{name}-{uuid.uuid4().hex[:6]}@search.mesh"
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Search-Routes-123",
            "display_name": display or name,
        },
    )
    assert r.status_code in (200, 201), r.text
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Search-Routes-123"}
    )
    token = r.json()["data"]["access_token"]
    me = (await client.get("/api/v1/users/me", headers=_h(token))).json()["data"]
    return token, me["user"]["id"]


async def _workspace(client, token: str, slug: str) -> dict:
    r = await client.post(
        "/api/v1/workspaces", json={"name": slug, "slug": slug}, headers=_h(token)
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _project(client, token: str, ws: str, name: str, key: str, visibility="public") -> str:
    r = await client.post(
        f"/api/v1/workspaces/{ws}/projects",
        json={"name": name, "key": key, "visibility": visibility},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _issue(client, token: str, ws: str, title: str, project_id: str | None = None) -> dict:
    body: dict = {"title": title}
    if project_id is not None:
        body["project_id"] = project_id
    r = await client.post(f"/api/v1/workspaces/{ws}/issues", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _agent(client, token: str, ws: str, name: str, visibility="workspace") -> str:
    r = await client.post(
        f"/api/v1/workspaces/{ws}/agents",
        json={"name": name, "visibility": visibility},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _view(
    client, token: str, ws: str, name: str, visibility="shared", project_id: str | None = None
) -> str:
    body: dict = {"name": name, "layout": "board", "visibility": visibility}
    if project_id is not None:
        body["project_id"] = project_id
    r = await client.post(f"/api/v1/workspaces/{ws}/views", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _chat(client, token: str, ws: str, agent_id: str, title: str | None = None) -> str:
    body: dict = {"agent_id": agent_id}
    if title:
        body["title"] = title
    r = await client.post(f"/api/v1/workspaces/{ws}/chat-sessions", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _add_member(client, token: str, ws: str, user_id: str, role: str) -> str:
    r = await client.post(
        f"/api/v1/workspaces/{ws}/members",
        json={"user_id": user_id, "role": role, "member_type": "human"},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def _search(client, token: str, ws: str, q: str, **params):
    query = {"q": q, **{k: v for k, v in params.items() if v is not None}}
    return await client.get(
        f"/api/v1/workspaces/{ws}/search", params=query, headers=_h(token)
    )


class SearchWorld:
    """A workspace populated with all six searchable object types."""

    def __init__(self) -> None:
        self.slug = f"sw-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def world(client) -> dict:
    # Every searchable object carries the token 「登录」 so one query can
    # exercise all six types at once.
    owner_token, owner_id = await _register_login(client, "owner", display="登录管理员")
    ws = await _workspace(client, owner_token, f"sw-{uuid.uuid4().hex[:8]}")
    ws_id = ws["id"]
    roster = (
        await client.get(f"/api/v1/workspaces/{ws_id}/members", headers=_h(owner_token))
    ).json()["data"]
    owner_member_id = next(
        m["id"] for m in roster if (m.get("profile") or {}).get("id") == owner_id
    )

    public_project = await _project(client, owner_token, ws_id, "登录官网改版", "WEB")
    private_project = await _project(
        client, owner_token, ws_id, "登录秘密计划项目", "SEC", visibility="private"
    )

    issue_hit = await _issue(
        client, owner_token, ws_id, "登录页在 Safari 崩溃", project_id=public_project
    )
    await _issue(client, owner_token, ws_id, "登录弹窗样式错位", project_id=public_project)
    secret_issue = await _issue(
        client, owner_token, ws_id, "登录密钥轮换流程", project_id=private_project
    )

    agent_id = await _agent(client, owner_token, ws_id, "登录代码助手")
    private_agent_id = await _agent(
        client, owner_token, ws_id, "秘密审计机器人", visibility="private"
    )

    shared_view = await _view(client, owner_token, ws_id, "登录问题视图")
    private_view = await _view(
        client, owner_token, ws_id, "私有登录视图", visibility="private"
    )
    project_view = await _view(
        client, owner_token, ws_id, "登录秘密项目视图", visibility="shared", project_id=private_project
    )

    chat_id = await _chat(client, owner_token, ws_id, agent_id, title="登录问题排查会话")

    # A plain member (non-admin) and a guest for visibility negatives.
    member_token, member_user_id = await _register_login(client, "member")
    member_id = await _add_member(client, owner_token, ws_id, member_user_id, "member")
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}/members/{member_id}",
        json={"display_override": "登录成员"},
        headers=_h(owner_token),
    )
    assert r.status_code == 200, r.text
    guest_token, guest_user_id = await _register_login(client, "guest")
    guest_id = await _add_member(client, owner_token, ws_id, guest_user_id, "guest")

    return {
        "ws_id": ws_id,
        "slug": ws["slug"],
        "owner_token": owner_token,
        "owner_id": owner_id,
        "owner_member_id": owner_member_id,
        "member_token": member_token,
        "member_id": member_id,
        "guest_token": guest_token,
        "guest_id": guest_id,
        "public_project": public_project,
        "private_project": private_project,
        "issue_hit": issue_hit,
        "secret_issue": secret_issue,
        "agent_id": agent_id,
        "private_agent_id": private_agent_id,
        "shared_view": shared_view,
        "private_view": private_view,
        "project_view": project_view,
        "chat_id": chat_id,
    }


async def test_six_object_types_grouped(client, world):
    r = await _search(client, world["owner_token"], world["ws_id"], "登录")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    types = {item["type"] for item in data}
    assert types == {"issue", "member", "agent", "project", "view", "chat_session"}

    for item in data:
        # §3.2 unified shape — structured context, no composed sentences.
        assert set(item) >= {"type", "id", "title", "context", "icon", "url"}
        assert "subtitle" not in item
        assert "score" not in item
        assert isinstance(item["context"], dict)
        assert item["url"].startswith(f"/w/{world['slug']}/")

    by_type = {item["type"]: item for item in data}
    issue_item = next(i for i in data if i["id"] == world["issue_hit"]["id"])
    assert issue_item["context"]["identifier"] == world["issue_hit"]["identifier"]
    assert issue_item["context"]["project"]["name"] == "登录官网改版"
    assert issue_item["context"]["status"]["category"]
    assert issue_item["badge"]["label_key"] == "search.badge.status"
    assert issue_item["url"] == (
        f"/w/{world['slug']}/issues/by-identifier/{issue_item['context']['identifier']}"
    )

    agent_item = by_type["agent"]
    assert agent_item["context"]["member_type"] == "agent"
    assert agent_item["context"]["capacity"] == {
        "running": 0,
        "queued": 0,
        "awaiting_approval": 0,
    }
    assert agent_item["badge"]["label_key"] == "search.badge.memberType.agent"
    assert agent_item["url"] == f"/w/{world['slug']}/members/{agent_item['id']}"

    project_item = next(i for i in data if i["id"] == world["public_project"])
    assert project_item["context"]["key"] == "WEB"
    # Owner sees the private project too (same token in its name).
    assert world["private_project"] in {i["id"] for i in data if i["type"] == "project"}
    view_item = next(i for i in data if i["id"] == world["shared_view"])
    assert view_item["context"]["scope"] == "workspace"
    chat_item = by_type["chat_session"]
    assert chat_item["context"]["participants_count"] == 2
    assert chat_item["context"]["agent"]["name"] == "登录代码助手"


async def test_empty_query_returns_empty_data(client, world):
    r = await _search(client, world["owner_token"], world["ws_id"], "")
    assert r.status_code == 200
    assert r.json() == {"data": [], "next_cursor": None}
    r = await _search(client, world["owner_token"], world["ws_id"], None)
    assert r.json() == {"data": [], "next_cursor": None}


async def test_validation_errors(client, world):
    t, ws = world["owner_token"], world["ws_id"]
    assert (await _search(client, t, ws, "x", types="bogus")).status_code == 400
    assert (await _search(client, t, ws, "x", limit=51)).status_code == 400
    assert (await _search(client, t, ws, "x", limit=0)).status_code == 400
    long_q = "登" * 121
    assert (await _search(client, t, ws, long_q)).status_code == 400
    # types whitelist subset works
    r = await _search(client, t, ws, "登录", types="issue,view")
    assert {i["type"] for i in r.json()["data"]} <= {"issue", "view"}


async def test_identifier_fast_path_pinned_case_insensitive(client, world):
    identifier = world["issue_hit"]["identifier"]
    for variant in (identifier, identifier.lower(), f"  {identifier.lower()} "):
        r = await _search(client, world["owner_token"], world["ws_id"], variant)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data, f"no results for {variant!r}"
        assert data[0]["id"] == world["issue_hit"]["id"], "identifier hit must be pinned first"
        assert data[0]["url"].endswith(f"/issues/by-identifier/{identifier}")


async def test_pagination_traversal_no_dup_no_miss(client, world):
    token, ws = world["owner_token"], world["ws_id"]
    project = world["public_project"]
    created = set()
    for n in range(1, 26):
        issue = await _issue(client, token, ws, f"分页遍历任务 {n:02d}", project_id=project)
        created.add(issue["id"])

    # Per-type candidate cap is 20 (§2.2), so 25 matches MUST require
    # cursor traversal — a single page can never hold them all.
    first = (await _search(client, token, ws, "分页遍历", limit=10)).json()
    assert first["next_cursor"] is not None

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        r = await _search(client, token, ws, "分页遍历", limit=10, cursor=cursor)
        body = r.json()
        seen.extend(item["id"] for item in body["data"])
        cursor = body["next_cursor"]
        pages += 1
        assert pages <= 5, "pagination did not terminate"
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 25, "duplicates or misses across pages"
    assert set(seen) == created


async def test_cursor_security(client, world):
    token, ws = world["owner_token"], world["ws_id"]
    # 「登录」 matches well over five objects, so limit=5 forces a cursor.
    body = (await _search(client, token, ws, "登录", limit=5)).json()
    cursor = body["next_cursor"]
    assert cursor

    # Reuse across q / types / workspace → 400.
    assert (await _search(client, token, ws, "其他查询", cursor=cursor)).status_code == 400
    assert (
        await _search(client, token, ws, "登录", types="issue", cursor=cursor)
    ).status_code == 400
    other_ws = await _workspace(client, token, f"cw-{uuid.uuid4().hex[:8]}")
    assert (
        await _search(client, token, other_ws["id"], "登录", cursor=cursor)
    ).status_code in (400, 404)

    # Tampered internals (signature mismatch) → 400.
    envelope = json.loads(
        base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    )
    envelope["t"][0] = 99
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    assert (await _search(client, token, ws, "登录", cursor=tampered)).status_code == 400
    assert (await _search(client, token, ws, "登录", cursor="garbage")).status_code == 400


async def test_non_member_workspace_not_found(client, world):
    outsider_token, _ = await _register_login(client, "Outsider")
    r = await _search(client, outsider_token, world["ws_id"], "登录")
    assert r.status_code == 404  # existence is never leaked
    assert (await _search(client, outsider_token, str(uuid.uuid4()), "登录")).status_code == 404


async def test_private_project_invisible_to_member(client, world):
    token, ws = world["member_token"], world["ws_id"]
    data = (await _search(client, token, ws, "登录")).json()["data"]
    ids = {item["id"] for item in data}
    # The private project, its issue and its project-scoped view never enter
    # results for a non-member — not in results, not in counts (§3.3).
    assert world["secret_issue"]["id"] not in ids
    assert world["private_project"] not in ids
    assert world["project_view"] not in ids
    # Owner sees all three.
    owner_data = (await _search(client, world["owner_token"], ws, "登录")).json()["data"]
    owner_ids = {item["id"] for item in owner_data}
    assert world["secret_issue"]["id"] in owner_ids
    assert world["private_project"] in owner_ids
    assert world["project_view"] in owner_ids


async def test_private_agent_invisible_to_non_owner(client, world):
    # Result ids are members.id (§3.2) — match the private agent by title.
    token, ws = world["member_token"], world["ws_id"]
    data = (await _search(client, token, ws, "机器人")).json()["data"]
    assert all(item["title"] != "秘密审计机器人" for item in data)
    owner_data = (await _search(client, world["owner_token"], ws, "机器人")).json()["data"]
    assert any(
        item["type"] == "agent" and item["title"] == "秘密审计机器人" for item in owner_data
    )


async def test_chat_session_participant_only(client, world):
    ws = world["ws_id"]
    member_data = (await _search(client, world["member_token"], ws, "排查会话")).json()["data"]
    assert all(item["id"] != world["chat_id"] for item in member_data)
    owner_data = (await _search(client, world["owner_token"], ws, "排查会话")).json()["data"]
    assert any(item["id"] == world["chat_id"] for item in owner_data)


async def test_private_view_owner_only(client, world):
    ws = world["ws_id"]
    member_data = (await _search(client, world["member_token"], ws, "问题视图")).json()["data"]
    view_ids = {item["id"] for item in member_data if item["type"] == "view"}
    assert world["private_view"] not in view_ids
    assert world["shared_view"] in view_ids


async def test_guest_sees_only_granted_project_issues(client, world):
    token, ws = world["guest_token"], world["ws_id"]
    data = (await _search(client, token, ws, "登录")).json()["data"]
    issue_ids = {item["id"] for item in data if item["type"] == "issue"}
    # Guest has no project grants and is neither assignee nor reporter.
    assert issue_ids == set()


async def test_highlight_codepoint_ranges(client, world):
    data = (await _search(client, world["owner_token"], world["ws_id"], "登录")).json()["data"]
    hit = next(i for i in data if i["id"] == world["issue_hit"]["id"])
    assert hit["highlight"]["title"]["unit"] == "codepoint"
    assert hit["highlight"]["title"]["ranges"] == [[0, 2]]


async def test_rename_makes_search_name_live(client, world):
    token = world["owner_token"]
    ws = world["ws_id"]
    member_id = world["member_id"]

    # Owner edits the member's display_override → immediately searchable.
    r = await client.patch(
        f"/api/v1/workspaces/{ws}/members/{member_id}",
        json={"display_override": "张三丰"},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text
    data = (await _search(client, token, ws, "张三丰")).json()["data"]
    assert any(item["id"] == member_id for item in data)

    # Rename → old name stops matching, new name matches (§2.2 acceptance).
    r = await client.patch(
        f"/api/v1/workspaces/{ws}/members/{member_id}",
        json={"display_override": "李四光"},
        headers=_h(token),
    )
    assert r.status_code == 200
    new_hits = (await _search(client, token, ws, "李四光")).json()["data"]
    old_hits = (await _search(client, token, ws, "张三丰")).json()["data"]
    assert any(item["id"] == member_id for item in new_hits)
    assert all(item["id"] != member_id for item in old_hits)


async def test_user_rename_resyncs_across_workspaces(client, world):
    token = world["owner_token"]
    # Owner updates their OWN display name via /users/me.
    r = await client.patch(
        "/api/v1/users/me", json={"display_name": "王重阳"}, headers=_h(token)
    )
    assert r.status_code == 200, r.text
    data = (await _search(client, token, world["ws_id"], "王重阳")).json()["data"]
    assert any(item["type"] == "member" for item in data)


async def test_normalized_accent_and_case_input(client, world):
    token, ws = world["owner_token"], world["ws_id"]
    await client.patch(
        f"/api/v1/workspaces/{ws}/members/{world['member_id']}",
        json={"display_override": "José Àncône"},
        headers=_h(token),
    )
    for q in ("JOSE", "jose", "José"):
        data = (await _search(client, token, ws, q)).json()["data"]
        assert any(item["id"] == world["member_id"] for item in data), q


async def test_prefix_path_one_two_chars(client, world):
    # 1–2 char queries hit the prefix path (trigram unusable) — still results.
    r = await _search(client, world["owner_token"], world["ws_id"], "登")
    assert r.status_code == 200
    assert any(item["type"] == "issue" for item in r.json()["data"])


async def test_rate_limit_429(client, world):
    token, ws = world["owner_token"], world["ws_id"]
    status = None
    for _ in range(310):
        r = await _search(client, token, ws, "登录")
        status = r.status_code
        if status == 429:
            assert "Retry-After" in r.headers
            break
    assert status == 429, "search endpoint must be rate limited"


async def test_query_not_logged(client, world, caplog):
    secret_q = "极度机密查询词xyzzy"
    with caplog.at_level(logging.DEBUG):
        r = await _search(client, world["owner_token"], world["ws_id"], secret_q)
    assert r.status_code == 200
    assert secret_q not in caplog.text, "raw q must never reach logs (§5.3)"


async def test_types_member_only_excludes_agent_rows(client, world):
    data = (
        await _search(client, world["owner_token"], world["ws_id"], "登录", types="member")
    ).json()["data"]
    assert data
    assert {item["type"] for item in data} == {"member"}


async def test_identifier_fast_path_miss(client, world):
    r = await _search(client, world["owner_token"], world["ws_id"], "zz-9999")
    assert r.status_code == 200
    assert r.json()["data"] == []


async def test_cross_type_pagination_keyset(client, world):
    """Pages that cut ACROSS types exercise every keyset branch."""
    token, ws = world["owner_token"], world["ws_id"]
    seen: list[tuple[str, str]] = []
    cursor = None
    pages = 0
    while True:
        body = (await _search(client, token, ws, "登录", limit=3, cursor=cursor)).json()
        seen.extend((item["type"], item["id"]) for item in body["data"])
        cursor = body["next_cursor"]
        pages += 1
        assert pages <= 8
        if cursor is None:
            break
    assert len(seen) == len(set(seen)), "cross-type pagination duplicated a row"
    assert len({t for t, _ in seen}) >= 4, "expected traversal across several types"


async def test_agent_capacity_snapshot(client, world, db_session):
    from sqlalchemy import text

    agent_id = world["agent_id"]
    ws = world["ws_id"]
    # Seed the capacity sources: one queued execution, one running, one
    # pending approval (→ awaiting_approval) — §6.12 badge snapshot.
    exec_ids = []
    for status in ("queued", "running"):
        row = (
            await db_session.execute(
                text(
                    "INSERT INTO task_executions (workspace_id, agent_id, \"trigger\", "
                    "status, priority) "
                    "VALUES (:ws, :agent, 'assign', :status, 100) RETURNING id"
                ),
                {"ws": ws, "agent": agent_id, "status": status},
            )
        ).scalar_one()
        exec_ids.append(row)
    await db_session.execute(
        text(
            "INSERT INTO approvals (workspace_id, subject_type, subject_execution_id, "
            "status, requested_by_member_id, action_summary, expires_at) "
            "VALUES (:ws, 'tool_call', :exec_id, 'pending', :member, '{}'::jsonb, "
            "now() + interval '1 hour')"
        ),
        {"ws": ws, "exec_id": exec_ids[1], "member": world["owner_member_id"]},
    )
    await db_session.commit()

    data = (await _search(client, world["owner_token"], ws, "登录代码助手")).json()["data"]
    agent_item = next(i for i in data if i["type"] == "agent")
    assert agent_item["context"]["capacity"] == {
        "running": 1,
        "queued": 1,
        "awaiting_approval": 1,
    }


async def test_guest_with_project_grant_sees_project_issues(client, world):
    ws, guest_token = world["ws_id"], world["guest_token"]
    r = await client.post(
        f"/api/v1/workspaces/{ws}/members/{world['guest_id']}/project-access",
        json={"project_id": world["public_project"], "permission": "read"},
        headers=_h(world["owner_token"]),
    )
    assert r.status_code in (200, 201), r.text
    data = (await _search(client, guest_token, ws, "登录")).json()["data"]
    issue_ids = {item["id"] for item in data if item["type"] == "issue"}
    assert world["issue_hit"]["id"] in issue_ids
    # The private project stays invisible even with an unrelated grant.
    assert world["secret_issue"]["id"] not in issue_ids


async def test_statement_timeout_maps_to_query_cost_exceeded(client, world, monkeypatch):
    """A canceled statement (statement_timeout backstop) → 422 (§3.5).

    The cancel is injected at the collection boundary — a real 1ms timeout
    would cascade cancellations through the session cleanup and mask the
    mapping under test.
    """
    import asyncpg

    import mesh.search.service as search_service

    async def _boom(*args, **kwargs):
        raise asyncpg.exceptions.QueryCanceledError(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setattr(search_service.SearchService, "_collect", _boom)
    r = await _search(client, world["owner_token"], world["ws_id"], "登录")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "query_cost_exceeded"


# ---------------------------------------------------------------------------
# Acceptance-regression tests (MES-79 验收打回:P0 / H1 / H2 / M1 + view LOW)
# ---------------------------------------------------------------------------


async def test_slug_workspace_ref_search_and_favorites(client, world):
    """P0 — {ws} accepts a slug (§3.1 「UUID 或 slug」): palettes on canonical
    /w/{slug}/… pages pass the slug; both search and favorites must resolve it
    (current slug AND historic slug after rename)."""
    token, slug = world["owner_token"], world["slug"]

    by_slug = await _search(client, token, slug, "登录")
    assert by_slug.status_code == 200
    assert by_slug.json()["data"], "slug-form search returned nothing"

    fav_slug = await client.get(
        "/api/v1/favorites", params={"workspace_id": slug}, headers=_h(token)
    )
    assert fav_slug.status_code == 200

    # Rename the workspace → the OLD slug still resolves (historic slug).
    r = await client.patch(
        f"/api/v1/workspaces/{world['ws_id']}",
        json={"slug": slug + "-renamed"},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text
    historic = await _search(client, token, slug, "登录")
    assert historic.status_code == 200
    assert historic.json()["data"]


async def test_pagination_no_row_loss_when_limit_exceeds_per_type_cap(client, world):
    """H1 — page size must not be capped by the per-type fetch budget: 25
    matches with the default limit=20 previously returned exactly 20 rows
    with next_cursor=null, silently losing 5."""
    token, ws_id = world["owner_token"], world["ws_id"]
    project = world["public_project"]
    created = []
    for n in range(1, 26):
        issue = await _issue(client, token, ws_id, f"分页丢行回归任务 {n:02d}", project)
        created.append(issue["id"])

    first = await _search(client, token, ws_id, "分页丢行回归", limit=20)
    body = first.json()
    first_ids = [item["id"] for item in body["data"]]
    assert len(first_ids) == 20
    assert body["next_cursor"] is not None, "25 matches must paginate, not stop at 20"

    walked = list(first_ids)
    cursor = body["next_cursor"]
    pages = 1
    while cursor is not None:
        page = await _search(client, token, ws_id, "分页丢行回归", limit=20, cursor=cursor)
        page_body = page.json()
        walked.extend(item["id"] for item in page_body["data"])
        cursor = page_body["next_cursor"]
        pages += 1
        assert pages <= 4
    assert len(walked) == len(set(walked)) == 25, "duplicates or missed rows across pages"
    assert set(created) <= set(walked)


async def test_prefix_path_walks_past_per_type_cap(client, world):
    """H1 on the 1–2 char path: 8 prefix matches must all be retrievable
    (the fixed PREFIX_TYPE_CAP=5 used to truncate the result set)."""
    token, ws_id = world["owner_token"], world["ws_id"]
    project = world["public_project"]
    created = []
    for n in range(1, 9):
        issue = await _issue(client, token, ws_id, f"Zz{n} 前缀溢出回归任务", project)
        created.append(issue["id"])

    walked = []
    cursor = None
    while True:
        page = await _search(client, token, ws_id, "zz", limit=20, cursor=cursor)
        body = page.json()
        walked.extend(item["id"] for item in body["data"] if item["type"] == "issue")
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert set(created) <= set(walked), "prefix path lost rows beyond the old cap of 5"


async def test_identifier_prefix_recall_on_short_query(client, world):
    """M1 — the 1–2 char path must match issue identifiers (idx_issues_
    identifier_prefix was dead before): q='tg' recalls TG-<n> issues even
    though their titles do not start with 'tg'."""
    token, ws_id = world["owner_token"], world["ws_id"]
    project = await _project(client, token, ws_id, "标识符前缀项目", "TG")
    issue = await _issue(client, token, ws_id, "完全不相关的标题内容", project)

    page = await _search(client, token, ws_id, "tg", types="issue")
    ids = [item["id"] for item in page.json()["data"]]
    assert issue["id"] in ids, "identifier prefix did not recall the issue"


async def test_cjk_same_length_multipage_traversal(client, world):
    """H2 — CJK titles with identical length and score bucket tie-break on
    title_lex; the DB order must equal the Python code-point merge (COLLATE
    "C"), or page boundaries duplicate/drop rows under en_US.utf8."""
    token, ws_id = world["owner_token"], world["ws_id"]
    project = world["public_project"]
    # Same prefix (same bucket + length), varying final code points.
    tails = ["一", "九", "二", "五", "十", "三", "七", "四", "六", "八"]
    created = []
    for tail in tails:
        issue = await _issue(client, token, ws_id, f"中文标题{tail}", project)
        created.append(issue["id"])

    walked = []
    cursor = None
    while True:
        page = await _search(client, token, ws_id, "中文标题", limit=3, cursor=cursor)
        body = page.json()
        page_ids = [item["id"] for item in body["data"] if item["id"] in set(created)]
        walked.extend(page_ids)
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert len(walked) <= len(created) + 3
    assert len(walked) == len(set(walked)), "CJK ties duplicated across pages"
    assert set(created) <= set(walked), "CJK ties dropped across pages"


async def test_private_view_hidden_from_admin(client, world):
    """§3.3 「私有视图仅 owner」 — even admins do not see other members'
    private views (no admin bypass)."""
    member_token = world["member_token"]
    owner_token = world["owner_token"]
    ws_id = world["ws_id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/views",
        json={"name": "成员私有视图回归", "layout": "board", "visibility": "private"},
        headers=_h(member_token),
    )
    assert r.status_code == 201, r.text
    view_id = r.json()["data"]["id"]

    # The owner (admin role) must NOT see the member's private view.
    owner_hits = await _search(client, owner_token, ws_id, "成员私有视图回归")
    assert all(item["id"] != view_id for item in owner_hits.json()["data"])

    # The owner sees it: their own private view.
    member_hits = await _search(client, member_token, ws_id, "成员私有视图回归")
    assert any(item["id"] == view_id for item in member_hits.json()["data"])

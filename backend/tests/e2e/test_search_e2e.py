"""Search — REAL end-to-end + plan/golden-set gates (search-command-palette.md §5).

Real uvicorn API subprocess (mesh_app role → RLS live), real PostgreSQL,
real HTTP. Covers: six-type real hits over the wire, golden-set Top-3 hit
rate (§5.4), the three query-path EXPLAIN assertions under a realistic row
distribution (§5.2 — no sequential scans; index expressions match query
expressions), cursor pagination stability incl. client-independence of the
server-side order (R2-H4), and same-owner cross-workspace isolation.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def client(api_client):
    """Local alias so tests read like the unit-level search suite."""
    return api_client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-strong-passw0rd", "display_name": email.split("@")[0]},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "a-strong-passw0rd"}
    )
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": slug, "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_project(client, token: str, ws: str, name: str, key: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws}/projects",
        json={"name": name, "key": key},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token: str, ws: str, title: str, project_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws}/issues",
        json={"title": title, "project_id": project_id},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _search(client, token: str, ws: str, q: str, **params) -> dict:
    resp = await client.get(
        f"/api/v1/workspaces/{ws}/search",
        params={"q": q, **params},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _golden_world(client) -> dict:
    token = await _register_and_login(client, f"golden-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _create_workspace(client, token, f"golden-{uuid.uuid4().hex[:8]}")
    project = await _create_project(client, token, ws["id"], "官网改版", "WEB")
    issues = {}
    for title in (
        "登录页在 Safari 崩溃",
        "Safari 崩溃日志收集",
        "注册流程转化率低",
        "支付回调偶发超时",
    ):
        issue = await _create_issue(client, token, ws["id"], title, project["id"])
        issues[title] = issue
    return {"token": token, "ws": ws, "project": project, "issues": issues}


async def test_six_type_real_hits_over_http(client):
    token = await _register_and_login(client, f"six-{uuid.uuid4().hex[:8]}@e2e.mesh")
    # Rename the owner identity so the human member row carries the token
    # too (user rename → cross-workspace member resync, §2.2 live contract).
    rename = await client.patch(
        "/api/v1/users/me", json={"display_name": "搜索端到端管理员"}, headers=_auth(token)
    )
    assert rename.status_code == 200, rename.text
    ws = await _create_workspace(client, token, f"six-{uuid.uuid4().hex[:8]}")
    project = await _create_project(client, token, ws["id"], "搜索端到端项目", "E2E")
    await _create_issue(client, token, ws["id"], "搜索端到端任务", project["id"])
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": "搜索端到端助手"},
            headers=_auth(token),
        )
    ).json()["data"]
    view = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/views",
            json={"name": "搜索端到端视图", "layout": "board", "visibility": "shared"},
            headers=_auth(token),
        )
    ).json()["data"]
    chat = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/chat-sessions",
            json={"agent_id": agent["id"], "title": "搜索端到端会话"},
            headers=_auth(token),
        )
    ).json()["data"]

    body = await _search(client, token, ws["id"], "搜索端到端")
    types = {item["type"] for item in body["data"]}
    assert types == {"issue", "member", "agent", "project", "view", "chat_session"}
    for item in body["data"]:
        assert item["url"].startswith(f"/w/{ws['slug']}/")
        assert "subtitle" not in item and "score" not in item
    ids = {item["id"] for item in body["data"]}
    assert view["id"] in ids and chat["id"] in ids


async def test_golden_set_top3_hit_rate(client):
    """Query → expected result must land in Top-3 (§5.4 regression gate)."""
    world = await _golden_world(client)
    token, ws = world["token"], world["ws"]
    web124 = world["issues"]["登录页在 Safari 崩溃"]

    golden = [
        ("登录", web124["id"]),
        ("saf", world["issues"]["Safari 崩溃日志收集"]["id"]),
        ("支付", world["issues"]["支付回调偶发超时"]["id"]),
        ("注册", world["issues"]["注册流程转化率低"]["id"]),
        (web124["identifier"], web124["id"]),  # canonical identifier, any case
        (web124["identifier"].lower(), web124["id"]),
    ]
    hits = 0
    for query, expected_id in golden:
        body = await _search(client, token, ws["id"], query)
        top3 = [item["id"] for item in body["data"][:3]]
        if expected_id in top3:
            hits += 1
    rate = hits / len(golden)
    assert rate >= 0.8, f"golden Top-3 hit rate {rate:.2f} below 0.80 threshold"

    # Identifier exact hits are PINNED first (case-insensitive canonical).
    body = await _search(client, token, ws["id"], web124["identifier"].lower())
    assert body["data"][0]["id"] == web124["id"]
    assert body["data"][0]["url"].endswith(f"/issues/by-identifier/{web124['identifier']}")


async def test_cursor_pagination_stable_and_client_independent(client):
    token = await _register_and_login(client, f"page-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _create_workspace(client, token, f"page-{uuid.uuid4().hex[:8]}")
    project = await _create_project(client, token, ws["id"], "分页项目", "PG")
    created = []
    for n in range(1, 31):
        issue = await _create_issue(client, token, ws["id"], f"分页稳定任务 {n:02d}", project["id"])
        created.append(issue["id"])

    # Walk with a small page size; collect everything.
    walked: list[str] = []
    cursor = None
    while True:
        params = {"limit": 7}
        if cursor is not None:
            params["cursor"] = cursor
        body = await _search(client, token, ws["id"], "分页稳定", **params)
        walked.extend(item["id"] for item in body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    walked_issues = [i for i in walked]
    assert len(walked_issues) == len(set(walked_issues)), "cursor duplicated rows"
    assert set(created) <= set(walked_issues), "cursor traversal missed rows"

    # A freshly registered non-member gets 404 (workspace membership gates
    # search; existence is never leaked) — by UUID AND by slug form.
    token2 = await _register_and_login(client, f"page2-{uuid.uuid4().hex[:8]}@e2e.mesh")
    outsider = await client.get(
        f"/api/v1/workspaces/{ws['id']}/search", params={"q": "分页稳定"}, headers=_auth(token2)
    )
    assert outsider.status_code == 404
    outsider_slug = await client.get(
        f"/api/v1/workspaces/{ws['slug']}/search", params={"q": "分页稳定"}, headers=_auth(token2)
    )
    assert outsider_slug.status_code == 404

    # Server order must NOT depend on client state (R2-H4): two independent
    # connections of the SAME member (distinct httpx clients carry no shared
    # state) get the identical single-page ordering.
    import httpx

    transport = client._transport
    async with httpx.AsyncClient(
        transport=transport, base_url=str(client.base_url), headers=_auth(token)
    ) as other:
        first = await _search(client, token, ws["id"], "分页稳定", limit=20)
        second = await _search(other, token, ws["id"], "分页稳定", limit=20)
    assert [i["id"] for i in first["data"]] == [i["id"] for i in second["data"]]


async def test_same_owner_cross_workspace_isolation(client):
    """One owner, two workspaces: searching A never returns B's objects."""
    token = await _register_and_login(client, f"iso-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws_a = await _create_workspace(client, token, f"iso-a-{uuid.uuid4().hex[:8]}")
    ws_b = await _create_workspace(client, token, f"iso-b-{uuid.uuid4().hex[:8]}")
    proj_b = await _create_project(client, token, ws_b["id"], "隔离项目", "ISO")
    issue_b = await _create_issue(client, token, ws_b["id"], "隔离探针任务", proj_b["id"])

    body = await _search(client, token, ws_a["id"], "隔离探针")
    assert body["data"] == []

    body_b = await _search(client, token, ws_b["id"], "隔离探针")
    assert any(item["id"] == issue_b["id"] for item in body_b["data"])


async def test_explain_three_query_paths(db_session, client):
    """§5.2: each path provably uses its index; no sequential scans.

    Bulk rows + ANALYZE approximate the §10 distribution shape so planner
    selectivity matches production (tiny tables make the planner prefer
    workspace-index scans — not a defect, but not the proof we want).
    """
    token = await _register_and_login(client, f"explain-{uuid.uuid4().hex[:8]}@e2e.mesh")
    ws = await _create_workspace(client, token, f"explain-{uuid.uuid4().hex[:8]}")
    ws_id = ws["id"]

    # §10-scale distribution (100k issues / 10k members) so planner
    # selectivity matches production — the §5.2 proofs run against this.
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, display_name) "
            "SELECT gen_random_uuid(), 'bulkexp-' || g || '@x.dev', 'Explain User ' || g "
            "FROM generate_series(1, 10000) g"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO members (workspace_id, member_type, user_id, role, "
            "display_override, search_name) "
            "SELECT :ws, 'human', u.id, 'member', 'Bulk ' || u.email, "
            "public.mesh_search_norm('Bulk ' || u.email) "
            "FROM (SELECT id, email FROM users WHERE email LIKE 'bulkexp-%') u"
        ),
        {"ws": ws_id},
    )
    # Issues need a valid status → reuse the workspace's default status.
    status_id = (
        await db_session.execute(
            text(
                "SELECT id FROM issue_statuses WHERE workspace_id = :ws AND is_default "
                "LIMIT 1"
            ),
            {"ws": ws_id},
        )
    ).scalar_one()
    await db_session.execute(
        text(
            "INSERT INTO issues (workspace_id, identifier_namespace_key, number, "
            "identifier, title, status_id, state_category) "
            "SELECT :ws, 'EXP', g, 'EXP-' || g, "
            "CASE WHEN g % 37 = 0 THEN 'Zebra migration task ' || g "
            "ELSE 'Ordinary workload item ' || g END, "
            ":status, 'todo' "
            "FROM generate_series(1, 100000) g"
        ),
        {"ws": ws_id, "status": status_id},
    )
    await db_session.execute(text("ANALYZE members"))
    await db_session.execute(text("ANALYZE issues"))
    await db_session.commit()

    async def explain(sql: str, params: dict | None = None) -> str:
        rows = (await db_session.execute(text(sql), params or {})).all()
        return "\n".join(row[0] for row in rows)

    # §5.2 requires EXPLAIN (ANALYZE, BUFFERS) — actual execution, not
    # planner estimates alone.
    async def explain_analyze(sql: str, params: dict | None = None) -> str:
        return await explain(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)

    # Visibility JOIN shape shared by the issue proofs (M5 — the proof must
    # carry the REAL visibility JOIN, not a de-permissioned simplification):
    # member-role form — public projects ∪ member's projects ∪ no project.
    issue_join = (
        "LEFT JOIN issue_statuses s "
        "ON s.workspace_id = i.workspace_id AND s.id = i.status_id "
        "LEFT JOIN projects p "
        "ON p.workspace_id = i.workspace_id AND p.id = i.project_id"
    )
    member_visibility = (
        "(i.project_id IS NULL "
        "OR i.project_id IN (SELECT project_id FROM project_members pm "
        "WHERE pm.workspace_id = i.workspace_id AND pm.member_id = :mid) "
        "OR i.project_id IN (SELECT id FROM projects pp "
        "WHERE pp.workspace_id = i.workspace_id AND pp.visibility = 'public' "
        "AND pp.deleted_at IS NULL))"
    )
    member_id = (
        await db_session.execute(
            text("SELECT id FROM members WHERE workspace_id = :ws LIMIT 1"),
            {"ws": ws_id},
        )
    ).scalar_one()

    # Path 1 — 1–2 char prefix (members projection, natural planning).
    plan = await explain_analyze(
        "SELECT id FROM members "
        "WHERE workspace_id = :ws AND status <> 'removed' "
        "AND search_name LIKE public.mesh_search_norm('jo') || '%'",
        {"ws": ws_id},
    )
    assert "idx_members_search_name_prefix" in plan, plan
    assert "Seq Scan on members" not in plan, plan

    # Path 1b — issue title prefix under NATURAL planning at §10 scale with
    # the real visibility JOIN (M5 — no enable_seqscan=off forcing; ~2.7%
    # selectivity must win the index on its own merits).
    plan = await explain_analyze(
        f"SELECT i.id FROM issues i {issue_join} "
        "WHERE i.workspace_id = :ws AND i.deleted_at IS NULL "
        f"AND {member_visibility} "
        "AND public.mesh_search_norm(i.title) LIKE public.mesh_search_norm('ze') || '%'",
        {"ws": ws_id, "mid": member_id},
    )
    assert "idx_issues_title_prefix" in plan, plan
    assert "Seq Scan on issues" not in plan, plan
    assert "project_members" in plan or "pp" in plan, "visibility JOIN missing"

    # Path 2 — canonical identifier equality fast path (natural planning).
    plan = await explain_analyze(
        "SELECT id FROM issues "
        "WHERE workspace_id = :ws AND identifier = upper(trim('exp-123'))",
        {"ws": ws_id},
    )
    assert "uq_issues_identifier" in plan, plan

    # Real-data behavior: the prefix path actually returns the right rows.
    rows = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM members "
                "WHERE workspace_id = :ws AND status <> 'removed' "
                "AND search_name LIKE public.mesh_search_norm('bulk') || '%'"
            ),
            {"ws": ws_id},
        )
    ).scalar_one()
    assert rows == 10000

    # Path 3 — trigram GIN under the §10 distribution with NATURAL planning
    # and the real visibility JOIN (M5): at 100k rows the selective trigram
    # predicate (~2.7% of the workspace) must beat the full workspace scan —
    # no forced GUCs, no index drops. §5.2 「无全表顺序扫描」 proof.
    plan = await explain_analyze(
        f"SELECT i.id FROM issues i {issue_join} "
        "WHERE i.workspace_id = :ws AND i.deleted_at IS NULL "
        f"AND {member_visibility} "
        "AND public.mesh_search_norm(i.title) % public.mesh_search_norm('zebra')",
        {"ws": ws_id, "mid": member_id},
    )
    assert "idx_issues_title_trgm" in plan, plan
    assert "Seq Scan on issues" not in plan, plan
    assert "Buffers:" in plan, "BUFFERS output missing (ANALYZE BUFFERS)"

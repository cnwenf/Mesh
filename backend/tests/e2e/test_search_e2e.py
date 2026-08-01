"""Global search REAL end-to-end tests (search-command-palette.md §5).

Real uvicorn API subprocess (RLS app role) + real PostgreSQL + real Redis —
no mocks on any contract path. Covers: six-type recall, the 1–2 char prefix
path, identifier exact fast-path pinning (lowercase input canonicalizes),
private-project / private-agent / private-view / chat-participant
invisibility negatives (owner/admin positives), keyset cursor paging with
no dupes/gaps over >limit pools, cursor binding + tamper 400s, request
validation 400s, trigger-synced members.search_name (rename hits
immediately) and accent/case normalized recall.
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from mesh.db.models.agent import Agent
from mesh.db.models.chat import ChatSession
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.view import View

pytestmark = pytest.mark.e2e

PASSWORD = "S3cure-Search-12345!"
REQUIRED_ITEM_KEYS = {"type", "id", "title", "context", "icon", "url"}
ALLOWED_ITEM_KEYS = REQUIRED_ITEM_KEYS | {"badge", "highlight"}
BADGE_LABEL_KEYS = {
    "search.badge.status",
    "search.badge.memberType.agent",
    "search.badge.memberType.human",
    "search.badge.visibility.private",
}
SEMANTIC_COLORS = {"info", "success", "warning", "danger", "neutral", "accent"}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_login(client: httpx.AsyncClient, name: str) -> tuple[str, str]:
    email = f"{uuid.uuid4().hex[:10]}@x.io"
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    assert register.status_code in (200, 201), register.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"], email


async def _seed_world(api_client, session_factory) -> dict:
    """Three humans + three agents + four projects' issues + views + chats."""
    tokens, emails = {}, {}
    for name in ("Admin", "José García", "Login Master"):
        tokens[name], emails[name] = await _register_login(api_client, name)

    slug = f"sea-e2e-{uuid.uuid4().hex[:8]}"
    ws_resp = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "SEARCH E2E", "slug": slug},
        headers=_auth(tokens["Admin"]),
    )
    assert ws_resp.status_code == 201, ws_resp.text
    ws_id = uuid.UUID(ws_resp.json()["data"]["id"])

    async with session_factory() as session, session.begin():
        users = {}
        for name in ("Admin", "José García", "Login Master"):
            users[name] = (
                await session.execute(select(User).where(User.email == emails[name]))
            ).scalar_one()
        admin_member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == ws_id, Member.user_id == users["Admin"].id
                )
            )
        ).scalar_one()
        m1 = Member(workspace_id=ws_id, user_id=users["José García"].id,
                    member_type="human", role="member", status="active")
        m2 = Member(workspace_id=ws_id, user_id=users["Login Master"].id,
                    member_type="human", role="member", status="active")
        session.add_all([m1, m2])
        await session.flush()

        # Agents: one workspace-visible utility bot, one PRIVATE (owned by m1),
        # one workspace-visible "Login Bot" for the six-type query.
        bot = Agent(workspace_id=ws_id, name="Workspace Bot",
                    owner_user_id=users["Admin"].id, visibility="workspace")
        secret = Agent(workspace_id=ws_id, name="Secret Bot",
                       owner_user_id=users["José García"].id, visibility="private")
        login_bot = Agent(workspace_id=ws_id, name="Login Bot",
                          owner_user_id=users["Admin"].id, visibility="workspace")
        session.add_all([bot, secret, login_bot])
        await session.flush()
        for agent in (bot, secret, login_bot):
            session.add(Member(workspace_id=ws_id, member_type="agent",
                               agent_id=agent.id, role="member", status="active"))
        await session.flush()
        secret_member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == ws_id, Member.agent_id == secret.id
                )
            )
        ).scalar_one()

        web = Project(workspace_id=ws_id, name="Web Revamp", key="WEB",
                      visibility="public")
        log = Project(workspace_id=ws_id, name="Login Portal", key="LOG",
                      visibility="public")
        vault = Project(workspace_id=ws_id, name="Vault Project", key="VAU",
                        visibility="private")
        session.add_all([web, log, vault])
        await session.flush()
        # Only m2 is a member of the private project (admin sees it via role).
        session.add(ProjectMember(workspace_id=ws_id, project_id=vault.id,
                                  member_id=m2.id, role="member"))

        todo = (
            await session.execute(
                select(IssueStatus).where(
                    IssueStatus.workspace_id == ws_id, IssueStatus.category == "todo"
                )
            )
        ).scalars().first()

        def issue(number, project, title):
            return Issue(
                workspace_id=ws_id, title=title,
                identifier_namespace_key=project.key, number=number,
                identifier=f"{project.key}-{number}", status_id=todo.id,
                state_category="todo", project_id=project.id,
            )

        # Identifier fast-path target + six-type issue + private-project issue.
        web_124 = issue(124, web, "登录页在 Safari 崩溃")
        login_issue = issue(102, web, "login page crash")
        vault_issue = issue(1, vault, "vault login flow")
        log_issue = issue(1, log, "login portal docs")
        # Long CJK-heavy title: trigram similarity for a short CJK query is
        # diluted far below the 0.3 threshold — recall must come from the
        # normalized-substring containment arm of the trgm path (§2.2).
        cjk_long = issue(125, web, "移动端灰度发布后登录页在 Safari 浏览器频繁崩溃需回归验证")
        session.add_all([web_124, login_issue, vault_issue, log_issue, cjk_long])
        # Paging pool: 25 near-identical titles (must traverse past the
        # per-query fuzzy fetch floor without gaps).
        pool = [issue(i, web, f"searchcase {i:02d}") for i in range(1, 26)]
        session.add_all(pool)
        await session.flush()

        # Views: shared workspace / private admin-owned / shared inside the
        # PRIVATE project (project-visibility AND) / login-named shared.
        view_shared = View(workspace_id=ws_id, name="Searchview Shared",
                           owner_member_id=admin_member.id, visibility="shared")
        view_private = View(workspace_id=ws_id, name="Searchview Private",
                            owner_member_id=admin_member.id, visibility="private")
        view_vault = View(workspace_id=ws_id, name="Searchview Vault",
                          owner_member_id=admin_member.id, visibility="shared",
                          project_id=vault.id)
        view_login = View(workspace_id=ws_id, name="Login Board",
                          owner_member_id=admin_member.id, visibility="shared")
        session.add_all([view_shared, view_private, view_vault, view_login])

        # Chat sessions: m1's (participant = m1) and admin's.
        chat_m1 = ChatSession(workspace_id=ws_id, owner_id=m1.id,
                              agent_id=bot.id, title="Login Chat")
        chat_admin = ChatSession(workspace_id=ws_id, owner_id=admin_member.id,
                                 agent_id=bot.id, title="Admin only chat notes")
        session.add_all([chat_m1, chat_admin])

    return {
        "tokens": tokens,
        "ws_id": ws_id,
        "slug": slug,
        "m1": m1,
        "m2": m2,
        "secret_member_id": secret_member.id,
        "vault": vault,
        "cjk_long": cjk_long,
    }


async def _search(client, token, slug_or_id, **params) -> httpx.Response:
    return await client.get(
        f"/api/v1/workspaces/{slug_or_id}/search",
        params=params,
        headers=_auth(token),
    )


def _by_type(items: list[dict], result_type: str) -> list[dict]:
    return [item for item in items if item["type"] == result_type]


def _assert_item_shape(items: list[dict]) -> None:
    for item in items:
        assert REQUIRED_ITEM_KEYS <= set(item.keys())
        assert set(item.keys()) <= ALLOWED_ITEM_KEYS
        if "badge" in item:
            assert item["badge"]["label_key"] in BADGE_LABEL_KEYS
            assert item["badge"]["color"] in SEMANTIC_COLORS
        if "highlight" in item:
            assert item["highlight"]["title"]["unit"] == "codepoint"


# ---------------------------------------------------------------------------
# Six-type recall + response contract
# ---------------------------------------------------------------------------


async def test_six_type_hits_and_shape(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="login")
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-RateLimit-Limit"] == "300"
    body = resp.json()
    items = body["data"]
    _assert_item_shape(items)
    assert {item["type"] for item in items} == {
        "issue", "member", "agent", "project", "view", "chat_session",
    }
    # m1 sees the public/login issue but NOT the private-project one.
    issue_titles = {i["title"] for i in _by_type(items, "issue")}
    assert "login page crash" in issue_titles
    assert "vault login flow" not in issue_titles
    # Chat context: participant count + agent snapshot; only m1's session.
    chats = _by_type(items, "chat_session")
    assert [c["title"] for c in chats] == ["Login Chat"]
    assert chats[0]["context"]["participants_count"] == 2
    assert chats[0]["context"]["agent"]["name"] == "Workspace Bot"
    assert chats[0]["url"] == f"/w/{world['slug']}/chat/{chats[0]['id']}"
    # Agent context carries the capacity snapshot (zeros on a fresh world).
    agents = _by_type(items, "agent")
    assert any(a["title"] == "Login Bot" for a in agents)
    assert agents[0]["context"]["capacity"] == {
        "running": 0, "queued": 0, "awaiting_approval": 0,
    }
    # Project badge only for private visibility; public carries none.
    projects = _by_type(items, "project")
    assert all("badge" not in p for p in projects)


async def test_admin_sees_private_project_issue(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="login", types="issue")
    assert resp.status_code == 200, resp.text
    titles = {i["title"] for i in resp.json()["data"]}
    assert "vault login flow" in titles


# ---------------------------------------------------------------------------
# Prefix path (1–2 chars) and identifier fast path
# ---------------------------------------------------------------------------


async def test_short_prefix_path(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="lo")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    _assert_item_shape(items)
    # Prefix-only recall: everything returned starts with the normalized q
    # on its searchable column (title / display name / project name / …).
    assert any(i["title"] == "login page crash" for i in _by_type(items, "issue"))
    assert any(i["title"] == "Login Master" for i in _by_type(items, "member"))
    assert any(i["title"] == "Login Portal" for i in _by_type(items, "project"))
    # Cap of 5 per type holds.
    for result_type in ("issue", "member", "agent", "project", "view", "chat_session"):
        assert len(_by_type(items, result_type)) <= 5


async def test_identifier_exact_pinned_lowercase(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    for raw in ("web-124", "WEB-124", "Web-124"):
        resp = await _search(api_client, world["tokens"]["José García"],
                             world["ws_id"], q=raw)
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]
        assert items, f"no hit for {raw}"
        first = items[0]
        assert first["type"] == "issue"
        assert first["context"]["identifier"] == "WEB-124"
        assert first["url"] == (
            f"/w/{world['slug']}/issues/by-identifier/WEB-124"
        )
        assert first["badge"]["label_key"] == "search.badge.status"
        assert first["context"]["status"]["category"] == "todo"
        assert first["context"]["project"]["name"] == "Web Revamp"


# ---------------------------------------------------------------------------
# Visibility negatives: private agent / project / view / chat
# ---------------------------------------------------------------------------


async def test_private_agent_invisible_to_non_owner(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    secret_id = str(world["secret_member_id"])
    # Non-owner / non-admin: no existence leak.
    resp = await _search(api_client, world["tokens"]["Login Master"],
                         world["ws_id"], q="secret")
    assert resp.status_code == 200, resp.text
    assert all(i["id"] != secret_id for i in resp.json()["data"])
    # Owner sees it.
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="secret")
    assert any(i["id"] == secret_id for i in resp.json()["data"])
    # Admin sees it.
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="secret")
    assert any(i["id"] == secret_id for i in resp.json()["data"])


async def test_private_project_never_leaks_to_non_member(
    api_client, session_factory
) -> None:
    world = await _seed_world(api_client, session_factory)
    vault_id = str(world["vault"].id)
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="vault")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    assert items == [] or all(
        i["id"] != vault_id and i["title"] != "vault login flow"
        for i in items
    )
    # Admin sees project + issue + the shared-but-project-scoped view.
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="vault")
    items = resp.json()["data"]
    assert any(i["id"] == vault_id and i["type"] == "project" for i in items)
    assert any(i["title"] == "vault login flow" for i in items)
    assert any(i["title"] == "Searchview Vault" for i in items)
    private_project = next(i for i in items if i["id"] == vault_id)
    assert private_project["badge"]["label_key"] == "search.badge.visibility.private"


async def test_view_visibility_matrix(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    # m1: only the shared workspace-level view.
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="searchview", types="view")
    titles = {i["title"] for i in resp.json()["data"]}
    assert titles == {"Searchview Shared"}
    # m2 (private-project member): shared + vault-scoped, not admin's private.
    resp = await _search(api_client, world["tokens"]["Login Master"],
                         world["ws_id"], q="searchview", types="view")
    titles = {i["title"] for i in resp.json()["data"]}
    assert titles == {"Searchview Shared", "Searchview Vault"}
    # Admin: all three; the private one flags owner_only.
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="searchview", types="view")
    items = resp.json()["data"]
    assert {i["title"] for i in items} == {
        "Searchview Shared", "Searchview Private", "Searchview Vault",
    }
    private_view = next(i for i in items if i["title"] == "Searchview Private")
    assert private_view["context"]["owner_only"] is True
    vault_view = next(i for i in items if i["title"] == "Searchview Vault")
    assert vault_view["context"]["scope"] == "project"
    assert vault_view["context"]["project"]["name"] == "Vault Project"


async def test_chat_session_participant_only(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    # m2 is not a participant of m1's session.
    resp = await _search(api_client, world["tokens"]["Login Master"],
                         world["ws_id"], q="login chat")
    assert all(i["title"] != "Login Chat" for i in resp.json()["data"])
    resp = await _search(api_client, world["tokens"]["José García"],
                         world["ws_id"], q="login chat")
    assert any(i["title"] == "Login Chat" for i in resp.json()["data"])


# ---------------------------------------------------------------------------
# Cursor paging + binding + tamper
# ---------------------------------------------------------------------------


async def _paged_ids(client, token, ws_id, limit, **extra) -> tuple[list[str], str | None]:
    """Walk the cursor chain to exhaustion; return (ids in order, last cursor)."""
    seen: list[str] = []
    cursor = None
    last_cursor = None
    for _ in range(20):  # hard stop — paging must terminate well before
        params = {"q": "searchcase", "types": "issue", "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await _search(client, token, ws_id, **params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        seen.extend(i["id"] for i in body["data"])
        last_cursor = cursor
        cursor = body["next_cursor"]
        if cursor is None:
            return seen, last_cursor
    raise AssertionError("cursor chain did not terminate")


async def test_cursor_paging_no_dupes_no_gaps(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    token, ws_id = world["tokens"]["Admin"], world["ws_id"]
    paged, _ = await _paged_ids(api_client, token, ws_id, 8)
    # Candidate caps are per-query fetch floors, never a permanent result-set
    # truncation: all 25 matches remain reachable through the keyset cursor.
    assert len(paged) == 25
    assert len(set(paged)) == 25  # no dupes across pages
    # Same pool, one shot: identical ordered result (deterministic total order).
    resp = await _search(api_client, token, ws_id, q="searchcase",
                         types="issue", limit=50)
    fresh = [i["id"] for i in resp.json()["data"]]
    assert fresh == paged
    assert resp.json()["next_cursor"] is None


async def test_cursor_binding_and_tamper_400(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    token, ws_id = world["tokens"]["Admin"], world["ws_id"]
    resp = await _search(api_client, token, ws_id, q="searchcase",
                         types="issue", limit=8)
    cursor = resp.json()["next_cursor"]
    assert cursor
    # Reuse with a different q → 400 validation_error.
    bad = await _search(api_client, token, ws_id, q="login", types="issue",
                        limit=8, cursor=cursor)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"
    # Reuse with a different types set → 400.
    bad = await _search(api_client, token, ws_id, q="searchcase",
                        types="issue,project", limit=8, cursor=cursor)
    assert bad.status_code == 400
    # Tampered payload (HMAC mismatch) → 400.
    padded = cursor + "=" * (-len(cursor) % 4)
    envelope = json.loads(base64.urlsafe_b64decode(padded))
    envelope["t"][0] = 999
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope).encode()
    ).rstrip(b"=").decode()
    bad = await _search(api_client, token, ws_id, q="searchcase",
                        types="issue", limit=8, cursor=tampered)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"
    # Garbage cursor → 400 (not 500).
    bad = await _search(api_client, token, ws_id, q="searchcase",
                        types="issue", limit=8, cursor="!!!not-a-cursor!!!")
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Validation + empty-q contract
# ---------------------------------------------------------------------------


async def test_validation_errors_and_empty_q(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    token, ws_id = world["tokens"]["Admin"], world["ws_id"]
    # Empty q → 200 empty (no DB object search).
    resp = await _search(api_client, token, ws_id, q="")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "next_cursor": None}
    # Missing q → same.
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/search", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "next_cursor": None}
    # Invalid types → 400.
    resp = await _search(api_client, token, ws_id, q="login", types="issue,bogus")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"
    # limit over 50 → 400; non-numeric limit → 400.
    resp = await _search(api_client, token, ws_id, q="login", limit=51)
    assert resp.status_code == 400
    resp = await _search(api_client, token, ws_id, q="login", limit="abc")
    assert resp.status_code == 400
    # q over 120 chars → 400 (and the detail does NOT echo the query).
    resp = await _search(api_client, token, ws_id, q="a" * 121)
    assert resp.status_code == 400
    assert "a" * 121 not in resp.text


async def test_non_member_cannot_search(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    # A registered user with NO membership gets the uniform 404.
    token, _ = await _register_login(api_client, "Outsider")
    resp = await _search(api_client, token, world["ws_id"], q="login")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Normalization: trigger-synced search_name + accent folding
# ---------------------------------------------------------------------------


async def test_member_rename_updates_search_immediately(
    api_client, session_factory
) -> None:
    world = await _seed_world(api_client, session_factory)
    token, ws_id = world["tokens"]["Admin"], world["ws_id"]
    # Trigger-synced projection is populated on insert.
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT search_name FROM members WHERE id = :m"),
                {"m": world["m2"].id},
            )
        ).one()
        assert row[0] == "login master"
    # Rename through users.display_name → trigger recomputes member rows.
    async with session_factory() as session, session.begin():
        user = (
            await session.execute(
                select(User).where(User.id == world["m2"].user_id)
            )
        ).scalar_one()
        user.display_name = "Renamed Person"
    resp = await _search(api_client, token, ws_id, q="renamed", types="member")
    ids = {i["id"] for i in resp.json()["data"]}
    assert str(world["m2"].id) in ids
    # Old name no longer hits.
    resp = await _search(api_client, token, ws_id, q="master", types="member")
    ids = {i["id"] for i in resp.json()["data"]}
    assert str(world["m2"].id) not in ids


async def test_accent_and_case_normalized_recall(api_client, session_factory) -> None:
    world = await _seed_world(api_client, session_factory)
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="JOSÉ", types="member")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    hit = next(i for i in items if i["id"] == str(world["m1"].id))
    assert hit["title"] == "José García"
    # Highlight maps back to the ORIGINAL title's code points.
    assert hit["highlight"]["title"]["unit"] == "codepoint"
    assert hit["highlight"]["title"]["ranges"] == [[0, 4]]


async def test_cjk_substring_recall_below_similarity_threshold(
    api_client, session_factory
) -> None:
    """Short CJK query inside a long mixed-script title (§2.2 trgm path).

    '登录页' vs the 24-codepoint title: trigram Jaccard ≈ 0.04, far under the
    0.3 ``%`` threshold — recall comes from the normalized-substring
    containment arm; the §4.6 ladder then classifies it SUBSTRING-or-better
    and highlights the literal occurrence on original code points.
    """
    world = await _seed_world(api_client, session_factory)
    resp = await _search(api_client, world["tokens"]["Admin"], world["ws_id"],
                         q="登录页", types="issue")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    hit = next(
        (i for i in items if i["id"] == str(world["cjk_long"].id)), None
    )
    assert hit is not None, f"CJK substring not recalled: {items!r}"
    assert hit["context"]["identifier"] == "WEB-125"
    assert hit["highlight"]["title"]["unit"] == "codepoint"
    # '登录页' starts at code point 8 of the original title.
    assert hit["highlight"]["title"]["ranges"] == [[8, 11]]

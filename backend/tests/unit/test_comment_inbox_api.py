"""In-process comment & inbox API tests (route layer, §6.14 envelopes).

Real create_app() via ASGITransport against real PostgreSQL + Redis:
endpoint surface, workspace resolution for workspace-less paths, RBAC
(guest cannot trigger agents), idempotency header, If-Match, cross-tenant
404s, and the inbox HTTP surface.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.config import load_settings

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-comment-inbox-test-secret-0000",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture(autouse=True)
async def _flush_redis(redis_url):
    c = aioredis.from_url(redis_url, decode_responses=True)
    await c.flushdb()
    yield
    await c.flushdb()
    await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, title="Bug") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": title},
        headers=_auth(token),
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
    await client.post("/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner))
    return joiner


@pytest.fixture
async def env(client):
    token = await _register_and_login(client, "alice@mesh.example")
    workspace = await _create_workspace(client, token, f"ws-{uuid.uuid4().hex[:10]}")
    issue = await _create_issue(client, token, workspace["id"])
    bob_token = await _invite_accept(
        client, token, workspace["id"], "bob@mesh.example", role="member"
    )
    return {
        "client": client,
        "token": token,
        "bob_token": bob_token,
        "workspace": workspace,
        "issue": issue,
    }


async def _post_comment(client, token, issue_id, body_markdown, **extra):
    return await client.post(
        f"/api/v1/issues/{issue_id}/comments",
        json={"body_markdown": body_markdown, **extra},
        headers=_auth(token),
    )


# ---------------------------------------------------------------------------
# comments CRUD surface
# ---------------------------------------------------------------------------


async def test_create_and_list_comments(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    resp = await _post_comment(client, token, issue["id"], "**hello** from the API")
    assert resp.status_code == 201, resp.text
    comment = resp.json()["data"]
    assert comment["body_html"] == "<p><strong>hello</strong> from the API</p>\n"
    assert comment["author"]["name"] == "alice"
    assert comment["author"]["member_type"] == "human"

    listing = await client.get(
        f"/api/v1/issues/{issue['id']}/comments", headers=_auth(token)
    )
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["data"]) == 1
    assert body["next_cursor"] is None
    assert body["data"][0]["reply_count"] == 0

    single = await client.get(f"/api/v1/comments/{comment['id']}", headers=_auth(token))
    assert single.status_code == 200
    assert single.json()["data"]["id"] == comment["id"]


async def test_create_empty_body_rejected(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    resp = await _post_comment(client, token, issue["id"], "")
    assert resp.status_code == 400


async def test_attachments_not_available_yet(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    resp = await _post_comment(
        client, token, issue["id"], "with file",
        attachment_ids=[str(uuid.uuid4())],
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "attachments_not_available"


async def test_unknown_issue_is_404_and_bad_uuid_is_404(env):
    client, token = env["client"], env["token"]
    resp = await _post_comment(client, token, str(uuid.uuid4()), "x")
    assert resp.status_code == 404
    resp2 = await client.get("/api/v1/issues/not-a-uuid/comments", headers=_auth(token))
    assert resp2.status_code == 404


async def test_idempotency_key_header_returns_first_result(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    headers = {**_auth(token), "Idempotency-Key": uuid.uuid4().hex}
    first = await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "sent once"}, headers=headers,
    )
    second = await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "sent twice?"}, headers=headers,
    )
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


async def test_edit_with_if_match_and_conflict(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    created = (await _post_comment(client, token, issue["id"], "v1")).json()["data"]
    ok = await client.patch(
        f"/api/v1/comments/{created['id']}",
        json={"body_markdown": "v2"},
        headers={**_auth(token), "If-Match": created["updated_at"]},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["edited_at"] is not None
    stale = await client.patch(
        f"/api/v1/comments/{created['id']}",
        json={"body_markdown": "v3"},
        headers={**_auth(token), "If-Match": created["updated_at"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"


async def test_edit_forbidden_for_other_member(env):
    client, token, bob_token, issue = (
        env["client"], env["token"], env["bob_token"], env["issue"],
    )
    created = (await _post_comment(client, token, issue["id"], "mine")).json()["data"]
    resp = await client.patch(
        f"/api/v1/comments/{created['id']}",
        json={"body_markdown": "hijack"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 403


async def test_delete_returns_204_and_placeholder(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    created = (await _post_comment(client, token, issue["id"], "gone soon")).json()["data"]
    deleted = await client.delete(
        f"/api/v1/comments/{created['id']}", headers=_auth(token)
    )
    assert deleted.status_code == 204
    fetched = await client.get(f"/api/v1/comments/{created['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    body = fetched.json()["data"]
    assert body["deleted_at"] is not None
    assert body["body_markdown"] == ""


async def test_reply_and_thread_ops(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    root = (await _post_comment(client, token, issue["id"], "root")).json()["data"]
    reply = (
        await _post_comment(client, token, issue["id"], "reply", parent_id=root["id"])
    ).json()["data"]
    assert reply["thread_root_id"] == root["id"]
    nested = await _post_comment(
        client, token, issue["id"], "normalized", parent_id=reply["id"]
    )
    assert nested.status_code == 201
    assert nested.json()["data"]["parent_id"] == root["id"]
    assert nested.json()["data"]["thread_root_id"] == root["id"]

    replies = await client.get(
        f"/api/v1/comments/{root['id']}/replies", headers=_auth(token)
    )
    assert [row["id"] for row in replies.json()["data"]] == [
        reply["id"],
        nested.json()["data"]["id"],
    ]

    resolved = await client.post(
        f"/api/v1/comments/{root['id']}/resolve", headers=_auth(token)
    )
    assert resolved.status_code == 200
    assert resolved.json()["data"]["resolved_at"] is not None
    reopened = await client.post(
        f"/api/v1/comments/{root['id']}/reopen", headers=_auth(token)
    )
    assert reopened.json()["data"]["resolved_at"] is None
    # resolving a reply is rejected
    bad = await client.post(
        f"/api/v1/comments/{reply['id']}/resolve", headers=_auth(token)
    )
    assert bad.status_code == 422


async def test_reactions_surface(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    created = (await _post_comment(client, token, issue["id"], "react!")).json()["data"]
    added = await client.post(
        f"/api/v1/comments/{created['id']}/reactions",
        json={"emoji": "👍"}, headers=_auth(token),
    )
    assert added.status_code == 200
    assert added.json()["data"][0]["reacted_by_me"] is True
    dup = await client.post(
        f"/api/v1/comments/{created['id']}/reactions",
        json={"emoji": "👍"}, headers=_auth(token),
    )
    assert dup.status_code == 409
    removed = await client.delete(
        f"/api/v1/comments/{created['id']}/reactions/👍", headers=_auth(token)
    )
    assert removed.status_code == 204
    missing = await client.delete(
        f"/api/v1/comments/{created['id']}/reactions/👍", headers=_auth(token)
    )
    assert missing.status_code == 404


async def test_rate_limit_headers_present(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    resp = await _post_comment(client, token, issue["id"], "rate")
    assert resp.headers.get("x-ratelimit-limit") == "120"
    assert "x-ratelimit-remaining" in resp.headers


async def test_comment_route_404_and_400_edges(env):
    client, token = env["client"], env["token"]
    # non-UUID comment ids → 404 on every comment-scoped route
    for method, path in (
        ("get", "/api/v1/comments/not-a-uuid"),
        ("get", "/api/v1/comments/not-a-uuid/replies"),
        ("get", "/api/v1/comments/not-a-uuid/reactions"),
        ("post", "/api/v1/comments/not-a-uuid/resolve"),
        ("post", "/api/v1/comments/not-a-uuid/reopen"),
        ("delete", "/api/v1/comments/not-a-uuid"),
    ):
        resp = await client.request(method, path, headers=_auth(token))
        assert resp.status_code == 404, (path, resp.status_code)
    # unknown-but-valid UUID comment → 404 via the resolver
    missing = await client.get(
        f"/api/v1/comments/{uuid.uuid4()}", headers=_auth(token)
    )
    assert missing.status_code == 404
    # resolve a reply (valid comment, wrong shape) → 422 not_thread_root
    created = (
        await _post_comment(client, token, env["issue"]["id"], "root")
    ).json()["data"]
    reply = (
        await _post_comment(
            client, token, env["issue"]["id"], "reply", parent_id=created["id"]
        )
    ).json()["data"]
    bad_resolve = await client.post(
        f"/api/v1/comments/{reply['id']}/resolve", headers=_auth(token)
    )
    assert bad_resolve.status_code == 422


async def test_reactions_list_route_and_archive_route(env):
    client, token, issue, workspace = (
        env["client"], env["token"], env["issue"], env["workspace"],
    )
    created = (await _post_comment(client, token, issue["id"], "r")).json()["data"]
    await client.post(
        f"/api/v1/comments/{created['id']}/reactions",
        json={"emoji": "🚀"}, headers=_auth(token),
    )
    listed = await client.get(
        f"/api/v1/comments/{created['id']}/reactions", headers=_auth(token)
    )
    assert listed.status_code == 200
    assert listed.json()["data"][0]["emoji"] == "🚀"
    # inbox single-archive route
    await _seed_inbox_notifications(env, count=1)
    notification = (
        await client.get(
            "/api/v1/inbox",
            params={"workspace_id": workspace["id"]}, headers=_auth(token),
        )
    ).json()["data"][0]
    archived = await client.post(
        f"/api/v1/inbox/{notification['id']}/archive",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert archived.status_code == 200
    assert archived.json()["data"]["archived_at"] is not None
    # inbox without workspace_id → 400 on every inbox route family
    no_ws = await client.get("/api/v1/inbox/unread-count", headers=_auth(token))
    assert no_ws.status_code == 400
    no_ws_prefs = await client.get(
        "/api/v1/notification-preferences", headers=_auth(token)
    )
    assert no_ws_prefs.status_code == 400


# ---------------------------------------------------------------------------
# mentions & triggers via HTTP
# ---------------------------------------------------------------------------


async def test_unknown_mention_is_422(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    ghost = uuid.uuid4()
    resp = await _post_comment(
        client, token, issue["id"], f"[@ghost](mention://member/{ghost})"
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mention_invalid"


async def test_plain_at_name_mention_resolves(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    resp = await _post_comment(client, token, issue["id"], "@bob 看一下")
    assert resp.status_code == 201
    mentions = resp.json()["data"]["mentions"]
    assert [m["name"] for m in mentions] == ["bob"]
    assert resp.json()["data"]["triggered_execution_ids"] == []  # human mention


async def test_guest_cannot_trigger_agent_mention(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    guest_token = await _invite_accept(
        client, token, env["workspace"]["id"], "guest@mesh.example", role="guest"
    )
    # Insert an agent roster row directly (the agent API lands later). main's
    # 0017_agent enforces members.agent_id → agents, so seed a real agents row
    # (with an owner user) before the member row.
    from mesh.db.models.agent import Agent
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async with client._transport.app.state.session_factory() as session, session.begin():  # noqa: SLF001
        owner = User(email=f"agent-owner-{uuid.uuid4().hex[:8]}@x.io", display_name="owner")
        session.add(owner)
        await session.flush()
        ws_id = uuid.UUID(env["workspace"]["id"])
        agent_row = Agent(workspace_id=ws_id, name="reviewer-bot", owner_user_id=owner.id)
        session.add(agent_row)
        await session.flush()
        agent = Member(
            workspace_id=ws_id,
            member_type="agent",
            agent_id=agent_row.id,
            role="member",
            display_override="reviewer-bot",
        )
        session.add(agent)
        await session.flush()
    resp = await _post_comment(
        client, guest_token, issue["id"],
        f"[@reviewer-bot](mention://member/{agent.id})",
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mention_invalid"
    # a full member CAN trigger — the enqueue id is returned
    ok = await _post_comment(
        client, token, issue["id"],
        f"[@reviewer-bot](mention://member/{agent.id})",
    )
    assert ok.status_code == 201
    # The enqueue outbox id is correlation-only; the canonical id appears
    # after runtime materialization via execution.queued.
    assert ok.json()["data"]["triggered_execution_ids"] == []


# ---------------------------------------------------------------------------
# cross-tenant isolation
# ---------------------------------------------------------------------------


async def test_cross_workspace_access_is_404(env, client):
    other_token = await _register_and_login(client, "outsider@mesh.example")
    issue_id = env["issue"]["id"]
    listing = await client.get(
        f"/api/v1/issues/{issue_id}/comments", headers=_auth(other_token)
    )
    assert listing.status_code == 404
    post = await _post_comment(client, other_token, issue_id, "intruder")
    assert post.status_code == 404


# ---------------------------------------------------------------------------
# inbox HTTP surface
# ---------------------------------------------------------------------------


async def _seed_inbox_notifications(env, count=2):
    """Post comments as bob → alice (reporter) gets fan-out notifications
    once the relay processes them; here we invoke the handler in-process."""
    client, bob_token, issue = env["client"], env["bob_token"], env["issue"]
    for index in range(count):
        resp = await _post_comment(client, bob_token, issue["id"], f"update {index}")
        assert resp.status_code == 201
    # drain the fan-out outbox events through the real handler
    from sqlalchemy import select

    from mesh.comment_inbox.notifications import FANOUT_EVENT_TYPE, NotificationFanoutHandler
    from mesh.db.models.outbox import OutboxEvent
    from mesh.db.tenant import set_tenant_context

    factory = client._transport.app.state.session_factory  # noqa: SLF001
    handler = NotificationFanoutHandler()
    async with factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == FANOUT_EVENT_TYPE,
                    OutboxEvent.status == "pending",
                )
            )
        ).scalars().all()
    for event in events:
        async with factory() as session, session.begin():
            await set_tenant_context(session, event.workspace_id)
            await handler.handle(session, event)


async def test_inbox_unread_count_and_listing(env):
    client, token, workspace = env["client"], env["token"], env["workspace"]
    await _seed_inbox_notifications(env, count=2)
    resp = await client.get(
        "/api/v1/inbox/unread-count",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert resp.status_code == 200
    # two comments inside the 60 s window aggregate into ONE notification row
    assert resp.json()["data"]["count"] == 1
    listing = await client.get(
        "/api/v1/inbox",
        params={"workspace_id": workspace["id"], "filter": "unread"},
        headers=_auth(token),
    )
    assert listing.status_code == 200
    items = listing.json()["data"]
    assert items and items[0]["type"] == "comment_created"
    assert items[0]["actor"]["name"] == "bob"
    assert items[0]["count"] == 2  # aggregated payload count


async def test_inbox_read_unread_archive(env):
    client, token, workspace = env["client"], env["token"], env["workspace"]
    await _seed_inbox_notifications(env, count=1)
    listing = await client.get(
        "/api/v1/inbox", params={"workspace_id": workspace["id"]}, headers=_auth(token)
    )
    notification_id = listing.json()["data"][0]["id"]
    read = await client.post(
        f"/api/v1/inbox/{notification_id}/read",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert read.status_code == 200
    assert read.json()["data"]["read_at"] is not None
    unread = await client.post(
        f"/api/v1/inbox/{notification_id}/unread",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert unread.json()["data"]["read_at"] is None
    read_all = await client.post(
        "/api/v1/inbox/read-all",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert read_all.json()["data"]["updated"] >= 1
    archived = await client.post(
        "/api/v1/inbox/archive-read",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert archived.json()["data"]["archived"] >= 1
    # foreign notification id → 404
    missing = await client.post(
        f"/api/v1/inbox/{uuid.uuid4()}/read",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert missing.status_code == 404


async def test_inbox_requires_workspace_id(env):
    client, token = env["client"], env["token"]
    resp = await client.get("/api/v1/inbox", headers=_auth(token))
    assert resp.status_code == 400
    bad = await client.get(
        "/api/v1/inbox", params={"workspace_id": "nope"}, headers=_auth(token)
    )
    assert bad.status_code == 400


async def test_mute_unmute_issue(env):
    client, token, issue = env["client"], env["token"], env["issue"]
    muted = await client.post(
        f"/api/v1/issues/{issue['id']}/mute", headers=_auth(token)
    )
    assert muted.status_code == 200
    assert muted.json()["data"]["muted"] is True
    unmuted = await client.post(
        f"/api/v1/issues/{issue['id']}/unmute", headers=_auth(token)
    )
    assert unmuted.json()["data"]["muted"] is False


async def test_notification_preferences_crud(env):
    client, token, workspace = env["client"], env["token"], env["workspace"]
    initial = await client.get(
        "/api/v1/notification-preferences",
        params={"workspace_id": workspace["id"]}, headers=_auth(token),
    )
    assert initial.status_code == 200
    assert initial.json()["data"] == []
    put = await client.put(
        "/api/v1/notification-preferences",
        params={"workspace_id": workspace["id"]},
        json={
            "preferences": [
                {"event_type": "all", "in_app": True, "email": "digest",
                 "quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
                {"event_type": "execution_finished", "in_app": True, "email": "digest"},
            ]
        },
        headers=_auth(token),
    )
    assert put.status_code == 200
    rows = {row["event_type"]: row for row in put.json()["data"]}
    assert rows["all"]["quiet_hours_start"] == "22:00:00"
    assert rows["execution_finished"]["in_app"] is True


# ---------------------------------------------------------------------------
# MEDIUM-S1 / LOW-S2 — private-project comment visibility
# ---------------------------------------------------------------------------


async def _create_project(client, token, ws_id, key, **fields) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"P {key}", "key": key, **fields},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue_in_project(client, token, ws_id, project_id, title="Bug") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": title, "project_id": project_id},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def test_private_project_comment_invisible_to_non_member_member(client):
    """MEDIUM-S1: a workspace member who is NOT in a private project cannot
    read or mutate that project's comments, even knowing the comment UUID.
    LOW-S2: the denial is a 404 (no private-project existence oracle)."""
    owner = await _register_and_login(client, "s1-owner@mesh.example")
    workspace = await _create_workspace(client, owner, f"ws-{uuid.uuid4().hex[:10]}")
    private_project = await _create_project(
        client, owner, workspace["id"], "PRV", visibility="private"
    )
    issue = await _create_issue_in_project(
        client, owner, workspace["id"], private_project["id"]
    )
    created = await _post_comment(client, owner, issue["id"], "secret comment")
    assert created.status_code == 201, created.text
    comment_id = created.json()["data"]["id"]

    outsider = await _invite_accept(
        client, owner, workspace["id"], "s1-outsider@mesh.example", role="member"
    )

    # read by UUID → 404 (not 403, not 200)
    got = await client.get(f"/api/v1/comments/{comment_id}", headers=_auth(outsider))
    assert got.status_code == 404
    # thread resolve / reopen → 404
    assert (
        await client.post(f"/api/v1/comments/{comment_id}/resolve", headers=_auth(outsider))
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/comments/{comment_id}/reopen", headers=_auth(outsider))
    ).status_code == 404
    # reactions list / add / remove → 404
    assert (
        await client.get(f"/api/v1/comments/{comment_id}/reactions", headers=_auth(outsider))
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/comments/{comment_id}/reactions",
            json={"emoji": "👍"}, headers=_auth(outsider),
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/v1/comments/{comment_id}/reactions/👍", headers=_auth(outsider)
        )
    ).status_code == 404
    # replies list → 404 ; edit / delete → 404
    assert (
        await client.get(f"/api/v1/comments/{comment_id}/replies", headers=_auth(outsider))
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/comments/{comment_id}",
            json={"body_markdown": "pwn"}, headers=_auth(outsider),
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/comments/{comment_id}", headers=_auth(outsider))
    ).status_code == 404
    # issue-scoped comment list is also gated → 404
    assert (
        await client.get(f"/api/v1/issues/{issue['id']}/comments", headers=_auth(outsider))
    ).status_code == 404
    # the owner (workspace manager) still reads the comment fine
    assert (
        await client.get(f"/api/v1/comments/{comment_id}", headers=_auth(owner))
    ).status_code == 200


async def test_comment_path_404_messages_are_indistinguishable(client):
    """DEBT-2: a known comment UUID must not reveal whether the comment exists
    in an invisible project. All comment-UUID path denials return the SAME
    message — a nonexistent comment and an invisible-project comment both say
    ``comment not found`` (no residual existence inference)."""
    owner = await _register_and_login(client, "s1d-owner@mesh.example")
    workspace = await _create_workspace(client, owner, f"ws-{uuid.uuid4().hex[:10]}")
    private_project = await _create_project(
        client, owner, workspace["id"], "PRVD", visibility="private"
    )
    issue = await _create_issue_in_project(
        client, owner, workspace["id"], private_project["id"]
    )
    created = await _post_comment(client, owner, issue["id"], "hidden comment")
    assert created.status_code == 201, created.text
    hidden_comment_id = created.json()["data"]["id"]

    outsider = await _invite_accept(
        client, owner, workspace["id"], "s1d-outsider@mesh.example", role="member"
    )

    # Existing comment in an invisible project → 404 "comment not found".
    invisible = await client.get(
        f"/api/v1/comments/{hidden_comment_id}", headers=_auth(outsider)
    )
    assert invisible.status_code == 404
    assert invisible.json()["error"]["message"] == "comment not found"

    # Nonexistent comment UUID → identical 404 + identical message.
    ghost = await client.get(
        f"/api/v1/comments/{uuid.uuid4()}", headers=_auth(outsider)
    )
    assert ghost.status_code == 404
    assert ghost.json()["error"]["message"] == "comment not found"
    assert ghost.json()["error"]["message"] == invisible.json()["error"]["message"]


async def test_private_project_comment_invisible_to_guest(client):
    """MEDIUM-S1: guests without a project grant also get 404 on the comment."""
    owner = await _register_and_login(client, "s1g-owner@mesh.example")
    workspace = await _create_workspace(client, owner, f"ws-{uuid.uuid4().hex[:10]}")
    private_project = await _create_project(
        client, owner, workspace["id"], "PRVG", visibility="private"
    )
    issue = await _create_issue_in_project(
        client, owner, workspace["id"], private_project["id"]
    )
    created = await _post_comment(client, owner, issue["id"], "guest-secret")
    comment_id = created.json()["data"]["id"]
    guest = await _invite_accept(
        client, owner, workspace["id"], "s1g-guest@mesh.example", role="guest"
    )
    assert (
        await client.get(f"/api/v1/comments/{comment_id}", headers=_auth(guest))
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/comments/{comment_id}/resolve", headers=_auth(guest))
    ).status_code == 404


async def test_public_project_comment_visible_to_member(client):
    """Control: comments on public-project issues stay readable by members."""
    owner = await _register_and_login(client, "s1p-owner@mesh.example")
    workspace = await _create_workspace(client, owner, f"ws-{uuid.uuid4().hex[:10]}")
    public_project = await _create_project(
        client, owner, workspace["id"], "PUB", visibility="public"
    )
    issue = await _create_issue_in_project(
        client, owner, workspace["id"], public_project["id"]
    )
    created = await _post_comment(client, owner, issue["id"], "public comment")
    comment_id = created.json()["data"]["id"]
    member = await _invite_accept(
        client, owner, workspace["id"], "s1p-member@mesh.example", role="member"
    )
    got = await client.get(f"/api/v1/comments/{comment_id}", headers=_auth(member))
    assert got.status_code == 200
    assert got.json()["data"]["body_markdown"] == "public comment"

"""E2E for the round-2 fixes: H1 notification producers (assign / status /
subscribed_update / creator subscription), M2 (no aggregation into archived
group), M3 (preference event_type validation).

Real uvicorn (mesh_app RLS) + real relay drain + real DB assertions.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.config import load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.user import User
from mesh.workers.main import build_relay

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
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


async def _create_issue(client, token, ws_id, title="t", assignee_id=None) -> dict:
    body = {"title": title}
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _member_id(factory, email: str) -> uuid.UUID:
    async with factory() as session:
        return (
            await session.execute(
                select(Member.id).join(User, Member.user_id == User.id).where(User.email == email)
            )
        ).scalar()


async def _invite_accept(client, owner_token, ws_id, email) -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": "member"},
        headers=_auth(owner_token),
    )
    token = await _register_and_login(client, email)
    accept_token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    await client.post("/api/v1/invitations/accept", json={"token": accept_token}, headers=_auth(token))
    return token


@pytest_asyncio.fixture
async def owner_factory(db_url, redis_url):
    engine = create_engine_from_settings(load_settings(database_url=db_url, redis_url=redis_url))
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def relay(db_url, redis_url):
    settings = load_settings(database_url=db_url, redis_url=redis_url, auth_mode="dev")
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    from mesh.api.app import build_object_storage

    r = build_relay(settings, factory, None, build_object_storage(settings), mailer=None)
    yield r
    await redis_client.aclose()
    await engine.dispose()


async def _drain(relay) -> None:
    for _ in range(8):
        if await relay.run_once() == 0:
            break
        await asyncio.sleep(0.05)


@pytest_asyncio.fixture
async def env(api_client, owner_factory):
    tag = uuid.uuid4().hex[:8]
    a_email = f"pa-{tag}@x.io"
    b_email = f"pb-{tag}@x.io"
    a = await _register_and_login(api_client, a_email)
    b = await _register_and_login(api_client, b_email)
    ws = await _create_workspace(api_client, a, f"ws-{tag}")
    await _invite_accept(api_client, a, ws["id"], b_email)
    return {
        "client": api_client,
        "a": a,
        "b": b,
        "a_email": a_email,
        "b_email": b_email,
        "ws": ws,
        "factory": owner_factory,
    }


async def test_assign_at_create_notifies_assignee(env, relay, owner_factory):
    client, a, ws, factory = env["client"], env["a"], env["ws"], env["factory"]
    c_email = f"assignee-{uuid.uuid4().hex[:6]}@x.io"
    await _invite_accept(client, a, ws["id"], c_email)
    c_mid = await _member_id(factory, c_email)

    await _create_issue(client, a, ws["id"], title="assign me", assignee_id=str(c_mid))
    await _drain(relay)

    async with factory() as session:
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == c_mid,
                    Notification.type == "assigned",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].priority == "critical"


async def test_status_change_notifies_creator_subscriber(env, relay, owner_factory):
    # The creator (a) is a seeded subscriber; a *different* member (b) changes
    # the status so self-suppression (§6.13) does not drop a's notification.
    client, a, b, ws, factory, a_email = (
        env["client"], env["a"], env["b"], env["ws"], env["factory"], env["a_email"],
    )
    a_mid = await _member_id(factory, a_email)
    issue = await _create_issue(client, a, ws["id"], title="status test")
    statuses = (
        await client.get(f"/api/v1/workspaces/{ws['id']}/statuses", headers=_auth(a))
    ).json()["data"]
    target = next(s for s in statuses if s["category"] == "in_progress")
    resp = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"status_id": target["id"]},
        headers={**_auth(b), "If-Match": issue["updated_at"]},
    )
    assert resp.status_code == 200, resp.text
    await _drain(relay)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == a_mid,
                    Notification.type == "status_changed",
                )
            )
        ).scalars().all()
    # creator is a seeded subscriber → receives status_changed (I3)
    assert len(rows) >= 1


async def test_m2_no_aggregation_into_archived_group(env, relay, owner_factory):
    client, a, b, ws, factory, a_email = (
        env["client"], env["a"], env["b"], env["ws"], env["factory"], env["a_email"],
    )
    a_mid = await _member_id(factory, a_email)
    issue = await _create_issue(client, a, ws["id"], title="m2")
    await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "first"},
        headers=_auth(b),
    )
    await _drain(relay)
    async with factory() as session:
        n = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == a_mid,
                    Notification.type == "comment_created",
                )
            )
        ).scalars().first()
    assert n is not None
    r = await client.post(
        f"/api/v1/inbox/{n.id}/archive",
        params={"workspace_id": ws["id"]},
        headers=_auth(a),
    )
    assert r.status_code == 200
    await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "second"},
        headers=_auth(b),
    )
    await _drain(relay)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == a_mid,
                    Notification.type == "comment_created",
                )
            )
        ).scalars().all()
    assert len(rows) == 2  # fresh row, NOT merged into the archived one
    assert sum(1 for r in rows if r.archived_at is not None) == 1
    assert sum(1 for r in rows if r.archived_at is None) == 1
    visible = next(r for r in rows if r.archived_at is None)
    assert visible.payload["count"] == 1


async def test_m3_preference_event_type_validation(env):
    client, a, ws = env["client"], env["a"], env["ws"]
    bad = await client.put(
        "/api/v1/notification-preferences",
        params={"workspace_id": ws["id"]},
        json={"preferences": [{"event_type": "not-a-real-type", "in_app": True, "email": "digest"}]},
        headers=_auth(a),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"
    good = await client.put(
        "/api/v1/notification-preferences",
        params={"workspace_id": ws["id"]},
        json={"preferences": [{"event_type": "all", "in_app": True, "email": "digest"}]},
        headers=_auth(a),
    )
    assert good.status_code == 200

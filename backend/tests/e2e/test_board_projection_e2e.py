"""Board projection layer — REAL end-to-end tests (README §9 matrix).

Real uvicorn API + realtime gateway subprocesses (mesh_app role → RLS live),
real PostgreSQL writes, real HTTP/WebSocket. Covers the kanban issue-projection
increment: grouped projection with the overall-cursor contract, atomic move +
WIP enforcement (T9-class concurrency, no overshoot), cross-project move (T22),
cross-tenant isolation (T1), stale-cursor resync reconciliation (T6), and
view.presence broadcast.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import websockets
from sqlalchemy import text

from mesh.events.vocab import REALTIME_PUBLISH
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.outbox.service import emit_realtime
from mesh.realtime.pubsub import RedisFanOut

pytestmark = pytest.mark.e2e


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-strong-passw0rd", "display_name": email.split("@")[0]},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "a-strong-passw0rd"})
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post("/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_project(client, token, ws_id: str, key: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": f"Project {key}", "key": key},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token, ws_id: str, **fields) -> dict:
    body = {"title": f"Issue {uuid.uuid4().hex[:6]}"}
    body.update(fields)
    resp = await client.post(f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_view(client, token, ws_id: str, **overrides) -> dict:
    body = {"name": f"Board {uuid.uuid4().hex[:6]}"}
    body.update(overrides)
    resp = await client.post(f"/api/v1/workspaces/{ws_id}/views", json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _statuses_by_category(client, token, ws_id: str) -> dict:
    resp = await client.get(f"/api/v1/workspaces/{ws_id}/statuses", headers=_auth(token))
    return {s["category"]: s["id"] for s in resp.json()["data"]}


async def _outbox_events(session_factory, event_name: str) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text("SELECT payload FROM outbox_events WHERE event_type = 'realtime.publish'")
                )
            )
            .scalars()
            .all()
        )
    return [row for row in rows if row.get("event") == event_name]


async def _position_rows(session_factory, view_id: str) -> list:
    async with session_factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT issue_id::text, group_key, sub_group_key, position "
                    "FROM view_issue_positions "
                    "WHERE view_id = :view_id"
                ),
                {"view_id": view_id},
            )
        ).all()


async def _issue_category(session_factory, issue_id: str) -> str:
    async with session_factory() as session:
        return await session.scalar(
            text("SELECT state_category FROM issues WHERE id = :id"), {"id": issue_id}
        )


async def _count_in_category(session_factory, ws_id: str, category: str) -> int:
    async with session_factory() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM issues WHERE workspace_id = :ws "
                "AND state_category = :cat AND deleted_at IS NULL"
            ),
            {"ws": ws_id, "cat": category},
        )


# ---------------------------------------------------------------------------
# grouped projection + overall-cursor contract + durability
# ---------------------------------------------------------------------------


async def test_projection_grouped_durable_and_overall_cursor(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-proj@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-proj")
    for _ in range(2):
        await _create_issue(api_client, owner, ws["id"], priority="high")
    await _create_issue(api_client, owner, ws["id"], priority="low")
    view = await _create_view(api_client, owner, ws["id"], visibility="shared", group_by="priority")
    view_id = view["id"]

    resp = await api_client.get(f"/api/v1/views/{view_id}/issues", headers=_auth(owner))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "data" not in payload  # grouped contract, not the single envelope
    by_key = {g["key"]: g for g in payload["groups"]}
    assert by_key["high"]["count"] == 2
    assert by_key["low"]["count"] == 1
    assert payload["next_cursor"] is None
    for group in payload["groups"]:
        assert "cursor" not in group  # no per-group cursor

    # Overall cursor pagination across a small limit.
    page1 = await api_client.get(f"/api/v1/views/{view_id}/issues?limit=2", headers=_auth(owner))
    body1 = page1.json()
    assert body1["next_cursor"] is not None
    page2 = await api_client.get(
        f"/api/v1/views/{view_id}/issues?limit=2&cursor={body1['next_cursor']}",
        headers=_auth(owner),
    )
    assert page2.status_code == 200

    # Atomic move persists a per-view position row (durability).
    issue = await _create_issue(api_client, owner, ws["id"])
    move = await api_client.post(
        f"/api/v1/views/{view_id}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": "urgent",
            "position": 2.5,
            "version": issue["version"],
        },
        headers=_auth(owner),
    )
    assert move.status_code == 200, move.text
    rows = await _position_rows(session_factory, view_id)
    assert any(r.issue_id == issue["id"] and r.group_key == "urgent" for r in rows)


async def test_swimlane_projection_cell_commands_and_idempotency(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-swimlanes@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-swimlanes")
    view = await _create_view(
        api_client,
        owner,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        sub_group_by="priority",
    )
    url = f"/api/v1/views/{view['id']}/issues"
    headers = {**_auth(owner), "Idempotency-Key": "e2e-swimlane-create-1"}
    first = await api_client.post(
        url,
        json={
            "title": "Created in a swimlane cell",
            "group_key": "todo",
            "sub_group_key": "low",
        },
        headers=headers,
    )
    replay = await api_client.post(
        url,
        json={
            "title": "Retry body must not win",
            "group_key": "done",
            "sub_group_key": "urgent",
        },
        headers=headers,
    )
    assert first.status_code == replay.status_code == 201
    issue = first.json()["data"]
    assert replay.json()["data"]["id"] == issue["id"]
    assert replay.json()["data"]["title"] == "Created in a swimlane cell"

    moved = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": "in_progress",
            "to_sub_group_key": "high",
            "position": 2.5,
            "version": issue["version"],
        },
        headers=_auth(owner),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["state_category"] == "in_progress"
    assert moved.json()["data"]["priority"] == "high"

    reordered = await api_client.post(
        f"/api/v1/views/{view['id']}/reorder",
        json={
            "issue_id": issue["id"],
            "to_group_key": "in_progress",
            "sub_group_key": "high",
            "position": 1.25,
        },
        headers=_auth(owner),
    )
    assert reordered.status_code == 200, reordered.text
    assert reordered.json()["data"]["sub_group_key"] == "high"

    projection = await api_client.get(f"{url}?limit=1", headers=_auth(owner))
    assert projection.status_code == 200, projection.text
    payload = projection.json()
    assert "groups" not in payload
    assert payload["columns"] and payload["lanes"]
    assert sum(len(group["data"]) for lane in payload["lanes"] for group in lane["groups"]) == 1
    high_lane = next(lane for lane in payload["lanes"] if lane["key"] == "high")
    in_progress = next(group for group in high_lane["groups"] if group["key"] == "in_progress")
    assert in_progress["count"] == 1
    assert in_progress["data"][0]["id"] == issue["id"]

    rows = await _position_rows(session_factory, view["id"])
    matching = [row for row in rows if row.issue_id == issue["id"]]
    assert len(matching) == 1
    assert (matching[0].group_key, matching[0].sub_group_key, matching[0].position) == (
        "in_progress",
        "high",
        1.25,
    )
    created = await _outbox_events(session_factory, "issue.created")
    detail_events = [frame for frame in created if frame["channel"] == f"issue:{issue['id']}"]
    assert len(detail_events) == 1
    move_events = await _outbox_events(session_factory, "issue.moved")
    assert any(
        frame["data"].get("from_sub_group") == "low" and frame["data"].get("to_sub_group") == "high"
        for frame in move_events
    )


# ---------------------------------------------------------------------------
# WIP enforcement (block / warn)
# ---------------------------------------------------------------------------


async def test_move_wip_block_rejected_durable(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-wipblock@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-wipblock")
    statuses = await _statuses_by_category(api_client, owner, ws["id"])
    await _create_issue(api_client, owner, ws["id"], status_id=statuses["in_progress"])
    mover = await _create_issue(api_client, owner, ws["id"])
    view = await _create_view(
        api_client,
        owner,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 1, "enforcement": "block"}}},
    )

    resp = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": mover["id"],
            "to_group_key": "in_progress",
            "position": 1.0,
            "version": mover["version"],
        },
        headers=_auth(owner),
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "wip_limit_exceeded"
    assert err["details"] == {"group_key": "in_progress", "limit": 1, "count": 1}
    # DB unchanged.
    assert await _issue_category(session_factory, mover["id"]) == "todo"
    assert await _position_rows(session_factory, view["id"]) == []


async def test_move_wip_warn_succeeds_and_emits(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-wipwarn@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-wipwarn")
    statuses = await _statuses_by_category(api_client, owner, ws["id"])
    await _create_issue(api_client, owner, ws["id"], status_id=statuses["in_progress"])
    mover = await _create_issue(api_client, owner, ws["id"])
    view = await _create_view(
        api_client,
        owner,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 1, "enforcement": "warn"}}},
    )

    resp = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": mover["id"],
            "to_group_key": "in_progress",
            "position": 1.0,
            "version": mover["version"],
        },
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    assert await _issue_category(session_factory, mover["id"]) == "in_progress"
    exceeded = await _outbox_events(session_factory, "view.wip_exceeded")
    assert any(f["data"]["group_key"] == "in_progress" for f in exceeded)


# ---------------------------------------------------------------------------
# T9 concurrent drag → exactly one conflict, converges
# ---------------------------------------------------------------------------


async def test_t9_concurrent_drag_exactly_one_conflict(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-t9@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-t9")
    issue = await _create_issue(api_client, owner, ws["id"])
    view = await _create_view(api_client, owner, ws["id"], visibility="shared", group_by="state_category")

    async def _move(to_group_key: str):
        return await api_client.post(
            f"/api/v1/views/{view['id']}/moves",
            json={
                "issue_id": issue["id"],
                "to_group_key": to_group_key,
                "position": 1.0,
                "version": issue["version"],
            },
            headers=_auth(owner),
        )

    r1, r2 = await asyncio.gather(_move("in_progress"), _move("done"))
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409]
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["error"]["code"] == "conflict"
    # Final state converges to the winner's column (no lost update).
    final = await _issue_category(session_factory, issue["id"])
    assert final in ("in_progress", "done")


# ---------------------------------------------------------------------------
# WIP concurrency — block limit must not be overshot (T9-class)
# ---------------------------------------------------------------------------


async def test_wip_concurrency_block_no_overshoot(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-wiprace@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-wiprace")
    movers = [await _create_issue(api_client, owner, ws["id"]) for _ in range(5)]
    view = await _create_view(
        api_client,
        owner,
        ws["id"],
        visibility="shared",
        group_by="state_category",
        board_settings={"wip": {"in_progress": {"limit": 2, "enforcement": "block"}}},
    )

    async def _move(issue):
        return await api_client.post(
            f"/api/v1/views/{view['id']}/moves",
            json={
                "issue_id": issue["id"],
                "to_group_key": "in_progress",
                "position": 1.0,
                "version": issue["version"],
            },
            headers=_auth(owner),
        )

    responses = await asyncio.gather(*[_move(issue) for issue in movers])
    successes = sum(1 for r in responses if r.status_code == 200)
    rejected = sum(
        1 for r in responses if r.status_code == 422 and r.json()["error"]["code"] == "wip_limit_exceeded"
    )
    assert successes == 2
    assert rejected == 3
    # The column never exceeds its limit.
    assert await _count_in_category(session_factory, ws["id"], "in_progress") == 2


# ---------------------------------------------------------------------------
# T22 cross-project move contract
# ---------------------------------------------------------------------------


async def test_t22_cross_project_move_contract(api_client, session_factory):
    owner = await _register_and_login(api_client, "owner-t22@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-t22")
    src = await _create_project(api_client, owner, ws["id"], "SRC")
    dst = await _create_project(api_client, owner, ws["id"], "DST")
    issue = await _create_issue(api_client, owner, ws["id"], project_id=src["id"])
    view = await _create_view(api_client, owner, ws["id"], visibility="shared", group_by="project")

    # Unconfirmed → 422 with preview.
    unconfirmed = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": dst["id"],
            "position": 1.0,
            "version": issue["version"],
        },
        headers=_auth(owner),
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"]["code"] == "move_confirmation_required"
    assert "preview" in unconfirmed.json()["error"]["details"]

    # dry_run → preview only (no write).
    dry = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": dst["id"],
            "position": 1.0,
            "version": issue["version"],
            "dry_run": True,
        },
        headers=_auth(owner),
    )
    assert dry.status_code == 200
    assert dry.json()["data"]["target_project_id"] == dst["id"]

    # Confirmed → single-transaction migration.
    confirmed = await api_client.post(
        f"/api/v1/views/{view['id']}/moves",
        json={
            "issue_id": issue["id"],
            "to_group_key": dst["id"],
            "position": 1.5,
            "version": issue["version"],
            "confirm": True,
        },
        headers=_auth(owner),
    )
    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["project_id"] == dst["id"]
    assert "move_result" in data
    # Identifier is immutable across the move (README §6.3).
    assert data["identifier"] == issue["identifier"]
    # Durable: project_id changed + issue.project_changed rode the outbox.
    async with session_factory() as session:
        project_id = await session.scalar(
            text("SELECT project_id::text FROM issues WHERE id = :id"), {"id": issue["id"]}
        )
    assert project_id == dst["id"]
    changed = await _outbox_events(session_factory, "issue.project_changed")
    assert any(f["data"]["id"] == issue["id"] for f in changed)


# ---------------------------------------------------------------------------
# T1 cross-tenant isolation (API 404 + composite FK rejected)
# ---------------------------------------------------------------------------


async def test_t1_cross_tenant_isolation(api_client, session_factory):
    token_a = await _register_and_login(api_client, "tenant-a-proj@corp.com")
    ws_a = await _create_workspace(api_client, token_a, "e2e-proj-a")
    view_a = await _create_view(api_client, token_a, ws_a["id"], visibility="shared")

    token_b = await _register_and_login(api_client, "tenant-b-proj@corp.com")
    await _create_workspace(api_client, token_b, "e2e-proj-b")

    # B's credentials cannot read A's view projection → 404.
    resp = await api_client.get(f"/api/v1/views/{view_a['id']}/issues", headers=_auth(token_b))
    assert resp.status_code == 404
    move = await api_client.post(
        f"/api/v1/views/{view_a['id']}/moves",
        json={"issue_id": str(uuid.uuid4()), "to_group_key": "todo", "position": 1.0},
        headers=_auth(token_b),
    )
    assert move.status_code == 404

    # A cross-tenant composite FK insert is rejected at the DB layer.
    from sqlalchemy.exc import DBAPIError

    issue_b_id = uuid.uuid4()
    with pytest.raises(DBAPIError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO view_issue_positions "
                    "(workspace_id, view_id, issue_id, group_key, position) "
                    "VALUES (:ws, :view, :issue, 'todo', 1.0)"
                ),
                {"ws": str(uuid.uuid4()), "view": view_a["id"], "issue": str(issue_b_id)},
            )


# ---------------------------------------------------------------------------
# T6 stale cursor → resync_required → REST reconciliation converges
# ---------------------------------------------------------------------------


async def test_t6_resync_then_board_converges(api_client, gateway_server, session_factory, redis_client):
    owner = await _register_and_login(api_client, "owner-t6@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-t6")
    await _create_issue(api_client, owner, ws["id"], priority="high")
    view = await _create_view(api_client, owner, ws["id"], visibility="shared", group_by="priority")
    channel = f"view:{view['id']}"
    token = f"mesh-dev:{ws['id']}"

    # Seed view events through the production outbox → projector → fan-out path
    # (this also registers the channel row the gateway needs to authorize).
    async def _publish(data):
        async with session_factory() as session, session.begin():
            await emit_realtime(
                session,
                workspace_id=uuid.UUID(ws["id"]),
                channel=channel,
                event="view.updated",
                data=data,
            )
        relay = OutboxRelay(
            session_factory,
            handlers={REALTIME_PUBLISH: project_realtime_event},
            fanout=RedisFanOut(redis_client),
        )
        await relay.run_once()

    for i in range(1, 4):
        await _publish({"v": i})

    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    conn = await websockets.connect(ws_url, open_timeout=10)
    try:
        await conn.send(json.dumps({"op": "auth", "token": token}))
        first = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
        assert first["op"] == "auth_ok"
        await conn.send(json.dumps({"op": "subscribe", "channel": channel, "resume_from": 0}))
        # Drain replayed events + subscribed confirmation.
        saw_subscribed = False
        for _ in range(10):
            frame = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
            if frame.get("op") == "subscribed":
                saw_subscribed = True
                break
        assert saw_subscribed
    finally:
        await conn.close()

    # Retention purge makes the cursor stale → resync_required on reconnect.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM realtime_events WHERE channel = :ch AND seq <= 3"),
            {"ch": channel},
        )
    conn2 = await websockets.connect(ws_url, open_timeout=10)
    try:
        await conn2.send(json.dumps({"op": "auth", "token": token}))
        assert json.loads(await asyncio.wait_for(conn2.recv(), timeout=5))["op"] == "auth_ok"
        await conn2.send(json.dumps({"op": "subscribe", "channel": channel, "resume_from": 1}))
        resync = json.loads(await asyncio.wait_for(conn2.recv(), timeout=5))
        assert resync["op"] == "resync_required"
        assert resync["rest"].startswith("/api/v1/realtime/events?channel=")
    finally:
        await conn2.close()

    # REST reconciliation + board re-read converges to the server truth.
    import httpx

    async with httpx.AsyncClient(base_url=api_client.base_url, timeout=10) as client:
        reconcile = await client.get(resync["rest"], headers={"Authorization": f"Bearer {token}"})
    assert reconcile.status_code == 200
    board = await api_client.get(f"/api/v1/views/{view['id']}/issues", headers=_auth(owner))
    assert board.status_code == 200
    by_key = {g["key"]: g for g in board.json()["groups"]}
    assert by_key["high"]["count"] == 1


# ---------------------------------------------------------------------------
# view.presence broadcast on subscribe
# ---------------------------------------------------------------------------


async def test_view_presence_broadcast_on_subscribe(
    api_client, gateway_server, session_factory, redis_client
):
    owner = await _register_and_login(api_client, "owner-presence@corp.com")
    ws = await _create_workspace(api_client, owner, "e2e-presence")
    view = await _create_view(api_client, owner, ws["id"], visibility="shared", group_by="state_category")
    channel = f"view:{view['id']}"
    token = f"mesh-dev:{ws['id']}"

    # Register the channel row (view.updated on creation already did, but ensure
    # the projector has run at least once for this channel).
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session,
            workspace_id=uuid.UUID(ws["id"]),
            channel=channel,
            event="view.updated",
            data={"seed": True},
        )
    relay = OutboxRelay(
        session_factory,
        handlers={REALTIME_PUBLISH: project_realtime_event},
        fanout=RedisFanOut(redis_client),
    )
    await relay.run_once()

    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    conn = await websockets.connect(ws_url, open_timeout=10)
    try:
        await conn.send(json.dumps({"op": "auth", "token": token}))
        assert json.loads(await asyncio.wait_for(conn.recv(), timeout=5))["op"] == "auth_ok"
        await conn.send(json.dumps({"op": "subscribe", "channel": channel}))
        # Drain until subscribed.
        for _ in range(10):
            frame = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
            if frame.get("op") == "subscribed":
                break
        # The subscribe hook writes a view.presence outbox event asynchronously
        # right after `subscribed`; wait for it to land, then run the relay so it
        # projects + fans out to this subscribed connection.
        presence_outbox = []
        for _ in range(50):
            presence_outbox = await _outbox_events(session_factory, "view.presence")
            if presence_outbox:
                break
            await asyncio.sleep(0.1)
        assert presence_outbox  # the gateway hook wrote it
        await relay.run_once()
        presence = None
        for _ in range(10):
            frame = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
            if frame.get("event") == "view.presence":
                presence = frame
                break
        assert presence is not None
        assert presence["payload"]["online"] >= 1
    finally:
        await conn.close()

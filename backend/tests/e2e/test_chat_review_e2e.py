"""MES-67 round-2 acceptance e2e — the negative / interface-layer cases the
reviewer re-tests (real uvicorn api + gateway + worker-relay + PG + Redis).

- H1: (MES-191 rewrite) a chat generation rides the SAME real runtime chain
  as issue executions — the relay materializes a queued trigger='chat' row,
  a registered daemon claims it, streams stdout (mirrored as SSE deltas) and
  its terminal PATCH finalizes the message and completes the execution.
- H2: a non-owner member's WS subscribe to ``chat_session:{other}`` and
  ``chat_list:{other_member}`` is rejected (forbidden); their own
  ``chat_list:{self}`` is accepted.
- M4: two concurrent sends on one session yield exactly one 201 + one 409
  (UNIQUE streaming partial index enforces single-concurrency at the DB layer).
- M1: favorites routes validation + idempotent DELETE branches (also drives
  favorites/routes.py coverage ≥90%).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from mesh.chat.engine import chat_execution_idempotency_key
from mesh.db.models.chat import ChatMessage
from mesh.db.models.member import Member
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
from mesh.db.models.user import User
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.realtime.pubsub import RedisFanOut
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE, enqueue_execution_handler
from tests.e2e.test_agent_e2e import _invite_accept
from tests.e2e.test_realtime_gateway_e2e import _recv_frame, _ws_connect
from tests.e2e.test_runtime_e2e import (
    _activated_runtime,
    _auth,
    _claim,
    _daemon,
    _setup_world,
)
from tests.unit.runtime_support import valid_result_v1

pytestmark = pytest.mark.e2e


async def _drain(relay, cycles=6):
    for _ in range(cycles):
        if await relay.run_once() == 0:
            break
        await asyncio.sleep(0.05)


def _build_relay(session_factory, redis_client):
    return OutboxRelay(
        session_factory,
        handlers={
            ENQUEUE_EVENT_TYPE: enqueue_execution_handler,
            "realtime.publish": project_realtime_event,
        },
        fanout=RedisFanOut(redis_client),
    )


async def _member_id_by_email(session_factory, ws_id, email):
    async with session_factory() as session:
        return await session.scalar(
            select(Member.id)
            .join(User, User.id == Member.user_id)
            .where(Member.workspace_id == uuid.UUID(ws_id), User.email == email)
        )


async def _owner_member_id(session_factory, ws_id):
    async with session_factory() as session:
        return await session.scalar(
            select(Member.id).where(
                Member.workspace_id == uuid.UUID(ws_id), Member.role == "owner"
            )
        )


async def _consume_to_done(client, token, stream_url, timeout=20.0):
    headers = _auth(token)
    headers["Accept"] = "text/event-stream"

    async def _read():
        events = []
        async with client.stream("GET", stream_url, headers=headers) as stream:
            cur = {}
            async for line in stream.aiter_lines():
                if line.startswith("event: "):
                    cur["event"] = line[7:]
                elif line.startswith("data: "):
                    cur["data"] = json.loads(line[6:])
                elif line == "" and cur.get("event"):
                    events.append(cur)
                    cur = {}
                    if events[-1]["event"] in (
                        "message.done", "message.interrupted", "error"
                    ):
                        return events
        return events

    return await asyncio.wait_for(_read(), timeout=timeout)


# ---------------------------------------------------------------------------
# H1 — chat generation rides the same real runtime chain as issue executions
# ---------------------------------------------------------------------------


async def test_h1_chat_generation_rides_real_runtime_chain(
    api_client, session_factory, redis_client
):
    """MES-191: send → outbox enqueue → relay materializes a queued
    trigger='chat' row → a registered daemon claims it over HTTP → streams
    stdout (mirrored as SSE deltas) → its terminal PATCH finalizes the
    message and completes the execution. No in-process placeholder path.
    """
    token, ws_id, agent_id = await _setup_world(api_client, "h1")
    relay = _build_relay(session_factory, redis_client)

    session = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_id}/chat-sessions",
            json={"agent_id": agent_id},
            headers=_auth(token),
        )
    ).json()["data"]
    sent = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_id}/chat-sessions/{session['id']}/messages",
            json={"content": "请分析"},
            headers=_auth(token),
        )
    ).json()["data"]

    # (a) The relay materializes the enqueue into a queued trigger='chat'
    # execution identified by its §6.5 idempotency key.
    await _drain(relay)
    real_key = chat_execution_idempotency_key(
        agent_id=uuid.UUID(agent_id), issue_id=None,
        trigger_event_id=uuid.UUID(sent["message_id"]),
    )
    chat_exec = await _wait_by_idem(session_factory, ws_id, real_key, "queued")
    assert chat_exec.trigger == "chat"
    assert chat_exec.issue_id is None

    # (b) A registered daemon claims it through the SAME claim endpoint issue
    # executions use, then drives logs + terminal transitions over HTTP.
    created, daemon_token = await _activated_runtime(
        api_client, token, ws_id, name="h1-rt", capabilities=["python"]
    )
    claimed = await _claim(api_client, created["id"], daemon_token)
    assert claimed.status_code == 200, claimed.text
    attempt = claimed.json()["data"]["attempt"]
    assert uuid.UUID(claimed.json()["data"]["execution"]["id"]) == chat_exec.id

    lines = ["H1 真实执行:第一块。", "H1 真实执行:第二块。"]
    logs = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/logs",
        json={
            "lease_seq": attempt["lease_seq"], "stream": "stdout",
            "start_offset": 0, "lines": lines,
        },
        headers=_daemon(daemon_token),
    )
    assert logs.status_code == 200, logs.text
    running = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": attempt["lease_seq"], "status": "running"},
        headers=_daemon(daemon_token),
    )
    assert running.status_code == 200, running.text
    done = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={
            "lease_seq": attempt["lease_seq"], "status": "completed",
            "result": valid_result_v1(),
        },
        headers=_daemon(daemon_token),
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["execution_status"] == "completed"

    # (c) The SSE stream terminates and the deltas are the mirrored stdout.
    events = await _consume_to_done(api_client, token, sent["stream_url"])
    assert events[-1]["event"] == "message.done"
    deltas = "".join(
        e["data"].get("delta", "") for e in events if e["event"] == "message.delta"
    )
    assert "H1 真实执行" in deltas

    # (d) Terminal write-back finalized BOTH the message and the execution.
    final = await _wait_by_idem(session_factory, ws_id, real_key, "completed")
    assert isinstance(final.result, dict)
    assert final.result.get("chat_message_id") == sent["message_id"]
    async with session_factory() as s:
        msg = (
            await s.execute(
                select(ChatMessage).where(
                    ChatMessage.generation_id == uuid.UUID(sent["generation_id"])
                )
            )
        ).scalar_one()
        attempts = (
            await s.execute(
                select(ExecutionAttempt).where(
                    ExecutionAttempt.execution_id == chat_exec.id
                )
            )
        ).scalars().all()
    assert msg.generation_status == "done"
    assert "H1 真实执行" in (msg.content or "")
    assert len(attempts) == 1 and attempts[0].status == "completed"


async def _wait_by_idem(session_factory, ws_id, idem, status, timeout=15.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.workspace_id == uuid.UUID(ws_id),
                        TaskExecution.idempotency_key == idem,
                    )
                )
            ).scalar_one_or_none()
        if row is not None and row.status == status:
            return row
        await asyncio.sleep(0.2)
    raise AssertionError(f"execution {idem} never reached {status}")


# ---------------------------------------------------------------------------
# H2 — non-owner WS subscribe to another's chat channels is forbidden
# ---------------------------------------------------------------------------


async def test_h2_cross_member_chat_subscribe_forbidden(
    api_client, gateway_server, session_factory
):
    token_a, ws_id, agent_id = await _setup_world(api_client, "h2")
    session = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_id}/chat-sessions",
            json={"agent_id": agent_id},
            headers=_auth(token_a),
        )
    ).json()["data"]
    email_b = f"h2-b-{uuid.uuid4().hex[:6]}@e2e.mesh"
    token_b = await _invite_accept(api_client, token_a, ws_id, email_b, role="member")
    owner_mid = str(await _owner_member_id(session_factory, ws_id))
    b_mid = str(await _member_id_by_email(session_factory, ws_id, email_b))
    assert owner_mid != b_mid

    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "auth", "token": token_b}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"
        # Another member's session channel → forbidden.
        await ws.send(json.dumps({"op": "subscribe", "channel": f"chat_session:{session['id']}"}))
        f1 = await _recv_frame(ws)
        assert f1["op"] == "error" and f1["code"] == "forbidden"
        # Another member's private list channel → forbidden.
        await ws.send(json.dumps({"op": "subscribe", "channel": f"chat_list:{owner_mid}"}))
        f2 = await _recv_frame(ws)
        assert f2["op"] == "error" and f2["code"] == "forbidden"
        # Their own list channel → accepted.
        await ws.send(json.dumps({"op": "subscribe", "channel": f"chat_list:{b_mid}"}))
        f3 = await _recv_frame(ws)
        assert f3["op"] == "subscribed" and f3["channel"] == f"chat_list:{b_mid}"
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# M4 — concurrent sends: exactly one 201 + one 409
# ---------------------------------------------------------------------------


async def test_m4_concurrent_send_single_winner(api_client):
    token, ws_id, agent_id = await _setup_world(api_client, "m4")
    session = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_id}/chat-sessions",
            json={"agent_id": agent_id},
            headers=_auth(token),
        )
    ).json()["data"]
    url = f"/api/v1/workspaces/{ws_id}/chat-sessions/{session['id']}/messages"

    async def _send(text):
        return await api_client.post(url, json={"content": text}, headers=_auth(token))

    r1, r2 = await asyncio.gather(_send("并发一"), _send("并发二"))
    codes = {r1.status_code, r2.status_code}
    assert codes == {201, 409}, (r1.status_code, r2.status_code, r1.text, r2.text)
    winner = r1 if r1.status_code == 201 else r2
    assert winner.json()["data"]["stream_url"]


# ---------------------------------------------------------------------------
# M1 — favorites routes validation + idempotent DELETE branches
# ---------------------------------------------------------------------------


async def test_m1_favorites_routes_validation_and_idempotent_delete(api_client):
    token, ws_id, agent_id = await _setup_world(api_client, "m1")
    session = (
        await api_client.post(
            f"/api/v1/workspaces/{ws_id}/chat-sessions",
            json={"agent_id": agent_id},
            headers=_auth(token),
        )
    ).json()["data"]
    sid = session["id"]
    bogus = str(uuid.uuid4())

    # Invalid target_type on PUT/GET → 400 validation_error.
    assert (
        await api_client.put(f"/api/v1/favorites/bogus_type/{bogus}", headers=_auth(token))
    ).status_code == 400
    assert (
        await api_client.get(
            f"/api/v1/favorites?workspace_id={ws_id}&target_type=bogus_type",
            headers=_auth(token),
        )
    ).status_code == 400
    # Unknown session on PUT → 404 (resolver None); DELETE → idempotent 204.
    assert (
        await api_client.put(f"/api/v1/favorites/chat_session/{bogus}", headers=_auth(token))
    ).status_code == 404
    assert (
        await api_client.delete(f"/api/v1/favorites/chat_session/{bogus}", headers=_auth(token))
    ).status_code == 204
    # Malformed cursor → 400 invalid_cursor.
    assert (
        await api_client.get(
            f"/api/v1/favorites?workspace_id={ws_id}&cursor=!!!not-base64!!!",
            headers=_auth(token),
        )
    ).status_code == 400
    # Happy path + idempotent DELETE (second DELETE on a now-absent row → 204).
    assert (
        await api_client.put(f"/api/v1/favorites/chat_session/{sid}", headers=_auth(token))
    ).status_code == 201
    d1 = await api_client.delete(f"/api/v1/favorites/chat_session/{sid}", headers=_auth(token))
    d2 = await api_client.delete(f"/api/v1/favorites/chat_session/{sid}", headers=_auth(token))
    assert d1.status_code == 204 and d2.status_code == 204

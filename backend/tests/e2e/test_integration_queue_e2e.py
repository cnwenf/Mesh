"""MES-88 queue + command plane e2e — real API server, real worker process,
real PostgreSQL/Redis. No mocks on the contract path (docs/specs/README §9).

Covers §5.6 acceptance behaviors:
- serial FIFO ordering through the real dispatcher + relay + terminal
  write-back + wake chain (M1→M2→M3 strict order);
- /stop two-phase cancel via the real inbound endpoint (cancelling holds the
  lane until execution.finished; next item dispatches only after);
- /btw append via the real inbound endpoint (im_btw row + feedback);
- queue query/summary/cancel/audit endpoints (real HTTP);
- delete protection: 409 without force, forced path produces self-describing
  terminal orphans visible via the audit endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.integration import (
    ExecutionContextAppend,
    ExternalIdentity,
    IntegrationMessageQueue,
)
from mesh.db.models.runtime import TaskExecution
from mesh.outbox.service import emit_event
from tests.e2e.conftest import BACKEND_DIR, pin_code_under_test
from tests.e2e.test_integrations_e2e import (
    _auth,
    encrypt,
    poll_until,
    setup_world,
    slack_sign,
)

pytestmark = pytest.mark.e2e

TEAM = "T_IMQ"
CHANNEL = "C_IMQ"


@pytest.fixture(scope="module")
async def queue_worker(provision_database):
    """Real worker process: relay + dispatcher + retention loops."""
    env = os.environ.copy()
    from tests.conftest import get_test_database_url, get_test_redis_url

    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_IM_DISPATCH_TICK_SECONDS"] = "0.2"
    env["MESH_IM_LEASE_REPAIR_INTERVAL_SECONDS"] = "1"
    env["MESH_STORAGE_ENDPOINT"] = os.environ.get(
        "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"
    )
    env["MESH_STORAGE_ACCESS_KEY"] = os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh")
    env["MESH_STORAGE_SECRET_KEY"] = os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret")
    pin_code_under_test(env)
    log_file = open("/tmp/imq_worker.log", "wb")
    process = subprocess.Popen(
        # sys.executable (NOT bare `python`): a shared machine's PATH python
        # may carry another checkout's stale editable install (MES-121).
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(BACKEND_DIR),
    )
    await asyncio.sleep(1.5)
    assert process.poll() is None, "queue worker died during startup"
    yield process
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


async def _make_serial_slack_world(api_client, suffix: str) -> dict:
    """Slack integration bound to CHANNEL with serial queue mode."""
    world = await setup_world(api_client, f"imq-{suffix}")
    signing_secret = f"sss-{uuid.uuid4().hex}"
    resp = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_slack",
            "name": f"slack-imq-{suffix}",
            "config": {
                "team_id": TEAM,
                "bot_user_id": "U_BOT",
                "signing_secret_ref": encrypt(signing_secret),
                "inbound_queue": "serial_conversation",
            },
        },
        headers=_auth(world["token"]),
    )
    assert resp.status_code == 201, resp.text
    world["integration_id"] = resp.json()["data"]["integration"]["id"]
    world["signing_secret"] = signing_secret
    bind = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration_id']}/bindings",
        json={
            "external_ref": CHANNEL,
            "bound_agent_id": world["agent_id"],
            "match_config": {"trigger_on": ["mention"]},
        },
        headers=_auth(world["token"]),
    )
    assert bind.status_code == 201, bind.text
    world["binding_id"] = bind.json()["data"]["id"]
    return world


def _mention_payload(ts_str: str, text_body: str = "<@U_BOT> do the task") -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "team_id": TEAM,
            "event": {
                "type": "message",
                "channel": CHANNEL,
                "user": "U_HUMAN",
                "text": text_body,
                "event_ts": ts_str,
            },
        }
    ).encode()


async def _post_inbound(api_client, world, body: bytes, ts: int):
    headers = slack_sign(world["signing_secret"], body, ts)
    return await api_client.post("/api/v1/integrations/slack/events", content=body, headers=headers)


def _now_ts() -> int:
    """Real wall-clock seconds — slack verification enforces a ±300s replay
    window against the server clock."""
    return int(time.time())


def _ts_str(offset: int) -> str:
    """Unique event_ts per message (dedupe key is team_id:event_ts)."""
    return f"{time.time() + offset / 1000:.6f}"


async def _items(session_factory, ws_id):
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationMessageQueue)
                    .where(IntegrationMessageQueue.workspace_id == uuid.UUID(ws_id))
                    .order_by(IntegrationMessageQueue.seq)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _executions(session_factory, ws_id):
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(TaskExecution)
                    .where(TaskExecution.workspace_id == uuid.UUID(ws_id))
                    .order_by(TaskExecution.queued_at)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _dispatch_forensics(session_factory, ws_id) -> str:
    """Snapshot pinning WHERE the dispatch chain is stuck — embedded in the
    wait-failure diagnostic so a CI failure self-locates from the log alone:
    items pending with NO execution.enqueue outbox row ⇒ the dispatcher
    sweep never ran (worker tick/pool stall); outbox row present but no
    execution ⇒ relay claim lag; execution queued but the item still
    pending ⇒ bind/write-back lag."""
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        items = (
            await session.execute(
                select(IntegrationMessageQueue)
                .where(IntegrationMessageQueue.workspace_id == uuid.UUID(ws_id))
                .order_by(IntegrationMessageQueue.seq)
            )
        ).scalars().all()
        execs = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.workspace_id == uuid.UUID(ws_id)
                )
            )
        ).scalars().all()
        outbox = (
            await session.execute(
                select(OutboxEvent.event_type, OutboxEvent.status).where(
                    OutboxEvent.workspace_id == uuid.UUID(ws_id),
                    OutboxEvent.event_type == "execution.enqueue",
                )
            )
        ).all()
    return " | ".join([
        "items=["
        + ", ".join(
            f"seq{i.seq}:{i.state}:exec={'y' if i.execution_id else 'n'}"
            for i in items
        )
        + "]",
        "execs=[" + ", ".join(f"{e.status}@{e.queued_at}" for e in execs) + "]",
        "enqueue_outbox=[" + ", ".join(f"{t}:{s}" for t, s in outbox) + "]",
    ])


async def _emit_finished(session_factory, ws_id, execution_id, status):
    """Write execution.finished exactly like the runtime state machine does
    (same event type, same idempotency key) — the worker consumes it."""
    async with session_factory() as session, session.begin():
        await emit_event(
            session,
            workspace_id=uuid.UUID(ws_id),
            event_type="execution.finished",
            payload={
                "execution_id": str(execution_id),
                "status": status,
                "failure_reason": None if status == "completed" else "cancelled_by_command",
            },
            idempotency_key=f"execution:{execution_id}:finished",
        )


async def _map_sender(session_factory, world):
    """Map the slack sender U_HUMAN to the world owner's user id."""
    async with session_factory() as session, session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT user_id FROM members WHERE workspace_id = :ws "
                    "AND member_type = 'human' LIMIT 1"
                ),
                {"ws": uuid.UUID(world["ws_id"])},
            )
        ).first()
        session.add(
            ExternalIdentity(
                provider="slack",
                provider_tenant_key=TEAM,
                external_user_key="U_HUMAN",
                user_id=row[0],
            )
        )


# ---------------------------------------------------------------------------
# Serial FIFO ordering through the real dispatcher
# ---------------------------------------------------------------------------


async def test_serial_fifo_strict_order(api_client, queue_worker, session_factory):
    world = await _make_serial_slack_world(api_client, "fifo")
    for i in range(3):
        body = _mention_payload(_ts_str(i), f"<@U_BOT> task {i}")
        resp = await _post_inbound(api_client, world, body, _now_ts())
        await asyncio.sleep(0.05)
        assert resp.status_code == 200, resp.text
        assert resp.json()["process_status"] == "dispatched"

    # M1 dispatched by the real dispatcher; M2/M3 wait (serial lane).
    async def _m1_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if len(items) == 3 and items[0].state == "processing" else None

    items = await poll_until(_m1_processing, timeout=60)
    assert items is not None, "M1 never reached processing via the dispatcher"
    assert [i.state for i in items] == ["processing", "pending", "pending"]
    execs = await _executions(session_factory, world["ws_id"])
    assert len(execs) == 1
    assert execs[0].trigger == "integration"
    assert items[0].execution_id == execs[0].id  # association write-back bound it

    # Complete M1 via the terminal event → write-back + wake → M2 dispatches.
    await _emit_finished(session_factory, world["ws_id"], execs[0].id, "completed")

    async def _m2_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if items[0].state == "done" and items[1].state == "processing" else None

    items = await poll_until(_m2_processing, timeout=60)
    assert items is not None, "M2 never dispatched after M1 terminal wake"

    execs = await _executions(session_factory, world["ws_id"])
    assert len(execs) == 2
    await _emit_finished(session_factory, world["ws_id"], execs[1].id, "completed")

    async def _m3_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if items[1].state == "done" and items[2].state == "processing" else None

    items = await poll_until(_m3_processing, timeout=60)
    assert items is not None, "M3 never dispatched"
    # strict execution order by started_at
    started = [i.started_at for i in items]
    assert started[0] <= started[1] <= started[2]
    assert all(s is not None for s in started)


# ---------------------------------------------------------------------------
# /stop two-phase through the real inbound endpoint
# ---------------------------------------------------------------------------


async def test_stop_two_phase_holds_lane(api_client, queue_worker, session_factory):
    world = await _make_serial_slack_world(api_client, "stop")
    await _map_sender(session_factory, world)
    for i in range(2):
        body = _mention_payload(_ts_str(10 + i), f"<@U_BOT> stop-task {i}")
        resp = await _post_inbound(api_client, world, body, _now_ts())
        assert resp.status_code == 200
        await asyncio.sleep(0.05)

    async def _m1_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if len(items) == 2 and items[0].state == "processing" else None

    items = await poll_until(_m1_processing, timeout=60)
    assert items is not None

    # Simulate the daemon having claimed the execution (no daemon in e2e):
    # a QUEUED execution cancels immediately (spec), so to exercise the
    # two-phase claimed/running → cancelling path, mark it running.
    execs = await _executions(session_factory, world["ws_id"])
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET status = 'running' WHERE id = :id"),
            {"id": execs[0].id},
        )

    # /stop arrives as an ordinary-looking slack message (no mention needed —
    # the command plane runs before matching).
    stop_body = _mention_payload(_ts_str(19), "/stop")
    resp = await _post_inbound(api_client, world, stop_body, _now_ts())
    assert resp.status_code == 200
    assert resp.json()["process_status"] == "processed"

    async def _cancelling():
        items = await _items(session_factory, world["ws_id"])
        return items if items[0].state == "cancelling" else None

    items = await poll_until(_cancelling, timeout=60)
    assert items is not None, "M1 never entered cancelling"
    # /stop two-target semantics (§3.7): the initiator's pending item cancels
    # immediately, the in-flight item keeps the lane until terminal.
    assert items[1].state == "cancelled"
    execs = await _executions(session_factory, world["ws_id"])
    assert len(execs) == 1  # the cancelled pending never dispatched
    assert execs[0].status == "cancelling"
    assert execs[0].cancel_requested_at is not None  # durable cancel intent

    # While cancelling, nothing new dispatches (lane held); after the graceful
    # stop reports terminal, the item flips cancelled.
    await _emit_finished(session_factory, world["ws_id"], execs[0].id, "cancelled")

    async def _terminal():
        items = await _items(session_factory, world["ws_id"])
        return items if items[0].state == "cancelled" else None

    items = await poll_until(_terminal, timeout=60)
    assert items is not None, "cancelling item never reached cancelled"
    # terminal-stage feedback written by the finished handler (im.send outbox)
    async with session_factory() as session:
        from mesh.db.models.outbox import OutboxEvent

        sends = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == uuid.UUID(world["ws_id"]),
                        OutboxEvent.event_type == "im.send",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(
        e.payload.get("stage") == "stopped" for e in sends
    ), "terminal-stage stopped feedback never written"


# ---------------------------------------------------------------------------
# /btw append through the real inbound endpoint
# ---------------------------------------------------------------------------


async def test_btw_appends_context_row(api_client, queue_worker, session_factory):
    world = await _make_serial_slack_world(api_client, "btw")
    await _map_sender(session_factory, world)
    resp = await _post_inbound(
        api_client, world, _mention_payload(_ts_str(20), "<@U_BOT> btw-task"), _now_ts()
    )
    assert resp.status_code == 200

    async def _processing():
        items = await _items(session_factory, world["ws_id"])
        return items if items and items[0].state in ("dispatching", "processing") else None

    items = await poll_until(_processing, timeout=60)
    assert items is not None

    resp = await _post_inbound(
        api_client,
        world,
        _mention_payload(_ts_str(21), "/btw use the staging environment"),
        _now_ts(),
    )
    assert resp.status_code == 200
    assert resp.json()["process_status"] == "processed"

    async def _append_row():
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ExecutionContextAppend).where(
                            ExecutionContextAppend.workspace_id == uuid.UUID(world["ws_id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
            return list(rows) if rows else None

    rows = await poll_until(_append_row, timeout=60)
    assert rows is not None, "/btw never wrote an append row"
    assert rows[0].source == "im_btw"
    assert rows[0].seq == 1
    assert "staging" in rows[0].payload["text"]
    # the btw message itself never enqueued (command plane consumed it)
    items = await _items(session_factory, world["ws_id"])
    assert len(items) == 1


# ---------------------------------------------------------------------------
# Queue HTTP endpoints (real server)
# ---------------------------------------------------------------------------


async def test_queue_endpoints_list_summary_cancel(api_client, queue_worker, session_factory):
    world = await _make_serial_slack_world(api_client, "api")
    for i in range(2):
        await _post_inbound(
            api_client, world, _mention_payload(_ts_str(30 + i), f"<@U_BOT> api {i}"),
            _now_ts(),
        )
        await asyncio.sleep(0.05)

    async def _m1_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if len(items) == 2 and items[0].state == "processing" else None

    # The wait result is ASSERTED, not discarded: a silent timeout would
    # otherwise cascade into the endpoint assertions below with both items
    # still pending — a misleading "wrong excerpt" failure instead of the
    # true root point (the serial dispatch never happened).
    #
    # Window rationale (60s): a healthy dispatch lands in ~2-4s (0.2s tick
    # + dispatcher sweep + 0.2s relay claim + bind, measured); the cap is
    # ~15x the healthy time, so reaching it means a TRANSIENT infrastructure
    # stall (observed: worker QueuePool checkout blocked 30s under
    # table-lock contention on a saturated runner — the per-test TRUNCATE
    # vs. worker transactions interleave), never a healthy slow path. The
    # stall self-resolves (pool timeout → retry → chain completes), so a
    # poll-through-stall wait is correct; the forensics snapshot makes a
    # cap hit self-locating (which chain stage is stuck).
    items = await poll_until(_m1_processing, timeout=60)
    assert items is not None, (
        "serial dispatcher never moved the first item to processing within "
        "60s — the endpoint assertions below require the {processing, "
        "pending} pair this wait establishes; forensics: "
        + await _dispatch_forensics(session_factory, world["ws_id"])
    )
    ws = world["ws_id"]
    integ = world["integration_id"]
    headers = _auth(world["token"])

    listing = (
        await api_client.get(f"/api/v1/workspaces/{ws}/integrations/{integ}/queue", headers=headers)
    ).json()["data"]
    assert len(listing) == 2
    by_state = {row["state"]: row for row in listing}
    pending_row = by_state["pending"]
    assert pending_row["position"] == 1
    assert pending_row["target_agent"]["id"] == world["agent_id"]
    assert "api 1" in pending_row["message_excerpt"]
    assert "conversation_key" in pending_row

    summary = (
        await api_client.get(
            f"/api/v1/workspaces/{ws}/integrations/{integ}/queue/summary", headers=headers
        )
    ).json()["data"]
    assert len(summary) == 1
    assert summary[0]["pending_count"] == 1
    assert len(summary[0]["in_flight"]) == 1

    # cancel the pending item (requester is admin → integration:manage)
    cancel = await api_client.post(
        f"/api/v1/workspaces/{ws}/integrations/{integ}/queue/{pending_row['id']}:cancel",
        headers=headers,
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["data"]["state"] == "cancelled"

    # cancelling a non-pending item → 422
    processing_row = by_state["processing"]
    again = await api_client.post(
        f"/api/v1/workspaces/{ws}/integrations/{integ}/queue/{processing_row['id']}:cancel",
        headers=headers,
    )
    assert again.status_code == 422
    assert again.json()["error"]["code"] == "queue_item_not_cancellable"

    # audit endpoint: no orphans yet → empty
    audit = (
        await api_client.get(f"/api/v1/workspaces/{ws}/integration-queue-audit", headers=headers)
    ).json()["data"]
    assert audit == []


# ---------------------------------------------------------------------------
# Delete protection + orphan audit (real server)
# ---------------------------------------------------------------------------


async def test_delete_protection_force_orphans(api_client, queue_worker, session_factory):
    world = await _make_serial_slack_world(api_client, "del")
    for i in range(2):
        await _post_inbound(
            api_client, world, _mention_payload(_ts_str(40 + i), f"<@U_BOT> del {i}"),
            _now_ts(),
        )
        await asyncio.sleep(0.05)

    async def _m1_processing():
        items = await _items(session_factory, world["ws_id"])
        return items if len(items) == 2 and items[0].state == "processing" else None

    # Asserted, not discarded (same rationale as the endpoints test above:
    # the delete-protection flow below requires the {processing, pending}
    # pair; a silent timeout would fail at the 409/force assertions with a
    # misleading shape).
    items = await poll_until(_m1_processing, timeout=60)
    assert items is not None, (
        "serial dispatcher never moved the first item to processing within "
        "60s; forensics: "
        + await _dispatch_forensics(session_factory, world["ws_id"])
    )
    headers = _auth(world["token"])
    ws = world["ws_id"]

    # Non-terminal items block deletion without force (§3.9).
    resp = await api_client.delete(
        f"/api/v1/workspaces/{ws}/integration-bindings/{world['binding_id']}", headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "binding_has_active_queue"

    # Forced path: terminate everything, then the DELETE succeeds and the
    # items survive as self-describing terminal orphans.
    resp = await api_client.delete(
        f"/api/v1/workspaces/{ws}/integration-bindings/{world['binding_id']}?force=cancel",
        headers=headers,
    )
    assert resp.status_code == 204, resp.text

    async def _orphans():
        items = await _items(session_factory, world["ws_id"])
        return items if items and all(i.binding_id is None for i in items) else None

    items = await poll_until(_orphans, timeout=60)
    assert items is not None, "items never became orphans after forced delete"
    assert all(i.state in ("done", "failed", "cancelled") for i in items)
    assert all(i.binding_display for i in items)  # self-describing snapshot

    # Orphans invisible on the normal queue endpoint, visible on audit.
    integ = world["integration_id"]
    listing = (
        await api_client.get(f"/api/v1/workspaces/{ws}/integrations/{integ}/queue", headers=headers)
    ).json()["data"]
    assert listing == []
    audit = (
        await api_client.get(f"/api/v1/workspaces/{ws}/integration-queue-audit", headers=headers)
    ).json()["data"]
    assert len(audit) == 2
    assert all(row["binding_display"] for row in audit)  # self-describing
    assert all(row["state"] in ("done", "failed", "cancelled") for row in audit)

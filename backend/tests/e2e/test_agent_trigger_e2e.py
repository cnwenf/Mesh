"""Dispatch-trigger e2e — assign → outbox → relay → execution.enqueue.

Real server + real API calls + real relay + real PostgreSQL (README §9 T5
shape, agent.md §3.3 / §5.1, README §6.5 / §6.9 / §6.11):

* assigning an issue to an agent emits ``issue.assigned`` in the business
  transaction; the relay's unified orchestration entry writes
  ``execution.enqueue`` with the §6.5 idempotency key and the §6.11
  reproducible snapshot, without broadcasting a placeholder
  ``execution.queued`` before the runtime materializes an execution id;
* §6.9 matrix lines: same-assignee re-select = no-op, unchanged save =
  no-op, reassignment = supersede(cancel) + enqueue, paused/
  trigger_on_assign=false → ``agent.trigger_skipped`` and no enqueue;
* redelivery idempotence: re-running the relay never duplicates the
  execution.enqueue row (§6.5).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from mesh.agent.guardrails import ENQUEUE_EVENT_TYPE
from mesh.agent.triggers import assign_orchestration_handler
from mesh.db.models.outbox import OUTBOX_STATUS_PENDING, OutboxEvent
from mesh.db.models.realtime import RealtimeEvent
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.issue.triggers import ASSIGN_EVENT_TYPE
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "E2E"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Trigger E2E", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_agent(client, token: str, ws_id: str, name: str = "小测", **overrides) -> dict:
    body = {
        "name": name,
        "system_instructions": "你是测试工程师。",
        "model_config": {"model_tier": "balanced", "temperature": 0.2},
    }
    body.update(overrides)
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token: str, ws_id: str, assignee_id: str | None = None) -> dict:
    body: dict = {"title": "修复登录态丢失"}
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _build_relay(session_factory) -> OutboxRelay:
    """The production handler set (workers/main.py::build_relay)."""
    return OutboxRelay(
        session_factory,
        handlers={
            REALTIME_PUBLISH: project_realtime_event,
            ASSIGN_EVENT_TYPE: assign_orchestration_handler,
        },
        poll_interval=0.05,
    )


async def _drain(relay: OutboxRelay, passes: int = 2) -> None:
    """Run the relay enough passes for handler-written realtime.publish rows
    to be projected (production polls continuously; tests drain explicitly).
    Pass 1: issue.assigned → handler → realtime.publish rows.
    Pass 2: projector → realtime_events.
    """
    for _ in range(passes):
        await relay.run_once()


async def _assign_events(session_factory, ws_id: str) -> list:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == uuid.UUID(ws_id),
                        OutboxEvent.event_type == ASSIGN_EVENT_TYPE,
                    )
                )
            )
            .scalars()
            .all()
        )
    return rows


async def _enqueue_events(session_factory, ws_id: str) -> list:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == uuid.UUID(ws_id),
                        OutboxEvent.event_type == ENQUEUE_EVENT_TYPE,
                    )
                )
            )
            .scalars()
            .all()
        )
    return rows


async def _realtime_rows(session_factory, event: str) -> list:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(RealtimeEvent).where(RealtimeEvent.event == event)
                )
            )
            .scalars()
            .all()
        )
    return rows


# --- the full chain ------------------------------------------------------------------


async def test_assign_to_agent_enqueues_execution_through_relay(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-1")
    agent = await _create_agent(api_client, owner, ws["id"])
    member_id = agent["member"]["id"]

    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=member_id)

    # Business transaction committed issue.assigned (same-tx outbox, §6.6).
    assigned = await _assign_events(session_factory, ws["id"])
    assert len(assigned) == 1
    assert assigned[0].payload["action"] == "enqueue"
    assert assigned[0].payload["agent_id"] == agent["id"]
    trigger_event_id = assigned[0].payload["trigger_event_id"]

    # Assignment relay dispatches → execution.enqueue only. The runtime
    # consumer publishes execution.queued after materializing its final id.
    relay = _build_relay(session_factory)
    await _drain(relay)

    enqueues = await _enqueue_events(session_factory, ws["id"])
    assert len(enqueues) == 1
    payload = enqueues[0].payload
    assert payload["intent"] == "enqueue"
    assert payload["agent_id"] == agent["id"]
    assert payload["issue_id"] == issue["id"]

    # §6.5 idempotency key: sha256(agent_id | issue_id | trigger_event_id).
    expected = hashlib.sha256(
        f"{agent['id']}|{issue['id']}|{trigger_event_id}".encode()
    ).hexdigest()
    assert payload["idempotency_key"] == expected

    # §6.11 snapshot: config version frozen, strict-typed capability fields.
    snapshot = payload["config_snapshot"]
    assert snapshot["agent_config_version_id"] == agent["active_config_version_id"]
    assert snapshot["trigger_event_id"] == trigger_event_id
    assert payload["required_capabilities"] == []
    assert isinstance(payload["required_capabilities"], list)
    assert all(isinstance(c, str) for c in payload["required_capabilities"])
    # §3.3 broker grants frozen into every agent execution (MES-95 task
    # principal surface): exactly the default set mirroring the task-token
    # scopes — nothing wider; squad grants are orchestrator-role-only and
    # absent here (plain assign trigger, no squad_role).
    assert sorted(snapshot["capability_grants"], key=lambda g: g["capability"]) == [
        {"capability": "issue.comment", "permission": "write"},
        {"capability": "issue.read", "permission": "read_only"},
        {"capability": "issue.status", "permission": "write"},
        {"capability": "project.read", "permission": "read_only"},
    ]
    assert isinstance(snapshot["capability_grants"], list)
    # §6.15 untrusted context isolation.
    assert "UNTRUSTED_DATA_BEGIN" in payload["task_spec"]["untrusted_context"]["issue"]["title"]

    # No placeholder frame lacking execution_id may escape this producer.
    assert await _realtime_rows(session_factory, "execution.queued") == []

    # Redelivery (crash-before-published simulation): reset to pending and
    # re-run — the idempotency key prevents a duplicate enqueue (§6.5).
    async with session_factory() as session, session.begin():
        for row in await _assign_events(session_factory, ws["id"]):
            evt = await session.get(OutboxEvent, row.id)
            evt.status = OUTBOX_STATUS_PENDING
            evt.delivery_attempts = 0
    await relay.run_once()
    assert len(await _enqueue_events(session_factory, ws["id"])) == 1


# --- §6.9 matrix lines ------------------------------------------------------------------


async def test_reselect_same_assignee_is_noop(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-same@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-2")
    agent = await _create_agent(api_client, owner, ws["id"])
    member_id = agent["member"]["id"]
    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=member_id)
    assert len(await _assign_events(session_factory, ws["id"])) == 1

    # PATCH the SAME assignee value → empty diff → no event (§6.9 line 2).
    resp = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"assignee_id": member_id},
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert len(await _assign_events(session_factory, ws["id"])) == 1


async def test_unrelated_field_save_does_not_retrigger(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-noop@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-3")
    agent = await _create_agent(api_client, owner, ws["id"])
    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=agent["member"]["id"])

    # Saving with no field change = no-op (§6.9 line 3).
    resp = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "修复登录态丢失"},  # identical title
        headers=_auth(owner),
    )
    assert resp.status_code == 200
    assert len(await _assign_events(session_factory, ws["id"])) == 1


async def test_reassignment_supersedes_then_enqueues(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-swap@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-4")
    agent_a = await _create_agent(api_client, owner, ws["id"], name="A")
    agent_b = await _create_agent(api_client, owner, ws["id"], name="B")
    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=agent_a["member"]["id"])

    resp = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"assignee_id": agent_b["member"]["id"]},
        headers=_auth(owner),
    )
    assert resp.status_code == 200

    actions = sorted(e.payload["action"] for e in await _assign_events(session_factory, ws["id"]))
    assert actions == ["enqueue", "enqueue", "supersede"]

    relay = _build_relay(session_factory)
    await _drain(relay)
    enqueues = await _enqueue_events(session_factory, ws["id"])
    intents = {(e.payload["intent"], e.payload["agent_id"]) for e in enqueues}
    assert ("cancel_in_flight", agent_a["id"]) in intents
    assert ("enqueue", agent_b["id"]) in intents
    cancel = next(e for e in enqueues if e.payload["intent"] == "cancel_in_flight")
    assert cancel.payload["failure_reason"] == "superseded"


async def test_paused_agent_trigger_skipped_event(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-pause@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-5")
    agent = await _create_agent(api_client, owner, ws["id"])
    await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents/{agent['id']}:pause",
        json={"reason": "维护"},
        headers=_auth(owner),
    )
    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=agent["member"]["id"])

    relay = _build_relay(session_factory)
    await _drain(relay)

    assert await _enqueue_events(session_factory, ws["id"]) == []
    skipped = await _realtime_rows(session_factory, "agent.trigger_skipped")
    assert len(skipped) == 1
    assert skipped[0].channel == f"workspace:{ws['id']}:agents"
    assert skipped[0].payload["reason"] == "lifecycle_not_active"
    assert skipped[0].payload["issue_id"] == issue["id"]


async def test_trigger_on_assign_false_does_not_enqueue(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-opt@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-6")
    agent = await _create_agent(api_client, owner, ws["id"], trigger_on_assign=False)
    await _create_issue(api_client, owner, ws["id"], assignee_id=agent["member"]["id"])

    relay = _build_relay(session_factory)
    await _drain(relay)

    assert await _enqueue_events(session_factory, ws["id"]) == []
    skipped = await _realtime_rows(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["reason"] == "trigger_on_assign_disabled"


async def test_human_assignee_never_triggers(api_client, session_factory):
    owner = await _register_and_login(api_client, "trig-human@corp.com")
    ws = await _create_workspace(api_client, owner, "trig-e2e-7")
    # The owner's own member id (human) — resolve via roster.
    roster = await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    owner_member = next(
        m for m in roster.json()["data"] if m["member_type"] == "human"
    )
    await _create_issue(api_client, owner, ws["id"], assignee_id=owner_member["id"])
    assert await _assign_events(session_factory, ws["id"]) == []

"""Shared ingestion core tests (integrations.md §2.10/§3.2/§3.8/§6.9, MES-87).

``ingest_verified_event()`` — the single core behind BOTH receive modes:
dedup, msgtype gate, binding match, IMQ enqueue (imq_seq advisory lock +
lock-order ack_window_at + seq snapshots), ack leader determination,
drain-then-switch dispatch-mode snapshot, parallel optimistic dispatch
(execution.enqueue outbox + queue_item_id), queue_updated invalidation
notices (project-level payload isolation).

Real PostgreSQL; envelopes built by the real DingTalk normalizer.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.integration import (
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.integrations.connectors import VerifiedEnvelope
from mesh.integrations.dingtalk import normalize_message_payload
from mesh.integrations.ingest import (
    IngestResult,
    enqueue_idempotency_key,
    ingest_verified_event,
)
from mesh.integrations.queue_events import IM_SEND_EVENT
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE
from tests.unit.integrations_support import (
    DINGTALK_CONVERSATION_ID,
    NOW,
    dingtalk_message_payload,
    make_binding,
    make_dingtalk_binding,
    seed_dingtalk_world,
    seed_world,
)

pytestmark = pytest.mark.unit


async def _integration(session_factory, world, key="integ_dingtalk") -> Integration:
    async with session_factory() as session:
        return await session.get(Integration, world[key])


def _settings(ack_window_seconds: float = 5.0):
    """Settings stand-in mirroring mesh.config.Settings defaults (the core
    resolves this fallback for callers that pass nothing; the ack window is
    the test knob — §3.8 lock-order window width)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        im_ack_coalesce_window_seconds=ack_window_seconds,
        im_inbound_per_identity_per_min=20,
        im_inbound_per_conversation_per_min=60,
        im_queue_max_pending_per_conversation=50,
        im_inbound_text_max_chars=4000,
        im_dispatch_lease_buffer_seconds=300,
        context_append_max_count=20,
        context_append_max_chars=32000,
    )


async def _run(
    session_factory, world, envelope, *, ack_window_seconds: float = 5.0
) -> IngestResult:
    integration = await _integration(session_factory, world)
    async with session_factory() as session, session.begin():
        return await ingest_verified_event(
            session, integration=integration, envelope=envelope, now=NOW,
            settings=_settings(ack_window_seconds),
        )


def _envelope(**payload_overrides) -> VerifiedEnvelope:
    return normalize_message_payload(
        dingtalk_message_payload(**payload_overrides), max_chars=4000, channel="http"
    )


async def _queue_items(session_factory, world) -> list[IntegrationMessageQueue]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IntegrationMessageQueue).order_by(IntegrationMessageQueue.seq)
            )
        ).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# Serial enqueue (DingTalk default)
# ---------------------------------------------------------------------------


async def test_serial_text_message_enqueues_pending_with_snapshots(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)

    result = await _run(session_factory, world, _envelope(text=" 帮我查下昨晚的报警"))

    assert result.process_status == "dispatched"
    assert result.status_code == 200
    items = await _queue_items(session_factory, world)
    assert len(items) == 1
    item = items[0]
    assert item.state == "pending"  # serial: awaits the dispatcher
    assert item.seq == 1
    assert item.dispatch_mode == "serial_conversation"
    assert item.conversation_key == f"dingtalk:dingcorp0001:{DINGTALK_CONVERSATION_ID}"
    assert item.sender_identity_key == "dingtalk:dingcorp0001:014728255240768602"
    assert item.target_agent_id == world["agent"]
    assert item.message_excerpt == "帮我查下昨晚的报警"  # @-bot space trimmed, ≤120
    assert "dingtalk-main" in item.binding_display
    assert item.integration_event_id == result.event_id
    # serial enqueue writes NO execution.enqueue outbox (dispatcher's job).
    async with session_factory() as session:
        enqueues = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == ENQUEUE_EVENT_TYPE)
            )
        ).scalars().all()
    assert enqueues == []


async def test_seq_increments_per_conversation(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    for i in range(3):
        result = await _run(session_factory, world, _envelope(text=f"任务 {i}"))
        assert result.process_status == "dispatched"
    items = await _queue_items(session_factory, world)
    assert [i.seq for i in items] == [1, 2, 3]
    assert all(i.state == "pending" for i in items)


async def test_dedup_same_msg_id_is_idempotent_200(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    envelope = _envelope(msg_id="msgFIXED000000000000==")

    first = await _run(session_factory, world, envelope)
    second = await _run(session_factory, world, envelope)

    assert first.process_status == "dispatched"
    assert second.process_status == "deduped"
    assert second.status_code == 200
    assert second.deduped is True
    assert len(await _queue_items(session_factory, world)) == 1


async def test_disabled_integration_rejects_distribution(session_factory):
    world = await seed_dingtalk_world(session_factory, status="disabled")
    await make_dingtalk_binding(session_factory, world=world)

    result = await _run(session_factory, world, _envelope())

    assert result.status_code == 401
    assert result.process_status == "rejected"
    assert result.body["error"]["code"] == "integration_disabled"
    assert await _queue_items(session_factory, world) == []
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.process_status == "rejected"
    assert event.signature_status == "valid"


# ---------------------------------------------------------------------------
# msgtype matrix — triggering is text-only (C-1)
# ---------------------------------------------------------------------------


async def test_non_text_message_is_audit_only(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)

    for msgtype in ("richText", "picture"):
        result = await _run(session_factory, world, _envelope(msgtype=msgtype))
        assert result.process_status == "processed"

    assert await _queue_items(session_factory, world) == []
    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
        acks = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT)
            )
        ).scalars().all()
    assert {e.process_status for e in events} == {"processed"}
    assert acks == []  # non-text: no ack


# ---------------------------------------------------------------------------
# Matching outcomes (§6.9 audit-only paths)
# ---------------------------------------------------------------------------


async def test_unmatched_conversation_is_audit_only(session_factory):
    world = await seed_dingtalk_world(session_factory)
    # No binding for the conversation.
    result = await _run(session_factory, world, _envelope())
    assert result.process_status == "received"
    assert await _queue_items(session_factory, world) == []


async def test_binding_without_agent_is_matched_audit_only(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world, bound_agent=False)
    result = await _run(session_factory, world, _envelope())
    assert result.process_status == "matched"
    assert await _queue_items(session_factory, world) == []


async def test_multiple_bindings_suppress_dispatch(session_factory):
    """§5.4: ambiguous routing (more than one matching binding) audits and
    triggers NOTHING. The global ``UNIQUE(provider, tenant, external_ref)``
    makes this unreachable at the DDL level (T29 asserts the 409), so the
    defensive branch is exercised with a detached second binding row."""
    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)

    second = IntegrationBinding(
        id=uuid.uuid4(),
        workspace_id=world["ws"],
        integration_id=uuid.uuid4(),
        provider="dingtalk",
        provider_tenant_key="dingcorp0001",
        external_ref=DINGTALK_CONVERSATION_ID,
        bound_agent_id=world["agent"],
    )
    integration = await _integration(session_factory, world)
    from unittest.mock import patch

    async with session_factory() as session, session.begin():
        with patch(
            "mesh.integrations.ingest.match_bindings",
            return_value=[binding, second],
        ):
            result = await ingest_verified_event(
                session, integration=integration, envelope=_envelope(), now=NOW
            )
    assert result.process_status == "matched"
    assert await _queue_items(session_factory, world) == []


# ---------------------------------------------------------------------------
# Ack leader determination (§3.8)
# ---------------------------------------------------------------------------


async def test_ack_leader_self_references_and_writes_im_send(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    result = await _run(session_factory, world, _envelope())

    item = (await _queue_items(session_factory, world))[0]
    assert item.ack_leader_id == item.id  # leader self-reference

    async with session_factory() as session:
        ack = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT)
            )
        ).scalar_one()
    assert ack.payload["kind"] == "ack"
    assert ack.payload["queue_item_id"] == str(item.id)
    assert ack.payload["template"] == "✅ 已接收，处理中"
    assert ack.payload["position_snapshot"] == 1
    # §6.5 registered key: sha256(queue_item_id | 'ack') (workspace-scoped).
    expected = hashlib.sha256(f"{item.id}|ack".encode()).hexdigest()
    assert ack.idempotency_key == f"ws:{world['ws']}:{expected}"
    assert result.queue_item_id == item.id


async def test_ack_window_follower_suppression_single_im_send(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)

    r1 = await _run(session_factory, world, _envelope(text="M1"))
    r2 = await _run(session_factory, world, _envelope(text="M2"))
    r3 = await _run(session_factory, world, _envelope(text="M3"))
    assert {r1.process_status, r2.process_status, r3.process_status} == {"dispatched"}

    items = await _queue_items(session_factory, world)
    leader, f1, f2 = items
    assert leader.ack_leader_id == leader.id
    assert f1.ack_leader_id == leader.id
    assert f2.ack_leader_id == leader.id

    async with session_factory() as session:
        acks = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT)
            )
        ).scalars().all()
    assert len(acks) == 1  # followers write NO im.send event


async def test_ack_outside_window_starts_new_leader(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    # Zero-width window → every item is its own leader.
    await _run(session_factory, world, _envelope(text="M1"), ack_window_seconds=0.0)
    await _run(session_factory, world, _envelope(text="M2"), ack_window_seconds=0.0)
    items = await _queue_items(session_factory, world)
    assert all(i.ack_leader_id == i.id for i in items)
    async with session_factory() as session:
        acks = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT)
            )
        ).scalars().all()
    assert len(acks) == 2


async def test_ack_template_empty_disables_ack_entirely(session_factory):
    world = await seed_dingtalk_world(session_factory, ack_template="")
    await make_dingtalk_binding(session_factory, world=world)
    await _run(session_factory, world, _envelope(text="M1"))
    await _run(session_factory, world, _envelope(text="M2"))

    items = await _queue_items(session_factory, world)
    assert all(i.ack_leader_id is None for i in items)  # no window occupancy
    async with session_factory() as session:
        acks = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT)
            )
        ).scalars().all()
    assert acks == []


# ---------------------------------------------------------------------------
# Parallel optimistic dispatch (feishu/slack baseline, §6.9)
# ---------------------------------------------------------------------------


def _slack_envelope(text: str = "<@U_BOT> 看一下", event_ts: str = "1753790400.000100") -> VerifiedEnvelope:
    return VerifiedEnvelope(
        provider="slack",
        provider_tenant_key="T_TEST",
        external_event_id=f"T_TEST:{event_ts}",
        event_type="message",
        external_ref="C_ONCALL",
        conversation_type=None,
        sender_key="U_HUMAN",
        text=text,
        truncated=False,
        msgtype="",
        raw_payload={"text": text},
        channel="http",
        is_direct_message=False,
        bot_mentioned=True,
    )


async def test_parallel_provider_dispatches_optimistically(session_factory):
    world = await seed_world(session_factory)  # slack integration, no inbound_queue
    await make_binding(session_factory, world=world, provider="slack", external_ref="C_ONCALL")

    integration = await _integration(session_factory, world, "integ_slack")
    async with session_factory() as session, session.begin():
        result = await ingest_verified_event(
            session, integration=integration, envelope=_slack_envelope(), now=NOW
        )
    assert result.process_status == "dispatched"

    items = await _queue_items(session_factory, world)
    assert len(items) == 1
    item = items[0]
    assert item.dispatch_mode == "parallel"
    assert item.state == "dispatching"  # optimistic direct dispatch
    assert item.lease_expires_at is not None

    async with session_factory() as session:
        enqueue = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == ENQUEUE_EVENT_TYPE)
            )
        ).scalar_one()
    payload = enqueue.payload
    assert payload["trigger"] == "integration"
    assert payload["queue_item_id"] == str(item.id)  # R5-2 additional contract
    assert payload["idempotency_key"] == enqueue_idempotency_key(
        agent_id=world["agent"], binding_id=items[0].binding_id,
        external_event_id="T_TEST:1753790400.000100",
    )


async def test_drain_then_switch_forces_serial_behind_live_serial_lane(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_PAR"
    )

    # A non-terminal serial item occupies the conversation lane (carrying its
    # parent refs — ck_imq_orphan_terminal admits only TERMINAL orphans).
    async with session_factory() as session, session.begin():
        session.add(IntegrationMessageQueue(
            workspace_id=world["ws"], integration_id=world["integ_slack"],
            binding_id=binding.id,
            conversation_key="slack:T_TEST:C_PAR", seq=1,
            dispatch_mode="serial_conversation", state="processing",
            sender_identity_key="slack:T_TEST:U_OTHER",
        ))
    integration = await _integration(session_factory, world, "integ_slack")
    envelope = VerifiedEnvelope(
        provider="slack", provider_tenant_key="T_TEST",
        external_event_id="T_TEST:1753790401.000200", event_type="message",
        external_ref="C_PAR", conversation_type=None, sender_key="U_HUMAN",
        text="新消息", truncated=False, msgtype="", raw_payload={},
        channel="http", bot_mentioned=True,
    )
    async with session_factory() as session, session.begin():
        result = await ingest_verified_event(
            session, integration=integration, envelope=envelope, now=NOW
        )
    assert result.process_status == "dispatched"
    items = await _queue_items(session_factory, world)
    new_item = next(i for i in items if i.seq == 2)
    # Drain-then-switch: the new item snapshots SERIAL (lane not empty).
    assert new_item.dispatch_mode == "serial_conversation"
    assert new_item.state == "pending"  # no optimistic dispatch behind a serial lane
    async with session_factory() as session:
        enqueues = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == ENQUEUE_EVENT_TYPE)
            )
        ).scalars().all()
    assert enqueues == []


# ---------------------------------------------------------------------------
# queue_updated invalidation notices (§3.6/§3.9)
# ---------------------------------------------------------------------------


async def _queue_updated_payloads(session_factory, world) -> list[dict]:
    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "realtime.publish",
                    OutboxEvent.payload["event"].astext == "integration.queue_updated",
                )
            )
        ).scalars().all()
    return [e.payload["data"] for e in events]


async def test_queue_updated_workspace_scope_carries_conversation_key(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    await _run(session_factory, world, _envelope())

    payloads = await _queue_updated_payloads(session_factory, world)
    assert len(payloads) == 1
    assert payloads[0]["conversation_key"] == f"dingtalk:dingcorp0001:{DINGTALK_CONVERSATION_ID}"
    assert payloads[0]["subject"] == "queue_updated"
    assert "scope" not in payloads[0]


async def test_queue_updated_project_scope_hides_conversation_key(session_factory):
    world = await seed_dingtalk_world(session_factory)
    project_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Project(
            id=project_id, workspace_id=world["ws"], name="INFRA", key="INFRA",
        ))
    await make_dingtalk_binding(
        session_factory, world=world, scope="project", project_id=project_id
    )
    await _run(session_factory, world, _envelope())

    payloads = await _queue_updated_payloads(session_factory, world)
    assert len(payloads) == 1
    assert "conversation_key" not in payloads[0]  # project isolation
    assert payloads[0]["scope"] == "project"
    assert payloads[0]["integration_id"] == str(world["integ_dingtalk"])
    # The queue row still carries the project snapshot for orphan auditing.
    item = (await _queue_items(session_factory, world))[0]
    assert item.project_id_snapshot == project_id


# ---------------------------------------------------------------------------
# Channel provenance + truncation audit
# ---------------------------------------------------------------------------


async def test_stream_channel_provenance_marked_in_ledger(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    envelope = normalize_message_payload(
        dingtalk_message_payload(), max_chars=4000, channel="stream"
    )
    await _run(session_factory, world, envelope)
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.payload["_mesh_channel"] == "stream"
    assert event.signature_status == "valid"


async def test_truncated_text_flagged_in_ledger(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    envelope = normalize_message_payload(
        dingtalk_message_payload(text="长" * 5000), max_chars=4000, channel="http"
    )
    assert envelope.truncated is True
    await _run(session_factory, world, envelope)
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.payload["truncated"] is True


# ---------------------------------------------------------------------------
# Command plane through the shared core (§3.7 — MES-88 commands module)
# ---------------------------------------------------------------------------


async def test_unknown_command_is_processed_audited_and_never_queued(session_factory):
    """§3.7:945/975 — unregistered /xxx via the unified core: bot help
    feedback (im.send command_feedback) + _mesh_command audit four-tuple
    {name, actor_identity_key, target_item_ids, result}; commands never
    queue and never trigger (probing defense)."""
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    result = await _run(session_factory, world, _envelope(text="/frobnicate args"))
    assert result.process_status == "processed"

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        feedback = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == IM_SEND_EVENT,
                    OutboxEvent.payload["kind"].astext == "command_feedback",
                )
            )
        ).scalars().all()
    audit = event.payload["_mesh_command"]
    assert audit["name"] == "frobnicate"
    assert audit["actor_identity_key"] == "014728255240768602"
    assert audit["target_item_ids"] == []
    assert audit["result"] == "unknown_command"
    assert len(feedback) == 1  # help text scheduled
    assert "/stop" in feedback[0].payload["text"]
    assert await _queue_items(session_factory, world) == []  # never queued


async def test_mid_text_slash_is_not_a_command(session_factory):
    """/stop mid-text is ordinary content → normal dispatch path."""
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    result = await _run(
        session_factory, world, _envelope(text="please /stop by the office")
    )
    assert result.process_status == "dispatched"


# ---------------------------------------------------------------------------
# Acceptance-round fix: M6 (MESH_IM_INBOUND_TEXT_MAX_CHARS wired on HTTP)
# ---------------------------------------------------------------------------


async def test_http_text_truncation_honors_configured_max_chars(session_factory):
    """M6: process_inbound honors the injected text ceiling (settings
    MESH_IM_INBOUND_TEXT_MAX_CHARS at the route) — the HTTP path no longer
    hardcodes 4000, matching the Stream path."""
    from mesh.integrations.inbound import process_inbound
    from tests.unit.integrations_support import TEST_SIGNING_SECRET, dingtalk_request

    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload(text="x" * 100))

    async with session_factory() as session, session.begin():
        status, resp = await process_inbound(
            session, kind="im_dingtalk", raw_body=body, headers=headers,
            signing_secret=TEST_SIGNING_SECRET, now=NOW,
            tolerance=__import__("datetime").timedelta(seconds=300),
            text_max_chars=10,  # configured ceiling, far below the 4000 default
        )
    assert status == 200
    assert resp["process_status"] == "dispatched"

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        item = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
    assert event.payload["truncated"] is True
    assert len(item.message_excerpt) == 10  # truncated to the CONFIGURED ceiling

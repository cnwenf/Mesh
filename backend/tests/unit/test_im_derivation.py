"""notification.fanout → IM delivery derivation tests (§3.3 / §3.10)."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select

from mesh.db.models.integration import (
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.integrations.im_outbound import (
    IM_SEND_EVENT_TYPE,
    chunk_idempotency_key,
    derive_im_deliveries_from_fanout,
)
from mesh.outbox.service import emit_event
from mesh.workers.main import _fanout_with_im_derivation
from tests.unit.integrations_support import TEST_SIGNING_SECRET, encrypt, seed_world

CORP = "dingcorpDERIVE"
CONV_KEY = "dingtalk:dingcorpDERIVE:cidDERIVE=="


async def _world(session_factory, *, verbosity: str = "final_only") -> dict:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        integration = Integration(
            id=uuid.uuid4(), workspace_id=world["ws"], kind="im_dingtalk",
            name="dt-derive",
            config={"app_key": "k", "corp_id": CORP, "robot_code": "r",
                    "verbosity": verbosity},
            secret_ref=encrypt("derive-secret"),
            created_by=world["member"],
        )
        session.add(integration)
        await session.flush()
        binding = IntegrationBinding(
            workspace_id=world["ws"], integration_id=integration.id,
            provider="dingtalk", provider_tenant_key=CORP,
            scope="workspace", external_ref="cidDERIVE==",
            match_config={}, bound_agent_id=world["agent"], status="active",
        )
        session.add(binding)
        await session.flush()
        execution = TaskExecution(
            workspace_id=world["ws"], agent_id=world["agent"],
            trigger="integration", status="failed",
            failure_reason="timeout", task_spec={},
        )
        session.add(execution)
        await session.flush()
        event_row = IntegrationEvent(
            workspace_id=world["ws"], integration_id=integration.id,
            external_event_id=f"msg-{uuid.uuid4().hex}",
            event_type="im.message.receive_v1",
            payload={"conversationType": "2"},
            signature_status="valid", process_status="processed",
        )
        session.add(event_row)
        await session.flush()
        item = IntegrationMessageQueue(
            workspace_id=world["ws"], integration_id=integration.id,
            binding_id=binding.id, integration_event_id=event_row.id,
            conversation_key=CONV_KEY, seq=1,
            dispatch_mode="serial_conversation", state="processing",
            execution_id=execution.id,
            sender_identity_key=f"dingtalk:{CORP}:staffDERIVE",
        )
        session.add(item)
    world.update({
        "integ": integration.id, "binding": binding.id,
        "execution": execution.id, "item": item.id,
    })
    return world


async def _notify(session_factory, world, ntype: str = "execution_finished") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type=ntype if ntype != "progress_custom" else "status_changed",
            priority="normal", execution_id=world["execution"],
        )
        session.add(notification)
        await session.flush()
    return notification.id


async def _fanout_event(session_factory, world, payload: dict):
    async with session_factory() as session, session.begin():
        return await emit_event(
            session, workspace_id=world["ws"],
            event_type="notification.fanout", payload=payload,
            idempotency_key=f"fanout-{uuid.uuid4().hex}",
        )


async def _deliveries(session_factory) -> list[NotificationDelivery]:
    async with session_factory() as session:
        rows = (await session.execute(
            select(NotificationDelivery).where(NotificationDelivery.channel == "im")
        )).scalars().all()
    return list(rows)


async def _im_events(session_factory):
    async with session_factory() as session:
        rows = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalars().all()
    return list(rows)


async def test_execution_finished_failure_derives_text_delivery(session_factory):
    world = await _world(session_factory)
    notification_id = await _notify(session_factory, world)
    event = await _fanout_event(session_factory, world, {
        "kind": "execution_finished", "execution_id": str(world["execution"]),
        "status": "failed", "failure_reason": "timeout",
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    deliveries = await _deliveries(session_factory)
    assert len(deliveries) == 1
    row = deliveries[0]
    assert row.provider == "dingtalk"
    assert row.destination_key == f"dingtalk:{world['binding']}:cidDERIVE=="
    assert row.state == "pending"
    meta = json.loads(row.external_target)
    assert meta["chunks_total"] == 1 and meta["conversation_type"] == "group"
    events = await _im_events(session_factory)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["kind"] == "notification"
    assert "执行失败" in payload["text"] and "timeout" in payload["text"]
    expected_key = chunk_idempotency_key(notification_id, 0)
    assert events[0].idempotency_key.endswith(expected_key)


async def test_verbosity_final_only_drops_progress_types(session_factory):
    world = await _world(session_factory, verbosity="final_only")
    await _notify(session_factory, world, ntype="progress_custom")
    event = await _fanout_event(session_factory, world, {
        "type": "status_changed", "execution_id": str(world["execution"]),
        "body": "进度: 50%",
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    assert await _deliveries(session_factory) == []
    assert await _im_events(session_factory) == []


async def test_verbosity_progress_pushes_progress_types(session_factory):
    world = await _world(session_factory, verbosity="progress")
    await _notify(session_factory, world, ntype="progress_custom")
    event = await _fanout_event(session_factory, world, {
        "type": "status_changed", "execution_id": str(world["execution"]),
        "body": "进度: 50%",
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    assert len(await _deliveries(session_factory)) == 1


async def test_non_integration_trigger_not_derived(session_factory):
    world = await _world(session_factory)
    async with session_factory() as session, session.begin():
        execution = await session.get(TaskExecution, world["execution"])
        execution.trigger = "mention"
    await _notify(session_factory, world)
    event = await _fanout_event(session_factory, world, {
        "kind": "execution_finished", "execution_id": str(world["execution"]),
        "status": "failed",
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    assert await _deliveries(session_factory) == []


async def test_comment_created_long_body_chunked(session_factory):
    world = await _world(session_factory)
    notification_id = await _notify(session_factory, world, ntype="comment_created")
    long_body = ("段落内容" * 2000 + "\n\n") * 6  # > 15000B → multiple chunks
    event = await _fanout_event(session_factory, world, {
        "type": "comment_created", "execution_id": str(world["execution"]),
        "body": long_body,
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    events = await _im_events(session_factory)
    assert len(events) >= 2
    for index, evt in enumerate(sorted(events, key=lambda e: e.payload["chunk_index"])):
        assert evt.payload["kind"] == "notification"
        assert evt.idempotency_key.endswith(chunk_idempotency_key(notification_id, index))
    deliveries = await _deliveries(session_factory)
    assert json.loads(deliveries[0].external_target)["chunks_total"] == len(events)


async def test_comment_created_honors_configured_max_chunks(session_factory):
    world = await _world(session_factory)
    await _notify(session_factory, world, ntype="comment_created")
    long_body = ("段落内容" * 2000 + "\n\n") * 6
    event = await _fanout_event(session_factory, world, {
        "type": "comment_created", "execution_id": str(world["execution"]),
        "body": long_body,
    })

    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event, max_chunks=2)

    events = await _im_events(session_factory)
    assert len(events) == 2
    delivery = (await _deliveries(session_factory))[0]
    assert json.loads(delivery.external_target)["chunks_total"] == 2


async def test_fanout_wrapper_forwards_configured_max_chunks(monkeypatch):
    captured: dict[str, int] = {}

    class _BaseHandler:
        async def handle(self, session, event):
            return None

    class _NestedTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _Session:
        def begin_nested(self):
            return _NestedTransaction()

    async def _derive(session, event, *, max_chunks):
        captured["max_chunks"] = max_chunks

    monkeypatch.setattr(
        "mesh.integrations.im_outbound.derive_im_deliveries_from_fanout",
        _derive,
    )
    handler = _fanout_with_im_derivation(_BaseHandler(), max_chunks=3)

    await handler(_Session(), object())

    assert captured == {"max_chunks": 3}


async def test_review_requested_derives_card_event(session_factory):
    world = await _world(session_factory)
    await _notify(session_factory, world, ntype="review_requested")
    approval_id = uuid.uuid4()
    event = await _fanout_event(session_factory, world, {
        "type": "review_requested", "execution_id": str(world["execution"]),
        "approval_id": str(approval_id),
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    events = await _im_events(session_factory)
    assert len(events) == 1
    assert events[0].payload["kind"] == "card"
    assert events[0].payload["approval_id"] == str(approval_id)
    expected = hashlib.sha256(f"{approval_id}|card".encode()).hexdigest()
    assert events[0].idempotency_key.endswith(expected)
    meta = json.loads((await _deliveries(session_factory))[0].external_target)
    assert meta["card"] is True


async def test_duplicate_fanout_derivation_is_idempotent(session_factory):
    world = await _world(session_factory)
    await _notify(session_factory, world)
    event = await _fanout_event(session_factory, world, {
        "kind": "execution_finished", "execution_id": str(world["execution"]),
        "status": "failed",
    })
    for _ in range(2):
        async with session_factory() as session, session.begin():
            await derive_im_deliveries_from_fanout(session, event)
    assert len(await _deliveries(session_factory)) == 1
    assert len(await _im_events(session_factory)) == 1  # events not re-emitted


async def test_direct_conversation_type_from_ingested_event(session_factory):
    world = await _world(session_factory)
    async with session_factory() as session, session.begin():
        item = await session.get(IntegrationMessageQueue, world["item"])
        event_row = await session.get(IntegrationEvent, item.integration_event_id)
        event_row.payload = {"conversationType": "1"}
    await _notify(session_factory, world)
    event = await _fanout_event(session_factory, world, {
        "kind": "execution_finished", "execution_id": str(world["execution"]),
        "status": "failed",
    })
    async with session_factory() as session, session.begin():
        await derive_im_deliveries_from_fanout(session, event)
    meta = json.loads((await _deliveries(session_factory))[0].external_target)
    assert meta["conversation_type"] == "direct"
    assert meta["sender_key"] == "staffDERIVE"


async def test_redaction_blacklist_includes_integration_secrets(session_factory):
    from mesh.runtime.credentials import load_redaction_blacklist

    world = await _world(session_factory)
    async with session_factory() as session:
        values = await load_redaction_blacklist(session, world["ws"], TEST_SIGNING_SECRET)
    assert "derive-secret" in values

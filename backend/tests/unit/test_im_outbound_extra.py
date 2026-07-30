"""IMSendRelay branch coverage (feedback / card / error paths, §3.8/§3.10)."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select

from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.integrations.dingtalk_cards import push_card_from_event
from mesh.integrations.im_outbound import IM_SEND_EVENT_TYPE, IMSendRelay
from mesh.outbox.service import emit_event
from tests.unit.integrations_dingtalk_support import (
    ScriptedDingTalkTransport,
    make_client,
)
from tests.unit.integrations_support import TEST_SIGNING_SECRET
from tests.unit.test_ack import CONVERSATION_KEY
from tests.unit.test_dingtalk_cards import _make_approval, _seed


async def _ensure_binding(session_factory, world):
    """test_dingtalk_cards._seed has no queue binding — add one so
    enqueue_item satisfies ck_imq_orphan_terminal."""
    if "binding_dingtalk" in world:
        return
    from mesh.db.models.integration import IntegrationBinding

    async with session_factory() as session, session.begin():
        binding = IntegrationBinding(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"],
            provider="dingtalk", provider_tenant_key="dingcorpTEST",
            scope="workspace", external_ref="cid6EUvB2O8qVF2RYQtHTKEsg==",
            match_config={}, bound_agent_id=world["agent"], status="active",
        )
        session.add(binding)
        await session.flush()
    world["binding_dingtalk"] = binding.id



def _relay(session_factory, redis_client, transport, *, card_pusher=None):
    return IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        card_pusher=card_pusher,
        rate_limit_base_seconds=0.05,
    )


async def _emit(session_factory, world, payload, key):
    async with session_factory() as session, session.begin():
        await emit_event(
            session, workspace_id=world["ws"], event_type=IM_SEND_EVENT_TYPE,
            payload=payload, idempotency_key=key,
        )


def _feedback_payload(world, **extra):
    return {
        "kind": "feedback",
        "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "conversation_type": "group",
        "text": "⏳ 正在停止任务…",
        **extra,
    }


async def _event_status(session_factory):
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        rows = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalars().all()
    return [(r.status, r.delivery_attempts) for r in rows]


async def test_feedback_send_publishes(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, _feedback_payload(world), "fb-1")
    assert await _relay(session_factory, redis_client, transport).run_once() == 1
    assert len(transport.group_sends()) == 1
    assert (await _event_status(session_factory))[0][0] == "published"


async def test_feedback_failure_still_publishes_no_retry(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport(send_status=500, send_body={"code": "x"})
    await _emit(session_factory, world, _feedback_payload(world), "fb-2")
    relay = _relay(session_factory, redis_client, transport)
    await relay.run_once()
    await relay.run_once()
    assert len(transport.group_sends()) == 1  # conversational: no retry
    assert (await _event_status(session_factory))[0][0] == "published"


async def test_unknown_kind_is_published(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {**_feedback_payload(world), "kind": "bogus"}, "fb-3")
    await _relay(session_factory, redis_client, transport).run_once()
    assert (await _event_status(session_factory))[0][0] == "published"
    assert transport.group_sends() == []


async def test_card_kind_without_pusher_is_published(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {
        "kind": "card", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY, "conversation_type": "group",
        "approval_id": str(approval.id),
    }, "card-nopusher")
    await _relay(session_factory, redis_client, transport).run_once()
    assert (await _event_status(session_factory))[0][0] == "published"
    assert transport.calls_for("/v1.0/card/instances/createAndDeliver") == []


async def test_card_missing_approval_terminal(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="review_requested", priority="critical",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk", destination_key="dingtalk:b:gone",
            external_target="{}", state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    await _emit(session_factory, world, {
        "kind": "card", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY, "conversation_type": "group",
        "approval_id": str(uuid.uuid4()),  # does not exist
        "delivery_id": str(delivery_id),
    }, "card-gone")
    await _relay(session_factory, redis_client, transport, card_pusher=push_card_from_event).run_once()
    assert (await _event_status(session_factory))[0][0] == "published"
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"


async def test_notification_invalid_credentials_terminal(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport(
        token_status=400, token_body={"code": "invalidAuthentication"}
    )
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="execution_finished", priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk", destination_key="dingtalk:b:c",
            external_target=json.dumps({"chunks_total": 1}), state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    await _emit(session_factory, world, {
        "kind": "notification", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY, "conversation_type": "group",
        "chunk_index": 0, "chunks_total": 1, "text": "result",
        "delivery_id": str(delivery_id),
    }, "notif-badcred")
    relay = _relay(session_factory, redis_client, transport)
    await relay.run_once()
    await relay.run_once()  # terminal: no retry
    assert (await _event_status(session_factory))[0][0] == "published"
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"
        assert row.error == "invalid_credentials"


async def test_notification_no_staff_id_terminal(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="execution_finished", priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk", destination_key="dingtalk:b:d",
            external_target=json.dumps({"chunks_total": 1}), state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    await _emit(session_factory, world, {
        "kind": "notification", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": "dingtalk:dingcorpTEST:direct1",
        "conversation_type": "direct",
        "target_user_key": "x=ZXh0ZXJuYWw",  # external contact
        "chunk_index": 0, "chunks_total": 1, "text": "result",
        "delivery_id": str(delivery_id),
    }, "notif-nostaff")
    await _relay(session_factory, redis_client, transport).run_once()
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"
        assert row.error == "no_staff_id"


async def test_ack_event_with_malformed_item_id_is_published(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {
        "kind": "ack", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "queue_item_id": "not-a-uuid",
    }, "ack-malformed")
    await _relay(session_factory, redis_client, transport).run_once()
    assert (await _event_status(session_factory))[0][0] == "published"
    assert transport.group_sends() == []


async def test_adapter_cached_per_integration(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, _feedback_payload(world), "fb-c1")
    await _emit(session_factory, world, _feedback_payload(world, text="第二条"), "fb-c2")
    relay = _relay(session_factory, redis_client, transport)
    await relay.run_once()
    assert len(transport.group_sends()) == 2
    assert len(relay._adapters) == 1  # single cached adapter


async def test_run_forever_respects_stop(session_factory, redis_client):
    await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    stop = asyncio.Event()
    stop.set()
    relay = _relay(session_factory, redis_client, transport)
    await asyncio.wait_for(relay.run_forever(stop), timeout=5)  # exits at once


async def test_run_forever_idle_poll_until_stop(session_factory, redis_client):
    await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    relay = IMSendRelay(
        session_factory, redis=redis_client, signing_secret=TEST_SIGNING_SECRET,
        http_client=make_client(transport), poll_interval=0.1,
    )
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(0.3, stop.set)
    await asyncio.wait_for(relay.run_forever(stop), timeout=5)


async def test_ack_send_timeout_recorded_as_loss(session_factory, redis_client):
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport(token_delay=1.0)  # slower than ack timeout
    from datetime import UTC, datetime

    from tests.unit.test_ack import enqueue_item

    item, _ = await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=datetime.now(UTC)
    )
    relay = IMSendRelay(
        session_factory, redis=redis_client, signing_secret=TEST_SIGNING_SECRET,
        http_client=make_client(transport), ack_send_timeout=0.2,
    )
    await relay.run_once()
    from mesh.db.models.integration import IntegrationMessageQueue

    async with session_factory() as session:
        row = await session.get(IntegrationMessageQueue, item.id)
        assert row.ack_attempted_at is not None
        assert row.ack_sent_at is None  # lost, never retried


async def test_ack_send_crash_recorded_as_loss(session_factory, redis_client):
    import httpx as _httpx

    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport(token_exc=_httpx.ConnectError("boom"))
    from datetime import UTC, datetime

    from tests.unit.test_ack import enqueue_item

    item, _ = await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=datetime.now(UTC)
    )
    relay = _relay(session_factory, redis_client, transport)
    await relay.run_once()
    from mesh.db.models.integration import IntegrationMessageQueue

    async with session_factory() as session:
        row = await session.get(IntegrationMessageQueue, item.id)
        assert row.ack_attempted_at is not None
        assert row.ack_sent_at is None


async def test_feedback_integration_unavailable_still_published(session_factory, redis_client):
    world = await _seed(session_factory)
    from mesh.db.models.integration import Integration

    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integ_dingtalk"])
        integration.status = "disabled"
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, _feedback_payload(world), "fb-disabled")
    await _relay(session_factory, redis_client, transport).run_once()
    assert (await _event_status(session_factory))[0][0] == "published"
    assert transport.group_sends() == []


async def test_card_upstream_error_exhausts_budget(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport(card_status=500, card_body={"code": "boom"})
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="review_requested", priority="critical",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk", destination_key="dingtalk:b:err",
            external_target="{}", state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    await _emit(session_factory, world, {
        "kind": "card", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY, "conversation_type": "group",
        "approval_id": str(approval.id), "delivery_id": str(delivery_id),
    }, "card-err")
    relay = IMSendRelay(
        session_factory, redis=redis_client, signing_secret=TEST_SIGNING_SECRET,
        http_client=make_client(transport), card_pusher=push_card_from_event,
        max_attempts=2, rate_limit_base_seconds=0.01,
    )
    from datetime import UTC, datetime

    for _ in range(2):
        async with session_factory() as session, session.begin():
            rows = (await session.execute(
                select(OutboxEvent).where(OutboxEvent.status == "pending")
            )).scalars().all()
            for row in rows:
                row.available_at = datetime.now(UTC)
        await relay.run_once()
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"

"""IMSendRelay branch coverage (feedback / card / error paths, §3.8/§3.10)."""

from __future__ import annotations

import asyncio
import functools
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.dingtalk_cards import push_card_from_event
from mesh.integrations.im_outbound import IM_SEND_EVENT_TYPE, IMSendRelay
from mesh.outbox.service import emit_event
from tests.unit.integrations_dingtalk_support import (
    CARD_CREATE_PATH,
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
    await _relay(
        session_factory,
        redis_client,
        transport,
        card_pusher=functools.partial(
            push_card_from_event,
            app_base_url="https://mesh.example.com",
        ),
    ).run_once()
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
        http_client=make_client(transport),
        card_pusher=functools.partial(
            push_card_from_event,
            app_base_url="https://mesh.example.com",
        ),
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


# ---------------------------------------------------------------------------
# §3.10 token_refresh_busy — retryable NON-failure (B: budget untouched,
# never terminal), exercised through the REAL relay + REAL Redis lock
# ---------------------------------------------------------------------------


async def _seed_chunk_delivery(session_factory, world) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="execution_finished", priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk",
            destination_key=f"dingtalk:{world.get('binding_dingtalk', 'b')}:busy",
            external_target=json.dumps({"chunks_total": 1, "sent_chunks": 0}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    return delivery_id


def _busy_relay(session_factory, redis_client, transport, *, card_pusher=None):
    return IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        card_pusher=card_pusher,
        rate_limit_base_seconds=0.05,
        token_follower_wait=0.3,        # fast busy for the test
        token_busy_backoff_seconds=0.2,
    )


async def test_notification_busy_defers_without_budget_then_succeeds(
    session_factory, redis_client
):
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    delivery_id = await _seed_chunk_delivery(session_factory, world)
    await _emit(session_factory, world, {
        "kind": "notification", "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY, "conversation_type": "group",
        "chunk_index": 0, "chunks_total": 1, "text": "busy 结果",
        "delivery_id": str(delivery_id),
    }, "notif-busy")

    # A foreign replica holds the refresh lease → this relay goes busy.
    lock_key = f"dingtalk:token_lock:{world['integ_dingtalk']}"
    await redis_client.set(lock_key, "foreign-owner", nx=True, ex=30)
    relay = _busy_relay(session_factory, redis_client, transport)
    await relay.run_once()

    # Event stays PENDING, budget UNTOUCHED, deferred — not terminal.
    async with session_factory() as session:
        event = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalar_one()
        assert event.status == "pending"
        assert event.delivery_attempts == 0
        assert event.available_at > datetime.now(UTC)
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "pending"
        assert row.error == "token_refresh_busy"  # ledger records the busy try
    assert transport.group_sends() == []

    # Lock released + backoff expired → delivered, budget STILL zero.
    await redis_client.delete(lock_key)
    async with session_factory() as session, session.begin():
        rows = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.status == "pending")
        )).scalars().all()
        for row in rows:
            row.available_at = datetime.now(UTC)
    await relay.run_once()
    async with session_factory() as session:
        event = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalar_one()
        assert event.status == "published"
        assert event.delivery_attempts == 0  # never consumed
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent"
        assert row.error is None  # transient busy trace cleared
    assert len(transport.group_sends()) == 1


async def test_card_busy_defers_without_budget_then_succeeds(
    session_factory, redis_client
):
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    approval = await _make_approval(session_factory, world)
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
            channel="im", provider="dingtalk",
            destination_key=f"dingtalk:{world['binding_dingtalk']}:cardbusy",
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
    }, "card-busy")

    lock_key = f"dingtalk:token_lock:{world['integ_dingtalk']}"
    await redis_client.set(lock_key, "foreign-owner", nx=True, ex=30)
    relay = _busy_relay(
        session_factory,
        redis_client,
        transport,
        card_pusher=functools.partial(
            push_card_from_event,
            app_base_url="https://mesh.example.com",
        ),
    )
    await relay.run_once()
    async with session_factory() as session:
        event = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalar_one()
        assert event.status == "pending"
        assert event.delivery_attempts == 0
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "pending" and row.error == "token_refresh_busy"
    assert transport.calls_for(CARD_CREATE_PATH) == []

    await redis_client.delete(lock_key)
    async with session_factory() as session, session.begin():
        rows = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.status == "pending")
        )).scalars().all()
        for row in rows:
            row.available_at = datetime.now(UTC)
    await relay.run_once()
    async with session_factory() as session:
        event = (await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )).scalar_one()
        assert event.status == "published"
        assert event.delivery_attempts == 0
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent" and row.error is None
    assert len(transport.calls_for(CARD_CREATE_PATH)) == 1


# ---------------------------------------------------------------------------
# MES-88 cross-slice alignment: the relay consumes the queue-plane payload
# shapes emitted by message_queue.py / queue_events.py / inbound.py
# (no workspace_id / conversation_type / target_user_key in the ack and
# command_feedback payloads — derived from the queue item + inbound event)
# ---------------------------------------------------------------------------

STAFF = "014728255240768602"


async def _enqueue_item_with_event(session_factory, world, *, conversation_type: str):
    """Queue item with a source inbound event (MES-88 wiring shape):
    conversationType "1" = direct, "2" = group."""
    from mesh.db.models.integration import IntegrationEvent, IntegrationMessageQueue

    async with session_factory() as session, session.begin():
        event = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            external_event_id=f"msg-{uuid.uuid4().hex}",
            event_type="im.message.receive_v1",
            payload={"conversationType": conversation_type},
            signature_status="valid",
            process_status="dispatched",
        )
        session.add(event)
        await session.flush()
        item = IntegrationMessageQueue(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            binding_id=world["binding_dingtalk"],
            integration_event_id=event.id,
            conversation_key=CONVERSATION_KEY,
            seq=1,
            dispatch_mode="serial_conversation",
            state="pending",
            sender_identity_key=f"dingtalk:dingcorpTEST:{STAFF}",
            ack_window_at=datetime.now(UTC),
        )
        session.add(item)
        await session.flush()
    return item


def _mes88_ack_payload(world, item) -> dict:
    """The exact ack payload message_queue.py emits — no workspace_id /
    conversation_type / target_user_key."""
    return {
        "kind": "ack",
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "queue_item_id": str(item.id),
        "template": "✅ 已接收，处理中",
        "position_snapshot": 1,
    }


async def test_ack_mes88_payload_group_delivery(session_factory, redis_client):
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    item = await _enqueue_item_with_event(session_factory, world, conversation_type="2")
    await _emit(session_factory, world, _mes88_ack_payload(world, item), f"ack88g-{item.id}")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.group_sends()
    assert sent.body["openConversationId"] == "cid6EUvB2O8qVF2RYQtHTKEsg=="
    assert transport.direct_sends() == []
    from mesh.db.models.integration import IntegrationMessageQueue

    async with session_factory() as session:
        row = await session.get(IntegrationMessageQueue, item.id)
        assert row.ack_sent_at is not None  # T2 completed through the full path


async def test_ack_mes88_payload_direct_delivery(session_factory, redis_client):
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    item = await _enqueue_item_with_event(session_factory, world, conversation_type="1")
    await _emit(session_factory, world, _mes88_ack_payload(world, item), f"ack88d-{item.id}")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.direct_sends()
    assert sent.body["userIds"] == [STAFF]  # derived from sender_identity_key
    assert transport.group_sends() == []


async def test_target_derivation_ignores_cross_workspace_source_event(
    session_factory, redis_client
):
    from mesh.db.models.integration import IntegrationMessageQueue

    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    item = await _enqueue_item_with_event(session_factory, world, conversation_type="1")
    mismatched_item = IntegrationMessageQueue(
        workspace_id=uuid.uuid4(),
        integration_event_id=item.integration_event_id,
        sender_identity_key=item.sender_identity_key,
    )
    payload: dict[str, str] = {}
    relay = _relay(session_factory, redis_client, ScriptedDingTalkTransport())

    async with session_factory() as session:
        await relay._fill_target_from_item(session, payload, item=mismatched_item)

    assert payload == {"conversation_type": "group"}


async def test_command_feedback_kind_delivered(session_factory, redis_client):
    """MES-88 /stop//btw two-phase feedback (queue_events.py payload shape,
    no workspace_id — the outbox row carries it)."""
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    item = await _enqueue_item_with_event(session_factory, world, conversation_type="2")
    await _emit(session_factory, world, {
        "kind": "command_feedback",
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "queue_item_id": str(item.id),
        "text": "⏳ 正在停止任务…",
    }, f"cmdfb-{item.id}")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.group_sends()
    assert "停止" in json.loads(sent.body["msgParam"])["content"]
    assert (await _event_status(session_factory))[0][0] == "published"


async def test_rate_limit_hint_kind_delivered(session_factory, redis_client):
    """MES-88 §2.10 one-per-minute rate-limit hint — MES-122 emitter shape:
    the payload is self-specified (no queue item exists for a rejected
    message), carrying the conversation type explicitly; group delivery."""
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {
        "kind": "rate_limit_hint",
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "conversation_type": "group",
        "text": "消息太快了，先歇一下",
    }, "hint-1")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.group_sends()
    assert "歇一下" in json.loads(sent.body["msgParam"])["content"]
    assert transport.direct_sends() == []


async def test_rate_limit_hint_direct_payload_oToMessages_delivery(
    session_factory, redis_client
):
    """MES-122: the single-chat rate-limit hint (inbound.py emitter shape —
    conversation_type=direct + target_user_key, no queue item) delivers via
    oToMessages/batchSend with ZERO group sends."""
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {
        "kind": "rate_limit_hint",
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "conversation_type": "direct",
        "target_user_key": STAFF,
        "text": "消息太快了，先歇一下",
    }, "hint-direct")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.direct_sends()
    assert sent.body["userIds"] == [STAFF]
    assert transport.group_sends() == []
    assert (await _event_status(session_factory))[0][0] == "published"


async def test_command_feedback_immediate_direct_payload_oToMessages_delivery(
    session_factory, redis_client
):
    """MES-122: immediate-stage /stop//btw feedback for a single chat
    (commands.py emitter shape — self-specified, no queue_item_id needed)
    delivers via oToMessages/batchSend with ZERO group sends."""
    world = await _seed(session_factory)
    await _ensure_binding(session_factory, world)
    transport = ScriptedDingTalkTransport()
    await _emit(session_factory, world, {
        "kind": "command_feedback",
        "stage": "immediate",
        "command": "stop",
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "conversation_type": "direct",
        "target_user_key": STAFF,
        "text": "⏳ Stopping task…",
    }, "cmdfb-immediate-direct")
    await _relay(session_factory, redis_client, transport).run_once()
    (sent,) = transport.direct_sends()
    assert sent.body["userIds"] == [STAFF]
    assert transport.group_sends() == []
    assert (await _event_status(session_factory))[0][0] == "published"

"""ack confirmation tests (integrations.md §3.8).

Election semantics are tested against the real ``integration_message_queue``
table; the IMSendRelay T1/T2 protocol runs against a scripted DingTalk
transport with real PostgreSQL + Redis (nothing on the contract path is
mocked).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mesh.db.models.integration import (
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.notification import NotificationDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.ack import (
    DEFAULT_ACK_TEMPLATE,
    IM_SEND_EVENT_TYPE,
    elect_ack_leader,
    position_hint,
)
from mesh.integrations.im_outbound import IMSendRelay
from tests.unit.integrations_dingtalk_support import (
    ScriptedDingTalkTransport,
    make_client,
)
from tests.unit.integrations_support import TEST_SIGNING_SECRET, encrypt, seed_world

T0 = datetime(2026, 7, 30, 10, 0, 0, tzinfo=UTC)
WINDOW = timedelta(seconds=5)
CONVERSATION_KEY = "dingtalk:dingcorpTEST:cid6EUvB2O8qVF2RYQtHTKEsg=="


# ---------------------------------------------------------------------------
# World seeding (dingtalk integration + queue-item factory)
# ---------------------------------------------------------------------------


async def seed_dingtalk(session_factory, *, ack_template: str = DEFAULT_ACK_TEMPLATE) -> dict:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        integration = Integration(
            id=uuid.uuid4(),
            workspace_id=world["ws"],
            kind="im_dingtalk",
            name="dingtalk-main",
            config={
                "app_key": "dingappkey",
                "corp_id": "dingcorpTEST",
                "robot_code": "robot-1",
                "receive_mode": "stream",
                "ack_template": ack_template,
                "inbound_queue": "serial_conversation",
                "verbosity": "final_only",
            },
            secret_ref=encrypt("ding-app-secret-plaintext"),
            created_by=world["member"],
        )
        session.add(integration)
        await session.flush()
        binding = IntegrationBinding(
            workspace_id=world["ws"],
            integration_id=integration.id,
            provider="dingtalk",
            provider_tenant_key="dingcorpTEST",
            scope="workspace",
            external_ref="cid6EUvB2O8qVF2RYQtHTKEsg==",
            match_config={"trigger_on": ["mention"]},
            bound_agent_id=world["agent"],
            status="active",
        )
        session.add(binding)
    world["integ_dingtalk"] = integration.id
    world["binding_dingtalk"] = binding.id
    return world


async def enqueue_item(
    session_factory,
    *,
    world: dict,
    seq: int,
    ack_window_at: datetime,
    conversation_key: str = CONVERSATION_KEY,
    sender_key: str = "014728255240768602",
    state: str = "pending",
    ack_template: str = DEFAULT_ACK_TEMPLATE,
    conversation_type: str = "group",
    enqueued_at: datetime | None = None,
    integration_event_id: uuid.UUID | None = None,
) -> IntegrationMessageQueue:
    """Simulate the MES-88 enqueue transaction's ack step under the
    documented contract (lock held, ack_window_at taken, item flushed).
    ``enqueued_at`` defaults to ``ack_window_at``; pass an explicit value
    to build genuinely crossed enqueue/window orderings (T39-16 shape)."""
    async with session_factory() as session, session.begin():
        item = IntegrationMessageQueue(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            binding_id=world["binding_dingtalk"],
            binding_display="dingtalk-main / 研发群",
            conversation_key=conversation_key,
            seq=seq,
            dispatch_mode="serial_conversation",
            state=state,
            message_excerpt=f"task {seq}",
            sender_identity_key=f"dingtalk:dingcorpTEST:{sender_key}",
            integration_event_id=integration_event_id,
            ack_window_at=ack_window_at,
            enqueued_at=enqueued_at or ack_window_at,
        )
        session.add(item)
        await session.flush()
        is_leader = await elect_ack_leader(
            session,
            item=item,
            ack_template=ack_template,
            coalesce_window=WINDOW,
            conversation_type=conversation_type,
            target_user_key=sender_key if conversation_type == "direct" else "",
        )
    return item, is_leader


async def _load_item(session_factory, item_id: uuid.UUID) -> IntegrationMessageQueue:
    async with session_factory() as session:
        return await session.get(IntegrationMessageQueue, item_id)


async def _im_send_events(session_factory) -> list[OutboxEvent]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
            )
        ).scalars().all()
    return list(rows)


async def _make_delivery(session_factory, world, *, chunks_total: int, destination_key: str):
    """A real notifications row + its pending IM delivery ledger row."""
    from mesh.db.models.notification import Notification

    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"],
            recipient_id=world["member"],
            type="execution_finished",
            priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"],
            notification_id=notification.id,
            channel="im",
            provider="dingtalk",
            destination_key=destination_key,
            external_target=json.dumps({"chunks_total": chunks_total, "sent_chunks": 0}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
    return delivery.id


def _make_relay(session_factory, redis_client, transport: ScriptedDingTalkTransport) -> IMSendRelay:
    return IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        max_attempts=5,
        http_client=make_client(transport),
        rate_limit_base_seconds=0.05,
    )


# ---------------------------------------------------------------------------
# Election semantics (leader / follower / window / template off)
# ---------------------------------------------------------------------------


async def test_first_item_becomes_leader_and_writes_im_send_event(session_factory):
    world = await seed_dingtalk(session_factory)
    item, is_leader = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    assert is_leader is True
    loaded = await _load_item(session_factory, item.id)
    assert loaded.ack_leader_id == loaded.id  # self-reference
    events = await _im_send_events(session_factory)
    assert len(events) == 1
    event = events[0]
    assert event.payload["kind"] == "ack"
    assert event.payload["queue_item_id"] == str(item.id)
    assert event.payload["template"] == DEFAULT_ACK_TEMPLATE
    assert event.payload["workspace_id"] == str(world["ws"])
    import hashlib

    expected_key = hashlib.sha256(f"{item.id}|ack".encode()).hexdigest()
    assert event.idempotency_key.endswith(expected_key)


async def test_second_item_within_window_is_follower_without_event(session_factory):
    world = await seed_dingtalk(session_factory)
    leader, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    follower, is_leader = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=1)
    )
    assert is_leader is False
    loaded = await _load_item(session_factory, follower.id)
    assert loaded.ack_leader_id == leader.id
    assert loaded.ack_leader_id != follower.id
    # followers write NO im.send event — only the leader's exists
    events = await _im_send_events(session_factory)
    assert len(events) == 1
    assert events[0].payload["queue_item_id"] == str(leader.id)


async def test_item_outside_window_becomes_new_leader(session_factory):
    world = await seed_dingtalk(session_factory)
    first, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    second, is_leader = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=5, milliseconds=1)
    )
    assert is_leader is True
    loaded = await _load_item(session_factory, second.id)
    assert loaded.ack_leader_id == second.id
    assert len(await _im_send_events(session_factory)) == 2


async def test_window_boundary_is_exclusive(session_factory):
    """Exactly window after the leader → outside [start, start+window)."""
    world = await seed_dingtalk(session_factory)
    await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    edge, is_leader = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + WINDOW
    )
    assert is_leader is True  # the half-open interval excludes the boundary


async def test_leader_order_follows_lock_ordered_ack_window_at_not_enqueued_at(session_factory):
    """T39-16 shape: an item whose enqueued_at is EARLIER but whose
    ack_window_at (lock-order clock) is later falls into the window of the
    later-enqueued item that took the lock first."""
    world = await seed_dingtalk(session_factory)
    # seq=1 took the lock first (ack_window_at = T0+1s) but was enqueued later
    first, _ = await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0 + timedelta(seconds=1)
    )
    # seq=2 started its transaction earlier (enqueued_at = T0) but took the
    # lock second (ack_window_at = T0+2s) → follower of seq=1
    second, is_leader = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=2)
    )
    assert is_leader is False
    loaded = await _load_item(session_factory, second.id)
    assert loaded.ack_leader_id == first.id


async def test_empty_ack_template_skips_all_processing(session_factory):
    world = await seed_dingtalk(session_factory, ack_template="")
    item1, leader1 = await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0, ack_template=""
    )
    item2, leader2 = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=1), ack_template=""
    )
    assert leader1 is False and leader2 is False
    loaded1 = await _load_item(session_factory, item1.id)
    loaded2 = await _load_item(session_factory, item2.id)
    assert loaded1.ack_leader_id is None  # no window occupancy at all
    assert loaded2.ack_leader_id is None
    assert await _im_send_events(session_factory) == []


async def test_position_hint_counts_smaller_pending(session_factory):
    world = await seed_dingtalk(session_factory)
    a, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    b, _ = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=6)
    )
    c, _ = await enqueue_item(
        session_factory, world=world, seq=3, ack_window_at=T0 + timedelta(seconds=7)
    )
    async with session_factory() as session:
        item_c = await session.get(IntegrationMessageQueue, c.id)
        assert await position_hint(session, item=item_c) == 3


# ---------------------------------------------------------------------------
# IMSendRelay — T1/T2 at-most-once protocol
# ---------------------------------------------------------------------------


async def test_ack_success_sets_five_fields_and_sends_exactly_once(
    session_factory, redis_client
):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    leader, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    f1, _ = await enqueue_item(
        session_factory, world=world, seq=2, ack_window_at=T0 + timedelta(seconds=1)
    )
    f2, _ = await enqueue_item(
        session_factory, world=world, seq=3, ack_window_at=T0 + timedelta(seconds=2)
    )
    relay = _make_relay(session_factory, redis_client, transport)
    processed = await relay.run_once()
    assert processed == 1  # only the leader event
    # platform got exactly one confirmation
    sends = transport.group_sends()
    assert len(sends) == 1
    content = json.loads(sends[0].body["msgParam"])["content"]
    assert content.startswith("✅ 已接收")
    loaded = await _load_item(session_factory, leader.id)
    assert loaded.ack_attempted_at is not None
    assert loaded.ack_sent_at is not None
    for follower_id in (f1.id, f2.id):
        follower = await _load_item(session_factory, follower_id)
        assert follower.ack_sent_at is None  # represented ≠ sent
        assert follower.ack_represented_at is not None
        assert follower.ack_merged_into == leader.id


async def test_ack_unattempted_event_reclaims_exactly_one_send(session_factory, redis_client):
    """An event never committed by T1 (pending ∧ attempted NULL — the state
    a pre-T1 crash leaves behind, guaranteed by T1's single transaction) is
    reclaimed on the next pass and sends exactly once. Two full relay passes
    stand in for the crash/restart cycle; the post-T1 crash (attempted ∧
    published) is tested separately below."""
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    leader, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    await relay.run_once()  # second pass: nothing pending
    assert len(transport.group_sends()) == 1
    loaded = await _load_item(session_factory, leader.id)
    assert loaded.ack_attempted_at is not None and loaded.ack_sent_at is not None


async def test_ack_post_t1_crash_is_lost_not_duplicated(session_factory, redis_client):
    """Simulate T1 committed (gate + published) then process death before
    the outbound call: attempted ∧ ¬sent ∧ published — no retry, no dup."""
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    leader, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    # Apply T1 by hand (the relay died right after committing it).
    async with session_factory() as session, session.begin():
        item = await session.get(IntegrationMessageQueue, leader.id)
        item.ack_attempted_at = datetime.now(UTC)
        events = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
            )
        ).scalars().all()
        assert len(events) == 1
        events[0].status = "published"
        events[0].published_at = datetime.now(UTC)
    relay = _make_relay(session_factory, redis_client, transport)
    processed = await relay.run_once()
    assert processed == 0  # published events are never re-claimed
    assert transport.group_sends() == []  # the ack is lost (at-most-once)
    loaded = await _load_item(session_factory, leader.id)
    assert loaded.ack_attempted_at is not None
    assert loaded.ack_sent_at is None  # loss is audit-visible


async def test_ack_t1_stall_blocks_second_worker_claim(session_factory, redis_client):
    """W1 committed T1 (published + attempted) — W2 polling the same event
    type finds nothing claimable: no lost+sent coexistence is possible."""
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()  # W1 completes T1 + send + T2
    # W2 (fresh relay, same store) must not re-claim
    relay2 = _make_relay(session_factory, redis_client, transport)
    async with session_factory() as session, session.begin():
        claimed = await relay2._claim_events(session, ack=True)
    assert claimed == []
    assert len(transport.group_sends()) == 1


async def test_ack_send_failure_is_not_retried(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport(send_status=500, send_body={"code": "busy"})
    leader, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    await relay.run_once()
    assert len(transport.group_sends()) == 1  # at-most-once: no retry
    loaded = await _load_item(session_factory, leader.id)
    assert loaded.ack_attempted_at is not None
    assert loaded.ack_sent_at is None
    events = await _im_send_events(session_factory)
    assert events[0].status == "published"


async def test_ack_carries_position_hint_for_queued_items(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    # seq=1 processing (not pending) → the new leader is position 1
    await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0 - timedelta(seconds=60),
        state="processing",
    )
    await enqueue_item(session_factory, world=world, seq=2, ack_window_at=T0)
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    sends = transport.group_sends()
    content = json.loads(sends[-1].body["msgParam"])["content"]
    assert "已接收" in content and "第" not in content  # position 1 → plain
    # now a leader with a smaller pending ahead → hedged position copy
    transport2 = ScriptedDingTalkTransport()
    await enqueue_item(session_factory, world=world, seq=3, ack_window_at=T0 + timedelta(seconds=1))
    relay2 = _make_relay(session_factory, redis_client, transport2)
    # seq=2 (pending, smaller seq) + seq=3 → seq=3 is follower; force a fresh
    # leader in a new conversation window instead:
    await enqueue_item(
        session_factory, world=world, seq=4, ack_window_at=T0 + timedelta(seconds=60)
    )
    await relay2.run_once()
    contents = [json.loads(s.body["msgParam"])["content"] for s in transport2.group_sends()]
    assert any("可能很快轮到" in c for c in contents)


# ---------------------------------------------------------------------------
# IMSendRelay — general path (feedback / notification chunks / budgets)
# ---------------------------------------------------------------------------


async def _emit_im_send(session_factory, world, payload: dict, key: str) -> OutboxEvent:
    from mesh.outbox.service import emit_event

    async with session_factory() as session, session.begin():
        return await emit_event(
            session,
            workspace_id=world["ws"],
            event_type=IM_SEND_EVENT_TYPE,
            payload=payload,
            idempotency_key=key,
        )


async def test_feedback_send_publishes_and_sends(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    await _emit_im_send(
        session_factory,
        world,
        {
            "kind": "feedback",
            "workspace_id": str(world["ws"]),
            "integration_id": str(world["integ_dingtalk"]),
            "conversation_key": CONVERSATION_KEY,
            "conversation_type": "group",
            "text": "⏳ 正在停止任务…",
        },
        key="feedback-1",
    )
    relay = _make_relay(session_factory, redis_client, transport)
    assert await relay.run_once() == 1
    sends = transport.group_sends()
    assert len(sends) == 1
    assert json.loads(sends[0].body["msgParam"])["content"].startswith("⏳")
    events = await _im_send_events(session_factory)
    assert events[0].status == "published"


async def test_notification_chunks_update_delivery_ledger(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    delivery_id = await _make_delivery(
        session_factory, world, chunks_total=2,
        destination_key=f"dingtalk:bind:{CONVERSATION_KEY}",
    )
    base = {
        "kind": "notification",
        "workspace_id": str(world["ws"]),
        "integration_id": str(world["integ_dingtalk"]),
        "conversation_key": CONVERSATION_KEY,
        "conversation_type": "group",
        "chunks_total": 2,
        "delivery_id": str(delivery_id),
    }
    await _emit_im_send(session_factory, world, {**base, "chunk_index": 0, "text": "part1"}, "c0")
    await _emit_im_send(session_factory, world, {**base, "chunk_index": 1, "text": "part2"}, "c1")
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    assert len(transport.group_sends()) == 2
    titles = [json.loads(s.body["msgParam"])["title"] for s in transport.group_sends()]
    assert titles == ["Mesh 执行结果 (1/2)", "Mesh 执行结果 (2/2)"]
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent"
        assert row.sent_at is not None
        assert json.loads(row.external_target)["sent_chunks"] == 2


async def test_notification_no_staff_id_is_terminal(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    delivery_id = await _make_delivery(
        session_factory, world, chunks_total=1, destination_key="dingtalk:bind:direct"
    )
    await _emit_im_send(
        session_factory,
        world,
        {
            "kind": "notification",
            "workspace_id": str(world["ws"]),
            "integration_id": str(world["integ_dingtalk"]),
            "conversation_key": "dingtalk:dingcorpTEST:directconv",
            "conversation_type": "direct",
            "target_user_key": "x=ZXh0ZXJuYWw",  # external contact → undeliverable
            "chunk_index": 0,
            "chunks_total": 1,
            "delivery_id": str(delivery_id),
            "text": "result",
        },
        "direct-1",
    )
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    await relay.run_once()  # terminal — no retry
    assert transport.direct_sends() == []
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"
        assert row.error == "no_staff_id"
    events = await _im_send_events(session_factory)
    assert events[0].status == "published"


async def test_rate_limit_defers_available_at_without_failure_budget(
    session_factory, redis_client
):
    """send.too.fast → event stays pending with available_at moved forward
    and delivery_attempts UNTOUCHED (retryable non-failure, §6.6 R4-4);
    the retry after the backoff window succeeds."""
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport(
        send_status=400,
        send_body={"code": "send.too.fast", "flowControlledStaffIdList": ["s1"]},
    )
    delivery_id = await _make_delivery(
        session_factory, world, chunks_total=1, destination_key="dingtalk:bind:rl"
    )
    await _emit_im_send(
        session_factory,
        world,
        {
            "kind": "notification",
            "workspace_id": str(world["ws"]),
            "integration_id": str(world["integ_dingtalk"]),
            "conversation_key": CONVERSATION_KEY,
            "conversation_type": "group",
            "chunk_index": 0,
            "chunks_total": 1,
            "delivery_id": str(delivery_id),
            "text": "result",
        },
        "notif-rl",
    )
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == IM_SEND_EVENT_TYPE,
                    OutboxEvent.status == "pending",
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].delivery_attempts == 0  # failure budget untouched
        assert events[0].available_at > datetime.now(UTC)  # deferred
        assert int((events[0].payload or {}).get("_mesh_rate_limit_hits") or 0) == 1
    # After the backoff window: the platform accepts the send.
    transport.send_status = 200
    transport.send_body = {"processQueryKey": "ok2"}
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.status == "pending"))
        ).scalars().all()
        for row in rows:
            row.available_at = datetime.now(UTC)
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent"


async def test_upstream_failure_exhausts_budget_then_terminal(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport(send_status=500, send_body={"code": "boom"})
    delivery_id = await _make_delivery(
        session_factory, world, chunks_total=1, destination_key="dingtalk:bind:y"
    )
    await _emit_im_send(
        session_factory,
        world,
        {
            "kind": "notification",
            "workspace_id": str(world["ws"]),
            "integration_id": str(world["integ_dingtalk"]),
            "conversation_key": CONVERSATION_KEY,
            "conversation_type": "group",
            "chunk_index": 0,
            "chunks_total": 1,
            "delivery_id": str(delivery_id),
            "text": "result",
        },
        "notif-fail",
    )
    relay = _make_relay(session_factory, redis_client, transport)
    for _ in range(5):
        # re-arm available_at each pass (backoff is tiny but nonzero)
        async with session_factory() as session, session.begin():
            rows = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.status == "pending"))
            ).scalars().all()
            for row in rows:
                row.available_at = datetime.now(UTC)
        await relay.run_once()
    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )
        assert event.status == "failed"
        assert event.delivery_attempts == 5
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "failed"


# ---------------------------------------------------------------------------
# §3.8 ledger: send result recorded on the source inbound event (R4/R8)
# ---------------------------------------------------------------------------


async def _make_inbound_event(session_factory, world) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        event = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            external_event_id=f"msg-{uuid.uuid4().hex}",
            event_type="im.message.receive_v1",
            payload={"conversationType": "2"},
            signature_status="valid",
            process_status="dispatched",
        )
        session.add(event)
        await session.flush()
    return event.id


async def test_ack_success_records_mesh_ack_in_event_ledger(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    event_id = await _make_inbound_event(session_factory, world)
    transport = ScriptedDingTalkTransport()
    await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0,
        integration_event_id=event_id,
    )
    await _make_relay(session_factory, redis_client, transport).run_once()
    assert len(transport.group_sends()) == 1
    async with session_factory() as session:
        row = await session.get(IntegrationEvent, event_id)
        audit = row.payload.get("_mesh_ack")
        assert audit is not None
        assert audit["status"] == "sent"
        assert audit["sent_at"]
        assert "_mesh_ack_failed" not in row.payload


async def test_ack_failure_writes_mesh_ack_failed_audit(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    event_id = await _make_inbound_event(session_factory, world)
    transport = ScriptedDingTalkTransport(send_status=500, send_body={"code": "boom"})
    item, _ = await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0,
        integration_event_id=event_id,
    )
    relay = _make_relay(session_factory, redis_client, transport)
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(IntegrationEvent, event_id)
        audit = row.payload.get("_mesh_ack_failed")
        assert audit is not None
        assert audit["status"] == "failed"
        assert audit["reason"] == "upstream_error"
        assert audit["at"]
        assert "_mesh_ack" not in row.payload
        # at-most-once unchanged: one attempt, never sent, never retried
        queue_item = await session.get(IntegrationMessageQueue, item.id)
        assert queue_item.ack_attempted_at is not None
        assert queue_item.ack_sent_at is None
    await relay.run_once()
    assert len(transport.group_sends()) == 1  # still no retry


async def test_ack_timeout_writes_mesh_ack_failed_audit(session_factory, redis_client):
    world = await seed_dingtalk(session_factory)
    event_id = await _make_inbound_event(session_factory, world)
    transport = ScriptedDingTalkTransport(token_delay=1.0)  # slower than ack timeout
    await enqueue_item(
        session_factory, world=world, seq=1, ack_window_at=T0,
        integration_event_id=event_id,
    )
    relay = IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        ack_send_timeout=0.2,
    )
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(IntegrationEvent, event_id)
        audit = row.payload.get("_mesh_ack_failed")
        assert audit is not None
        assert audit["reason"] == "timeout"


async def test_ack_ledger_skipped_gracefully_without_event_link(session_factory, redis_client):
    """Items without a linked inbound event: the send still happens and the
    relay does not crash on the missing ledger target."""
    world = await seed_dingtalk(session_factory)
    transport = ScriptedDingTalkTransport()
    item, _ = await enqueue_item(session_factory, world=world, seq=1, ack_window_at=T0)
    await _make_relay(session_factory, redis_client, transport).run_once()
    assert len(transport.group_sends()) == 1
    loaded = await _load_item(session_factory, item.id)
    assert loaded.ack_sent_at is not None


# ---------------------------------------------------------------------------
# ③ — genuinely crossed enqueued_at / ack_window_at samples (T39-16 shape)
# ---------------------------------------------------------------------------


async def test_leader_election_with_crossed_enqueue_and_window_times(session_factory):
    """seq=2's transaction STARTED first (earlier enqueued_at) but took the
    imq_seq lock LATER (later ack_window_at) → it must fall into seq=1's
    window as a follower, even though its enqueued_at is the earlier one.
    Election reads ONLY lock-ordered ack_window_at, never enqueued_at."""
    world = await seed_dingtalk(session_factory)
    first, is_leader_1 = await enqueue_item(
        session_factory, world=world, seq=1,
        ack_window_at=T0 + timedelta(seconds=1),   # lock taken first
        enqueued_at=T0 + timedelta(seconds=5),     # transaction started later
    )
    assert is_leader_1 is True
    second, is_leader_2 = await enqueue_item(
        session_factory, world=world, seq=2,
        ack_window_at=T0 + timedelta(seconds=2),   # lock taken later
        enqueued_at=T0,                            # transaction started FIRST
    )
    assert is_leader_2 is False
    loaded_first = await _load_item(session_factory, first.id)
    loaded_second = await _load_item(session_factory, second.id)
    # the crossing is real in the data (not trivially equal timestamps)
    assert loaded_second.enqueued_at < loaded_first.enqueued_at
    assert loaded_second.ack_window_at > loaded_first.ack_window_at
    # yet the later-enqueued item owns the window
    assert loaded_second.ack_leader_id == first.id
    # only the leader wrote an im.send event
    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
            )
        ).scalars().all()
    assert len(events) == 1
    assert events[0].payload["queue_item_id"] == str(first.id)

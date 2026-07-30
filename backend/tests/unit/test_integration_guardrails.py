"""Post-signature semantic guardrail tests (integrations.md §2.10, MES-87).

Real Redis rolling windows + real PostgreSQL pending-depth counts through
the shared ingestion core: per-identity (20/min, global across sessions),
per-conversation (60/min), pending depth (50) — over-limit messages are
NEVER queued/executed/acked: ``rejected`` + ``_mesh_reject_reason`` with
the REAL msgId occupying the dedup slot, plus the self-throttled (≤1/min)
rate-limit notice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mesh.db.models.integration import (
    Integration,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.dingtalk import normalize_message_payload
from mesh.integrations.guardrails import InboundGuardrails
from mesh.integrations.ingest import ingest_verified_event
from tests.unit.integrations_support import (
    NOW,
    dingtalk_message_payload,
    make_dingtalk_binding,
    seed_dingtalk_world,
)

pytestmark = pytest.mark.unit


def _guardrails(redis, **overrides) -> InboundGuardrails:
    return InboundGuardrails(redis, **overrides)


def _envelope(staff_id: str = "014728255240768602", msg_id: str | None = None):
    return normalize_message_payload(
        dingtalk_message_payload(staff_id=staff_id, msg_id=msg_id),
        max_chars=4000,
        channel="http",
    )


async def _ingest(session_factory, world, envelope, guardrails):
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    async with session_factory() as session, session.begin():
        return await ingest_verified_event(
            session,
            integration=integration,
            envelope=envelope,
            now=NOW,
            guardrails=guardrails,
        )


async def test_identity_limit_20_per_min_across_conversations(session_factory, redis_client):
    """One identity flooding TWO conversations trips the global identity
    counter at message 21 (cross-session — the key is the full triple)."""
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world, external_ref="cidAaaaaaaaaaa==")
    await make_dingtalk_binding(session_factory, world=world, external_ref="cidBbbbbbbbbbb==")
    guardrails = _guardrails(redis_client)

    verdicts = []
    for i in range(22):
        ref = "cidAaaaaaaaaaa==" if i % 2 == 0 else "cidBbbbbbbbbbb=="
        envelope = normalize_message_payload(
            dingtalk_message_payload(conversation_id=ref),
            max_chars=4000,
            channel="http",
        )
        result = await _ingest(session_factory, world, envelope, guardrails)
        verdicts.append(result.process_status)

    assert verdicts[:20] == ["dispatched"] * 20
    assert verdicts[20] == "rejected"
    assert verdicts[21] == "rejected"


async def test_rejected_carries_reason_and_real_msg_id_dedup_slot(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client, per_identity_per_min=1)

    first = await _ingest(session_factory, world, _envelope(msg_id="msgSAME00000000001=="), guardrails)
    second = await _ingest(session_factory, world, _envelope(msg_id="msgSAME00000000001=="), guardrails)

    assert first.process_status == "dispatched"
    # Over-limit: rejected WITH the real msgId holding the dedup slot…
    assert second.process_status == "deduped"  # same msgId → idempotent, no storm

    # Force a distinct-msgId rejection to inspect the reason marker.
    third = await _ingest(session_factory, world, _envelope(msg_id="msgOTHER000000001=="), guardrails)
    assert third.process_status == "rejected"
    async with session_factory() as session:
        event = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.external_event_id == "msgOTHER000000001=="
                )
            )
        ).scalar_one()
    assert event.process_status == "rejected"
    assert event.payload["_mesh_reject_reason"] == "rate_limited"

    # The rejected message's REAL msgId occupies the dedup slot — a platform
    # retry of the same message meets idempotent dedup, not a reject storm.
    fourth = await _ingest(
        session_factory, world, _envelope(msg_id="msgOTHER000000001=="), guardrails
    )
    assert fourth.process_status == "deduped"


async def test_over_limit_is_never_queued_executed_acked(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client, per_identity_per_min=1)

    await _ingest(session_factory, world, _envelope(), guardrails)  # dispatched
    await _ingest(session_factory, world, _envelope(), guardrails)  # rejected

    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        enqueues = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
            )
        ).scalars().all()
        acks = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "im.send",
                    OutboxEvent.payload["kind"].astext == "ack",
                )
            )
        ).scalars().all()
    assert len(queues) == 1  # only the first message
    assert len(enqueues) == 1
    assert len(acks) == 1


async def test_conversation_limit_60_per_min_multi_identity(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client, per_conversation_per_min=60)

    verdicts = []
    for i in range(62):
        # Distinct identity each message → identity counter never trips.
        envelope = _envelope(staff_id=f"user{i:06d}")
        result = await _ingest(session_factory, world, envelope, guardrails)
        verdicts.append(result.process_status)

    assert verdicts[:60] == ["dispatched"] * 60
    assert verdicts[60] == "rejected"
    assert verdicts[61] == "rejected"


async def test_pending_depth_limit_50(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory)  # serial → stays pending
    binding = await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client, max_pending_per_conversation=50)

    # Pre-seed 50 pending queue rows for the conversation.
    conversation_key = f"dingtalk:dingcorp0001:{binding.external_ref}"
    async with session_factory() as session, session.begin():
        for seq in range(1, 51):
            session.add(IntegrationMessageQueue(
                workspace_id=world["ws"],
                integration_id=world["integ_dingtalk"],
                binding_id=binding.id,
                conversation_key=conversation_key,
                seq=seq,
                dispatch_mode="serial_conversation",
                state="pending",
                sender_identity_key="dingtalk:dingcorp0001:someone",
            ))

    result = await _ingest(session_factory, world, _envelope(), guardrails)
    assert result.process_status == "rejected"

    async with session_factory() as session:
        event = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.process_status == "rejected"
                )
            )
        ).scalar_one()
    assert event.payload["_mesh_reject_reason"] == "rate_limited"


async def test_rate_limit_notice_self_throttled_once_per_minute(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client, per_identity_per_min=1)

    await _ingest(session_factory, world, _envelope(), guardrails)  # dispatched
    await _ingest(session_factory, world, _envelope(), guardrails)  # rejected + notice
    await _ingest(session_factory, world, _envelope(), guardrails)  # rejected, NO notice
    await _ingest(session_factory, world, _envelope(), guardrails)  # rejected, NO notice

    async with session_factory() as session:
        notices = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "im.send",
                    OutboxEvent.payload["kind"].astext == "rate_limit_notice",
                )
            )
        ).scalars().all()
    assert len(notices) == 1  # ≤1 per conversation per minute
    assert notices[0].payload["template"].startswith("⚠️")


async def test_distinct_conversations_get_distinct_notices(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world, external_ref="cidAaaaaaaaaaa==")
    await make_dingtalk_binding(session_factory, world=world, external_ref="cidBbbbbbbbbbb==")
    guardrails = _guardrails(redis_client, per_identity_per_min=1)

    for ref in ("cidAaaaaaaaaaa==", "cidBbbbbbbbbbb=="):
        for _ in range(2):
            envelope = normalize_message_payload(
                dingtalk_message_payload(conversation_id=ref),
                max_chars=4000,
                channel="http",
            )
            await _ingest(session_factory, world, envelope, guardrails)

    async with session_factory() as session:
        notices = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "im.send",
                    OutboxEvent.payload["kind"].astext == "rate_limit_notice",
                )
            )
        ).scalars().all()
    # One per conversation — the throttle is per-conversation, not global.
    assert len(notices) == 2


async def test_within_limits_everything_dispatches(session_factory, redis_client):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    guardrails = _guardrails(redis_client)

    for _ in range(5):
        result = await _ingest(session_factory, world, _envelope(), guardrails)
        assert result.process_status == "dispatched"


async def test_window_guardrail_runs_before_command_plane(session_factory, redis_client):
    """§3.7:975 — command handling is constrained by the §2.10 frequency
    counters: an over-limit identity's /command is rejected before the
    command plane ever sees it."""
    from mesh.db.models.integration import Integration
    from mesh.integrations.dingtalk import normalize_message_payload
    from mesh.integrations.guardrails import InboundGuardrails
    from mesh.integrations.ingest import COMMAND_REGISTRY, CommandOutcome, ingest_verified_event
    from tests.unit.integrations_support import (
        NOW as _NOW,
    )
    from tests.unit.integrations_support import (
        dingtalk_message_payload,
        make_dingtalk_binding,
        seed_dingtalk_world,
    )

    handled = []

    async def _handler(session, envelope, name, args):
        handled.append(name)
        return CommandOutcome()

    COMMAND_REGISTRY["deploy"] = _handler
    try:
        world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
        await make_dingtalk_binding(session_factory, world=world)
        guardrails = InboundGuardrails(redis_client, per_identity_per_min=1)

        async with session_factory() as session:
            integration = await session.get(Integration, world["integ_dingtalk"])

        async def _send(text: str):
            envelope = normalize_message_payload(
                dingtalk_message_payload(text=text), max_chars=4000, channel="http"
            )
            async with session_factory() as session, session.begin():
                return await ingest_verified_event(
                    session, integration=integration, envelope=envelope,
                    now=_NOW, guardrails=guardrails,
                )

        first = await _send("/deploy now")
        assert first.process_status == "processed"  # command handled
        assert handled == ["deploy"]
        second = await _send("/deploy again")  # over the 1/min identity window
        assert second.process_status == "rejected"
        assert second.body.get("reason") == "rate_limited"
        assert handled == ["deploy"]  # command plane NOT reached the 2nd time
    finally:
        del COMMAND_REGISTRY["deploy"]

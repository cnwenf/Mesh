"""DingTalk HTTP callback pipeline tests (integrations.md §3.2 / §5.6 M1, MES-87).

Full ``process_inbound(kind='im_dingtalk')`` runs against real PostgreSQL
with REAL signatures (constructed per the official timestamp+sign scheme):
positive/negative signature cases, the ±3600s replay boundary (never
narrowed), routing-field tampering (chatbotCorpId mislocation), msgId
dedup, disabled integration, the msgtype matrix, and the pre-signature
(integration, IP) silent-200 limiter (auth.md §3.6).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.integration import IntegrationEvent, IntegrationMessageQueue
from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.inbound import process_inbound
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE
from tests.unit.integrations_support import (
    NOW,
    TEST_SIGNING_SECRET,
    dingtalk_message_payload,
    dingtalk_request,
    make_dingtalk_binding,
    seed_dingtalk_world,
)

pytestmark = pytest.mark.unit

TOLERANCE = timedelta(seconds=300)  # global setting — DingTalk MUST widen to ±3600s


async def _run(session_factory, body: bytes, headers: dict) -> tuple[int, dict]:
    async with session_factory() as session, session.begin():
        return await process_inbound(
            session, kind="im_dingtalk", raw_body=body, headers=headers,
            signing_secret=TEST_SIGNING_SECRET, now=NOW, tolerance=TOLERANCE,
        )


# ---------------------------------------------------------------------------
# M1 executable signature assertions
# ---------------------------------------------------------------------------


async def test_valid_signature_200_dispatched(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload())

    status, payload = await _run(session_factory, body, headers)

    assert status == 200
    assert payload["process_status"] == "dispatched"
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        assert event.signature_status == "valid"
        assert event.process_status == "dispatched"
        assert event.payload["_mesh_channel"] == "http"


async def test_wrong_secret_rejected_never_dispatched(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(
        dingtalk_message_payload(), secret="attacker-guessed-secret"
    )

    status, payload = await _run(session_factory, body, headers)

    assert status == 401
    assert payload["error"]["code"] == "invalid_signature"
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        assert event.signature_status == "invalid"
        assert event.process_status == "rejected"
        assert event.external_event_id.startswith("rejected:")
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        outbox = (await session.execute(select(OutboxEvent))).scalars().all()
    assert queues == []  # never dispatched
    assert outbox == []  # never routed


async def test_missing_sign_header_is_missing_status(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload())
    del headers["sign"]

    status, payload = await _run(session_factory, body, headers)

    assert status == 401
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.signature_status == "missing"
    assert event.process_status == "rejected"


async def test_replay_boundary_59min_ago_passes(session_factory):
    """Inside the official ±3600s window (59 minutes) — MUST pass: a
    narrower implementation window would reject legitimate callbacks."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    ts_ms = str(int((NOW.timestamp() - 3540) * 1000))  # 59 min ago
    body, headers = dingtalk_request(dingtalk_message_payload(), ts_ms=ts_ms)

    status, payload = await _run(session_factory, body, headers)
    assert status == 200
    assert payload["process_status"] == "dispatched"


async def test_replay_beyond_3600s_rejected(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    ts_ms = str(int((NOW.timestamp() - 3601) * 1000))
    body, headers = dingtalk_request(dingtalk_message_payload(), ts_ms=ts_ms)

    status, payload = await _run(session_factory, body, headers)
    assert status == 401
    assert payload["error"]["code"] == "invalid_signature"


async def test_global_300s_tolerance_is_widened_for_dingtalk(session_factory):
    """The caller passes the global ±300s setting; the DingTalk adapter
    refuses to narrow the official ±3600s (700s-old callback still valid)."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    ts_ms = str(int((NOW.timestamp() - 700) * 1000))  # beyond 300s, inside 3600s
    body, headers = dingtalk_request(dingtalk_message_payload(), ts_ms=ts_ms)
    status, _payload = await _run(session_factory, body, headers)
    assert status == 200


async def test_routing_field_tampering_mislocates_to_401(session_factory):
    """M1 ④: chatbotCorpId changed to another value → integration
    mislocated → signature cannot verify → 401, never dispatched."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(
        dingtalk_message_payload(corp_id="dingOTHERcorp9999")
    )
    status, payload = await _run(session_factory, body, headers)
    assert status == 401
    assert payload["error"]["code"] == "invalid_signature"
    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert queues == []


async def test_duplicate_msg_id_is_idempotent_200_deduped(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    payload_msg = dingtalk_message_payload(msg_id="msgDUPLICATE0000000==")
    body, headers = dingtalk_request(payload_msg)

    first_status, first = await _run(session_factory, body, headers)
    second_status, second = await _run(session_factory, body, headers)

    assert first["process_status"] == "dispatched"
    assert first_status == 200
    assert second_status == 200
    assert second["process_status"] == "deduped"
    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert len(queues) == 1  # never queued twice


async def test_disabled_integration_401_integration_disabled(session_factory):
    world = await seed_dingtalk_world(session_factory, status="disabled")
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload())

    status, payload = await _run(session_factory, body, headers)

    assert status == 401
    assert payload["error"]["code"] == "integration_disabled"
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.process_status == "rejected"


# ---------------------------------------------------------------------------
# msgtype matrix via the HTTP path (C-1)
# ---------------------------------------------------------------------------


async def test_group_picture_is_audit_only_no_dispatch(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload(msgtype="picture"))

    status, payload = await _run(session_factory, body, headers)

    assert status == 200
    assert payload["process_status"] == "processed"  # audited, never triggered
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        assert event.process_status == "processed"
        assert event.payload["msgtype"] == "picture"
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert queues == []


async def test_group_without_at_is_matched_audit_only(session_factory):
    """Group message NOT @-ing the bot → matched audit, no trigger (§6.9)."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(
        dingtalk_message_payload(is_in_at_list=False, text="闲聊内容")
    )
    status, payload = await _run(session_factory, body, headers)
    assert status == 200
    assert payload["process_status"] == "matched"
    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert queues == []


async def test_direct_message_triggers_without_at(session_factory):
    """Single chat (conversationType '1') triggers on text (direct_message)."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(
        dingtalk_message_payload(conversation_type="1", is_in_at_list=False)
    )
    status, payload = await _run(session_factory, body, headers)
    assert status == 200
    assert payload["process_status"] == "dispatched"


async def test_text_truncation_flag_survives_http_path(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload(text="长" * 5000))
    status, _ = await _run(session_factory, body, headers)
    assert status == 200
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        queue = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
    assert event.payload["truncated"] is True
    assert len(queue.message_excerpt) <= 120


# ---------------------------------------------------------------------------
# Parallel (config inbound_queue='parallel') direct dispatch via HTTP
# ---------------------------------------------------------------------------


async def test_parallel_config_dispatches_optimistically(session_factory):
    world = await seed_dingtalk_world(session_factory, inbound_queue="parallel")
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(dingtalk_message_payload())

    status, payload = await _run(session_factory, body, headers)
    assert status == 200
    assert payload["process_status"] == "dispatched"
    async with session_factory() as session:
        queue = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
        enqueue = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == ENQUEUE_EVENT_TYPE)
            )
        ).scalar_one()
    assert queue.state == "dispatching"
    assert queue.dispatch_mode == "parallel"
    assert enqueue.payload["queue_item_id"] == str(queue.id)
    assert enqueue.payload["trigger"] == "integration"


# ---------------------------------------------------------------------------
# Pre-signature (integration, IP) silent-200 limiter (auth.md §3.6)
# ---------------------------------------------------------------------------


async def test_pre_limit_sliding_window_exceeds_at_120(redis_client):
    from unittest.mock import Mock

    from mesh.integrations.inbound_routes import (
        DINGTALK_PRE_LIMIT_PER_MIN,
        _dingtalk_pre_limit_exceeded,
    )

    assert DINGTALK_PRE_LIMIT_PER_MIN == 120
    integration_id = uuid.uuid4()
    request = Mock()
    request.app.state.redis = redis_client
    request.client = Mock(host="203.0.113.7")

    verdicts = [
        await _dingtalk_pre_limit_exceeded(request, integration_id=integration_id)
        for _ in range(125)
    ]
    assert verdicts[:120] == [False] * 120
    assert all(verdicts[120:])  # over-limit → silent-200 signal


async def test_pre_limit_is_scoped_per_integration_and_ip(redis_client):
    from unittest.mock import Mock

    from mesh.integrations.inbound_routes import _dingtalk_pre_limit_exceeded

    integration_a, integration_b = uuid.uuid4(), uuid.uuid4()

    def make_request(host: str) -> Mock:
        request = Mock()
        request.app.state.redis = redis_client
        request.client = Mock(host=host)
        return request

    for _ in range(120):
        assert not await _dingtalk_pre_limit_exceeded(
            make_request("203.0.113.7"), integration_id=integration_a
        )
    # Same integration, SAME ip → exceeded.
    assert await _dingtalk_pre_limit_exceeded(
        make_request("203.0.113.7"), integration_id=integration_a
    )
    # Same integration, OTHER ip → its own budget.
    assert not await _dingtalk_pre_limit_exceeded(
        make_request("203.0.113.9"), integration_id=integration_a
    )
    # OTHER integration, original ip → its own budget ((integration, IP) tuple).
    assert not await _dingtalk_pre_limit_exceeded(
        make_request("203.0.113.7"), integration_id=integration_b
    )


async def test_official_sample_ids_flow_through_http_path(session_factory):
    """N-1: official documentation sample values through the real pipeline —
    the base64-like conversationId ('cid…==') stores intact in the key."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    body, headers = dingtalk_request(
        dingtalk_message_payload(
            conversation_id="cid6EUvB2O8qVF2RYQtHTKEsg==",
            sender_id="$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9",
            staff_id=None,  # external contact → x=<base64url>
        )
    )
    status, payload = await _run(session_factory, body, headers)
    assert status == 200
    assert payload["process_status"] == "dispatched"
    async with session_factory() as session:
        queue = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
    assert queue.conversation_key == (
        "dingtalk:dingcorp0001:cid6EUvB2O8qVF2RYQtHTKEsg=="
    )
    assert queue.sender_identity_key.startswith("dingtalk:dingcorp0001:x=")
    assert ":" not in queue.sender_identity_key.split(":", 2)[2]


# ---------------------------------------------------------------------------
# Review-round fixes: H1 (Content-Length pre-check) + M1 (malformed closure)
# ---------------------------------------------------------------------------


def test_declared_body_too_large_pre_check():
    """H1: the Content-Length PRE-CHECK rejects a declared-oversize body
    before buffering (§3.2 item 2, first of the two passes)."""
    from unittest.mock import Mock

    from mesh.integrations.inbound_routes import (
        INBOUND_BODY_MAX_BYTES,
        _declared_body_too_large,
    )

    oversized = Mock()
    oversized.headers = {"content-length": str(INBOUND_BODY_MAX_BYTES + 1)}
    resp = _declared_body_too_large(oversized)
    assert resp is not None
    assert resp.status_code == 413

    garbage = Mock()
    garbage.headers = {"content-length": "not-a-number"}
    assert _declared_body_too_large(garbage).status_code == 413

    fine = Mock()
    fine.headers = {"content-length": "1024"}
    assert _declared_body_too_large(fine) is None

    absent = Mock()  # chunked / absent → post-read pass still applies
    absent.headers = {}
    assert _declared_body_too_large(absent) is None


async def test_malformed_payload_missing_msg_id_rejected_not_4xx(session_factory):
    """M1: signed-but-malformed payload → bare-JSON 200 rejected + audit
    row, never a §6.14 envelope / 4xx retry trigger, never dispatched."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    payload = dingtalk_message_payload()
    del payload["msgId"]
    body, headers = dingtalk_request(payload)

    status, resp = await _run(session_factory, body, headers)

    assert status == 200  # never non-2xx at the platform
    assert resp == {
        "received": True,
        "event_id": "",
        "process_status": "rejected",
        "reason": "malformed_payload",
    }
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert event.signature_status == "valid"  # signature WAS fine
    assert event.process_status == "rejected"
    assert event.external_event_id.startswith("rejected:")
    assert queues == []


async def test_malformed_conversation_id_colon_rejected_inside_core(session_factory):
    """M1 (core layer): a colon-carrying conversationId that defeats the
    §2.10 N-1 segment rules is a rejection inside ingest_verified_event —
    audit on the ledger row with _mesh_reject_reason, bare-JSON 200."""
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(
        session_factory, world=world, external_ref="cidWITHcolon:injection"
    )
    payload = dingtalk_message_payload(conversation_id="cidWITHcolon:injection")
    body, headers = dingtalk_request(payload)

    status, resp = await _run(session_factory, body, headers)

    assert status == 200
    assert resp["process_status"] == "rejected"
    assert resp["reason"] == "malformed_payload"
    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert event.process_status == "rejected"
    assert event.payload["_mesh_reject_reason"] == "malformed_payload"
    assert queues == []

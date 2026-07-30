"""Inbound pipeline × MES-88 queue/command hook tests (§2.10/§3.7/§3.8).

Exercises the REAL pipeline (real Slack signatures) through process_inbound
with redis + settings wired, covering: parallel direct-dispatch with queue
row, serial pending, ack leader event, ack_template='' zero window, command
plane before matching, /btw passthrough, non-text audit-only, and the
post-signature frequency guards (depth + conversation window + one hint).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from mesh.config import load_settings
from mesh.db.models.integration import IntegrationEvent, IntegrationMessageQueue
from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.inbound import process_inbound
from tests.unit.integrations_support import (
    NOW,
    TEST_SIGNING_SECRET,
    make_binding,
    seed_world,
    slack_request,
)

pytestmark = pytest.mark.unit

TOLERANCE = timedelta(seconds=300)


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


def _slack_message(text_body: str, *, ts: str = "1753790400.000100") -> dict:
    return {
        "type": "event_callback",
        "team_id": "T_TEST",
        "event": {
            "type": "message",
            "channel": "C_QHOOK",
            "user": "U_HUMAN",
            "text": text_body,
            "event_ts": ts,
        },
    }


async def _run(session_factory, world, *, text_body: str, ts: str = "1753790400.000100",
               settings=None, redis=None):
    body, headers = slack_request(
        world["secrets"]["slack_signing_secret"], _slack_message(text_body, ts=ts)
    )
    async with session_factory() as session, session.begin():
        return await process_inbound(
            session, kind="im_slack", raw_body=body, headers=headers,
            signing_secret=TEST_SIGNING_SECRET, now=NOW, tolerance=TOLERANCE,
            redis=redis, settings=settings,
        )


async def _bind(session_factory, world):
    return await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_QHOOK",
        provider_tenant_key="T_TEST",
    )


async def _set_inbound_queue(session_factory, world, mode: str):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE integrations SET config = jsonb_set(config, '{inbound_queue}', :v) "
                "WHERE id = :id"
            ),
            {"v": f'"{mode}"', "id": world["integ_slack"]},
        )


async def _set_ack_template(session_factory, world, template: str):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE integrations SET config = jsonb_set(config, '{ack_template}', :v) "
                "WHERE id = :id"
            ),
            {"v": f'"{template}"', "id": world["integ_slack"]},
        )


async def _map_sender(session_factory, world):
    """Link the slack sender U_HUMAN to the world's user (command authz)."""
    from mesh.db.models.integration import ExternalIdentity

    async with session_factory() as session, session.begin():
        session.add(
            ExternalIdentity(
                provider="slack", provider_tenant_key="T_TEST",
                external_user_key="U_HUMAN", user_id=world["user"],
            )
        )


async def _queue_items(session_factory):
    async with session_factory() as session:
        return list(
            
                (
                    await session.execute(
                        select(IntegrationMessageQueue).order_by(
                            IntegrationMessageQueue.seq
                        )
                    )
                ).scalars().all()
            
        )


async def _outbox(session_factory, event_type: str):
    async with session_factory() as session:
        return list(
            
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == event_type)
                    )
                ).scalars().all()
            
        )


class TestQueueHook:
    async def test_parallel_direct_dispatch_queue_row(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        status, payload = await _run(
            session_factory, world, text_body="<@U_BOT> fix the build",
            settings=_settings(), redis=redis_client,
        )
        assert status == 200
        assert payload["process_status"] == "dispatched"
        items = await _queue_items(session_factory)
        assert len(items) == 1
        assert items[0].state == "dispatching"  # parallel optimistic dispatch
        assert items[0].dispatch_mode == "parallel"
        assert items[0].sender_identity_key == "slack:T_TEST:U_HUMAN"
        assert "fix the build" in items[0].message_excerpt
        enqueues = await _outbox(session_factory, "execution.enqueue")
        assert len(enqueues) == 1
        assert enqueues[0].payload["queue_item_id"] == str(items[0].id)
        # default ack template → leader im.send written at ingest
        sends = await _outbox(session_factory, "im.send")
        acks = [e for e in sends if e.payload.get("kind") == "ack"]
        assert len(acks) == 1
        assert items[0].ack_leader_id == items[0].id

    async def test_serial_mode_stays_pending(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _set_inbound_queue(session_factory, world, "serial_conversation")
        status, payload = await _run(
            session_factory, world, text_body="<@U_BOT> task one",
            settings=_settings(), redis=redis_client,
        )
        assert payload["process_status"] == "dispatched"
        items = await _queue_items(session_factory)
        assert items[0].state == "pending"
        assert items[0].dispatch_mode == "serial_conversation"
        assert await _outbox(session_factory, "execution.enqueue") == []

    async def test_ack_disabled_no_event_no_window(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _set_ack_template(session_factory, world, "")
        for i in range(2):
            await _run(
                session_factory, world, text_body=f"<@U_BOT> task {i}",
                ts=f"1753790400.00020{i}", settings=_settings(), redis=redis_client,
            )
        items = await _queue_items(session_factory)
        assert all(i.ack_leader_id is None for i in items)
        assert await _outbox(session_factory, "im.send") == []

    async def test_non_text_audit_only(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        status, payload = await _run(
            session_factory, world, text_body="",
            settings=_settings(), redis=redis_client,
        )
        assert status == 200
        assert payload["process_status"] == "processed"
        assert await _queue_items(session_factory) == []
        async with session_factory() as session:
            event = (await session.execute(select(IntegrationEvent))).scalar_one()
        assert event.payload.get("_mesh_trigger_skipped") == "non_text"


class TestCommandHook:
    async def test_stop_command_processed_no_queue(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        status, payload = await _run(
            session_factory, world, text_body="/stop",
            settings=_settings(), redis=redis_client,
        )
        assert status == 200
        assert payload["process_status"] == "processed"
        assert await _queue_items(session_factory) == []
        sends = await _outbox(session_factory, "im.send")
        assert any(e.payload.get("kind") == "command_feedback" for e in sends)

    async def test_btw_passthrough_enqueues_stripped_text(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _map_sender(session_factory, world)
        # passthrough text still goes through binding matching (§3.7 step 4:
        # 匹配 → 护栏 → 入队), so the stripped argument carries the mention
        status, payload = await _run(
            session_factory, world, text_body="/btw <@U_BOT> check the logs please",
            settings=_settings(), redis=redis_client,
        )
        assert payload["process_status"] == "dispatched"
        items = await _queue_items(session_factory)
        assert len(items) == 1
        assert "check the logs please" in items[0].message_excerpt
        assert "/btw" not in items[0].message_excerpt
        sends = await _outbox(session_factory, "im.send")
        assert any("new message" in e.payload.get("text", "") for e in sends)

    async def test_mid_message_slash_not_a_command(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _run(
            session_factory, world, text_body="<@U_BOT> please /stop breaking things",
            settings=_settings(), redis=redis_client,
        )
        items = await _queue_items(session_factory)
        assert len(items) == 1  # ordinary task message, enqueued


class TestGuards:
    async def test_pending_depth_guard_rejects(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _set_inbound_queue(session_factory, world, "serial_conversation")
        settings = _settings(im_queue_max_pending_per_conversation=1)
        s1, p1 = await _run(
            session_factory, world, text_body="<@U_BOT> one", ts="1753790400.000301",
            settings=settings, redis=redis_client,
        )
        assert p1["process_status"] == "dispatched"
        s2, p2 = await _run(
            session_factory, world, text_body="<@U_BOT> two", ts="1753790400.000302",
            settings=settings, redis=redis_client,
        )
        assert s2 == 200  # bare 200 — non-2xx would trigger platform re-push
        assert p2["process_status"] == "rejected"
        async with session_factory() as session:
            event = (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.process_status == "rejected"
                    )
                )
            ).scalar_one()
        assert event.payload.get("_mesh_reject_reason") == "rate_limited"
        # real msgId occupies the dedupe key (event stored, not dropped)
        assert not event.external_event_id.startswith("rejected:")
        items = await _queue_items(session_factory)
        assert len(items) == 1  # second never enqueued
        # one-shot hint emitted
        sends = await _outbox(session_factory, "im.send")
        hints = [e for e in sends if e.payload.get("kind") == "rate_limit_hint"]
        assert len(hints) == 1

    async def test_hint_only_once_per_minute(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        await _set_inbound_queue(session_factory, world, "serial_conversation")
        settings = _settings(im_queue_max_pending_per_conversation=1)
        await _run(session_factory, world, text_body="<@U_BOT> one",
                   ts="1753790400.000401", settings=settings, redis=redis_client)
        for i in range(3):
            await _run(session_factory, world, text_body=f"<@U_BOT> flood {i}",
                       ts=f"1753790400.00041{i}", settings=settings, redis=redis_client)
        sends = await _outbox(session_factory, "im.send")
        hints = [e for e in sends if e.payload.get("kind") == "rate_limit_hint"]
        assert len(hints) == 1  # reflection guard: max one per window

    async def test_conversation_rate_guard(self, session_factory, redis_client):
        world = await seed_world(session_factory)
        await _bind(session_factory, world)
        settings = _settings(
            im_inbound_per_conversation_per_min=2,
            im_queue_max_pending_per_conversation=50,
        )
        results = []
        for i in range(3):
            _status, payload = await _run(
                session_factory, world, text_body=f"<@U_BOT> msg {i}",
                ts=f"1753790400.00050{i}", settings=settings, redis=redis_client,
            )
            results.append(payload["process_status"])
        assert results == ["dispatched", "dispatched", "rejected"]

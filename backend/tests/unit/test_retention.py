"""Retention purges (§6.7 realtime window, §6.6 outbox cleanup)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.workers.retention import (
    outbox_retention_loop,
    purge_expired_events,
    purge_processed_outbox_events,
    retention_loop,
)


async def _seed_event(session_factory, workspace_id, channel, seq, age_days):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO realtime_channels (channel, workspace_id, last_seq) "
                "VALUES (:ch, :ws, :seq) ON CONFLICT (channel) DO NOTHING"
            ),
            {"ch": channel, "ws": workspace_id, "seq": seq},
        )
        row = await session.execute(
            text(
                "INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id) "
                "VALUES (:ws, :ch, :seq, 'issue.updated', '{}', gen_random_uuid()) RETURNING id"
            ),
            {"ws": workspace_id, "ch": channel, "seq": seq},
        )
        event_id = row.scalar_one()
        await session.execute(
            text(
                "UPDATE realtime_events SET created_at = now() - (:days || ' days')::interval WHERE id = :id"
            ),
            {"days": str(age_days), "id": event_id},
        )


async def test_purge_deletes_only_expired_rows(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_event(session_factory, workspace.id, "issue:ret", 1, age_days=10)
    await _seed_event(session_factory, workspace.id, "issue:ret", 2, age_days=1)

    deleted = await purge_expired_events(session_factory, retention=timedelta(days=7), now=datetime.now(UTC))
    assert deleted == 1
    async with session_factory() as session:
        remaining = (await session.execute(select(RealtimeEvent.seq))).scalars().all()
        channels = (await session.execute(select(RealtimeChannel.channel))).scalars().all()
    assert remaining == [2]
    assert channels == ["issue:ret"]  # channel row survives event purge


async def test_retention_loop_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_event(session_factory, workspace.id, "issue:loop", 1, age_days=30)
    stop = asyncio.Event()
    task = asyncio.create_task(
        retention_loop(
            session_factory,
            retention=timedelta(days=7),
            interval=0.05,
            stop=stop,
            clock=lambda: datetime.now(UTC),
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with session_factory() as session:
        assert (await session.execute(select(RealtimeEvent))).all() == []


# --- M3: outbox retention purge (§6.6) ---


async def _seed_outbox_event(session_factory, workspace_id, *, status, age_days, key=None):
    async with session_factory() as session, session.begin():
        row = await session.execute(
            text(
                "INSERT INTO outbox_events "
                "(workspace_id, event_type, payload, status, idempotency_key) "
                "VALUES (:ws, 'realtime.publish', '{}', :status, :key) RETURNING id"
            ),
            {"ws": workspace_id, "status": status, "key": key},
        )
        event_id = row.scalar_one()
        await session.execute(
            text("UPDATE outbox_events SET created_at = now() - (:days || ' days')::interval WHERE id = :id"),
            {"days": str(age_days), "id": event_id},
        )
    return event_id


async def _outbox_ids(session_factory):
    async with session_factory() as session:
        return set((await session.execute(select(OutboxEvent.id))).scalars().all())


async def test_outbox_purge_deletes_only_terminal_expired_rows(session_factory, workspace_factory):
    workspace = await workspace_factory()
    old_published = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=10
    )
    old_failed = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_FAILED, age_days=10
    )
    # A stuck pending row older than the window must survive: purging it would
    # silently drop queued work.
    old_pending = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PENDING, age_days=10
    )
    fresh_published = await _seed_outbox_event(
        session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=1
    )

    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC)
    )
    assert deleted == 2
    remaining = await _outbox_ids(session_factory)
    assert remaining == {old_pending, fresh_published}
    assert old_published not in remaining
    assert old_failed not in remaining


async def test_outbox_purge_respects_batch_limit(session_factory, workspace_factory):
    workspace = await workspace_factory()
    for i in range(5):
        await _seed_outbox_event(
            session_factory,
            workspace.id,
            status=OUTBOX_STATUS_PUBLISHED,
            age_days=10,
            key=f"batch-{i}",
        )
    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC), batch_limit=2
    )
    assert deleted == 2
    async with session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(OutboxEvent))
    assert remaining == 3


async def test_outbox_purge_returns_zero_and_logs_nothing_when_empty(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_outbox_event(session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=1)
    deleted = await purge_processed_outbox_events(
        session_factory, retention=timedelta(days=7), now=datetime.now(UTC)
    )
    assert deleted == 0


async def test_outbox_retention_loop_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_outbox_event(session_factory, workspace.id, status=OUTBOX_STATUS_PUBLISHED, age_days=30)
    await _seed_outbox_event(session_factory, workspace.id, status=OUTBOX_STATUS_FAILED, age_days=30)
    stop = asyncio.Event()
    task = asyncio.create_task(
        outbox_retention_loop(
            session_factory,
            retention=timedelta(days=7),
            interval=0.05,
            stop=stop,
            clock=lambda: datetime.now(UTC),
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with session_factory() as session:
        assert (await session.execute(select(OutboxEvent))).all() == []


# --- Integration ledger retention (§2.4/§2.6, MEDIUM-P3) ---


async def _seed_integration_event(session_factory, world, *, external_id, age_days):
    from mesh.db.models.integration import IntegrationEvent

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            external_event_id=external_id,
            event_type="message",
            payload={},
            signature_status="valid",
            process_status="received",
        )
        session.add(row)
        await session.flush()
        event_id = row.id
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE integration_events "
                "SET received_at = now() - (:days || ' days')::interval WHERE id = :id"
            ),
            {"days": str(age_days), "id": event_id},
        )
    return event_id


async def _seed_delivery(session_factory, world, subscription_id, *, state, age_days):
    from mesh.db.models.integration import WebhookSubscriptionDelivery

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = WebhookSubscriptionDelivery(
            workspace_id=world["ws"],
            subscription_id=subscription_id,
            event_ref=f"evt-{state}-{age_days}",
            state=state,
        )
        session.add(row)
        await session.flush()
        delivery_id = row.id
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE webhook_subscription_deliveries "
                "SET created_at = now() - (:days || ' days')::interval WHERE id = :id"
            ),
            {"days": str(age_days), "id": delivery_id},
        )
    return delivery_id


async def test_purge_integration_events_deletes_only_old_rows(session_factory):
    from mesh.db.models.integration import IntegrationEvent
    from mesh.workers.retention import purge_integration_events
    from tests.unit.integrations_support import seed_world

    world = await seed_world(session_factory)
    old = await _seed_integration_event(session_factory, world, external_id="old", age_days=40)
    fresh = await _seed_integration_event(session_factory, world, external_id="fresh", age_days=1)
    deleted = await purge_integration_events(
        session_factory, retention=timedelta(days=30), now=datetime.now(UTC)
    )
    assert deleted == 1
    async with session_factory() as session:
        remaining = set((await session.execute(select(IntegrationEvent.id))).scalars().all())
    assert remaining == {fresh}
    assert old not in remaining


async def test_purge_webhook_deliveries_never_deletes_pending(session_factory):
    """Old sent/failed rows are purged; pending rows (still in the retry
    cycle) are NEVER eligible — purging them would drop queued work."""
    from mesh.db.models.integration import WebhookSubscriptionDelivery
    from mesh.integrations import outbound as ob
    from mesh.workers.retention import purge_webhook_deliveries
    from tests.unit.integrations_support import TEST_SIGNING_SECRET, seed_world

    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, _ = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://hooks.example.com/x",
            signing_secret=TEST_SIGNING_SECRET,
        )
    old_sent = await _seed_delivery(session_factory, world, subscription.id, state="sent", age_days=40)
    old_failed = await _seed_delivery(session_factory, world, subscription.id, state="failed", age_days=40)
    # A stuck pending row older than the window MUST survive.
    old_pending = await _seed_delivery(session_factory, world, subscription.id, state="pending", age_days=40)
    fresh_sent = await _seed_delivery(session_factory, world, subscription.id, state="sent", age_days=1)
    deleted = await purge_webhook_deliveries(
        session_factory, retention=timedelta(days=30), now=datetime.now(UTC)
    )
    assert deleted == 2
    async with session_factory() as session:
        remaining = set((await session.execute(select(WebhookSubscriptionDelivery.id))).scalars().all())
    assert remaining == {old_pending, fresh_sent}
    assert old_sent not in remaining and old_failed not in remaining


async def test_integration_ledger_retention_loop_stops_on_event(session_factory):
    from mesh.db.models.integration import IntegrationEvent
    from mesh.workers.retention import integration_ledger_retention_loop
    from tests.unit.integrations_support import seed_world

    world = await seed_world(session_factory)
    await _seed_integration_event(session_factory, world, external_id="loop", age_days=60)
    stop = asyncio.Event()
    task = asyncio.create_task(
        integration_ledger_retention_loop(
            session_factory,
            retention=timedelta(days=30),
            interval=0.05,
            stop=stop,
            clock=lambda: datetime.now(UTC),
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with session_factory() as session:
        assert (await session.execute(select(IntegrationEvent))).all() == []

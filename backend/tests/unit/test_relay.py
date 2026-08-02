"""Outbox relay: SKIP LOCKED claim, dispatch, retry/failed policy (§6.6 / §2.2)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.outbox.relay import OutboxRelay, RelayResult, isolated_optional_step
from mesh.outbox.service import emit_event


async def _seed(session_factory, workspace_id, event_type="test.event", count=1):
    events = []
    async with session_factory() as session, session.begin():
        for i in range(count):
            events.append(
                await emit_event(
                    session,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    payload={"i": i},
                )
            )
    return events


async def test_run_once_publishes_with_handler(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=3)
    seen: list[str] = []

    async def handler(session, event):
        seen.append(str(event.id))
        return None

    relay = OutboxRelay(session_factory, handlers={"test.event": handler})
    result = await relay.run_once()
    assert result == RelayResult(claimed=3, published=3, failed=0)
    assert len(seen) == 3
    async with session_factory() as session:
        statuses = (await session.execute(select(OutboxEvent.status))).scalars().all()
        assert statuses == [OUTBOX_STATUS_PUBLISHED] * 3
        published_ats = (await session.execute(select(OutboxEvent.published_at))).scalars().all()
        assert all(value is not None for value in published_ats)


async def test_concurrent_relays_do_not_double_process(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=10)
    processed: list[str] = []
    lock = asyncio.Lock()

    async def handler(session, event):
        async with lock:
            processed.append(str(event.id))
        await asyncio.sleep(0.01)  # widen the concurrency window
        return None

    relays = [OutboxRelay(session_factory, handlers={"test.event": handler}, batch_size=10) for _ in range(3)]
    results = await asyncio.gather(*(relay.run_once() for relay in relays))
    assert sum(r.claimed for r in results) == 10
    assert len(processed) == 10
    assert len(set(processed)) == 10  # every event exactly once


async def test_slow_event_does_not_preclaim_later_rows_from_concurrent_relay(
    session_factory, workspace_factory
):
    """A slow handler may lock its own row, never the rest of the batch.

    This is the deterministic MES-152 regression for the production lock
    cycle: the old batch-wide transaction selected and locked every row before
    dispatching the first one.  A single blocked handler therefore prevented a
    second replica (and maintenance needing ``AccessExclusive``) from making
    progress on otherwise independent rows.
    """
    from datetime import UTC, datetime, timedelta

    workspace = await workspace_factory()
    events = await _seed(session_factory, workspace.id, count=2)
    moment = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        first_row = await session.get(OutboxEvent, events[0].id)
        second_row = await session.get(OutboxEvent, events[1].id)
        first_row.available_at = moment - timedelta(seconds=2)
        first_row.created_at = moment - timedelta(seconds=2)
        second_row.available_at = moment - timedelta(seconds=1)
        second_row.created_at = moment - timedelta(seconds=1)
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    processed: list[int] = []

    async def handler(session, event):
        event_number = int(event.payload["i"])
        processed.append(event_number)
        if event_number == 0:
            slow_started.set()
            await release_slow.wait()
        return None

    first = OutboxRelay(session_factory, handlers={"test.event": handler}, batch_size=2)
    contender = OutboxRelay(session_factory, handlers={"test.event": handler}, batch_size=1)
    first_task = asyncio.create_task(first.run_once())
    contender_result: RelayResult | None = None
    try:
        await asyncio.wait_for(slow_started.wait(), timeout=5)
        contender_result = await asyncio.wait_for(contender.run_once(), timeout=5)
    finally:
        release_slow.set()
        first_result = await asyncio.wait_for(first_task, timeout=5)

    assert contender_result == RelayResult(claimed=1, published=1, failed=0)
    assert first_result == RelayResult(claimed=1, published=1, failed=0)
    assert sorted(processed) == [0, 1]


async def test_relay_applies_bounded_lock_timeout_to_handler_transaction(
    session_factory, workspace_factory
):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id)
    observed: list[str] = []

    async def handler(session, event):
        observed.append(str((await session.execute(text("SHOW lock_timeout"))).scalar_one()))
        return None

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        lock_timeout=0.25,
    )
    assert await relay.run_once() == RelayResult(claimed=1, published=1, failed=0)
    assert observed == ["250ms"]


async def test_lock_timeout_defers_event_without_consuming_failure_budget(
    session_factory, workspace_factory
):
    """Real PostgreSQL contention yields quickly and preserves retry budget."""
    from datetime import UTC, datetime

    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id)

    async def handler(session, event_):
        await session.execute(text("SELECT count(*) FROM webhook_subscriptions"))
        return None

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        lock_timeout=0.05,
        failure_backoff=0.2,
    )
    async with session_factory() as blocker:
        await blocker.begin()
        await blocker.execute(text("LOCK TABLE webhook_subscriptions IN ACCESS EXCLUSIVE MODE"))
        try:
            result = await asyncio.wait_for(relay.run_once(), timeout=2)
        finally:
            await blocker.rollback()

    assert result == RelayResult(claimed=1, published=0, failed=0)
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 0
        assert row.available_at > datetime.now(UTC)


async def test_handler_failure_increments_attempts_then_fails(session_factory, workspace_factory):
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)

    async def boom(session, event_):
        raise RuntimeError("downstream exploded")

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": boom},
        max_attempts=3,
        failure_backoff=0,
        failure_backoff_max=0,
    )
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 1

    await relay.run_once()
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_FAILED
        assert row.delivery_attempts == 3

    # Failed rows are not re-claimed.
    result = await relay.run_once()
    assert result.claimed == 0


async def test_unregistered_event_type_is_not_claimed(session_factory, workspace_factory):
    """Multi-relay architecture (integrations.md §3.8): the general relay
    claims ONLY event types with registered handlers — unregistered types
    (e.g. ``im.send``, delivered by the dedicated ack fast relay) stay
    pending and must never consume the failure budget here."""
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, event_type="im.send", count=1)
    relay = OutboxRelay(session_factory, handlers={}, max_attempts=5)
    result = await relay.run_once()
    assert result == RelayResult()  # nothing claimed
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 0


async def test_dispatch_one_without_handler_fails_defensively(session_factory, workspace_factory):
    """The no-handler branch survives as a defensive fallback for events
    that somehow get claimed without a registered handler."""
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, event_type="nobody.handles", count=1)
    relay = OutboxRelay(session_factory, handlers={}, max_attempts=5)
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxEvent, event.id)
        frames = await relay.dispatch_one(session, row)
        assert frames == []
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.delivery_attempts == 1


async def test_run_once_empty_backlog(session_factory):
    relay = OutboxRelay(session_factory, handlers={})
    assert await relay.run_once() == RelayResult()


async def test_frames_are_fanned_out_after_commit(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=1)
    published_frames: list[tuple[str, dict]] = []

    class FakeFanOut:
        async def publish_frames(self, frames):
            published_frames.extend(frames)

    async def handler(session, event):
        return [("issue:x", {"op": "event", "seq": 1})]

    relay = OutboxRelay(session_factory, handlers={"test.event": handler}, fanout=FakeFanOut())
    await relay.run_once()
    assert published_frames == [("issue:x", {"op": "event", "seq": 1})]


async def test_committed_frames_are_fanned_out_before_a_later_claim_failure(
    session_factory, workspace_factory
):
    """A later transaction failure cannot strand an already-committed frame."""
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=2)
    published_frames: list[tuple[str, dict]] = []

    class FakeFanOut:
        async def publish_frames(self, frames):
            published_frames.extend(frames)

    async def handler(session, event):
        return [("issue:x", {"op": "event", "event_id": str(event.id)})]

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        batch_size=2,
        fanout=FakeFanOut(),
    )
    configure_calls = 0
    original_configure = relay._configure_transaction

    async def fail_second_transaction(session):
        nonlocal configure_calls
        configure_calls += 1
        if configure_calls == 2:
            raise RuntimeError("later claim failed")
        await original_configure(session)

    relay._configure_transaction = fail_second_transaction
    with pytest.raises(RuntimeError, match="later claim failed"):
        await relay.run_once()

    assert len(published_frames) == 1
    async with session_factory() as session:
        statuses = (await session.execute(select(OutboxEvent.status))).scalars().all()
    assert statuses.count(OUTBOX_STATUS_PUBLISHED) == 1
    assert statuses.count(OUTBOX_STATUS_PENDING) == 1


async def test_db_error_poison_event_does_not_block_batch(session_factory, workspace_factory):
    """A handler DB error fails only that event (savepoint); the batch proceeds."""
    from sqlalchemy import text

    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=3)
    # Poison exactly one row.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE outbox_events SET payload = '{\"poison\": true}' "
                "WHERE id = (SELECT id FROM outbox_events LIMIT 1)"
            )
        )

    async def handler(session, event):
        if event.payload.get("poison"):
            # Database-level failure: aborts the savepoint, not the batch.
            await session.execute(text("SELECT 1/0"))
        return None

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        max_attempts=2,
        failure_backoff=0,
        failure_backoff_max=0,
    )
    result = await relay.run_once()
    assert result.claimed == 3
    assert result.published == 2  # healthy events delivered in the same batch

    async with session_factory() as session:
        poison = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.status == OUTBOX_STATUS_PENDING))
        ).scalar_one()
        assert poison.delivery_attempts == 1  # increment persisted despite the error

    # Next pass claims only the poison row and fails it at max_attempts.
    result2 = await relay.run_once()
    assert result2.claimed == 1
    assert result2.failed == 1


async def test_run_forever_stops_on_event(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=1)
    processed = []

    async def handler(session, event):
        processed.append(event.id)
        return None

    relay = OutboxRelay(session_factory, handlers={"test.event": handler}, poll_interval=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run_forever(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(processed) == 1


# ---------------------------------------------------------------------------
# §6.6 — available_at claim filter + RetryableDelay (MES-82 R4-4)
# ---------------------------------------------------------------------------


async def test_claim_batch_skips_deferred_rows(session_factory, workspace_factory):
    """Rows with available_at in the future are invisible to the claim."""
    from datetime import UTC, datetime, timedelta

    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)
    # Defer the row into the future.
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxEvent, event.id)
        row.available_at = datetime.now(UTC) + timedelta(hours=1)
    relay = OutboxRelay(session_factory, handlers={"test.event": lambda s, e: None})
    async with session_factory() as session:
        assert await relay.claim_batch(session) == [], "deferred row stays invisible"
    assert (await relay.run_once()).claimed == 0
    # Bring it back into the claim window → it is claimed.
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxEvent, event.id)
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await relay.run_once()).claimed == 1


async def test_retryable_delay_defers_without_consuming_budget(session_factory, workspace_factory):
    """RetryableDelay moves available_at forward but leaves status pending and
    delivery_attempts UNCHANGED — unlike a plain failure (which increments)."""
    from datetime import UTC, datetime

    from mesh.outbox.relay import RetryableDelay

    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)

    async def busy(session, event_):
        raise RetryableDelay(delay=30, reason="token_refresh_busy")

    relay = OutboxRelay(session_factory, handlers={"test.event": busy}, max_attempts=5)
    result = await relay.run_once()
    # Not published, not failed — still claimed once this pass.
    assert result.claimed == 1 and result.published == 0 and result.failed == 0
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 0, "failure budget must NOT be consumed"
        assert row.available_at > datetime.now(UTC), "available_at moved forward"
    # The deferred row is not hot-looped: an immediate pass claims nothing.
    assert (await relay.run_once()).claimed == 0


async def test_plain_failure_increments_budget_contrast(session_factory, workspace_factory):
    """Contrast: a plain handler failure DOES increment delivery_attempts."""
    from datetime import UTC, datetime

    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)

    async def boom(session, event_):
        raise RuntimeError("hard failure")

    relay = OutboxRelay(session_factory, handlers={"test.event": boom}, max_attempts=5)
    await relay.run_once()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING
        assert row.delivery_attempts == 1
        assert row.available_at > datetime.now(UTC), "failures back off instead of hot-looping"
    assert (await relay.run_once()).claimed == 0


# ---------------------------------------------------------------------------
# MES-147: deadlock-recovery robustness (publisher must not wedge)
# ---------------------------------------------------------------------------


async def test_run_forever_survives_transient_pass_failure(session_factory, workspace_factory):
    """A failed relay pass (deadlock / connection reset) must NOT kill the loop.

    The publisher backs off briefly and retries; pending events still publish.
    Dying here leaves recovery to the external supervisor only — the wedge
    that held e2e waits pending until job timeout (MES-147).
    """
    workspace = await workspace_factory()
    await _seed(session_factory, workspace.id, count=2)

    async def handler(session, event_):
        return None

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        poll_interval=0.01,
        error_backoff=0.01,
    )
    original_run_once = relay.run_once
    failures = {"count": 0}

    async def flaky_run_once():
        if failures["count"] == 0:
            failures["count"] += 1
            raise RuntimeError("simulated transient db failure")
        return await original_run_once()

    relay.run_once = flaky_run_once
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run_forever(stop))
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            async with session_factory() as session:
                pending = (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.status == OUTBOX_STATUS_PENDING)
                    )
                ).scalars().all()
            if len(pending) == 0:
                break
            assert asyncio.get_running_loop().time() < deadline, "relay never recovered"
            await asyncio.sleep(0.02)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    assert failures["count"] == 1, "exactly the injected pass failed"
    assert not task.cancelled()


async def test_isolated_optional_step_swallows_non_db_error(session_factory):
    """Non-DB failure in an optional step: logged, skipped, txn stays usable."""
    ran: list[str] = []

    async def logic_bug():
        ran.append("step")
        raise ValueError("matcher logic bug")

    async with session_factory() as session, session.begin():
        await isolated_optional_step(session, "unit-step", "evt-1", logic_bug())
        # Outer transaction still fully usable after the swallowed failure.
        assert (await session.execute(select(1))).scalar() == 1
    assert ran == ["step"]


async def test_isolated_optional_step_reraises_db_error_and_keeps_txn_usable(session_factory):
    """DB failure in an optional step: savepoint rolls back, error re-raised,
    and the surrounding transaction is NOT aborted (the MES-147 wedge)."""
    async with session_factory() as session, session.begin():
        async def db_failure():
            # Real aborting statement (undefined table) — same abort semantics
            # as a deadlock victim inside the step.
            await session.execute(text('SELECT 1 FROM "mesh_mes147_missing"'))

        with pytest.raises(DBAPIError):
            await isolated_optional_step(session, "unit-step", "evt-1", db_failure())
        # The wedge was exactly this: statements after the swallowed deadlock
        # failed with "current transaction is aborted". Must work now.
        assert (await session.execute(select(1))).scalar() == 1


async def test_relay_batch_survives_optional_step_db_error_and_redelivers(
    session_factory, workspace_factory
):
    """End-to-end: a DB error in an isolated optional step consumes ONE failure
    attempt, keeps the batch transaction usable, and the event redelivers to
    published on the next pass — no wedged publisher, no lost event."""
    workspace = await workspace_factory()
    (event,) = await _seed(session_factory, workspace.id, count=1)
    failed_once = {"done": False}

    async def handler(session, event_):
        async def optional_step():
            if not failed_once["done"]:
                failed_once["done"] = True
                await session.execute(text('SELECT 1 FROM "mesh_mes147_missing"'))

        await isolated_optional_step(session, "optional", event_.id, optional_step())
        return None

    relay = OutboxRelay(
        session_factory,
        handlers={"test.event": handler},
        max_attempts=5,
        failure_backoff=0,
        failure_backoff_max=0,
    )

    first = await relay.run_once()
    assert first == RelayResult(claimed=1, published=0, failed=0)
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PENDING, "event stays pending for redelivery"
        assert row.delivery_attempts == 1, "failure budget consumed exactly once"

    second = await relay.run_once()
    assert second == RelayResult(claimed=1, published=1, failed=0)
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event.id)
        assert row.status == OUTBOX_STATUS_PUBLISHED

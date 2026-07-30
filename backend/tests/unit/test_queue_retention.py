"""Queue-orphan audit retention tests (integrations.md §2.10 / §3.9).

The retention loop purges ONLY ``binding_id IS NULL`` TERMINAL orphan rows past
the retention window. Live items (parent intact) and non-terminal rows are never
eligible; deletion is batch-capped; the supervisor loop honors ``stop`` and
swallows per-pass errors.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mesh.db.models.integration import IntegrationMessageQueue
from mesh.workers.queue_retention import (
    QUEUE_AUDIT_PURGE_BATCH_LIMIT,
    integration_queue_audit_retention_loop,
    purge_queue_audit_orphans,
)
from tests.unit.integrations_support import make_binding, seed_world

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=45)  # past the default 30-day window
RECENT = NOW - timedelta(days=1)  # within the window
RETENTION = timedelta(days=30)


def _orphan_item(
    *,
    workspace_id,
    conversation_key: str,
    seq: int,
    state: str,
    updated_at: datetime,
    project_id=None,
    binding_display: str = "binding-display",
) -> IntegrationMessageQueue:
    """A self-describing terminal orphan (NULL parents — allowed only terminal)."""
    return IntegrationMessageQueue(
        workspace_id=workspace_id,
        integration_id=None,
        binding_id=None,
        binding_display=binding_display,
        project_id_snapshot=project_id,
        conversation_key=conversation_key,
        seq=seq,
        dispatch_mode="serial_conversation",
        state=state,
        message_excerpt="orphan excerpt",
        sender_identity_key="slack:T_TEST:U_ORPHAN",
        enqueued_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
    )


async def _seed(session_factory, *items: IntegrationMessageQueue) -> list[uuid.UUID]:
    async with session_factory() as session, session.begin():
        for item in items:
            session.add(item)
        await session.flush()
        return [item.id for item in items]


async def _existing_ids(session_factory, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    from sqlalchemy import select

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationMessageQueue.id).where(IntegrationMessageQueue.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        return set(rows)


# ---------------------------------------------------------------------------
# Eligibility matrix
# ---------------------------------------------------------------------------


async def test_purge_removes_only_terminal_orphans_past_window(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_RET"
    )
    ws = world["ws"]

    # A: terminal orphan, old → ELIGIBLE.
    (a_id,) = await _seed(
        session_factory,
        _orphan_item(workspace_id=ws, conversation_key="conv:orphan:a", seq=1, state="done", updated_at=OLD),
    )
    # B: terminal but parent intact, old → NOT eligible (live audit belongs to
    # the binding; only binding_id IS NULL rows are purgeable).
    # C: pending with parent, old → NOT eligible (non-terminal).
    async with session_factory() as session, session.begin():
        b_item = IntegrationMessageQueue(
            workspace_id=ws,
            integration_id=binding.integration_id,
            binding_id=binding.id,
            conversation_key="conv:bound:b",
            seq=1,
            dispatch_mode="serial_conversation",
            state="done",
            message_excerpt="bound terminal",
            sender_identity_key="slack:T_TEST:U_B",
            enqueued_at=OLD,
            created_at=OLD,
            updated_at=OLD,
        )
        c_item = IntegrationMessageQueue(
            workspace_id=ws,
            integration_id=binding.integration_id,
            binding_id=binding.id,
            conversation_key="conv:bound:c",
            seq=1,
            dispatch_mode="serial_conversation",
            state="pending",
            message_excerpt="bound pending",
            sender_identity_key="slack:T_TEST:U_C",
            enqueued_at=OLD,
            created_at=OLD,
            updated_at=OLD,
        )
        session.add_all([b_item, c_item])
        await session.flush()
        b_id, c_id = b_item.id, c_item.id
    # D: terminal orphan but RECENT → NOT eligible (inside the window).
    (d_id,) = await _seed(
        session_factory,
        _orphan_item(
            workspace_id=ws, conversation_key="conv:orphan:d", seq=1,
            state="cancelled", updated_at=RECENT,
        ),
    )

    deleted = await purge_queue_audit_orphans(session_factory, retention=RETENTION, now=NOW)
    assert deleted == 1

    survivors = await _existing_ids(session_factory, [a_id, b_id, c_id, d_id])
    assert a_id not in survivors, "old terminal orphan purged"
    assert b_id in survivors, "terminal item with a parent is never purged"
    assert c_id in survivors, "pending item is never purged"
    assert d_id in survivors, "recent orphan is inside the retention window"


async def test_purge_each_terminal_state_eligible(session_factory):
    """done / failed / cancelled orphans past the window are all purged."""
    world = await seed_world(session_factory)
    ws = world["ws"]
    ids = await _seed(
        session_factory,
        _orphan_item(workspace_id=ws, conversation_key="conv:t:1", seq=1, state="done", updated_at=OLD),
        _orphan_item(workspace_id=ws, conversation_key="conv:t:2", seq=1, state="failed", updated_at=OLD),
        _orphan_item(workspace_id=ws, conversation_key="conv:t:3", seq=1, state="cancelled", updated_at=OLD),
    )
    deleted = await purge_queue_audit_orphans(session_factory, retention=RETENTION, now=NOW)
    assert deleted == 3
    assert await _existing_ids(session_factory, ids) == set()


async def test_purge_batch_limit_caps_deletion(session_factory):
    """A backlog larger than the batch cap is reclaimed incrementally."""
    world = await seed_world(session_factory)
    ws = world["ws"]
    ids = await _seed(
        session_factory,
        *[
            _orphan_item(
                workspace_id=ws, conversation_key=f"conv:cap:{i}", seq=1, state="done", updated_at=OLD
            )
            for i in range(3)
        ],
    )
    deleted = await purge_queue_audit_orphans(
        session_factory, retention=RETENTION, now=NOW, batch_limit=2
    )
    assert deleted == 2
    survivors = await _existing_ids(session_factory, ids)
    assert len(survivors) == 1
    # A second pass reclaims the remainder.
    deleted_again = await purge_queue_audit_orphans(
        session_factory, retention=RETENTION, now=NOW, batch_limit=2
    )
    assert deleted_again == 1
    assert await _existing_ids(session_factory, ids) == set()


def test_batch_cap_constant_matches_spec():
    # §3.9: the worker purges in bounded batches (1000 per pass).
    assert QUEUE_AUDIT_PURGE_BATCH_LIMIT == 1000


# ---------------------------------------------------------------------------
# Supervisor loop
# ---------------------------------------------------------------------------


async def test_loop_purges_and_honors_stop(session_factory):
    world = await seed_world(session_factory)
    ws = world["ws"]
    (orphan_id,) = await _seed(
        session_factory,
        _orphan_item(workspace_id=ws, conversation_key="conv:loop:a", seq=1, state="done", updated_at=OLD),
    )
    settings = SimpleNamespace(
        im_queue_audit_retention=RETENTION, im_queue_audit_retention_interval=0.05
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        integration_queue_audit_retention_loop(session_factory, settings=settings, stop=stop)
    )
    # Let at least one pass run, then signal stop and await a clean exit.
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert await _existing_ids(session_factory, [orphan_id]) == set(), "loop purged the orphan"


async def test_loop_swallows_per_pass_errors(session_factory):
    """A failing purge pass is logged and the loop keeps running until stop."""

    def broken_factory():
        raise RuntimeError("transient storage failure")

    settings = SimpleNamespace(
        im_queue_audit_retention=RETENTION, im_queue_audit_retention_interval=0.05
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        integration_queue_audit_retention_loop(broken_factory, settings=settings, stop=stop)
    )
    await asyncio.sleep(0.15)  # several failing passes
    assert not task.done(), "the loop must survive per-pass errors"
    stop.set()
    await asyncio.wait_for(task, timeout=5)  # exits cleanly on stop

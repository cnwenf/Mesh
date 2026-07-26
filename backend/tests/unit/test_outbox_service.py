"""Outbox write services (§6.6)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from mesh.db.models.outbox import OUTBOX_STATUS_PENDING, OutboxEvent
from mesh.errors import BusinessRuleError
from mesh.events.vocab import REALTIME_PUBLISH, UnregisteredEventError
from mesh.outbox.service import emit_event, emit_realtime, scope_idempotency_key


async def test_emit_event_inserts_pending_row_in_caller_transaction(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        event = await emit_event(
            session,
            workspace_id=workspace.id,
            event_type="issue.assigned",
            payload={"issue_id": "x"},
        )
        assert event.status == OUTBOX_STATUS_PENDING
        assert event.id is not None
    # Committed and visible from another session.
    async with session_factory() as other:
        row = await other.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert row is not None
        assert row.payload == {"issue_id": "x"}


async def test_emit_event_duplicate_idempotency_key_returns_existing(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        first = await emit_event(
            session,
            workspace_id=workspace.id,
            event_type="execution.enqueue",
            payload={"n": 1},
            idempotency_key="k-1",
        )
        second = await emit_event(
            session,
            workspace_id=workspace.id,
            event_type="execution.enqueue",
            payload={"n": 2},
            idempotency_key="k-1",
        )
        assert second.id == first.id
    async with session_factory() as other:
        count = len(
            (
                await other.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key
                        == scope_idempotency_key(workspace.id, "k-1")
                    )
                )
            ).all()
        )
        assert count == 1


async def test_emit_event_stored_key_carries_workspace_scope(session_factory, workspace_factory):
    """L1: the helper forces workspace context into the stored key so dedup
    can never match a row from another tenant."""
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        event = await emit_event(
            session,
            workspace_id=workspace.id,
            event_type="execution.enqueue",
            payload={},
            idempotency_key="client-key",
        )
    assert event.idempotency_key == f"ws:{workspace.id}:client-key"
    assert event.idempotency_key.endswith(":client-key")


async def test_emit_event_same_key_in_different_workspaces_is_not_deduped(
    session_factory, workspace_factory
):
    """L1: the same client-supplied key in two workspaces must create two rows —
    global de-duplication would return a foreign tenant's row."""
    workspace_a = await workspace_factory(name="A")
    workspace_b = await workspace_factory(name="B")
    async with session_factory() as session, session.begin():
        event_a = await emit_event(
            session,
            workspace_id=workspace_a.id,
            event_type="execution.enqueue",
            payload={"w": "a"},
            idempotency_key="shared-key",
        )
        event_b = await emit_event(
            session,
            workspace_id=workspace_b.id,
            event_type="execution.enqueue",
            payload={"w": "b"},
            idempotency_key="shared-key",
        )
        assert event_b.id != event_a.id
    async with session_factory() as other:
        total = await other.scalar(select(func.count()).select_from(OutboxEvent))
        assert total == 2


async def test_emit_realtime_writes_realtime_publish_envelope(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        event = await emit_realtime(
            session,
            workspace_id=workspace.id,
            channel="issue:abc",
            event="issue.updated",
            data={"title": "t"},
        )
    assert event.event_type == REALTIME_PUBLISH
    assert event.payload == {"channel": "issue:abc", "event": "issue.updated", "data": {"title": "t"}}


async def test_emit_realtime_rejects_unregistered_event_name(session_factory, workspace_factory):
    workspace = await workspace_factory()
    with pytest.raises(UnregisteredEventError):
        async with session_factory() as session, session.begin():
            await emit_realtime(
                session,
                workspace_id=workspace.id,
                channel="issue:abc",
                event="agent.run_started",
                data={},
            )


async def test_emit_realtime_rejects_invalid_channel(session_factory, workspace_factory):
    workspace = await workspace_factory()
    with pytest.raises(BusinessRuleError) as excinfo:
        async with session_factory() as session, session.begin():
            await emit_realtime(
                session,
                workspace_id=workspace.id,
                channel="bad channel",
                event="issue.updated",
                data={},
            )
    assert excinfo.value.code == "invalid_channel"

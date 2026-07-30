"""queue_events tests — terminal write-back (§3.9) + invalidation shapes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from mesh.db.models.outbox import OutboxEvent
from mesh.integrations.queue_events import (
    queue_execution_finished_handler,
    stopped_feedback_text,
)
from tests.unit.test_integration_dispatcher import (
    _item,
    _seed_execution,
    _seed_item,
    _seed_world,
)

pytestmark = pytest.mark.unit


class _FakeEvent:
    def __init__(self, payload: dict):
        self.payload = payload
        self.id = uuid.uuid4()


async def _bind(session_factory, item_id, exec_id, state: str = "processing"):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE integration_message_queue SET execution_id = :e, state = :s, "
                "started_at = now() WHERE id = :id"
            ),
            {"e": exec_id, "s": state, "id": item_id},
        )


class TestTerminalWriteBack:
    async def test_completed_to_done(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="processing")
        await _bind(session_factory, item_id, exec_id)

        await _run_handler(session_factory, exec_id, "completed")

        item = await _item(session_factory, item_id)
        assert item.state == "done"
        assert item.finished_at is not None
        wakes = await _events(session_factory, "imq.dispatch_wake")
        assert len(wakes) == 1
        updates = await _events(session_factory, "realtime.publish")
        assert any(
            e.payload.get("event") == "integration.queue_updated" for e in updates
        )

    async def test_failed_statuses_map_to_failed(self, session_factory):
        for status in ("failed", "timeout"):
            world = await _seed_world(session_factory)
            exec_id = await _seed_execution(session_factory, world, status=status)
            # distinct conversation per iteration — (conversation_key, seq)
            # uniqueness is global
            conv = f"dingtalk:dingsample:cid-{status}=="
            item_id, _ = await _seed_item(
                session_factory, world, seq=1, state="processing", conv_key=conv
            )
            await _bind(session_factory, item_id, exec_id)
            await _run_handler(session_factory, exec_id, status)
            item = await _item(session_factory, item_id)
            assert item.state == "failed"

    async def test_cancelling_to_cancelled_emits_terminal_feedback(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="cancelled")
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="cancelling")
        await _bind(session_factory, item_id, exec_id, state="cancelling")

        await _run_handler(session_factory, exec_id, "cancelled", failure_reason="cancelled_by_command")

        item = await _item(session_factory, item_id)
        assert item.state == "cancelled"
        sends = await _events(session_factory, "im.send")
        assert len(sends) == 1
        assert sends[0].payload["stage"] == "stopped"
        assert sends[0].payload["text"] == stopped_feedback_text(item.message_excerpt)

    async def test_terminal_to_terminal_is_noop(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="done")
        await _bind(session_factory, item_id, exec_id, state="done")
        before = len(await _events(session_factory, "imq.dispatch_wake"))
        await _run_handler(session_factory, exec_id, "completed")
        after = len(await _events(session_factory, "imq.dispatch_wake"))
        assert before == after  # no duplicate wake on redelivery

    async def test_no_queue_item_noop(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        await _run_handler(session_factory, exec_id, "completed")  # no item bound
        assert await _events(session_factory, "imq.dispatch_wake") == []

    async def test_project_level_payload_hides_conversation_key(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="processing")
        project_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE integration_message_queue SET project_id_snapshot = :p WHERE id = :id"
                ),
                {"p": project_id, "id": item_id},
            )
        await _bind(session_factory, item_id, exec_id)

        await _run_handler(session_factory, exec_id, "completed")

        updates = await _events(session_factory, "realtime.publish")
        qu = [
            e for e in updates if e.payload.get("event") == "integration.queue_updated"
        ]
        assert len(qu) == 1
        data = qu[0].payload["data"]
        assert "conversation_key" not in data  # project isolation (写死)
        assert data["scope"] == "project"
        assert data["integration_id"] == str(world["integration"])


async def _run_handler(session_factory, exec_id, status, failure_reason=None):
    async with session_factory() as session, session.begin():
        await queue_execution_finished_handler(
            session,
            _FakeEvent(
                {
                    "execution_id": str(exec_id),
                    "status": status,
                    "failure_reason": failure_reason,
                }
            ),
        )


async def _events(session_factory, event_type) -> list[OutboxEvent]:
    async with session_factory() as session:
        return list(
            
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == event_type)
                    )
                )
                .scalars()
                .all()
            
        )

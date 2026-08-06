"""Runtime terminal notification contract regressions (agent.md §4.7).

The producer and the unified inbox consumer share one payload vocabulary.
These tests intentionally exercise the persisted outbox row so a producer-only
dictionary assertion cannot drift from relay input unnoticed.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution, TaskLogSegment
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import seed_default_statuses
from mesh.runtime.attempts import _emit_terminal_notification
from tests.unit.runtime_support import make_execution, seed_world


async def test_failed_execution_emits_consumable_deep_link_notification(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=world["ws_id"])
        member = await session.get(Member, world["member_id"])
        session.expunge(member)
    issue = await IssueService(session_factory).create_issue(
        actor=member,
        workspace_id=world["ws_id"],
        body=CreateIssueRequest(title="Observe a failed run"),
    )
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        issue_id=uuid.UUID(issue["id"]),
    )

    async with session_factory() as session, session.begin():
        row = await session.get(TaskExecution, execution.id)
        row.status = "failed"
        row.failure_reason = "provider_unavailable"
        await _emit_terminal_notification(
            session,
            workspace_id=world["ws_id"],
            execution=row,
        )

    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
        )

    assert event is not None
    assert event.payload["type"] == "execution_finished"
    assert event.payload["execution_status"] == "failed"
    assert event.payload["execution_id"] == str(execution.id)
    assert event.payload["issue_id"] == issue["id"]
    assert event.payload["failure_reason"] == "provider_unavailable"
    assert "kind" not in event.payload
    assert "status" not in event.payload


async def test_failed_execution_notification_includes_redacted_log_tail(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        status="failed",
    )
    attempt_id = uuid.uuid4()
    first_ref = "logs/first.json"
    second_ref = "logs/second.json"
    blobs = {
        first_ref: json.dumps(
            [
                {"s": "stdout", "o": 0, "l": "line-1"},
                {"s": "stdout", "o": 7, "l": "line-2"},
                {"s": "stderr", "o": 14, "l": "line-3"},
            ]
        ).encode(),
        second_ref: json.dumps(
            [
                {"s": "stdout", "o": 21, "l": "line-4"},
                {"s": "stdout", "o": 28, "l": "line-5"},
                {"s": "stderr", "o": 35, "l": "secret=[REDACTED]"},
                {"s": "stderr", "o": 53, "l": "line-7\u001b[31m"},
            ]
        ).encode(),
    }

    class StubStorage:
        async def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
            assert max_bytes <= 2 * 1024 * 1024
            return blobs[key]

    async with session_factory() as session, session.begin():
        session.add(
            ExecutionAttempt(
                id=attempt_id,
                workspace_id=world["ws_id"],
                execution_id=execution.id,
                attempt_number=1,
                status="failed",
                failure_reason="provider_unavailable",
            )
        )
        await session.flush()
        session.add_all(
            [
                TaskLogSegment(
                    workspace_id=world["ws_id"],
                    attempt_id=attempt_id,
                    start_offset=0,
                    end_offset=21,
                    storage_ref=first_ref,
                    line_count=3,
                    sealed=True,
                ),
                TaskLogSegment(
                    workspace_id=world["ws_id"],
                    attempt_id=attempt_id,
                    start_offset=21,
                    end_offset=61,
                    storage_ref=second_ref,
                    line_count=4,
                    sealed=True,
                ),
            ]
        )
        row = await session.get(TaskExecution, execution.id)
        row.failure_reason = "provider_unavailable"
        await _emit_terminal_notification(
            session,
            workspace_id=world["ws_id"],
            execution=row,
            attempt_id=attempt_id,
            storage=StubStorage(),
        )

    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
        )

    assert event is not None
    assert event.payload["log_summary"] == (
        "line-3\nline-4\nline-5\nsecret=[REDACTED]\nline-7"
    )
    assert "provider_unavailable" in event.payload["preview"]
    assert "line-7" in event.payload["preview"]

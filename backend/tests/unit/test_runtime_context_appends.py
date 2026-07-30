"""Runtime context appends — the /btw landing mechanism (runtime.md §3.2, MES-82).

Service-level tests against REAL PostgreSQL implementing integrations.md §5.6
①–⑥ and the executable guard chains schema_r2_validation.sql T39-10 / T39-19:

* M3 caps (count 20 / chars 32000) enforced under the ``eca:`` execution-level
  advisory lock — concurrent writers never pierce the caps (§5.6 ⑤);
* single-pointer receipt ACK closure (T39-10): A receipt → requeue reset → B
  re-receives → wrong-lease 0 rows (receipt untouched, watermark unmoved) →
  correct lease overwrites → B GET empty → A late ACK fenced out;
* approval-resume reset chain (T39-19): A ACK → suspend → approve reset → B
  re-receives from the persisted watermark (at-least-once, §5.6 ③);
* attempt-scoped GET filter + ``inject_context`` downlink ``from_seq`` = server
  watermark (§5.6 ④);
* append state gates (cancelling / terminal) and heartbeat integration.

Delivery is **at-least-once** and append payloads are **untrusted data**
(README §6.15): a duplicate ``(execution_id, seq)`` block is semantically
identical to a single block — never a second instruction.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mesh.db.models.integration import ExecutionContextAppend
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError
from mesh.runtime import context_appends as ca
from mesh.runtime.service import RuntimeService
from tests.unit.runtime_support import (
    make_execution,
    make_runtime,
    make_settings,
    seed_world,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Seed / inspection helpers (models directly; TaskExecution needs agent_id +
# idempotency_key + spec/label/capability/snapshot columns — see T39 seed).
# ---------------------------------------------------------------------------


async def _set_execution_status(session_factory, execution_id: uuid.UUID, status: str) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET status = :s WHERE id = :e"),
            {"s": status, "e": execution_id},
        )


async def make_attempt(
    session_factory,
    execution: TaskExecution,
    *,
    runtime_id: uuid.UUID,
    attempt_number: int,
    status: str = "running",
    lease_seq: int = 0,
    failure_reason: str | None = None,
) -> ExecutionAttempt:
    attempt = ExecutionAttempt(
        workspace_id=execution.workspace_id,
        execution_id=execution.id,
        attempt_number=attempt_number,
        runtime_id=runtime_id,
        status=status,
        lease_seq=lease_seq,
        failure_reason=failure_reason,
    )
    async with session_factory() as session, session.begin():
        session.add(attempt)
        await session.flush()
        session.expunge(attempt)
    return attempt


async def append_one(
    session_factory,
    settings,
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    text_: str,
) -> ExecutionContextAppend:
    """One append in its OWN transaction (advisory lock is tx-scoped)."""
    async with session_factory() as session, session.begin():
        return await ca.append_context(
            session,
            settings=settings,
            workspace_id=workspace_id,
            execution_id=execution_id,
            payload={"text": text_},
        )


async def add_appends(session_factory, settings, execution: TaskExecution, n: int) -> None:
    for i in range(n):
        await append_one(
            session_factory,
            settings,
            workspace_id=execution.workspace_id,
            execution_id=execution.id,
            text_=f"btw-{i}",
        )


async def ack_one(
    session_factory,
    *,
    workspace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    attempt: ExecutionAttempt,
    execution: TaskExecution,
    reported: int,
    lease: int,
) -> int:
    async with session_factory() as session, session.begin():
        return await ca.ack_context_progress(
            session,
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            entries=[
                {
                    "attempt_id": attempt.id,
                    "execution_id": execution.id,
                    "injected_through_seq": reported,
                    "lease_seq": lease,
                }
            ],
        )


async def get_watermark(session_factory, execution_id: uuid.UUID) -> int:
    async with session_factory() as session:
        return (
            await session.execute(
                text("SELECT context_injected_through_seq FROM task_executions WHERE id = :e"),
                {"e": execution_id},
            )
        ).scalar_one()


async def get_receipts(
    session_factory, execution_id: uuid.UUID
) -> list[tuple[int, uuid.UUID | None, object]]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ExecutionContextAppend)
                .where(ExecutionContextAppend.execution_id == execution_id)
                .order_by(ExecutionContextAppend.seq)
            )
        ).scalars().all()
    return [(r.seq, r.injected_attempt_id, r.injected_at) for r in rows]


async def simulate_requeue(session_factory, execution: TaskExecution, attempt: ExecutionAttempt) -> None:
    """Reaper requeue: reclaim attempt, execution → queued, reset receipts.

    Holds the ``task_executions`` FOR UPDATE lock across the reset (R7-2 lock
    contract — same order as the ACK path).
    """
    async with session_factory() as session, session.begin():
        await session.execute(
            select(TaskExecution).where(TaskExecution.id == execution.id).with_for_update()
        )
        row = await session.get(ExecutionAttempt, attempt.id)
        row.status = "reclaimed"
        await session.execute(
            text("UPDATE task_executions SET status = 'queued' WHERE id = :e"),
            {"e": execution.id},
        )
        await ca.reset_context_receipts_tx(session, execution_id=execution.id)


# ---------------------------------------------------------------------------
# M3 caps (§5.6 ⑤, integrations.md「/btw 追加上限」)
# ---------------------------------------------------------------------------


async def test_append_count_cap_rejects_21st(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")

    for i in range(20):
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=execution.id, text_=f"m{i}",
        )
    with pytest.raises(BusinessRuleError) as exc:
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=execution.id, text_="over",
        )
    assert exc.value.code == "append_limit_exceeded"

    async with session_factory() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM execution_context_appends WHERE execution_id = :e"),
                {"e": execution.id},
            )
        ).scalar_one()
    assert count == 20


async def test_append_char_cap_rejects_overflow(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")

    big = "x" * 20000
    # First 20000-char append fits (0 + 20000 <= 32000).
    await append_one(
        session_factory, settings,
        workspace_id=world["ws_id"], execution_id=execution.id, text_=big,
    )
    # Second would push the cumulative total to 40000 > 32000 → rejected.
    with pytest.raises(BusinessRuleError) as exc:
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=execution.id, text_=big,
        )
    assert exc.value.code == "append_limit_exceeded"


async def test_append_concurrent_writers_respect_caps(session_factory):
    """§5.6 ⑤: N concurrent /btw writes under the eca: lock never pierce caps."""
    world = await seed_world(session_factory)
    settings = make_settings()
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")

    async def one(i: int) -> bool:
        try:
            await append_one(
                session_factory, settings,
                workspace_id=world["ws_id"], execution_id=execution.id, text_=f"c{i}",
            )
            return True
        except BusinessRuleError as exc:  # noqa: PERF203
            assert exc.code == "append_limit_exceeded"
            return False

    results = await asyncio.gather(*[one(i) for i in range(25)])
    assert sum(results) == 20  # exactly the cap succeed

    async with session_factory() as session:
        seqs = (
            await session.execute(
                text("SELECT seq FROM execution_context_appends WHERE execution_id = :e"),
                {"e": execution.id},
            )
        ).scalars().all()
    assert sorted(seqs) == list(range(1, 21))  # distinct, gapless 1..20


async def test_append_ten_concurrent_all_succeed_distinct_seq(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")

    async def one(i: int) -> bool:
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=execution.id, text_=f"t{i}",
        )
        return True

    results = await asyncio.gather(*[one(i) for i in range(10)])
    assert sum(results) == 10  # all 10 under the cap succeed
    async with session_factory() as session:
        seqs = (
            await session.execute(
                text("SELECT seq FROM execution_context_appends WHERE execution_id = :e"),
                {"e": execution.id},
            )
        ).scalars().all()
    assert sorted(seqs) == list(range(1, 11))


# ---------------------------------------------------------------------------
# T39-10: single-pointer receipt ACK closure chain (R6-1/R7-1)
# ---------------------------------------------------------------------------


async def test_t39_10_receipt_closure_chain(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 4)
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=3
    )

    # ① A receipt seq<=4 (fencing: current valid attempt) → 4 rows, watermark 4.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=4, lease=3,
        )
        == 4
    )
    assert await get_watermark(session_factory, execution.id) == 4
    assert all(r[1] == attempt_a.id for r in await get_receipts(session_factory, execution.id))

    # ② A reclaimed + requeue: receipts cleared, watermark reset 0.
    await simulate_requeue(session_factory, execution, attempt_a)
    receipts = await get_receipts(session_factory, execution.id)
    assert all(r[1] is None and r[2] is None for r in receipts)
    assert await get_watermark(session_factory, execution.id) == 0

    # ③ B claims (attempt 2, lease 1) → GET hits all 4 (re-visible, not lost).
    await _set_execution_status(session_factory, execution.id, "running")
    attempt_b = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=2, lease_seq=1
    )
    async with session_factory() as session:
        pending = await ca.list_pending_appends(
            session, workspace_id=world["ws_id"], execution_id=execution.id,
            since_seq=0, current_attempt_id=attempt_b.id,
        )
    assert len(pending) == 4

    # ④a wrong lease_seq (99): whole ACK 0 rows, receipts untouched, watermark 0.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_b, execution=execution, reported=4, lease=99,
        )
        == 0
    )
    assert await get_watermark(session_factory, execution.id) == 0
    assert all(r[1] is None for r in await get_receipts(session_factory, execution.id))

    # ④b correct lease (1) on the same rows → exactly 4, watermark advances to 4.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_b, execution=execution, reported=4, lease=1,
        )
        == 4
    )
    assert await get_watermark(session_factory, execution.id) == 4

    # ⑤ B re-GET → 0 rows (current-attempt receipted rows not redelivered).
    async with session_factory() as session:
        pending = await ca.list_pending_appends(
            session, workspace_id=world["ws_id"], execution_id=execution.id,
            since_seq=0, current_attempt_id=attempt_b.id,
        )
    assert pending == []

    # ⑥ A late ACK (reclaimed, not latest) → fenced 0 rows, B receipts intact.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=4, lease=3,
        )
        == 0
    )
    assert all(r[1] == attempt_b.id for r in await get_receipts(session_factory, execution.id))
    assert await get_watermark(session_factory, execution.id) == 4


# ---------------------------------------------------------------------------
# T39-19: approval-resume receipt reset chain (R7-2)
# ---------------------------------------------------------------------------


async def test_t39_19_approval_resume_chain(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 4)
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=2
    )

    # ① A ACKs all 4 (lease 2) → watermark recomputed to 4.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=4, lease=2,
        )
        == 4
    )
    assert await get_watermark(session_factory, execution.id) == 4

    # ② approval suspend: attempt A cancelled(awaiting_approval), exec parked.
    async with session_factory() as session, session.begin():
        row = await session.get(ExecutionAttempt, attempt_a.id)
        row.status = "cancelled"
        row.failure_reason = "awaiting_approval"
        await session.execute(
            text("UPDATE task_executions SET status = 'awaiting_approval' WHERE id = :e"),
            {"e": execution.id},
        )

    # ③ approve-resume: execution → queued, SAME row-lock tx clears receipts +
    #    resets the watermark (R7-2 unified reset, same path as requeue).
    async with session_factory() as session, session.begin():
        await session.execute(
            select(TaskExecution).where(TaskExecution.id == execution.id).with_for_update()
        )
        await session.execute(
            text("UPDATE task_executions SET status = 'queued' WHERE id = :e"),
            {"e": execution.id},
        )
        await ca.reset_context_receipts_tx(session, execution_id=execution.id)
    receipts = await get_receipts(session_factory, execution.id)
    assert all(r[1] is None and r[2] is None for r in receipts)
    assert await get_watermark(session_factory, execution.id) == 0

    # ④ B claims → re-GETs all 4 from the persisted watermark (at-least-once).
    await _set_execution_status(session_factory, execution.id, "running")
    attempt_b = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=2, lease_seq=1
    )
    watermark = await get_watermark(session_factory, execution.id)
    async with session_factory() as session:
        pending = await ca.list_pending_appends(
            session, workspace_id=world["ws_id"], execution_id=execution.id,
            since_seq=watermark, current_attempt_id=attempt_b.id,
        )
    assert len(pending) == 4

    # ⑤ B ACKs → receipts all B, watermark back to 4; A late ACK fenced 0 rows.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_b, execution=execution, reported=4, lease=1,
        )
        == 4
    )
    assert await get_watermark(session_factory, execution.id) == 4
    assert all(r[1] == attempt_b.id for r in await get_receipts(session_factory, execution.id))
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=4, lease=2,
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Attempt-scoped GET filter (§5.6 ④)
# ---------------------------------------------------------------------------


async def test_get_filter_attempt_scoped(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 4)
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=5
    )
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=4, lease=5,
        )
        == 4
    )

    # Current attempt A: its own receipted rows are NOT redelivered.
    async with session_factory() as session:
        assert (
            await ca.list_pending_appends(
                session, workspace_id=world["ws_id"], execution_id=execution.id,
                since_seq=0, current_attempt_id=attempt_a.id,
            )
            == []
        )

    # A different attempt sees A's receipts (other-attempt rows included).
    other = uuid.uuid4()
    async with session_factory() as session:
        rows = await ca.list_pending_appends(
            session, workspace_id=world["ws_id"], execution_id=execution.id,
            since_seq=0, current_attempt_id=other,
        )
    assert [r.seq for r in rows] == [1, 2, 3, 4]

    # since_seq bounds the window.
    async with session_factory() as session:
        rows = await ca.list_pending_appends(
            session, workspace_id=world["ws_id"], execution_id=execution.id,
            since_seq=2, current_attempt_id=other,
        )
    assert [r.seq for r in rows] == [3, 4]


# ---------------------------------------------------------------------------
# inject_context downlink (§5.6 ④, runtime.md「inject_context 下行指令」)
# ---------------------------------------------------------------------------


async def test_compute_inject_commands_uses_server_watermark(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=1
    )

    async def commands() -> list[dict]:
        async with session_factory() as session:
            attempts = (
                await session.execute(select(ExecutionAttempt).where(ExecutionAttempt.id == attempt_a.id))
            ).scalars().all()
            return await ca.compute_inject_commands(
                session, workspace_id=world["ws_id"], runtime_id=runtime.id, attempt_rows=attempts
            )

    # No appends → no command.
    assert await commands() == []

    # Append seq 1 (watermark 0) → inject_context from_seq=0.
    await append_one(
        session_factory, settings,
        workspace_id=world["ws_id"], execution_id=execution.id, text_="x",
    )
    cmds = await commands()
    assert cmds == [
        {
            "type": "inject_context",
            "attempt_id": str(attempt_a.id),
            "execution_id": str(execution.id),
            "from_seq": 0,
        }
    ]

    # A ACKs seq 1 → watermark 1 → nothing beyond it → no command.
    assert (
        await ack_one(
            session_factory, workspace_id=world["ws_id"], runtime_id=runtime.id,
            attempt=attempt_a, execution=execution, reported=1, lease=1,
        )
        == 1
    )
    assert await commands() == []

    # Append seq 2 → command resumes from the SERVER watermark (1).
    await append_one(
        session_factory, settings,
        workspace_id=world["ws_id"], execution_id=execution.id, text_="y",
    )
    cmds = await commands()
    assert len(cmds) == 1 and cmds[0]["from_seq"] == 1


async def test_compute_inject_commands_empty_attempts(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session:
        assert (
            await ca.compute_inject_commands(
                session, workspace_id=world["ws_id"], runtime_id=uuid.uuid4(), attempt_rows=[]
            )
            == []
        )


# ---------------------------------------------------------------------------
# Append state gates (integrations.md §3.7, runtime.md「写入方与准入」)
# ---------------------------------------------------------------------------


async def test_append_state_gates(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()

    # cancelling → append_not_acceptable ("任务正在停止,无法补充").
    cancelling = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="cancelling")
    with pytest.raises(BusinessRuleError) as exc:
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=cancelling.id, text_="x",
        )
    assert exc.value.code == "append_not_acceptable"

    # awaiting_approval is parked → also not acceptable.
    parked = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="awaiting_approval"
    )
    with pytest.raises(BusinessRuleError) as exc:
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=parked.id, text_="x",
        )
    assert exc.value.code == "append_not_acceptable"

    # terminal statuses → append_execution_terminal ("任务已结束").
    for terminal in ("completed", "failed", "cancelled", "timeout"):
        done = await make_execution(session_factory, world["ws_id"], world["agent_id"], status=terminal)
        with pytest.raises(BusinessRuleError) as exc:
            await append_one(
                session_factory, settings,
                workspace_id=world["ws_id"], execution_id=done.id, text_="x",
            )
        assert exc.value.code == "append_execution_terminal"

    # missing execution → 404.
    with pytest.raises(NotFoundError):
        await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=uuid.uuid4(), text_="x",
        )


async def test_append_accepts_queued_claimed_running(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    for status in ("queued", "claimed", "running"):
        execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status=status)
        row = await append_one(
            session_factory, settings,
            workspace_id=world["ws_id"], execution_id=execution.id, text_="ok",
        )
        assert row.seq == 1 and row.source == "im_btw"


# ---------------------------------------------------------------------------
# Heartbeat integration (runtime.md §3.2 context_progress + inject downlink)
# ---------------------------------------------------------------------------


async def test_heartbeat_persists_receipts_and_downlinks_inject(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    service = RuntimeService(session_factory, settings)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=3
    )
    await add_appends(session_factory, settings, execution, 4)

    # Heartbeat ACKs seq 1..4 → receipts + watermark persisted.
    resp = await service.heartbeat(
        runtime=runtime, current_load=1, health="healthy", metrics={}, inflight=[str(attempt_a.id)],
        context_progress=[
            {
                "attempt_id": attempt_a.id,
                "execution_id": execution.id,
                "injected_through_seq": 4,
                "lease_seq": 3,
            }
        ],
    )
    assert await get_watermark(session_factory, execution.id) == 4
    assert all(r[1] == attempt_a.id for r in await get_receipts(session_factory, execution.id))
    # Everything receipted → no inject command on this response.
    assert not any(c["type"] == "inject_context" for c in resp["commands"])

    # A new /btw row (seq 5) → the next heartbeat downlinks inject_context,
    # from_seq = SERVER watermark (4), not the daemon-reported value.
    await append_one(
        session_factory, settings,
        workspace_id=world["ws_id"], execution_id=execution.id, text_="more",
    )
    resp2 = await service.heartbeat(
        runtime=runtime, current_load=1, health="healthy", metrics={}, inflight=[str(attempt_a.id)],
        context_progress=[],
    )
    inject = [c for c in resp2["commands"] if c["type"] == "inject_context"]
    assert inject == [
        {
            "type": "inject_context",
            "attempt_id": str(attempt_a.id),
            "execution_id": str(execution.id),
            "from_seq": 4,
        }
    ]


async def test_heartbeat_old_daemon_without_context_progress(session_factory):
    """Absence of context_progress (old daemon) must not affect semantics."""
    world = await seed_world(session_factory)
    settings = make_settings()
    service = RuntimeService(session_factory, settings)
    runtime = await make_runtime(session_factory, world["ws_id"])

    resp = await service.heartbeat(
        runtime=runtime, current_load=0, health="healthy", metrics={}, inflight=[]
    )
    assert "server_time" in resp
    assert resp["commands"] == []


async def test_heartbeat_wrong_lease_never_fails_never_advances(session_factory):
    """Best-effort fencing: a mis-fenced report is dropped, never an error."""
    world = await seed_world(session_factory)
    settings = make_settings()
    service = RuntimeService(session_factory, settings)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    attempt_a = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=3
    )
    await add_appends(session_factory, settings, execution, 4)

    # Stale lease_seq 99 → 0 rows; the heartbeat still succeeds.
    resp = await service.heartbeat(
        runtime=runtime, current_load=1, health="healthy", metrics={}, inflight=[str(attempt_a.id)],
        context_progress=[
            {
                "attempt_id": attempt_a.id,
                "execution_id": execution.id,
                "injected_through_seq": 4,
                "lease_seq": 99,
            }
        ],
    )
    assert "server_time" in resp
    assert await get_watermark(session_factory, execution.id) == 0
    assert all(r[1] is None for r in await get_receipts(session_factory, execution.id))


async def test_ack_skips_missing_execution_without_raising(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    async with session_factory() as session, session.begin():
        written = await ca.ack_context_progress(
            session,
            workspace_id=world["ws_id"],
            runtime_id=runtime.id,
            entries=[
                {
                    "attempt_id": uuid.uuid4(),
                    "execution_id": uuid.uuid4(),
                    "injected_through_seq": 4,
                    "lease_seq": 1,
                }
            ],
        )
    assert written == 0


async def test_ack_accepts_object_entries(session_factory):
    """Entries may be objects (pydantic ContextProgressEntry) as well as dicts."""
    from mesh.runtime.schemas import ContextProgressEntry

    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 2)
    attempt = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=7
    )
    entry = ContextProgressEntry(
        attempt_id=attempt.id, execution_id=execution.id, injected_through_seq=2, lease_seq=7
    )
    async with session_factory() as session, session.begin():
        written = await ca.ack_context_progress(
            session,
            workspace_id=world["ws_id"],
            runtime_id=runtime.id,
            entries=[entry],
        )
    assert written == 2
    assert await get_watermark(session_factory, execution.id) == 2


# ---------------------------------------------------------------------------
# Daemon GET core: ownership + serialization (runtime.md API table row)
# ---------------------------------------------------------------------------


async def test_get_for_daemon_rejects_attempt_execution_mismatch(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    other = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 2)
    attempt = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=1
    )

    # Attempt belongs to THIS runtime but to a DIFFERENT execution → 422.
    with pytest.raises(BusinessRuleError) as exc:
        await ca.get_context_appends_for_daemon(
            session_factory, runtime=runtime, execution_id=other.id, since_seq=0, attempt_id=attempt.id
        )
    assert exc.value.code == "invalid_state_transition"

    # Unknown attempt → 404; foreign-runtime attempt → 403.
    with pytest.raises(NotFoundError):
        await ca.get_context_appends_for_daemon(
            session_factory, runtime=runtime, execution_id=execution.id, since_seq=0,
            attempt_id=uuid.uuid4(),
        )
    foreign_runtime = await make_runtime(session_factory, world["ws_id"], name="foreign")
    foreign_attempt = await make_attempt(
        session_factory, execution, runtime_id=foreign_runtime.id, attempt_number=2, lease_seq=1
    )
    with pytest.raises(ForbiddenError):
        await ca.get_context_appends_for_daemon(
            session_factory, runtime=runtime, execution_id=execution.id, since_seq=0,
            attempt_id=foreign_attempt.id,
        )


async def test_get_for_daemon_serializes_pending_rows(session_factory):
    world = await seed_world(session_factory)
    settings = make_settings()
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"], status="running")
    await add_appends(session_factory, settings, execution, 3)
    attempt = await make_attempt(
        session_factory, execution, runtime_id=runtime.id, attempt_number=1, lease_seq=1
    )

    rows = await ca.get_context_appends_for_daemon(
        session_factory, runtime=runtime, execution_id=execution.id, since_seq=0, attempt_id=attempt.id
    )
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert all(set(r) == {"seq", "source", "payload", "created_at"} for r in rows)
    assert rows[0]["source"] == "im_btw"
    assert rows[0]["payload"]["text"] == "btw-0"

    # since_seq bounds the window.
    rows = await ca.get_context_appends_for_daemon(
        session_factory, runtime=runtime, execution_id=execution.id, since_seq=1, attempt_id=attempt.id
    )
    assert [r["seq"] for r in rows] == [2, 3]


# ---------------------------------------------------------------------------
# Real daemon HTTP e2e: GET /daemon/executions/{id}/context-appends
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def daemon_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="context-appends-daemon-test-secret-000000000",
        daemon_tls_required=False,
        storage_endpoint=os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        storage_public_endpoint=os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        storage_access_key=os.environ.get("MESH_STORAGE_ACCESS_KEY", "meshminio"),
        storage_secret_key=os.environ.get("MESH_STORAGE_SECRET_KEY", "meshminio123"),
        storage_bucket="mesh-context-appends-test",
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _make_world(client: httpx.AsyncClient, suffix: str) -> tuple[str, str, str]:
    email = f"ctx-appends-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Ctx-Appends-12345", "display_name": "Ctx"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Ctx-Appends-12345"}
    )
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"Ctx {suffix}", "slug": f"ctx-appends-{suffix}"},
            headers=headers,
        )
    ).json()["data"]
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": f"ctx-agent-{suffix}"},
            headers=headers,
        )
    ).json()["data"]
    return token, ws["id"], agent["id"]


async def _activate_daemon(client: httpx.AsyncClient, token: str, ws_id: str) -> tuple[str, str]:
    created = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/runtimes",
            json={"name": "ctx-rt", "kind": "self_hosted", "labels": {}, "max_concurrent": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["data"]
    activated = await client.post(
        "/api/v1/daemon/runtimes:activate",
        json={
            "activation_code": created["activation"]["code"],
            "metadata": {"hostname": "h", "capabilities": ["python"]},
        },
    )
    assert activated.status_code == 200, activated.text
    return created["id"], activated.json()["data"]["runtime_token"]


async def test_daemon_get_context_appends_over_http(daemon_client, session_factory):
    client = daemon_client
    token, ws_id, agent_id = await _make_world(client, "e2e")
    runtime_id, daemon_token = await _activate_daemon(client, token, ws_id)
    daemon_headers = {"Authorization": f"Bearer {daemon_token}"}

    # Seed a queued execution and claim it (builds the runtime's attempt).
    execution = await make_execution(
        session_factory, uuid.UUID(ws_id), uuid.UUID(agent_id), status="queued"
    )
    claimed = await client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}/executions:claim",
        headers=daemon_headers,
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    attempt_id = claimed.json()["data"]["attempt"]["id"]

    # /btw writes 3 appends against the claimed execution.
    settings = make_settings()
    await add_appends(session_factory, settings, execution, 3)

    resp = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        params={"attempt_id": attempt_id, "since_seq": 0},
        headers=daemon_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[0]["source"] == "im_btw"
    assert rows[0]["payload"]["text"] == "btw-0"

    # since_seq windows the result.
    resp = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        params={"attempt_id": attempt_id, "since_seq": 2},
        headers=daemon_headers,
    )
    assert [r["seq"] for r in resp.json()["data"]] == [3]

    # Heartbeat ACKs the injection via context_progress → watermark advances,
    # and this attempt's receipted rows stop being returned by GET.
    lease_seq = claimed.json()["data"]["attempt"]["lease_seq"]
    hb = await client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}:heartbeat",
        headers=daemon_headers,
        json={
            "current_load": 1,
            "health": "healthy",
            "inflight": [attempt_id],
            "context_progress": [
                {
                    "attempt_id": attempt_id,
                    "execution_id": str(execution.id),
                    "injected_through_seq": 3,
                    "lease_seq": lease_seq,
                }
            ],
        },
    )
    assert hb.status_code == 200, hb.text
    assert await get_watermark(session_factory, execution.id) == 3
    resp = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        params={"attempt_id": attempt_id, "since_seq": 0},
        headers=daemon_headers,
    )
    assert resp.json()["data"] == []

    # A 4th /btw append → next heartbeat downlinks inject_context, from_seq =
    # SERVER watermark (3).
    await append_one(
        session_factory, settings,
        workspace_id=execution.workspace_id, execution_id=execution.id, text_="more",
    )
    hb2 = await client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}:heartbeat",
        headers=daemon_headers,
        json={"current_load": 1, "health": "healthy", "inflight": [attempt_id], "context_progress": []},
    )
    inject = [c for c in hb2.json()["data"]["commands"] if c["type"] == "inject_context"]
    assert inject == [
        {
            "type": "inject_context",
            "attempt_id": attempt_id,
            "execution_id": str(execution.id),
            "from_seq": 3,
        }
    ]

    # attempt_id is REQUIRED → 400 validation_error when omitted (§6.14);
    # non-UUID → 404.
    missing = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        headers=daemon_headers,
    )
    assert missing.status_code == 400
    bad = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        params={"attempt_id": "not-a-uuid"},
        headers=daemon_headers,
    )
    assert bad.status_code == 404
    # No daemon token → 401.
    anon = await client.get(
        f"/api/v1/daemon/executions/{execution.id}/context-appends",
        params={"attempt_id": attempt_id},
    )
    assert anon.status_code == 401

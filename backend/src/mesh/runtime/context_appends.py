"""Runtime context appends — the /btw landing mechanism (runtime.md §3.2).

An in-flight execution can be supplemented with extra context (today only the
IM command plane's ``/btw``, integrations.md §3.7). Append rows are
**untrusted data** (README §6.15): they never mutate the execution's source of
truth or its frozen ``config_snapshot`` (§6.11) — the agent merely reads them as
data at a *later* turn boundary.

Delivery semantics are deliberately **at-least-once** (runtime.md「运行期上下文
追加」/「注入投递语义」, R4-2 honest downgrade): the runtime contract has no
per-turn conversation checkpoint primitive, so an injection receipt cannot be
published atomically with "conversation state". Every append is delivered to the
execution *at least once*; inside a narrow crash window (injected into the
dialogue, crashed before the receipt landed) it may be delivered more than once.
**Downstream tolerance is hard-wired**: a repeated ``(execution_id, seq)`` block
is semantically identical to a single block (the same "by the way" shown twice,
not two different instructions); injection is not a tool call, triggers nothing,
and accumulates no side effects.

The receipt (``injected_attempt_id``) and the server watermark
(``task_executions.context_injected_through_seq``) are a **dedup fast path only**
— they shrink the duplicate window, they are *not* an exactly-once source of
truth. The single-pointer receipt keeps only the latest attempt's ACK
(``IS DISTINCT FROM`` overwrite); every "back to queued + new attempt" path
(reaper requeue, approval resume, manual retry) clears all receipts and resets
the watermark to 0 in the same ``task_executions`` row-lock transaction
(R7-2 unified reset), so the new attempt re-receives every seq at least once.

Lock ordering (must match across the ACK and reset paths): ``SELECT … FOR UPDATE``
on the ``task_executions`` row FIRST, then the append-table mutation.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import Settings
from mesh.db.models.integration import ExecutionContextAppend
from mesh.db.models.runtime import (
    EXECUTION_TERMINAL_STATUSES,
    ExecutionAttempt,
    Runtime,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.runtime.attempts import _load_daemon_attempt

logger = logging.getLogger(__name__)

#: The only append source registered this iteration (integrations.md §3.7).
DEFAULT_APPEND_SOURCE = "im_btw"

#: Execution statuses that accept a context append (runtime.md「写入方与准入」).
#: ``cancelling`` and ``awaiting_approval`` are in-flight but NOT acceptable;
#: terminal statuses are rejected with a distinct code.
_APPEND_ACCEPTABLE_STATUSES = frozenset({"queued", "claimed", "running"})


def _entry_field(entry: object, name: str) -> object:
    """Read a field from a mapping OR an object (heartbeat passes dicts)."""
    if isinstance(entry, dict):
        return entry[name]
    return getattr(entry, name)


async def append_context(
    session: AsyncSession,
    *,
    settings: Settings,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    source: str = DEFAULT_APPEND_SOURCE,
    payload: dict,
) -> ExecutionContextAppend:
    """Append one untrusted context row to an in-flight execution.

    Runs in the CALLER's transaction (the IM command plane service layer calls
    this directly — never over daemon HTTP). runtime.md「写入方与准入」/「每执行
    追加上限 (M3)」:

    * gate on execution status: ``queued/claimed/running`` only; ``cancelling``
      → ``append_not_acceptable`` (422); terminal → ``append_execution_terminal``
      (422); missing → 404;
    * take the per-execution advisory lock ``eca:<execution_id>`` (shared with
      seq numbering) so the cap check + seq assignment are atomic — concurrent
      writers cannot pierce the M3 caps;
    * caps INSIDE the lock: ``COUNT(*) >= context_append_max_count`` OR
      ``COALESCE(SUM(char_length(payload->>'text')),0) + char_length(new_text)
      > context_append_max_chars`` → ``append_limit_exceeded`` (422);
    * ``seq = COALESCE(max(seq),0)+1`` INSERT (``uq_eca_execution_seq`` backstop).

    ``payload`` is untrusted data (README §6.15); callers wrap it with a
    structured ``source='im_btw'`` marker so the agent treats it as data, not
    instructions.
    """
    execution = (
        await session.execute(
            select(TaskExecution)
            .where(
                TaskExecution.id == execution_id,
                TaskExecution.workspace_id == workspace_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if execution is None:
        raise NotFoundError("execution not found")
    if execution.status in EXECUTION_TERMINAL_STATUSES:
        raise BusinessRuleError(
            "execution is terminal; cannot append context",
            code="append_execution_terminal",
            details={"status": execution.status},
        )
    if execution.status not in _APPEND_ACCEPTABLE_STATUSES:
        # 'cancelling' renders "任务正在停止,无法补充"; 'awaiting_approval' is
        # parked — neither accepts appends (integrations.md §3.7).
        raise BusinessRuleError(
            "execution is not accepting context appends",
            code="append_not_acceptable",
            details={"status": execution.status},
        )

    # Per-execution transactional advisory lock — serializes the cap check and
    # the seq numbering so concurrent /btw writes never exceed the M3 caps.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"eca:{execution_id}"},
    )

    new_text = str(payload.get("text") or "")
    exceeded = (
        await session.execute(
            text(
                "SELECT COUNT(*) >= :max_count AS count_exceeded, "
                "COALESCE(SUM(char_length(payload->>'text')), 0) "
                "+ char_length(:new_text) > :max_chars AS chars_exceeded "
                "FROM execution_context_appends WHERE execution_id = :eid"
            ),
            {
                "max_count": settings.context_append_max_count,
                "max_chars": settings.context_append_max_chars,
                "new_text": new_text,
                "eid": execution_id,
            },
        )
    ).mappings().one()
    if exceeded["count_exceeded"] or exceeded["chars_exceeded"]:
        raise BusinessRuleError(
            "context append limit exceeded",
            code="append_limit_exceeded",
            details={
                "max_count": settings.context_append_max_count,
                "max_chars": settings.context_append_max_chars,
            },
        )

    next_seq = (
        await session.execute(
            select(func.coalesce(func.max(ExecutionContextAppend.seq), 0) + 1).where(
                ExecutionContextAppend.execution_id == execution_id
            )
        )
    ).scalar_one()
    append = ExecutionContextAppend(
        workspace_id=workspace_id,
        execution_id=execution_id,
        seq=int(next_seq),
        source=source,
        payload=payload,
    )
    session.add(append)
    await session.flush()
    return append


async def ack_context_progress(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    entries: list,
) -> int:
    """Write best-effort injection receipts from heartbeat ``context_progress``.

    Runs in the CALLER's transaction. runtime.md「尽力去重」/「服务端连续水位」,
    R6-1/R7-1. For each ``{attempt_id, execution_id, injected_through_seq,
    lease_seq}`` entry:

    1. ``SELECT … FOR UPDATE`` the ``task_executions`` row FIRST (same lock
       order as the requeue reset path);
    2. single fenced UPDATE (modeled on schema_r2_validation.sql T39-10④):
       write ``injected_at``/``injected_attempt_id`` for ``seq <= reported``
       where the receipt ``IS DISTINCT FROM`` the attempt, guarded by an EXISTS
       that requires workspace/runtime ownership + the attempt is the LATEST
       (max ``attempt_number``, not reclaimed) + ``claimed/running`` + an EXACT
       ``lease_seq`` match. ANY mismatch → 0 rows for that entry (fencing; never
       raises);
    3. only when rows were written, recompute the server watermark with the
       first-gap formula (T39-10④a/④b/①) and persist it.

    Returns the total number of receipt rows written. Lost/mis-fenced reports
    only widen the duplicate window — they never break at-least-once semantics.
    """
    written = 0
    for entry in entries:
        attempt_id = _entry_field(entry, "attempt_id")
        execution_id = _entry_field(entry, "execution_id")
        reported = _entry_field(entry, "injected_through_seq")
        lease_seq = _entry_field(entry, "lease_seq")

        # 1) Lock the execution row first (matches the reset lock order).
        locked = (
            await session.execute(
                select(TaskExecution.id)
                .where(
                    TaskExecution.id == execution_id,
                    TaskExecution.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            # Execution gone (or other workspace): 0 rows, skip — never raise.
            continue

        # 2) Fenced single-pointer overwrite receipt.
        result = await session.execute(
            text(
                "UPDATE execution_context_appends "
                "SET injected_at = now(), injected_attempt_id = :attempt "
                "WHERE execution_id = :e AND workspace_id = :ws "
                "AND seq <= :reported "
                "AND injected_attempt_id IS DISTINCT FROM :attempt "
                "AND EXISTS ("
                "  SELECT 1 FROM execution_attempts a "
                "  WHERE a.id = :attempt AND a.execution_id = :e "
                "    AND a.workspace_id = :ws "
                "    AND a.status IN ('claimed','running') "
                "    AND a.lease_seq = :lease "
                "    AND (a.runtime_id = :rt OR a.claimed_by_runtime_id = :rt) "
                "    AND a.attempt_number = ("
                "      SELECT max(a2.attempt_number) FROM execution_attempts a2 "
                "      WHERE a2.execution_id = :e AND a2.workspace_id = :ws"
                "    )"
                ")"
            ),
            {
                "attempt": attempt_id,
                "e": execution_id,
                "ws": workspace_id,
                "reported": reported,
                "lease": lease_seq,
                "rt": runtime_id,
            },
        )
        rows = result.rowcount or 0
        written += rows
        if rows <= 0:
            # Fencing rejected the whole entry: do NOT advance the watermark
            # (a stale attempt's recompute could otherwise regress it).
            continue

        # 3) Recompute the contiguous-prefix watermark for the current attempt:
        #    W = first seq NOT receipted-by-attempt (or max+1 if all) − 1.
        await session.execute(
            text(
                "UPDATE task_executions SET context_injected_through_seq = "
                "COALESCE("
                "  (SELECT min(seq) FROM execution_context_appends "
                "   WHERE execution_id = :e "
                "     AND injected_attempt_id IS DISTINCT FROM :attempt), "
                "  (SELECT COALESCE(max(seq),0)+1 FROM execution_context_appends "
                "   WHERE execution_id = :e)"
                ") - 1 "
                "WHERE id = :e AND workspace_id = :ws"
            ),
            {"e": execution_id, "attempt": attempt_id, "ws": workspace_id},
        )
    return written


async def list_pending_appends(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    since_seq: int,
    current_attempt_id: uuid.UUID,
) -> list[ExecutionContextAppend]:
    """Attempt-scoped pending set for ``GET …/context-appends?since_seq=N``.

    runtime.md API table + 「下行起点以服务端水位为准」: rows with
    ``seq > since_seq`` whose receipt is NULL OR belongs to ANOTHER attempt
    (the current attempt's already-receipted rows are not redelivered; an old
    attempt's receipts were cleared on requeue — single-pointer model). Ordered
    by seq.
    """
    stmt = (
        select(ExecutionContextAppend)
        .where(
            ExecutionContextAppend.workspace_id == workspace_id,
            ExecutionContextAppend.execution_id == execution_id,
            ExecutionContextAppend.seq > since_seq,
            or_(
                ExecutionContextAppend.injected_attempt_id.is_(None),
                ExecutionContextAppend.injected_attempt_id != current_attempt_id,
            ),
        )
        .order_by(ExecutionContextAppend.seq)
    )
    return list((await session.execute(stmt)).scalars().all())


async def compute_inject_commands(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    attempt_rows: list[ExecutionAttempt],
) -> list[dict]:
    """Build heartbeat ``inject_context`` downlink commands.

    runtime.md「inject_context 下行指令」: for each in-flight attempt on this
    runtime whose execution has append rows with ``seq >
    context_injected_through_seq`` AND receipt NULL/other-attempt, emit
    ``{type:'inject_context', attempt_id, execution_id, from_seq}`` where
    ``from_seq`` is the SERVER watermark (never the daemon-reported value — a
    restarting daemon reporting 0 must not trigger a replay).
    """
    if not attempt_rows:
        return []
    attempt_ids = [attempt.id for attempt in attempt_rows]
    stmt = text(
        "SELECT a.id AS attempt_id, a.execution_id AS execution_id, "
        "e.context_injected_through_seq AS watermark "
        "FROM execution_attempts a "
        "JOIN task_executions e "
        "  ON e.id = a.execution_id AND e.workspace_id = a.workspace_id "
        "WHERE a.workspace_id = :ws "
        "AND a.id IN (:ids) "
        "AND a.status IN ('claimed','running') "
        "AND (a.runtime_id = :rt OR a.claimed_by_runtime_id = :rt) "
        "AND EXISTS ("
        "  SELECT 1 FROM execution_context_appends c "
        "  WHERE c.execution_id = a.execution_id "
        "    AND c.seq > e.context_injected_through_seq "
        "    AND (c.injected_attempt_id IS NULL OR c.injected_attempt_id <> a.id)"
        ") "
        "ORDER BY a.id"
    ).bindparams(bindparam("ids", expanding=True))
    rows = (
        await session.execute(
            stmt, {"ws": workspace_id, "ids": attempt_ids, "rt": runtime_id}
        )
    ).mappings().all()
    return [
        {
            "type": "inject_context",
            "attempt_id": str(row["attempt_id"]),
            "execution_id": str(row["execution_id"]),
            "from_seq": int(row["watermark"]),
        }
        for row in rows
    ]


async def reset_context_receipts_tx(
    session: AsyncSession,
    *,
    execution_id: uuid.UUID,
) -> None:
    """Clear all receipts + reset the watermark to 0 (R7-2 unified reset).

    runtime.md「receipt/水位统一重置」: every "execution back to ``queued`` and a
    new attempt is built afterwards" path — reaper requeue (lost contact),
    approval resume (``awaiting_approval → queued``), manual retry — calls this
    in the SAME ``task_executions`` row-lock transaction. The single-pointer
    model keeps only the latest receipt (no history audit), so clearing lets the
    next attempt re-receive every seq from 0 (at-least-once: duplicates allowed,
    never lost).

    LOCK CONTRACT: the caller MUST already hold ``SELECT … FOR UPDATE`` on the
    ``task_executions`` row (same lock order as :func:`ack_context_progress`),
    so the two commit orders both close (A commits first → reset clears it;
    reset commits first → A's fencing fails).
    """
    await session.execute(
        text(
            "UPDATE execution_context_appends "
            "SET injected_at = NULL, injected_attempt_id = NULL "
            "WHERE execution_id = :e"
        ),
        {"e": execution_id},
    )
    await session.execute(
        text(
            "UPDATE task_executions SET context_injected_through_seq = 0 "
            "WHERE id = :e"
        ),
        {"e": execution_id},
    )


async def get_context_appends_for_daemon(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    runtime: Runtime,
    execution_id: uuid.UUID,
    since_seq: int,
    attempt_id: uuid.UUID,
) -> list[dict]:
    """Daemon ``GET /daemon/executions/{id}/context-appends`` core.

    runtime.md API table row: the endpoint is daemon-authenticated exactly like
    the approvals endpoint — the token may only read through an attempt that
    belongs to ITS runtime (``_load_daemon_attempt``: 404 unknown / foreign
    workspace, 403 another runtime) AND to the path execution. Returns the
    attempt-scoped pending set (``seq > since_seq``, receipt NULL/other-attempt)
    as ``{seq, source, payload, created_at}`` rows for the ``{"data": [...]}``
    envelope (README §6.14).
    """
    workspace_id = runtime.workspace_id
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        attempt = await _load_daemon_attempt(
            session, attempt_id=attempt_id, runtime=runtime, for_update=False
        )
        if attempt.execution_id != execution_id:
            raise BusinessRuleError(
                "attempt does not belong to this execution",
                code="invalid_state_transition",
            )
        rows = await list_pending_appends(
            session,
            workspace_id=workspace_id,
            execution_id=execution_id,
            since_seq=since_seq,
            current_attempt_id=attempt_id,
        )
        return [
            {
                "seq": row.seq,
                "source": row.source,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

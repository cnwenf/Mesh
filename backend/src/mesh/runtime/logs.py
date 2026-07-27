"""Execution log streaming — runtime.md §2.3 / §3.3 / §4.9.

Content lives in object storage; ``task_log_segments`` is a byte-offset
index. The global per-attempt ``offset`` is cumulative BYTES and is the sole
resume / dedupe authority: clients reconnect with their last offset, the
server backfills ``[offset, sealed)`` from storage then continues live via
the ``execution:{id}:logs`` channel.

Redaction happens BEFORE anything is stored or pushed (§4.9: the frontend
never sees plaintext secrets).
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.runtime import (
    ExecutionAttempt,
    TaskExecution,
    TaskLogSegment,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, NotFoundError, StorageError
from mesh.outbox.service import emit_realtime
from mesh.runtime.attempts import _assert_lease, _load_daemon_attempt
from mesh.runtime.credentials import load_redaction_blacklist, redact_text

# One pushed realtime event per appended batch; payloads carry the line list
# and the client expands to per-line frames (offset protocol unchanged).
MAX_PUSH_LINES = 500


def _line_bytes(line: str) -> int:
    """Each line occupies its UTF-8 bytes plus the trailing newline."""
    return len(line.encode("utf-8")) + 1


async def _expected_offset(session: AsyncSession, attempt_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.max(TaskLogSegment.end_offset), 0)).where(
                    TaskLogSegment.attempt_id == attempt_id
                )
            )
        ).scalar_one()
    )


async def append_log_lines(
    session_factory: async_sessionmaker[AsyncSession],
    storage: object | None,
    *,
    attempt_id: uuid.UUID,
    runtime,  # Runtime — loose typing avoids an import cycle
    lease_seq: int,
    stream: str,
    start_offset: int,
    lines: list[str],
    signing_secret: str,
) -> dict:
    """Daemon: append redacted lines at the exact expected offset.

    Continuity is enforced: ``start_offset`` must equal the attempt's current
    end offset (409 otherwise — the daemon refetches its position and
    retries). Empty payloads are accepted as no-op heartbeats.
    """
    workspace_id = runtime.workspace_id
    if len(lines) > MAX_PUSH_LINES * 4:
        lines = lines[: MAX_PUSH_LINES * 4]

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        attempt = await _load_daemon_attempt(session, attempt_id=attempt_id, runtime=runtime)
        if attempt.status not in ("claimed", "running", "cancelling"):
            raise ConflictError(
                "attempt not in flight",
                code="attempt_terminal",
                details={"status": attempt.status},
            )
        _assert_lease(attempt, lease_seq)

        expected = await _expected_offset(session, attempt.id)
        if start_offset != expected:
            raise ConflictError(
                "log offset mismatch",
                code="offset_mismatch",
                details={"expected": expected, "received": start_offset},
            )
        if not lines:
            return {"accepted_end_offset": expected, "redacted_hits": 0}

        # Full-channel redaction BEFORE persistence / push (§6.16).
        blacklist = await load_redaction_blacklist(session, workspace_id, signing_secret)
        redacted_lines: list[str] = []
        total_hits = 0
        for line in lines:
            clean, hits = redact_text(line, blacklist)
            redacted_lines.append(clean)
            total_hits += hits

        offset = start_offset
        records = []
        for line in redacted_lines:
            records.append({"s": stream, "o": offset, "l": line})
            offset += _line_bytes(line)
        end_offset = offset

        if storage is None:
            raise StorageError("log storage unavailable")
        storage_ref = f"logs/{workspace_id}/{attempt.id.hex}/{start_offset}-{end_offset}.json"
        try:
            await storage.put_bytes(  # type: ignore[attr-defined]
                storage_ref,
                json.dumps(records).encode("utf-8"),
                content_type="application/json",
            )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 — neutral 502 on storage faults
            raise StorageError("log storage write failed") from exc

        session.add(
            TaskLogSegment(
                workspace_id=workspace_id,
                attempt_id=attempt.id,
                start_offset=start_offset,
                end_offset=end_offset,
                storage_ref=storage_ref,
                line_count=len(redacted_lines),
                sealed=True,
            )
        )

        execution = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.id == attempt.execution_id)
            )
        ).scalar_one()
        # F11 (§3.3): one frame PER LINE on the logs channel — the documented
        # wire shape {"type":"log","stream","offset","line"}; clients dedupe
        # by byte offset against their resume position.
        for record in records[:MAX_PUSH_LINES]:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}:logs",
                event="execution.log",
                data={
                    "type": "log",
                    "stream": record["s"],
                    "offset": record["o"],
                    "line": record["l"],
                    "attempt_id": str(attempt.id),
                },
                idempotency_key=f"log:{attempt.id}:{record['o']}",
            )
        return {"accepted_end_offset": end_offset, "redacted_hits": total_hits}


async def read_execution_logs(
    session_factory: async_sessionmaker[AsyncSession],
    storage: object | None,
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    offset: int = 0,
    stream: str | None = None,
    max_lines: int = 1000,
) -> dict:
    """REST backfill / resume read across the execution's attempts.

    Reads the LATEST attempt's segments (requeue logs are per-attempt and do
    not interleave — §2.3), returns decoded lines from ``offset`` onward.
    """
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.id == execution_id,
                    TaskExecution.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError("execution not found")
        attempt = (
            await session.execute(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
                .order_by(ExecutionAttempt.attempt_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if attempt is None:
            return {
                "execution_id": str(execution_id),
                "attempt_id": None,
                "execution_status": execution.status,
                "lines": [],
                "next_offset": offset,
            }
        segments = (
            await session.execute(
                select(TaskLogSegment)
                .where(
                    TaskLogSegment.attempt_id == attempt.id,
                    TaskLogSegment.end_offset > offset,
                )
                .order_by(TaskLogSegment.start_offset.asc())
            )
        ).scalars().all()

        out: list[dict] = []
        next_offset = offset
        if storage is not None:
            for segment in segments:
                try:
                    raw = await storage.get_bytes(  # type: ignore[attr-defined]
                        segment.storage_ref, max_bytes=16 * 1024 * 1024
                    )
                except Exception:  # noqa: BLE001 — unreadable segment: skip
                    continue
                try:
                    records = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                for record in records[:MAX_PUSH_LINES]:
                    line_offset = int(record.get("o", 0))
                    record_stream = record.get("s", "stdout")
                    if line_offset < offset:
                        continue
                    if stream is not None and record_stream != stream:
                        next_offset = max(next_offset, line_offset + _line_bytes(str(record.get("l", ""))))
                        continue
                    out.append(
                        {"stream": record_stream, "offset": line_offset, "line": record.get("l", "")}
                    )
                    next_offset = max(next_offset, line_offset + _line_bytes(str(record.get("l", ""))))
                    if len(out) >= max_lines:
                        break
                if len(out) >= max_lines:
                    break
        return {
            "execution_id": str(execution_id),
            "attempt_id": str(attempt.id),
            "execution_status": execution.status,
            "lines": out,
            "next_offset": next_offset,
        }

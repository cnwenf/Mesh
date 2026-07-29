"""LogUploader — redacted, offset-idempotent log relay (spec §3.9 / design §8.3).

Lines are redacted FIRST; the upload ``start_offset`` is counted in REDACTED
UTF-8 bytes and is the journal's authoritative watermark. Batches ship when
ANY threshold trips (64 lines / 256 KiB / 500 ms). On a 409 ``offset_mismatch``
the uploader reconciles against the server's ``expected`` offset — drops the
confirmed prefix and retries — instead of dying; a lease fencing 409 is
re-raised for the supervisor to handle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mesh_runtime.errors import LeaseConflictError
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.timeutil import Clock, SystemClock

if TYPE_CHECKING:
    from mesh_runtime.api import RuntimeApiClient
    from mesh_runtime.attempt import AttemptContext
    from mesh_runtime.journal import Journal

DEFAULT_BATCH_LINES = 64
DEFAULT_BATCH_BYTES = 256 * 1024
DEFAULT_BATCH_INTERVAL = 0.5


@dataclass(frozen=True)
class SubmitResult:
    redacted_hits: int


class LogUploader:
    def __init__(
        self,
        api: RuntimeApiClient,
        journal: Journal,
        redactor: RedactionPipeline,
        *,
        clock: Clock | None = None,
        batch_lines: int = DEFAULT_BATCH_LINES,
        batch_bytes: int = DEFAULT_BATCH_BYTES,
        batch_interval: float = DEFAULT_BATCH_INTERVAL,
    ) -> None:
        self._api = api
        self._journal = journal
        self._redactor = redactor
        self._clock = clock or SystemClock()
        self._batch_lines = batch_lines
        self._batch_bytes = batch_bytes
        self._batch_interval = batch_interval
        self._buffers: dict[tuple[str, str], list[str]] = {}
        self._buffer_bytes: dict[tuple[str, str], int] = {}
        self._first_at: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def submit(self, ctx: AttemptContext, stream: str, line: str) -> SubmitResult:
        result = self._redactor.redact(line)
        key = (ctx.attempt_id, stream)
        should_flush = False
        async with self._lock:
            buf = self._buffers.setdefault(key, [])
            if not buf:
                self._first_at[key] = self._clock.now()
            buf.append(result.text)
            self._buffer_bytes[key] = self._buffer_bytes.get(key, 0) + len(result.text.encode("utf-8"))
            elapsed = self._clock.now() - self._first_at.get(key, self._clock.now())
            if (
                len(buf) >= self._batch_lines
                or self._buffer_bytes[key] >= self._batch_bytes
                or elapsed >= self._batch_interval
            ):
                should_flush = True
        if should_flush:
            await self._flush_stream(ctx, stream, sealed=False)
        return SubmitResult(redacted_hits=result.hit_count)

    async def flush(self, ctx: AttemptContext, *, sealed: bool = False) -> None:
        await self._flush_stream(ctx, "stdout", sealed=sealed)
        await self._flush_stream(ctx, "stderr", sealed=sealed)

    async def _flush_stream(self, ctx: AttemptContext, stream: str, *, sealed: bool) -> None:
        key = (ctx.attempt_id, stream)
        async with self._lock:
            lines = self._buffers.pop(key, [])
            self._buffer_bytes.pop(key, None)
            self._first_at.pop(key, None)
        if not lines and not sealed:
            return
        offset_field = f"log_offset_{stream}"
        async with ctx.lock:
            entry = await self._journal.get(ctx.attempt_id)
            start = getattr(entry, offset_field) if entry else 0
            try:
                ack = await self._api.append_logs(
                    ctx.attempt_id, lease_seq=ctx.lease_seq, stream=stream,
                    start_offset=start, lines=lines, sealed=sealed,
                )
            except LeaseConflictError as exc:
                if exc.code != "offset_mismatch":
                    raise  # genuine lease fencing — supervisor handles it
                expected = int(exc.details.get("expected", start))
                remaining = _lines_after_bytes(lines, start, expected)
                ack = await self._api.append_logs(
                    ctx.attempt_id, lease_seq=ctx.lease_seq, stream=stream,
                    start_offset=expected, lines=remaining, sealed=sealed,
                )
            await self._journal.update(ctx.attempt_id, **{offset_field: ack.accepted_end_offset})


def _lines_after_bytes(lines: list[str], start: int, expected: int) -> list[str]:
    """Drop the prefix already confirmed by the server (``expected`` bytes from
    ``start``); keep the first not-fully-confirmed line onward."""
    if expected <= start:
        return list(lines)
    running = start
    out: list[str] = []
    for line in lines:
        end = running + len(line.encode("utf-8"))
        if end <= expected:
            running = end
            continue
        out.append(line)
        running = end
    return out

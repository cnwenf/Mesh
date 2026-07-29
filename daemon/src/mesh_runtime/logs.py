"""LogUploader — redacted, offset-idempotent log relay (spec §3.9 / design §8.3).

Lines are redacted FIRST; the upload ``start_offset`` is counted in REDACTED
UTF-8 bytes and is the journal's authoritative watermark. Batches ship when
ANY threshold trips (64 lines / 256 KiB / 500 ms). On a 409 ``offset_mismatch``
the uploader reconciles against the server's ``expected`` offset — drops the
confirmed prefix and retries — instead of dying; a lease fencing 409 is
re-raised for the supervisor to handle.

Durability (§3.9.3): a batch is written to the :class:`~mesh_runtime.spool.LogSpool`
BEFORE upload and removed only after the server acks its end offset, so a
crash, restart, or transient network failure can never permanently lose
already-redacted lines. A transient (non-409) failure mid-stream is swallowed
and retried on the next flush — it must NOT fail the attempt; only a sealed
(terminal) flush propagates it, and even then the spool retains the batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mesh_runtime.errors import DaemonError, LeaseConflictError
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.spool import LogSpool, SpooledBatch
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
        spool: LogSpool | None = None,
        batch_lines: int = DEFAULT_BATCH_LINES,
        batch_bytes: int = DEFAULT_BATCH_BYTES,
        batch_interval: float = DEFAULT_BATCH_INTERVAL,
    ) -> None:
        self._api = api
        self._journal = journal
        self._redactor = redactor
        self._clock = clock or SystemClock()
        self._spool = spool
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

    async def drain_attempt(self, attempt_id: str) -> None:
        """Drop any residual spooled batches for a terminal attempt (§3.9.3)."""
        if self._spool is not None:
            self._spool.drain(attempt_id)

    async def _flush_stream(self, ctx: AttemptContext, stream: str, *, sealed: bool) -> None:
        key = (ctx.attempt_id, stream)
        offset_field = f"log_offset_{stream}"
        async with ctx.lock:
            entry = await self._journal.get(ctx.attempt_id)
            start = getattr(entry, offset_field) if entry else 0
            batches = self._collect_batches(ctx, stream, key, start)
            if not batches:
                if not sealed:
                    return
                # Nothing buffered, but the stream still needs its sealed close.
                await self._upload_one(ctx, stream, start, [], offset_field=offset_field, sealed=True)
                return
            last = len(batches) - 1
            for index, (batch_offset, batch_lines) in enumerate(batches):
                try:
                    await self._upload_one(
                        ctx, stream, batch_offset, batch_lines,
                        offset_field=offset_field, sealed=sealed and index == last,
                    )
                except LeaseConflictError:
                    raise  # fencing — supervisor handles it
                except DaemonError:
                    if sealed:
                        raise  # terminal flush: supervisor decides; spool retains the batch
                    if self._spool is None:
                        await self._rebuffer(ctx.attempt_id, stream, batch_lines)
                    break  # transient mid-stream failure: retry on the next flush
                if self._spool is not None:
                    self._spool.ack(ctx.attempt_id, stream, batch_offset)

    def _collect_batches(
        self, ctx: AttemptContext, stream: str, key: tuple[str, str], start: int
    ) -> list[tuple[int, list[str]]]:
        """Return the ``(start_offset, lines)`` batches to upload, in offset
        order. Must run under ``ctx.lock``. A still-spooled batch (previous
        upload failed transiently) is retried first and new buffer lines stay
        queued, so offsets stay monotonic and replay stays idempotent."""
        if self._spool is not None and self._spool.has_pending(ctx.attempt_id, stream):
            return [
                (batch.start_offset, list(batch.lines))
                for batch in self._spool.pending(ctx.attempt_id, stream)
            ]
        lines = self._buffers.pop(key, [])
        self._buffer_bytes.pop(key, None)
        self._first_at.pop(key, None)
        if not lines:
            return []
        if self._spool is not None:
            try:
                # Durable BEFORE upload — survives crash/restart.
                self._spool.write(SpooledBatch(ctx.attempt_id, stream, start, tuple(lines)))
            except DaemonError:
                # SpoolFullError (backpressure): put the lines back so nothing
                # is lost, then let the error propagate to the supervisor.
                self._rebuffer_sync(key, lines)
                raise
        return [(start, lines)]

    async def _upload_one(
        self, ctx: AttemptContext, stream: str, start_offset: int, lines: list[str],
        *, offset_field: str, sealed: bool,
    ) -> None:
        try:
            ack = await self._api.append_logs(
                ctx.attempt_id, lease_seq=ctx.lease_seq, stream=stream,
                start_offset=start_offset, lines=lines, sealed=sealed,
            )
        except LeaseConflictError as exc:
            if exc.code != "offset_mismatch":
                raise  # genuine lease fencing — supervisor handles it
            expected = int(exc.details.get("expected", start_offset))
            remaining = _lines_after_bytes(lines, start_offset, expected)
            ack = await self._api.append_logs(
                ctx.attempt_id, lease_seq=ctx.lease_seq, stream=stream,
                start_offset=expected, lines=remaining, sealed=sealed,
            )
        await self._journal.update(ctx.attempt_id, **{offset_field: ack.accepted_end_offset})

    async def _rebuffer(self, attempt_id: str, stream: str, lines: list[str]) -> None:
        if not lines:
            return
        self._rebuffer_sync((attempt_id, stream), lines)

    def _rebuffer_sync(self, key: tuple[str, str], lines: list[str]) -> None:
        """Re-queue lines at the FRONT of the buffer (no loss on failure).

        Synchronous variant for use where the caller already serializes (under
        ``ctx.lock``); the async wrapper is for paths without that guarantee."""
        buf = self._buffers.setdefault(key, [])
        buf[:0] = lines
        added = sum(len(line.encode("utf-8")) for line in lines)
        self._buffer_bytes[key] = self._buffer_bytes.get(key, 0) + added
        self._first_at.setdefault(key, self._clock.now())


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

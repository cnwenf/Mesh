"""LogUploader — redacted, offset-idempotent log relay (spec §3.9 / design §8.3).

Lines are redacted FIRST; the upload ``start_offset`` is counted in REDACTED
UTF-8 bytes. Per the server contract the offset is a SINGLE cumulative
watermark ACROSS both streams of an attempt (the journal mirrors it in both
``log_offset_*`` fields). Batches ship when ANY threshold trips (64 lines /
256 KiB / 500 ms). On a 409 ``offset_mismatch``
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
import logging
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

logger = logging.getLogger("mesh_runtime.logs")

DEFAULT_BATCH_LINES = 64
DEFAULT_BATCH_BYTES = 256 * 1024
DEFAULT_BATCH_INTERVAL = 0.5
#: Resolution of the independent flush timer (§3.9.2 "any condition sends"):
#: a sparse stream that never reaches the line/byte thresholds is flushed once
#: the batch interval elapses, without waiting for the next line.
DEFAULT_TICK_INTERVAL = 0.1


@dataclass(frozen=True)
class SubmitResult:
    redacted_hits: int


def _line_bytes(line: str) -> int:
    """Wire bytes occupied by one line. MUST mirror the server's
    ``backend/src/mesh/runtime/logs.py::_line_bytes`` — UTF-8 bytes PLUS the
    trailing newline. The daemon's self-computed continuation offsets are
    compared against the server watermark on the retry path; counting raw
    ``len(utf8)`` (no newline) drifts them N bytes low (N = line count) and the
    offset reconciliation then drops already-correct lines (MES-96 P2-1)."""
    return len(line.encode("utf-8")) + 1


def _batch_wire_end(batch: SpooledBatch) -> int:
    """The exclusive end offset a batch occupies on the wire (§3.9 offset
    protocol): its start plus every line's wire bytes (newline included)."""
    return batch.start_offset + sum(_line_bytes(line) for line in batch.lines)


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
        tick_interval: float = DEFAULT_TICK_INTERVAL,
    ) -> None:
        self._api = api
        self._journal = journal
        self._redactor = redactor
        self._clock = clock or SystemClock()
        self._spool = spool
        self._batch_lines = batch_lines
        self._batch_bytes = batch_bytes
        self._batch_interval = batch_interval
        self._tick_interval = tick_interval
        self._buffers: dict[tuple[str, str], list[str]] = {}
        self._buffer_bytes: dict[tuple[str, str], int] = {}
        self._first_at: dict[tuple[str, str], float] = {}
        self._contexts: dict[str, AttemptContext] = {}
        self._lock = asyncio.Lock()
        self._tick_task: asyncio.Task | None = None
        self._tick_stop = asyncio.Event()

    async def submit(self, ctx: AttemptContext, stream: str, line: str) -> SubmitResult:
        result = self._redactor.redact(line)
        key = (ctx.attempt_id, stream)
        self._contexts[ctx.attempt_id] = ctx  # timer needs the lease lock/seq
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
        self._contexts.pop(attempt_id, None)

    # -- independent flush timer (§3.9.2) ------------------------------------

    async def start_ticking(self) -> None:
        """Run the interval arm of "any condition sends" on its own clock.

        ``submit`` only evaluates the 500 ms condition when a NEW line
        arrives, so a sparse stream (one line, then silence until finalize)
        would otherwise stall in the buffer. The tick loop flushes any stream
        whose oldest buffered line is past the batch interval, independent of
        arrivals."""
        if self._tick_task is not None and not self._tick_task.done():
            return
        self._tick_stop = asyncio.Event()
        self._tick_task = asyncio.create_task(
            self._tick_loop(), name="log-flush-timer"
        )

    async def stop_ticking(self) -> None:
        task = self._tick_task
        self._tick_task = None
        if task is None:
            return
        self._tick_stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _tick_loop(self) -> None:
        while not self._tick_stop.is_set():
            await self._clock.sleep(self._tick_interval)
            try:
                await self.flush_due()
            except DaemonError as exc:
                # Keep the timer alive: mid-stream failures are the uploader's
                # retry/spool problem, lease fencing is the supervisor's. A
                # dead timer would silently re-stall sparse streams.
                logger.warning("flush timer tick error: %s", type(exc).__name__)

    async def flush_due(self) -> None:
        """Flush every stream whose oldest buffered line has aged past the
        batch interval (§3.9.2 interval arm). Called on each timer tick; also
        directly callable for deterministic checks. Never seals — a sealed
        close is the supervisor's terminal decision."""
        now = self._clock.now()
        due: list[tuple[str, str]] = []
        async with self._lock:
            for key, first_at in self._first_at.items():
                if now - first_at >= self._batch_interval:
                    due.append(key)
        for attempt_id, stream in due:
            ctx = self._contexts.get(attempt_id)
            if ctx is not None:
                await self._flush_stream(ctx, stream, sealed=False)

    async def _flush_stream(self, ctx: AttemptContext, stream: str, *, sealed: bool) -> None:
        key = (ctx.attempt_id, stream)
        async with ctx.lock:
            entry = await self._journal.get(ctx.attempt_id)
            # Server contract (MES-98): start_offset is cumulative BYTES per
            # attempt ACROSS both streams — a single monotonic watermark, not
            # per-stream counters. Both journal fields mirror that watermark.
            start = max(entry.log_offset_stdout, entry.log_offset_stderr) if entry else 0
            batches = self._collect_batches(ctx, stream, key, start, sealed=sealed)
            if not batches:
                if not sealed:
                    return
                # Nothing buffered anywhere, but the stream still needs its
                # sealed close.
                await self._upload_one(ctx, stream, start, [], sealed=True)
                return
            # The sealed (terminal) marker lands on the LAST batch belonging to
            # THIS stream — the sibling stream's replayed batches ride along
            # unsealed so the single cross-stream watermark stays contiguous.
            # A sealed flush that carries ONLY sibling-stream replayed batches
            # (this stream has nothing) seals the final drained batch — the
            # terminal flush must never leave un-acked lines behind while
            # certifying completion.
            own_indices = [
                i for i, (batch_stream, _, _) in enumerate(batches) if batch_stream == stream
            ]
            seal_at = own_indices[-1] if own_indices else len(batches) - 1
            for index, (batch_stream, batch_offset, batch_lines) in enumerate(batches):
                try:
                    await self._upload_one(
                        ctx, batch_stream, batch_offset, batch_lines,
                        sealed=sealed and index == seal_at,
                    )
                except LeaseConflictError:
                    raise  # fencing — supervisor handles it
                except DaemonError:
                    if self._spool is None:
                        # Re-queue BEFORE propagating so the supervisor's retry
                        # (sealed) or the next flush (mid-stream) re-sends the
                        # very same lines — nothing is lost on failure. Rebuffer
                        # under the batch's OWN stream key.
                        await self._rebuffer(ctx.attempt_id, batch_stream, batch_lines)
                    # With a spool the batch is already durable on disk and is
                    # replayed by the next collect; no rebuffer needed.
                    if sealed:
                        raise  # terminal flush: supervisor retries, then demotes
                    break  # transient mid-stream failure: retry on the next flush
                if self._spool is not None:
                    self._spool.ack(ctx.attempt_id, batch_stream, batch_offset)

    def _collect_batches(
        self, ctx: AttemptContext, stream: str, key: tuple[str, str], start: int,
        *, sealed: bool = False,
    ) -> list[tuple[str, int, list[str]]]:
        """Return the ``(stream, start_offset, lines)`` batches to upload, in
        offset order. Must run under ``ctx.lock``.

        This stream's own still-spooled batches (a previous upload failed
        transiently) are ALWAYS replayed first. When this flush ALSO carries new
        buffered lines, the SIBLING stream's un-acked spooled batches are folded
        in too, ascending by offset, so they upload BEFORE the new batch — the
        offset is a single cumulative watermark ACROSS both streams, and a new
        batch may never start inside a range the sibling's un-acked batch
        already occupies. ``next_offset`` is the max wire end over every
        replayed batch of either stream, so the continuation batch starts right
        after the last spooled WIRE byte and the watermark stays monotonic
        (MES-96 P2-1; §3.9.2/§3.9.3). New buffer lines are made durable in the
        spool before upload as well, so offsets stay monotonic and replay stays
        idempotent."""
        batches: list[tuple[str, int, list[str]]] = []
        next_offset = start
        lines = self._buffers.pop(key, [])
        self._buffer_bytes.pop(key, None)
        self._first_at.pop(key, None)
        if self._spool is not None:
            pending: list[SpooledBatch] = list(self._spool.pending(ctx.attempt_id, stream))
            if lines or sealed:
                # A new batch is going out, OR this is the terminal (sealed)
                # flush: serialize behind the sibling's un-acked range too.
                # For new batches, the single watermark would overlap
                # otherwise; for sealed flushes, the sibling's un-acked lines
                # must drain BEFORE completion is certified — incomplete logs
                # may never be sealed.
                for pending_stream in ("stdout", "stderr"):
                    if pending_stream != stream and self._spool.has_pending(
                        ctx.attempt_id, pending_stream
                    ):
                        pending.extend(self._spool.pending(ctx.attempt_id, pending_stream))
            pending.sort(key=lambda batch: batch.start_offset)
            for batch in pending:
                batches.append((batch.stream, batch.start_offset, list(batch.lines)))
                next_offset = max(next_offset, _batch_wire_end(batch))
        if lines:
            if self._spool is not None:
                try:
                    # Durable BEFORE upload — survives crash/restart.
                    self._spool.write(
                        SpooledBatch(ctx.attempt_id, stream, next_offset, tuple(lines))
                    )
                except DaemonError:
                    # SpoolFullError (backpressure): put the lines back so
                    # nothing is lost, then let the error propagate to the
                    # supervisor.
                    self._rebuffer_sync(key, lines)
                    raise
            batches.append((stream, next_offset, lines))
        return batches

    async def _upload_one(
        self, ctx: AttemptContext, stream: str, start_offset: int, lines: list[str],
        *, sealed: bool,
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
        await self._journal.update(
            ctx.attempt_id,
            log_offset_stdout=ack.accepted_end_offset,
            log_offset_stderr=ack.accepted_end_offset,
        )

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
    ``start``); keep the first not-fully-confirmed line onward. Advances by the
    WIRE bytes per line (newline included) so the prefix boundary lines up with
    the server watermark, which counts the same way (``_line_bytes``)."""
    if expected <= start:
        return list(lines)
    running = start
    out: list[str] = []
    for line in lines:
        end = running + _line_bytes(line)
        if end <= expected:
            running = end
            continue
        out.append(line)
        running = end
    return out

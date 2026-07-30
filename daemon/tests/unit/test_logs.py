import asyncio

import pytest

from mesh_runtime.api import LogAck
from mesh_runtime.attempt import AttemptContext
from mesh_runtime.errors import LeaseConflictError, ServerError
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.spool import LogSpool, SpoolFullError
from mesh_runtime.timeutil import FakeClock

SECRET = "sk-live-TopSecret123"


class StubApi:
    """Records append_logs calls; scriptable ack / error per call."""

    def __init__(self):
        self.calls = []
        self.acks = None  # None -> echo start_offset + sum(line bytes)
        self.errors = []  # FIFO of exceptions to raise

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        self.calls.append(
            dict(attempt_id=attempt_id, lease_seq=lease_seq, stream=stream,
                 start_offset=start_offset, lines=list(lines), sealed=sealed)
        )
        if self.errors:
            raise self.errors.pop(0)
        if self.acks is not None:
            return self.acks.pop(0)
        # Server contract (backend/src/mesh/runtime/logs.py::_line_bytes): each
        # line occupies its UTF-8 bytes PLUS the trailing newline.
        end = start_offset + sum(len(line.encode()) + 1 for line in lines)
        return LogAck(accepted_end_offset=end, redacted_hits=0)


class RecordingApi:
    """Server-contract fake that enforces the single cumulative watermark.

    Records every ACCEPTED batch as ``(stream, start, end, lines)`` where the
    wire ``end`` mirrors the server's ``_line_bytes`` (UTF-8 bytes + trailing
    newline). Like ``backend/src/mesh/runtime/logs.py``, a batch whose
    ``start_offset`` differs from the accepted end is a 409 ``offset_mismatch``
    (counted). ``fail_first_stdout`` drives the first stdout batch into the
    spool with a transient error so the cross-stream replay path is exercised."""

    def __init__(self, *, fail_first_stdout: bool = False):
        self.accepted: list[tuple[str, int, int, list[str]]] = []
        self.sealed_calls: list[tuple[str, int]] = []
        self.offset_mismatch_count = 0
        self._expected = 0
        self._fail_first_stdout = fail_first_stdout

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        if self._fail_first_stdout and stream == "stdout":
            self._fail_first_stdout = False
            raise ServerError("500")
        if start_offset != self._expected:
            self.offset_mismatch_count += 1
            raise LeaseConflictError(
                "409", code="offset_mismatch",
                details={"expected": self._expected, "received": start_offset},
            )
        if sealed:
            self.sealed_calls.append((stream, start_offset))
        if not lines:
            return LogAck(accepted_end_offset=self._expected, redacted_hits=0)
        end = start_offset + sum(len(line.encode()) + 1 for line in lines)
        self.accepted.append((stream, start_offset, end, list(lines)))
        self._expected = end
        return LogAck(accepted_end_offset=end, redacted_hits=0)

    @property
    def watermark(self) -> int:
        return self._expected

    def received_lines(self) -> dict[str, list[str]]:
        """Accepted lines per stream, reconstructed in offset order."""
        by_stream: dict[str, list[tuple[int, list[str]]]] = {}
        for stream, start, _end, lines in self.accepted:
            by_stream.setdefault(stream, []).append((start, lines))
        return {
            stream: [line for _, group in sorted(groups) for line in group]
            for stream, groups in by_stream.items()
        }

    def no_overlap(self) -> bool:
        """No two accepted [start, end) ranges overlap (across both streams)."""
        ranges = sorted((start, end) for _, start, end, _ in self.accepted)
        return all(ranges[i][1] <= ranges[i + 1][0] for i in range(len(ranges) - 1))


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


@pytest.fixture
def ctx():
    return AttemptContext(attempt_id="att-1", execution_id="exec-1", runtime_id="rt-1", lease_seq=1)


def uploader(api, journal, redactor=None, *, clock, spool=None, **kw):
    redactor = redactor or RedactionPipeline(secrets=[], rule_version="v1")
    return LogUploader(api, journal, redactor, clock=clock, spool=spool, **kw)


async def seed(journal, ctx):
    await journal.put(ctx.attempt_id, execution_id=ctx.execution_id,
                      runtime_id=ctx.runtime_id, lease_seq=ctx.lease_seq, status="running")


class TestBatching:
    async def test_submit_buffers_until_line_threshold(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=3)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "a")
        await up.submit(ctx, "stdout", "b")
        assert api.calls == []  # not yet at threshold
        await up.submit(ctx, "stdout", "c")
        assert len(api.calls) == 1
        assert api.calls[0]["lines"] == ["a", "b", "c"]

    async def test_submit_flushes_on_byte_threshold(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=100, batch_bytes=10)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "01234")  # 5 bytes
        assert api.calls == []
        await up.submit(ctx, "stdout", "56789")  # 10 bytes total -> flush
        assert len(api.calls) == 1

    async def test_submit_flushes_after_interval(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=100, batch_bytes=10_000, batch_interval=0.5)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "line")
        assert api.calls == []
        clock.advance(0.6)
        await up.submit(ctx, "stdout", "later")  # elapsed >= interval -> flush
        assert len(api.calls) == 1
        assert api.calls[0]["lines"] == ["line", "later"]


class TestOffsets:
    async def test_start_offset_from_journal_and_advances(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")  # 3 bytes + newline = 4 wire bytes
        await up.submit(ctx, "stdout", "de")   # next batch starts at 4
        assert api.calls[0]["start_offset"] == 0
        assert api.calls[1]["start_offset"] == 4
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 7  # 4 + (2 + 1)

    async def test_offset_is_unified_across_streams(self, journal, ctx):
        # Server contract (MES-98): start_offset is cumulative BYTES per attempt
        # across BOTH streams. A stderr batch must continue where stdout ended,
        # never restart at 0 — otherwise the server 409s offset_mismatch.
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")  # [0, 4)
        await up.submit(ctx, "stderr", "xy")   # must start at 4, not 0
        stderr_calls = [c for c in api.calls if c["stream"] == "stderr"]
        assert stderr_calls[0]["start_offset"] == 4
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 7
        assert entry.log_offset_stderr == 7

    async def test_interleaved_streams_keep_single_watermark(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "aaaa")  # [0, 5)
        await up.submit(ctx, "stderr", "bb")    # [5, 8)
        await up.submit(ctx, "stdout", "cc")    # [8, 11)
        starts = [(c["stream"], c["start_offset"]) for c in api.calls]
        assert starts == [("stdout", 0), ("stderr", 5), ("stdout", 8)]
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 11
        assert entry.log_offset_stderr == 11


class TestCrossStreamWatermark:
    async def test_other_stream_pending_blocks_overlapping_offsets(
        self, journal, ctx, tmp_path
    ):
        """MES-96 P2-1: a transiently-failed stdout batch sits in the spool
        occupying [0, 6). A following stderr flush must NOT start its batch at
        that same offset — the single cumulative watermark is serialized across
        BOTH streams' pending batches, so the spooled stdout batch replays first
        (in offset order) and stderr continues right after it. Result: zero
        overlapping uploads, zero offset_mismatch on the retry, and both
        streams' lines fully delivered in order with one shared watermark."""
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        api = RecordingApi(fail_first_stdout=True)
        up = uploader(api, journal, clock=FakeClock(), spool=spool, batch_lines=1)
        await seed(journal, ctx)
        # stdout trips the line threshold; the upload fails once -> spooled [0, 6)
        await up.submit(ctx, "stdout", "out-1")
        assert spool.has_pending(ctx.attempt_id, "stdout")
        # stderr flush must replay stdout's spooled batch first, then continue
        await up.submit(ctx, "stderr", "err-1")
        await up.flush(ctx, sealed=True)  # terminal flush / replay settles it
        assert api.received_lines() == {"stdout": ["out-1"], "stderr": ["err-1"]}
        assert api.no_overlap()
        assert api.offset_mismatch_count == 0
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == entry.log_offset_stderr == api.watermark

    async def test_sealed_flush_drains_sibling_spool_before_certifying(
        self, journal, ctx, tmp_path
    ):
        """MES-96 P2-1 (sealed completeness): a transiently-failed stdout batch
        sits in the spool while a terminal SEALED flush is addressed to the
        sibling stream. Incomplete logs may never certify completed — the
        sealed flush must drain the sibling's un-acked batch FIRST (folding
        both streams' pending in offset order), and ``sealed=True`` must land
        exactly once, on the final drained batch. Pre-fix this sent an empty
        sealed heartbeat on stderr and left stdout's lines spooled forever."""
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        api = RecordingApi(fail_first_stdout=True)
        up = uploader(api, journal, clock=FakeClock(), spool=spool, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "out-1")  # fails once -> spooled [0, 6)
        assert spool.has_pending(ctx.attempt_id, "stdout")
        # Terminal flush addressed to the stream with no content of its own.
        await up._flush_stream(ctx, "stderr", sealed=True)
        assert api.received_lines() == {"stdout": ["out-1"]}
        assert not spool.has_pending(ctx.attempt_id, "stdout")
        # sealed landed exactly once — on the final drained batch, not on an
        # empty heartbeat that would certify still-missing lines.
        assert api.sealed_calls == [("stdout", 0)]
        assert api.offset_mismatch_count == 0


class TestRedaction:
    async def test_lines_redacted_before_upload(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        redactor = RedactionPipeline(secrets=[SECRET], rule_version="v1")
        up = uploader(api, journal, redactor, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        result = await up.submit(ctx, "stdout", f"token={SECRET}")
        assert api.calls[0]["lines"] == ["token=***"]
        assert SECRET not in api.calls[0]["lines"][0]
        assert result.redacted_hits == 1

    async def test_offset_uses_redacted_bytes(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        redactor = RedactionPipeline(secrets=[SECRET], rule_version="v1")
        up = uploader(api, journal, redactor, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", SECRET)        # redacted -> "***" (3 bytes + newline)
        await up.submit(ctx, "stdout", "x")
        assert api.calls[1]["start_offset"] == 4  # redacted wire length, not original


class TestFlushAndReconcile:
    async def test_flush_drains_remaining_with_sealed(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=100)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "only")
        assert api.calls == []
        await up.flush(ctx, sealed=True)
        sealed_calls = [c for c in api.calls if c["stream"] == "stdout"]
        assert sealed_calls[0]["lines"] == ["only"]
        assert sealed_calls[0]["sealed"] is True

    async def test_offset_mismatch_reconciles_and_retries(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=100)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "aaaa")  # 4 bytes, journal offset now 4
        await up.submit(ctx, "stdout", "bbbb")
        api.calls.clear()
        # Server says it already has 4 bytes (expected=4); our start would be 4 too,
        # but simulate drift: journal thinks 0-ish. Force a mismatch on first try.
        api.errors = [
            LeaseConflictError("409", code="offset_mismatch", details={"expected": 4})
        ]
        await journal.update(ctx.attempt_id, log_offset_stdout=0)  # drift behind server
        await up.flush(ctx, sealed=True)
        # retried with expected offset 4; confirmed prefix dropped
        retry = [c for c in api.calls if c["stream"] == "stdout"][-1]
        assert retry["start_offset"] == 4

    async def test_lease_mismatch_propagates(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        api.errors = [LeaseConflictError("409", code="lease_seq_mismatch")]
        with pytest.raises(LeaseConflictError):
            await up.submit(ctx, "stdout", "x")


class TestNoLossAndSpool:
    """HIGH-2: a transient (non-409) upload failure must never permanently lose
    already-redacted lines. With a spool the batch is durable and replayed
    idempotently; even without one the lines are re-buffered and retried."""

    async def test_transient_failure_rebuffers_and_retries_without_spool(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=100)  # spool=None
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")
        api.errors = [ServerError("500")]
        await up.flush(ctx, sealed=False)  # transient -> swallowed + re-buffered
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 0
        await up.flush(ctx, sealed=True)  # retry succeeds
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 4  # not lost

    async def test_transient_failure_does_not_fail_the_attempt_on_submit(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        api.errors = [ServerError("500")]
        # Mid-stream threshold flush fails transiently; submit must NOT raise
        # (a 5xx must not kill the attempt) and the line must survive.
        await up.submit(ctx, "stdout", "abc")
        api.errors = []
        await up.flush(ctx, sealed=True)
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 4

    async def test_spool_replays_unacked_batch_after_transient_failure(self, journal, ctx, tmp_path):
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")
        api.errors = [ServerError("500")]
        await up.flush(ctx, sealed=False)
        assert spool.has_pending(ctx.attempt_id, "stdout")  # durable, not lost
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 0
        await up.flush(ctx, sealed=False)  # replays the spooled batch, acks it
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 4
        assert not spool.has_pending(ctx.attempt_id, "stdout")

    async def test_spool_survives_restart_and_replays(self, journal, ctx, tmp_path):
        api, clock = StubApi(), FakeClock()
        dir_ = tmp_path / "spool"
        up1 = uploader(api, journal, clock=clock, batch_lines=100,
                       spool=LogSpool(dir_, max_bytes=4096))
        await seed(journal, ctx)
        await up1.submit(ctx, "stdout", "abc")
        api.errors = [ServerError("500")]
        await up1.flush(ctx, sealed=False)  # spooled, upload failed
        # "restart": a brand-new uploader + spool handle over the same directory
        spool2 = LogSpool(dir_, max_bytes=4096)
        up2 = uploader(api, journal, clock=clock, batch_lines=100, spool=spool2)
        await up2.flush(ctx, sealed=True)
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 4
        assert not spool2.has_pending(ctx.attempt_id, "stdout")

    async def test_sealed_flush_transient_failure_propagates_but_spool_retains(
        self, journal, ctx, tmp_path
    ):
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")
        api.errors = [ServerError("500")]
        with pytest.raises(ServerError):  # sealed -> supervisor decides
            await up.flush(ctx, sealed=True)
        assert spool.has_pending(ctx.attempt_id, "stdout")  # retained for next run

    async def test_sealed_flush_replays_spooled_batch_then_sparse_tail_seals_last(
        self, journal, ctx, tmp_path
    ):
        """Pinned negative scenario: a mid-stream transient 5xx leaves batch A
        durable on disk, a sparse tail B never reaches a send threshold and
        stays in memory, and the attempt then ends. The sealed flush must
        upload A, THEN B, and land ``sealed`` on the TRUE last batch (B) —
        never on the replayed spool batch, never drop B."""
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "AAAA")  # batch A buffered
        api.errors = [ServerError("500")]
        await up.flush(ctx, sealed=False)  # A spooled, upload failed -> on disk
        assert spool.has_pending(ctx.attempt_id, "stdout")
        await up.submit(ctx, "stdout", "bb")  # sparse tail B: below threshold
        assert len(api.calls) == 1  # B not uploaded yet

        await up.flush(ctx, sealed=True)  # attempt ends -> terminal flush

        stdout_calls = [c for c in api.calls if c["stream"] == "stdout"]
        assert len(stdout_calls) == 3  # failed A + replayed A + continuation B
        replay_a, tail_b = stdout_calls[1], stdout_calls[2]
        assert replay_a["lines"] == ["AAAA"]
        assert replay_a["start_offset"] == 0
        assert replay_a["sealed"] is False  # sealed must NOT ride the replay
        assert tail_b["lines"] == ["bb"]
        assert tail_b["start_offset"] == 5  # continues right after A's wire bytes
        assert tail_b["sealed"] is True  # sealed lands on the TRUE last batch
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 8
        assert not spool.has_pending(ctx.attempt_id, "stdout")

    async def test_sealed_flush_failure_retry_replays_all_and_seals_last(
        self, journal, ctx, tmp_path
    ):
        """If the first SEALED attempt fails transiently while replaying the
        spooled batch, the in-memory tail is already durable (spooled before
        upload) and the supervisor's retry replays BOTH batches in order,
        sealing only the true last one."""
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "AAAA")
        api.errors = [ServerError("500")]
        await up.flush(ctx, sealed=False)  # A on disk
        await up.submit(ctx, "stdout", "bb")  # B in memory
        api.errors = [ServerError("500")]
        with pytest.raises(ServerError):
            await up.flush(ctx, sealed=True)  # first sealed attempt fails on A
        # B was made durable as the continuation BEFORE the failed upload.
        pending = spool.pending(ctx.attempt_id, "stdout")
        assert [(b.start_offset, b.lines) for b in pending] == [
            (0, ("AAAA",)),
            (5, ("bb",)),
        ]
        await up.flush(ctx, sealed=True)  # supervisor retry
        stdout_sealed = [
            c for c in api.calls if c["stream"] == "stdout" and c["sealed"]
        ]
        assert len(stdout_sealed) == 1
        assert stdout_sealed[0]["lines"] == ["bb"]
        assert stdout_sealed[0]["start_offset"] == 5
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 8
        assert not spool.has_pending(ctx.attempt_id, "stdout")

    async def test_spool_full_backpressures_and_preserves_lines(self, journal, ctx, tmp_path):
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=2)  # too small for "abcd"
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abcd")
        with pytest.raises(SpoolFullError):
            await up.flush(ctx, sealed=False)
        # The lines were NOT dropped: a second flush still tries to spool them
        # (still over cap), proving they remain buffered for backpressure.
        with pytest.raises(SpoolFullError):
            await up.flush(ctx, sealed=True)
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 0

    async def test_offset_mismatch_reconcile_with_spool(self, journal, ctx, tmp_path):
        api, clock = StubApi(), FakeClock()
        spool = LogSpool(tmp_path / "spool", max_bytes=4096)
        up = uploader(api, journal, clock=clock, batch_lines=100, spool=spool)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "aaaa")  # journal offset -> 4
        await up.submit(ctx, "stdout", "bbbb")
        await journal.update(ctx.attempt_id, log_offset_stdout=0)  # drift behind server
        api.errors = [LeaseConflictError("409", code="offset_mismatch", details={"expected": 4})]
        await up.flush(ctx, sealed=True)
        retry = [c for c in api.calls if c["stream"] == "stdout"][-1]
        assert retry["start_offset"] == 4
        assert not spool.has_pending(ctx.attempt_id, "stdout")



class TestIntervalTimer:
    """§3.9.2 "any condition sends": the 500 ms arm must fire on its own
    clock — a sparse stream (one line, then silence) cannot wait for the next
    line, which may never come before finalize."""

    async def test_flush_due_sends_sparse_stream_without_new_lines(self, journal, ctx):
        """Deterministic core: once the batch interval has elapsed, flush_due
        sends the buffered line even though no further submit() ever ran."""
        api, clock = StubApi(), FakeClock()
        up = uploader(
            api, journal, clock=clock,
            batch_lines=100, batch_bytes=10_000, batch_interval=0.5,
        )
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "lonely")
        assert api.calls == []  # below every threshold
        clock.advance(0.6)
        await up.flush_due()  # interval elapsed -> sends, NO new line needed
        assert len(api.calls) == 1
        assert api.calls[0]["lines"] == ["lonely"]
        assert api.calls[0]["sealed"] is False  # timer flushes are never sealed
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 7
        await up.flush_due()  # idempotent — buffer already drained
        assert len(api.calls) == 1

    async def test_flush_due_noop_before_interval(self, journal, ctx):
        """Negative anchor: before the interval elapses, flush_due sends
        nothing — the line still needs the next line / a threshold."""
        api, clock = StubApi(), FakeClock()
        up = uploader(
            api, journal, clock=clock,
            batch_lines=100, batch_bytes=10_000, batch_interval=0.5,
        )
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "lonely")
        clock.advance(0.49)
        await up.flush_due()
        assert api.calls == []

    async def test_timer_task_flushes_sparse_stream_without_new_lines(self, journal, ctx):
        """Wiring: the background tick task drives flush_due on its own —
        proved by an Event (no yield-count polling race)."""
        flushed = asyncio.Event()
        api = StubApi()
        original = api.append_logs

        async def append_and_signal(*args, **kw):
            ack = await original(*args, **kw)
            flushed.set()
            return ack

        api.append_logs = append_and_signal
        clock = FakeClock()
        up = uploader(
            api, journal, clock=clock,
            batch_lines=100, batch_bytes=10_000, batch_interval=0.5,
        )
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "lonely")
        await up.start_ticking()
        try:
            await asyncio.wait_for(flushed.wait(), timeout=5)
        finally:
            await up.stop_ticking()
        assert api.calls[0]["lines"] == ["lonely"]
        assert api.calls[0]["sealed"] is False

    async def test_timer_survives_transient_upload_failure(self, journal, ctx):
        """A transient failure on a due flush must not kill the timer: the
        re-buffered lines go out on a later tick."""
        attempts = {"n": 0}
        recovered = asyncio.Event()
        api = StubApi()
        original = api.append_logs

        async def flaky_append(*args, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ServerError("500")
            ack = await original(*args, **kw)
            recovered.set()
            return ack

        api.append_logs = flaky_append
        clock = FakeClock()
        up = uploader(
            api, journal, clock=clock,
            batch_lines=100, batch_bytes=10_000, batch_interval=0.5,
        )
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "retry-me")
        await up.start_ticking()
        try:
            await asyncio.wait_for(recovered.wait(), timeout=5)

            async def watermark_caught_up() -> None:
                while (await journal.get(ctx.attempt_id)).log_offset_stdout == 0:
                    await asyncio.sleep(0)

            await asyncio.wait_for(watermark_caught_up(), timeout=5)
        finally:
            await up.stop_ticking()
        assert api.calls[-1]["lines"] == ["retry-me"]  # same lines, not lost
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 9

    async def test_stop_ticking_is_idempotent(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock)
        await seed(journal, ctx)
        await up.stop_ticking()  # never started -> no-op
        await up.start_ticking()
        await up.stop_ticking()
        await up.stop_ticking()  # second stop -> no-op

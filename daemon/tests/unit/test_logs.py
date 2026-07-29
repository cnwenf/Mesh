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
        end = start_offset + sum(len(line.encode()) for line in lines)
        return LogAck(accepted_end_offset=end, redacted_hits=0)


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
        await up.submit(ctx, "stdout", "abc")  # 3 bytes
        await up.submit(ctx, "stdout", "de")   # next batch starts at 3
        assert api.calls[0]["start_offset"] == 0
        assert api.calls[1]["start_offset"] == 3
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 5

    async def test_offset_is_unified_across_streams(self, journal, ctx):
        # Server contract (MES-98): start_offset is cumulative BYTES per attempt
        # across BOTH streams. A stderr batch must continue where stdout ended,
        # never restart at 0 — otherwise the server 409s offset_mismatch.
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")  # [0, 3)
        await up.submit(ctx, "stderr", "xy")   # must start at 3, not 0
        stderr_calls = [c for c in api.calls if c["stream"] == "stderr"]
        assert stderr_calls[0]["start_offset"] == 3
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 5
        assert entry.log_offset_stderr == 5

    async def test_interleaved_streams_keep_single_watermark(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "aaaa")  # [0, 4)
        await up.submit(ctx, "stderr", "bb")    # [4, 6)
        await up.submit(ctx, "stdout", "cc")    # [6, 8)
        starts = [(c["stream"], c["start_offset"]) for c in api.calls]
        assert starts == [("stdout", 0), ("stderr", 4), ("stdout", 6)]
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 8
        assert entry.log_offset_stderr == 8


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
        await up.submit(ctx, "stdout", SECRET)        # redacted -> "***" (3 bytes)
        await up.submit(ctx, "stdout", "x")
        assert api.calls[1]["start_offset"] == 3  # redacted byte length, not original


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
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 3  # not lost

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
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 3

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
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 3
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
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 3
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
        assert tail_b["start_offset"] == 4  # continues right after A's bytes
        assert tail_b["sealed"] is True  # sealed lands on the TRUE last batch
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 6
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
            (4, ("bb",)),
        ]
        await up.flush(ctx, sealed=True)  # supervisor retry
        stdout_sealed = [
            c for c in api.calls if c["stream"] == "stdout" and c["sealed"]
        ]
        assert len(stdout_sealed) == 1
        assert stdout_sealed[0]["lines"] == ["bb"]
        assert stdout_sealed[0]["start_offset"] == 4
        assert (await journal.get(ctx.attempt_id)).log_offset_stdout == 6
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


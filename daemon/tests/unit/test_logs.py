import pytest

from mesh_runtime.api import LogAck
from mesh_runtime.attempt import AttemptContext
from mesh_runtime.errors import LeaseConflictError
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.redaction import RedactionPipeline
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


def uploader(api, journal, redactor=None, *, clock, **kw):
    redactor = redactor or RedactionPipeline(secrets=[], rule_version="v1")
    return LogUploader(api, journal, redactor, clock=clock, **kw)


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

    async def test_separate_offsets_per_stream(self, journal, ctx):
        api, clock = StubApi(), FakeClock()
        up = uploader(api, journal, clock=clock, batch_lines=1)
        await seed(journal, ctx)
        await up.submit(ctx, "stdout", "abc")
        await up.submit(ctx, "stderr", "xy")
        entry = await journal.get(ctx.attempt_id)
        assert entry.log_offset_stdout == 3
        assert entry.log_offset_stderr == 2


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

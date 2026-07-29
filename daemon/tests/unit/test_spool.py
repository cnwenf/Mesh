import os
import stat

import pytest

from mesh_runtime.spool import LogSpool, SpooledBatch, SpoolFullError


@pytest.fixture
def spool(tmp_path):
    return LogSpool(tmp_path / "spool", max_bytes=1024)


def batch(attempt="att-1", stream="stdout", offset=0, lines=("a", "b")):
    return SpooledBatch(attempt_id=attempt, stream=stream, start_offset=offset, lines=tuple(lines))


class TestWriteAndPending:
    def test_write_then_pending_roundtrips_lines(self, spool):
        spool.write(batch(offset=0, lines=("alpha", "beta")))
        pending = spool.pending("att-1", "stdout")
        assert len(pending) == 1
        assert pending[0].lines == ("alpha", "beta")
        assert pending[0].start_offset == 0

    def test_pending_is_sorted_by_offset(self, spool):
        spool.write(batch(offset=30, lines=("c",)))
        spool.write(batch(offset=10, lines=("a",)))
        spool.write(batch(offset=20, lines=("b",)))
        offsets = [b.start_offset for b in spool.pending("att-1", "stdout")]
        assert offsets == [10, 20, 30]

    def test_pending_is_scoped_to_attempt_and_stream(self, spool):
        spool.write(batch(attempt="att-1", stream="stdout", offset=0))
        spool.write(batch(attempt="att-1", stream="stderr", offset=0))
        spool.write(batch(attempt="att-2", stream="stdout", offset=0))
        assert len(spool.pending("att-1", "stdout")) == 1
        assert len(spool.pending("att-1", "stderr")) == 1
        assert len(spool.pending("att-2", "stdout")) == 1
        assert spool.pending("att-3", "stdout") == []

    def test_has_pending(self, spool):
        assert not spool.has_pending("att-1", "stdout")
        spool.write(batch())
        assert spool.has_pending("att-1", "stdout")


class TestDurabilityAndPermissions:
    def test_survives_a_new_spool_handle(self, tmp_path):
        dir_ = tmp_path / "spool"
        LogSpool(dir_, max_bytes=1024).write(batch(lines=("persisted",)))
        reopened = LogSpool(dir_, max_bytes=1024)  # simulates daemon restart
        assert reopened.pending("att-1", "stdout")[0].lines == ("persisted",)

    def test_spool_files_are_0600_and_dir_0700(self, tmp_path):
        dir_ = tmp_path / "spool"
        s = LogSpool(dir_, max_bytes=1024)
        s.write(batch())
        assert stat.S_IMODE(os.stat(dir_).st_mode) == 0o700
        files = list(dir_.iterdir())
        assert files
        for f in files:
            assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


class TestAckAndDrain:
    def test_ack_removes_only_that_batch(self, spool):
        spool.write(batch(offset=0, lines=("a",)))
        spool.write(batch(offset=5, lines=("b",)))
        spool.ack("att-1", "stdout", 0)
        offsets = [b.start_offset for b in spool.pending("att-1", "stdout")]
        assert offsets == [5]

    def test_ack_of_missing_batch_is_a_noop(self, spool):
        spool.ack("att-1", "stdout", 999)  # nothing raised

    def test_drain_removes_every_stream_of_an_attempt(self, spool):
        spool.write(batch(attempt="att-1", stream="stdout", offset=0))
        spool.write(batch(attempt="att-1", stream="stderr", offset=0))
        spool.write(batch(attempt="att-2", stream="stdout", offset=0))
        spool.drain("att-1")
        assert spool.pending("att-1", "stdout") == []
        assert spool.pending("att-1", "stderr") == []
        assert len(spool.pending("att-2", "stdout")) == 1


class TestBackpressure:
    def test_write_over_cap_raises_spool_full(self, tmp_path):
        s = LogSpool(tmp_path / "spool", max_bytes=10)
        s.write(batch(offset=0, lines=("12345",)))  # 5 bytes
        with pytest.raises(SpoolFullError):
            s.write(batch(offset=5, lines=("1234567890",)))  # would exceed 10
        # the rejected batch was NOT persisted
        assert [b.start_offset for b in s.pending("att-1", "stdout")] == [0]

    def test_total_bytes_tracks_spooled_content(self, spool):
        assert spool.total_bytes() == 0
        spool.write(batch(offset=0, lines=("abcd",)))  # 4 bytes
        spool.write(batch(offset=4, lines=("ef",)))    # 2 bytes
        assert spool.total_bytes() == 6
        spool.ack("att-1", "stdout", 0)
        assert spool.total_bytes() == 2

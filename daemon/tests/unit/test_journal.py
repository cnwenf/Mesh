import stat

import pytest

from mesh_runtime.journal import ACTIVE_STATUSES, Journal, JournalEntry


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


class TestJournal:
    async def test_open_creates_0600_file(self, tmp_path):
        path = tmp_path / "ledger.sqlite3"
        j = Journal(path)
        await j.open()
        await j.close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    async def test_put_and_get_roundtrip(self, journal):
        await journal.put(
            "att-1", execution_id="exec-1", runtime_id="rt-1",
            lease_seq=1, status="claimed", work_dir="/w/att-1",
        )
        entry = await journal.get("att-1")
        assert isinstance(entry, JournalEntry)
        assert entry.attempt_id == "att-1"
        assert entry.execution_id == "exec-1"
        assert entry.lease_seq == 1
        assert entry.status == "claimed"
        assert entry.log_offset_stdout == 0

    async def test_get_missing_returns_none(self, journal):
        assert await journal.get("nope") is None

    async def test_update_allowed_fields(self, journal):
        await journal.put("att-1", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        await journal.update("att-1", status="running", lease_seq=5, log_offset_stdout=128)
        entry = await journal.get("att-1")
        assert entry.status == "running"
        assert entry.lease_seq == 5
        assert entry.log_offset_stdout == 128

    async def test_update_rejects_unknown_field(self, journal):
        await journal.put("att-1", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        with pytest.raises(ValueError, match="field"):
            await journal.update("att-1", evil="x")

    async def test_update_missing_attempt_noop(self, journal):
        await journal.update("ghost", status="running")  # no raise

    async def test_list_active_filters_terminal(self, journal):
        await journal.put("a1", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        await journal.put("a2", execution_id="e", runtime_id="r", lease_seq=1, status="running")
        await journal.put("a3", execution_id="e", runtime_id="r", lease_seq=1, status="terminal_reported")
        await journal.put("a4", execution_id="e", runtime_id="r", lease_seq=1, status="lease_lost")
        active = {e.attempt_id for e in await journal.list_active()}
        assert active == {"a1", "a2"}
        assert set(ACTIVE_STATUSES) == {"claimed", "running"}

    async def test_delete_removes_entry(self, journal):
        await journal.put("a1", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        await journal.delete("a1")
        assert await journal.get("a1") is None

    async def test_delete_missing_ok(self, journal):
        await journal.delete("ghost")  # no raise

    async def test_persistence_across_reopen(self, tmp_path):
        path = tmp_path / "ledger.sqlite3"
        j1 = Journal(path)
        await j1.open()
        await j1.put("a1", execution_id="e", runtime_id="r", lease_seq=3, status="running")
        await j1.close()
        j2 = Journal(path)
        await j2.open()
        entry = await j2.get("a1")
        assert entry is not None and entry.lease_seq == 3
        await j2.close()

    async def test_never_stores_secret_fields(self, journal, tmp_path):
        await journal.put("a1", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        raw = (tmp_path / "ledger.sqlite3").read_bytes()
        assert b"mesh_rt_" not in raw
        assert b"prompt" not in raw.lower()

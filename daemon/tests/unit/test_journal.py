import os
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

    async def test_open_tightens_state_dir_to_0700(self, tmp_path):
        """SQLite creates transient ``-journal``/``-wal``/``-shm`` aux files
        under the process umask (often 0644). A 0700 state dir keeps them
        unreadable to other users regardless of umask (§2.3, defense in depth
        on top of the doctor's directory check)."""
        import os

        state = tmp_path / "state"
        state.mkdir(mode=0o755)  # deliberately too permissive
        j = Journal(state / "ledger.sqlite3")
        await j.open()
        await j.put("a", execution_id="e", runtime_id="r", lease_seq=1, status="claimed")
        await j.close()
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o700

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
        # terminal BUT still owning spooled batches — reconciliation owns it
        await journal.put("a5", execution_id="e", runtime_id="r", lease_seq=1, status="terminal_seal_pending")
        active = {e.attempt_id for e in await journal.list_active()}
        assert active == {"a1", "a2", "a5"}
        assert set(ACTIVE_STATUSES) == {"claimed", "running", "terminal_seal_pending"}

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


class TestCleanupStateAndMigration:
    """A2 journal: cleanup_state + sandbox_handle columns, with an idempotent
    migration for A1-era databases that lack them."""

    async def test_new_columns_default_empty(self, journal):
        await journal.put("a1", execution_id="e1", runtime_id="r1", lease_seq=1, status="claimed")
        entry = await journal.get("a1")
        assert entry.cleanup_state == ""
        assert entry.sandbox_handle == ""

    async def test_update_cleanup_state_and_sandbox_handle(self, journal):
        await journal.put("a1", execution_id="e1", runtime_id="r1", lease_seq=1, status="claimed")
        await journal.update("a1", cleanup_state="cgroup_killed", sandbox_handle="mesh/a1")
        entry = await journal.get("a1")
        assert entry.cleanup_state == "cgroup_killed"
        assert entry.sandbox_handle == "mesh/a1"

    async def test_legacy_database_is_migrated_on_open(self, tmp_path):
        import sqlite3

        path = tmp_path / "ledger.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE attempts (
                attempt_id        TEXT PRIMARY KEY,
                execution_id      TEXT NOT NULL,
                runtime_id        TEXT NOT NULL,
                lease_seq         INTEGER NOT NULL,
                lease_expires_at  REAL NOT NULL DEFAULT 0,
                status            TEXT NOT NULL,
                log_offset_stdout INTEGER NOT NULL DEFAULT 0,
                log_offset_stderr INTEGER NOT NULL DEFAULT 0,
                work_dir          TEXT NOT NULL DEFAULT '',
                created_at        REAL NOT NULL
            );
            INSERT INTO attempts (attempt_id, execution_id, runtime_id, lease_seq,
                                  status, created_at)
            VALUES ('old-1', 'e1', 'r1', 4, 'running', 0);
            """
        )
        conn.commit()
        conn.close()

        j = Journal(path)
        await j.open()  # must migrate, not fail
        entry = await j.get("old-1")
        assert entry is not None
        assert entry.lease_seq == 4
        assert entry.cleanup_state == ""
        assert entry.sandbox_handle == ""
        await j.close()

        # Migration is idempotent: re-opening an already-migrated db works.
        j2 = Journal(path)
        await j2.open()
        assert (await j2.get("old-1")).cleanup_state == ""
        await j2.close()


class TestFilePermissions:
    async def test_created_0600_under_permissive_umask(self, tmp_path):
        """The ledger is pre-created with an explicit 0600 — even umask 000
        must not leave it world-readable, at any point (§2.3)."""
        old = os.umask(0o000)
        try:
            j = Journal(tmp_path / "state" / "ledger.sqlite3")
            await j.open()
            await j.close()
        finally:
            os.umask(old)
        mode = stat.S_IMODE((tmp_path / "state" / "ledger.sqlite3").stat().st_mode)
        assert mode == 0o600

    async def test_open_refuses_symlinked_ledger_path(self, tmp_path):
        """A symlink planted at the ledger path fails closed (O_NOFOLLOW)."""
        real = tmp_path / "state" / "real.sqlite3"
        real.parent.mkdir(mode=0o700)
        real.write_bytes(b"")
        link = tmp_path / "state" / "ledger.sqlite3"
        link.symlink_to(real)
        j = Journal(link)
        with pytest.raises(OSError):
            await j.open()

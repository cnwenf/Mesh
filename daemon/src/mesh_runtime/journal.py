"""Attempt journal — crash-recovery ledger (spec §2.3 / design §7.5).

SQLite, mode 0600, METADATA ONLY: ids, lease_seq, log offsets, work dir,
cleanup status. Never prompt, output, token or secret. On restart the daemon
reconciles these rows against the server FIRST — it never resumes a provider
or guesses a continuation from local state alone.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

#: Rows still considered in-flight and needing reconciliation on startup.
ACTIVE_STATUSES = frozenset({"claimed", "running"})

_UPDATABLE_FIELDS = frozenset(
    {
        "lease_seq",
        "lease_expires_at",
        "status",
        "log_offset_stdout",
        "log_offset_stderr",
        "work_dir",
        "cleanup_state",
        "sandbox_handle",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id        TEXT PRIMARY KEY,
    execution_id      TEXT NOT NULL,
    runtime_id        TEXT NOT NULL,
    lease_seq         INTEGER NOT NULL,
    lease_expires_at  REAL NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,
    log_offset_stdout INTEGER NOT NULL DEFAULT 0,
    log_offset_stderr INTEGER NOT NULL DEFAULT 0,
    work_dir          TEXT NOT NULL DEFAULT '',
    cleanup_state     TEXT NOT NULL DEFAULT '',
    sandbox_handle    TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);
"""

#: Columns added after A1; legacy databases are migrated idempotently on open.
_MIGRATED_COLUMNS = (
    ("cleanup_state", "TEXT NOT NULL DEFAULT ''"),
    ("sandbox_handle", "TEXT NOT NULL DEFAULT ''"),
)


@dataclass(frozen=True)
class JournalEntry:
    attempt_id: str
    execution_id: str
    runtime_id: str
    lease_seq: int
    lease_expires_at: float
    status: str
    log_offset_stdout: int
    log_offset_stderr: int
    work_dir: str
    created_at: float
    cleanup_state: str = ""
    sandbox_handle: str = ""


class Journal:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        import os

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite creates transient ``-journal``/``-wal``/``-shm`` rollback files
        # under the process umask (often 0644). Tightening the state directory to
        # 0700 keeps those aux files unreadable to other users regardless of
        # umask — defense in depth on top of the doctor's directory check (§2.3).
        os.chmod(self._path.parent, 0o700)
        # check_same_thread=False: every call is dispatched via asyncio.to_thread
        # (arbitrary worker threads) but serialized by self._lock, so the
        # connection is never used concurrently — only from varying threads.
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        self._migrate_sync(conn)
        conn.commit()
        self._conn = conn
        # Restrict AFTER creation so the file exists with 0600 regardless of umask.
        os.chmod(self._path, 0o600)

    @staticmethod
    def _migrate_sync(conn: sqlite3.Connection) -> None:
        """Add columns introduced after A1 to databases created by older
        versions. Idempotent: existing columns are left untouched."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(attempts)")}
        for name, definition in _MIGRATED_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE attempts ADD COLUMN {name} {definition}")

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("journal not opened")
        return self._conn

    async def put(
        self,
        attempt_id: str,
        *,
        execution_id: str,
        runtime_id: str,
        lease_seq: int,
        status: str,
        work_dir: str = "",
        lease_expires_at: float = 0.0,
        log_offset_stdout: int = 0,
        log_offset_stderr: int = 0,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._put_sync,
                attempt_id, execution_id, runtime_id, lease_seq, status,
                work_dir, lease_expires_at, log_offset_stdout, log_offset_stderr,
            )

    def _put_sync(
        self, attempt_id, execution_id, runtime_id, lease_seq, status,
        work_dir, lease_expires_at, log_offset_stdout, log_offset_stderr,
    ) -> None:
        self._require().execute(
            """
            INSERT INTO attempts (
                attempt_id, execution_id, runtime_id, lease_seq, lease_expires_at,
                status, log_offset_stdout, log_offset_stderr, work_dir, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(attempt_id) DO UPDATE SET
                execution_id=excluded.execution_id,
                runtime_id=excluded.runtime_id,
                lease_seq=excluded.lease_seq,
                lease_expires_at=excluded.lease_expires_at,
                status=excluded.status,
                work_dir=excluded.work_dir
            """,
            (
                attempt_id, execution_id, runtime_id, lease_seq, lease_expires_at,
                status, log_offset_stdout, log_offset_stderr, work_dir, time.time(),
            ),
        )
        self._require().commit()

    async def update(self, attempt_id: str, **fields) -> None:
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"journal.update: unknown field(s) {sorted(unknown)}")
        if not fields:
            return
        async with self._lock:
            await asyncio.to_thread(self._update_sync, attempt_id, fields)

    def _update_sync(self, attempt_id: str, fields: dict) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        params = [*fields.values(), attempt_id]
        self._require().execute(
            f"UPDATE attempts SET {assignments} WHERE attempt_id = ?", params
        )
        self._require().commit()

    async def get(self, attempt_id: str) -> JournalEntry | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, attempt_id)

    def _get_sync(self, attempt_id: str) -> JournalEntry | None:
        row = self._require().execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    async def list_active(self) -> list[JournalEntry]:
        async with self._lock:
            return await asyncio.to_thread(self._list_active_sync)

    def _list_active_sync(self) -> list[JournalEntry]:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        rows = self._require().execute(
            f"SELECT * FROM attempts WHERE status IN ({placeholders})",
            tuple(ACTIVE_STATUSES),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    async def delete(self, attempt_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, attempt_id)

    def _delete_sync(self, attempt_id: str) -> None:
        self._require().execute("DELETE FROM attempts WHERE attempt_id = ?", (attempt_id,))
        self._require().commit()


def _row_to_entry(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        attempt_id=row["attempt_id"],
        execution_id=row["execution_id"],
        runtime_id=row["runtime_id"],
        lease_seq=row["lease_seq"],
        lease_expires_at=row["lease_expires_at"],
        status=row["status"],
        log_offset_stdout=row["log_offset_stdout"],
        log_offset_stderr=row["log_offset_stderr"],
        work_dir=row["work_dir"],
        created_at=row["created_at"],
        cleanup_state=row["cleanup_state"],
        sandbox_handle=row["sandbox_handle"],
    )

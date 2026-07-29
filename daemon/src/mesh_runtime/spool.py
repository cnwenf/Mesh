"""Redacted log batch spool — durable relay buffer (spec §3.9.3 / design §8.3).

A batch is written to the spool BEFORE upload and removed only after the server
acks its end offset, so a crash, restart, or transient network failure can never
permanently lose already-redacted lines. Replay is idempotent: the server keys
log writes by ``(attempt, stream, start_offset)``, so re-sending a spooled batch
after a failure is safe. Contents are already redacted; files are 0600 and the
spool directory 0700. When the spool reaches its frozen cap it raises
:class:`SpoolFullError` so the supervisor can backpressure/terminate the
provider instead of buffering without bound.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.errors import DaemonError

_FILENAME_OFFSET_WIDTH = 20

#: Frozen per-attempt spool cap (§3.9.3: backpressure the provider, then
#: terminate). Redacted text only; a generous bound that still bounds disk use.
DEFAULT_SPOOL_MAX_BYTES = 64 * 1024 * 1024


class SpoolFullError(DaemonError):
    """Spool reached its frozen byte cap — backpressure the provider (§3.9.3)."""


@dataclass(frozen=True)
class SpooledBatch:
    attempt_id: str
    stream: str
    start_offset: int
    lines: tuple[str, ...]

    @property
    def byte_size(self) -> int:
        return sum(len(line.encode("utf-8")) for line in self.lines)


class LogSpool:
    """File-per-batch durable store keyed by ``(attempt, stream, start_offset)``.

    ``start_offset`` is zero-padded in the filename so lexicographic listing
    order equals numeric offset order. All IO is synchronous and small (tmpfs,
    one batch at a time); callers serialize it under the attempt lock.
    """

    def __init__(self, spool_dir: Path, *, max_bytes: int) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        self._dir = Path(spool_dir)
        self._max_bytes = max_bytes

    # -- writes -------------------------------------------------------------

    def write(self, batch: SpooledBatch) -> None:
        """Persist ``batch`` durably BEFORE upload. Raises :class:`SpoolFullError`
        (without persisting) if it would exceed the frozen cap."""
        if self.total_bytes() + batch.byte_size > self._max_bytes:
            raise SpoolFullError(
                f"spool cap {self._max_bytes} bytes exceeded; backpressure provider"
            )
        self._ensure_dir()
        payload = json.dumps(
            {
                "attempt_id": batch.attempt_id,
                "stream": batch.stream,
                "start_offset": batch.start_offset,
                "lines": list(batch.lines),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        final = self._path(batch.attempt_id, batch.stream, batch.start_offset)
        tmp = final.with_name(final.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)  # defend against a permissive umask
        os.replace(tmp, final)

    def ack(self, attempt_id: str, stream: str, start_offset: int) -> None:
        """Remove a batch once the server has confirmed it. Missing → no-op."""
        try:
            self._path(attempt_id, stream, start_offset).unlink()
        except FileNotFoundError:
            pass

    def drain(self, attempt_id: str) -> None:
        """Remove every spooled batch for an attempt (terminal cleanup)."""
        if not self._dir.is_dir():
            return
        prefix = f"{_escape(attempt_id)}__"
        for path in self._dir.iterdir():
            if path.name.startswith(prefix) and not path.name.endswith(".tmp"):
                path.unlink(missing_ok=True)

    # -- reads --------------------------------------------------------------

    def pending(self, attempt_id: str, stream: str) -> list[SpooledBatch]:
        """All unacked batches for ``(attempt, stream)``, ascending by offset."""
        if not self._dir.is_dir():
            return []
        prefix = f"{_escape(attempt_id)}__{_escape(stream)}__"
        found: list[SpooledBatch] = []
        for path in sorted(self._dir.iterdir()):
            name = path.name
            if not name.startswith(prefix) or name.endswith(".tmp"):
                continue
            batch = self._read(path)
            if batch is not None:
                found.append(batch)
        found.sort(key=lambda b: b.start_offset)
        return found

    def has_pending(self, attempt_id: str, stream: str) -> bool:
        if not self._dir.is_dir():
            return False
        prefix = f"{_escape(attempt_id)}__{_escape(stream)}__"
        return any(
            p.name.startswith(prefix) and not p.name.endswith(".tmp")
            for p in self._dir.iterdir()
        )

    def total_bytes(self) -> int:
        if not self._dir.is_dir():
            return 0
        total = 0
        for path in self._dir.iterdir():
            if path.name.endswith(".tmp"):
                continue
            batch = self._read(path)
            if batch is not None:
                total += batch.byte_size
        return total

    # -- internals ----------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def _path(self, attempt_id: str, stream: str, start_offset: int) -> Path:
        name = (
            f"{_escape(attempt_id)}__{_escape(stream)}__"
            f"{start_offset:0{_FILENAME_OFFSET_WIDTH}d}.json"
        )
        return self._dir / name

    def _read(self, path: Path) -> SpooledBatch | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return SpooledBatch(
                attempt_id=str(doc["attempt_id"]),
                stream=str(doc["stream"]),
                start_offset=int(doc["start_offset"]),
                lines=tuple(str(line) for line in doc["lines"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None  # corrupt/partial entry — skip; never crash the relay


def _escape(value: str) -> str:
    """Make a value safe as a filename segment (attempt ids are UUIDs and
    streams are ``stdout``/``stderr``, so this is belt-and-braces)."""
    return value.replace("/", "_").replace("\\", "_")

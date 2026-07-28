"""Error report attachment writer (import-export.md §2.4 / §5).

The FULL per-row error detail (potentially 100k rows) streams into a
scratch CSV — columns ``row,field,code,message`` — which the worker
registers as the job's result attachment. The inline ``error_report``
JSONB only ever carries the first N entries (preview cap), so memory and
JSONB size stay bounded regardless of the failure count.
"""

from __future__ import annotations

import csv
import os
import tempfile
from typing import Any

_REPORT_COLUMNS = ("row", "field", "code", "message")


class ErrorReportWriter:
    """Append-only CSV writer over a scratch file (streamed, never in memory)."""

    def __init__(self) -> None:
        fd, self._path = tempfile.mkstemp(prefix="mesh-error-report-", suffix=".csv")
        self._handle = os.fdopen(fd, "w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(_REPORT_COLUMNS)
        self._count = 0
        self._closed = False

    @property
    def path(self) -> str:
        return self._path

    @property
    def count(self) -> int:
        return self._count

    def add(self, entry: dict[str, Any]) -> None:
        """Append one ``{row, field, code, message}`` entry."""
        self._writer.writerow(
            [entry.get("row", ""), entry.get("field", ""), entry.get("code", ""), entry.get("message", "")]
        )
        self._count += 1

    def finish(self) -> str:
        """Flush + close; return the scratch path for upload (caller unlinks)."""
        if not self._closed:
            self._handle.flush()
            self._handle.close()
            self._closed = True
        return self._path

    def size_bytes(self) -> int:
        if not self._closed:
            self._handle.flush()
        return os.path.getsize(self._path)

    def cleanup(self) -> None:
        """Best-effort removal of the scratch file after upload."""
        try:
            if not self._closed:
                self._handle.close()
                self._closed = True
        finally:
            try:
                os.unlink(self._path)
            except OSError:
                pass

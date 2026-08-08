"""Daemon logging setup (runtime-executor.md §4.3 `log_level`).

Every daemon module emits records via ``logging.getLogger(...)``; until this
setup runs the default root configuration swallows everything below WARNING,
so a freshly installed daemon ran effectively silent — heartbeat failures,
cancel dispatch errors and self-heal escalations produced no output at all.

``configure_logging`` installs a structured single-line format on the root
logger so operators get one grep-able line per record regardless of which
module logged it:

    ts=<iso-utc> level=<LEVEL> logger=<name> msg='<message>'

The message is ``repr``-quoted: embedded newlines cannot forge extra log
lines (log-injection guard) and whitespace survives parsing. Exceptions keep
the standard traceback render after the line, so ``logger.exception`` loses
nothing. Output goes to stderr — stdout is reserved for machine-readable CLI
results (``version``, ``manifest hash``).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

DEFAULT_LOG_LEVEL = "INFO"


class StructuredFormatter(logging.Formatter):
    """Single-line ``ts=… level=… logger=… msg=…`` records (see module doc)."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        line = (
            f"ts={ts} level={record.levelname} "
            f"logger={record.name} msg={record.getMessage()!r}"
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str) -> None:
    """Install the structured root handler at ``level`` (idempotent).

    Raises ``ValueError`` for an unknown level — callers that already
    validated through :class:`~mesh_runtime.config.DaemonConfig` never hit
    this; the second check keeps the function safe standalone.
    """
    normalized = str(level).strip().upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(
            f"log_level must be one of {sorted(LOG_LEVELS)} (got {level!r})"
        )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(normalized)


__all__ = [
    "DEFAULT_LOG_LEVEL",
    "LOG_LEVELS",
    "StructuredFormatter",
    "configure_logging",
]

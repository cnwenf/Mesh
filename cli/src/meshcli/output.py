"""Output rendering — the stdout/stderr discipline (cli.md §3.5 / §4.1).

stdout carries RESULT DATA only: with ``--output json`` exactly ONE legal
JSON document (the envelope verbatim, or per-line jq results); with
``--output table`` a TTY-aware table (auto-degrades to plain aligned text
when piped; ``--no-header`` for scripts). Everything else — progress,
spinner, verbose traces, warnings, errors — goes to stderr, so
``mesh issue list --output json | jq`` is never polluted.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.jqeval import JqError, compile_expression

MAX_CELL_WIDTH = 60


def stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def is_tty(stream: TextIO | None = None) -> bool:
    stream = stream if stream is not None else sys.stdout
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


# --- json (the scripting contract) -------------------------------------------------


def emit_json(envelope: Any, *, out: TextIO | None = None) -> None:
    """Write exactly ONE legal JSON document to stdout (cli.md §3.5)."""
    out = out if out is not None else sys.stdout
    json.dump(envelope, out, ensure_ascii=False, indent=None)
    out.write("\n")
    out.flush()


def emit_json_lines(values: Iterable[Any], *, out: TextIO | None = None) -> None:
    """jq array results: one JSON value per line (pipe-friendly, C24)."""
    out = out if out is not None else sys.stdout
    for value in values:
        json.dump(value, out, ensure_ascii=False)
        out.write("\n")
    out.flush()


def apply_jq(envelope: Any, expression: str) -> list[Any]:
    """Evaluate --jq against the success envelope's ``data`` (C24).

    Only compatible with ``--output json``; compile/eval failures exit 3 with
    a positioned stderr message. Error envelopes never pass through jq — the
    caller raises before getting here.
    """
    try:
        program = compile_expression(expression)
    except JqError as exc:
        raise CliError(
            f"--jq: {exc}",
            exit_code=EXIT_VALIDATION,
            hint="Check the jq expression syntax (subset: .field .[] .[n] | select).",
        ) from exc
    data = envelope.get("data") if isinstance(envelope, dict) else envelope
    try:
        return program.evaluate(data)
    except JqError as exc:
        raise CliError(f"--jq evaluation failed: {exc}", exit_code=EXIT_VALIDATION) from exc


# --- table --------------------------------------------------------------------------


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("\n", " ")
    if len(text) > MAX_CELL_WIDTH:
        text = text[: MAX_CELL_WIDTH - 1] + "…"
    return text


def render_table(
    rows: list[dict],
    columns: list[str],
    *,
    no_header: bool = False,
    tty: bool | None = None,
) -> str:
    """Render rows as an aligned table; plain text when not a TTY (§4.1)."""
    tty = is_tty() if tty is None else tty
    if not rows:
        return ""
    header = [c.upper() for c in columns]
    table_rows = [[_format_cell(row.get(c)) for c in columns] for row in rows]
    widths = [
        max(len(header[i]), *(len(r[i]) for r in table_rows)) if table_rows else len(header[i])
        for i in range(len(columns))
    ]

    def join(cells: list[str]) -> str:
        if tty:
            return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
        # Non-TTY: no padding decorations — stable, grep-friendly text.
        return "\t".join(cells)

    lines: list[str] = []
    if not no_header:
        lines.append(join(header))
    lines.extend(join(r) for r in table_rows)
    return "\n".join(lines)


def emit_table(
    rows: list[dict],
    columns: list[str],
    *,
    no_header: bool = False,
    out: TextIO | None = None,
) -> None:
    out = out if out is not None else sys.stdout
    rendered = render_table(rows, columns, no_header=no_header)
    if rendered:
        out.write(rendered + "\n")
        out.flush()

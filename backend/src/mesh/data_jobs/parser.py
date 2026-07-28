"""Streaming source-file parsing (import-export.md §5 memory RED LINE).

Sources are parsed from a local scratch copy the worker streamed out of
object storage (hashing in-line); CSV is read row-by-row and JSON via an
incremental ``raw_decode`` loop — the whole file is NEVER held in memory.

Row-key allocation (R3/R4, §2.5) also lives here: the first occurrence of
a mapped ``external_ref`` claims the stable ``ref:<value>`` key; any
further occurrence (and every unmapped row) falls back to the
content-addressed ``row:<n>:<sha256>`` — deterministic across reruns of
the same (hash-verified) source.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterator
from typing import Any, TextIO

# A single JSON object larger than this is a pathology, not a row — bound
# the incremental decoder's buffer so one giant blob cannot OOM the worker.
_MAX_JSON_OBJECT_BYTES = 10 * 1024 * 1024
_READ_CHUNK = 256 * 1024


class SourceParseError(Exception):
    """The source file cannot be parsed (job-level failure: source_unparseable)."""


def iter_source_rows(path: str, format: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(row_number, raw_row)`` — 1-based data rows, streamed."""
    if format == "csv":
        yield from _iter_csv_rows(path)
    elif format == "json":
        yield from _iter_json_rows(path)
    else:  # validated long before; defensive
        raise SourceParseError(f"unsupported format: {format}")


def _iter_csv_rows(path: str) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise SourceParseError("CSV source has no header row")
            for index, raw in enumerate(reader, start=1):
                # Drop the None-keyed overflow bucket (rows longer than the
                # header) into a stable, hashable shape.
                row = {key: value for key, value in raw.items() if key is not None}
                yield index, row
    except csv.Error as exc:
        raise SourceParseError(f"CSV parse failure: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SourceParseError("source is not valid UTF-8 text") from exc


def _iter_json_rows(path: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Incrementally decode a JSON array (or concatenated objects / NDJSON)."""
    decoder = json.JSONDecoder()
    index = 0
    buffer = ""
    position = 0  # parse cursor within buffer
    started = False  # saw the opening '[' (array mode)
    array_mode: bool | None = None
    finished = False
    try:
        with open(path, encoding="utf-8-sig") as handle:
            while not finished:
                chunk = handle.read(_READ_CHUNK)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    # Trim consumed input so the buffer stays bounded.
                    if position > _READ_CHUNK:
                        buffer = buffer[position:]
                        position = 0
                    length = len(buffer)
                    while position < length and buffer[position] in " \t\r\n":
                        position += 1
                    if position >= length:
                        break  # need more input
                    char = buffer[position]
                    if array_mode is None:
                        array_mode = char == "["
                        started = True
                        if array_mode:
                            position += 1
                            continue
                    if array_mode:
                        if char == "]":
                            finished = True
                            position += 1
                            break
                        if char == ",":
                            position += 1
                            continue
                    try:
                        value, end = decoder.raw_decode(buffer, position)
                    except ValueError:
                        if length - position > _MAX_JSON_OBJECT_BYTES:
                            raise SourceParseError("JSON object exceeds the per-row size bound") from None
                        break  # incomplete object — need more input
                    position = end
                    index += 1
                    if not isinstance(value, dict):
                        raise SourceParseError("JSON source rows must be objects")
                    yield index, value
        if not started:
            raise SourceParseError("JSON source is empty")
        if array_mode:
            # A well-formed array MUST have reached its closing bracket —
            # EOF before ']' means truncated input (incl. trailing comma).
            if not finished:
                raise SourceParseError("JSON array is not terminated")
        elif buffer[position:].strip():
            # Concatenated-object mode ends cleanly between objects; any
            # leftover text is a half-decoded object.
            raise SourceParseError("JSON source is truncated")
    except SourceParseError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise SourceParseError(f"JSON parse failure: {exc}") from exc


def read_headers(path: str, format: str, *, sample_rows: int = 5) -> tuple[list[str], list[dict[str, Any]]]:
    """Header list + a few sample rows for auto-inference (§3.2), streamed."""
    headers: list[str] = []
    samples: list[dict[str, Any]] = []
    if format == "csv":
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or ())
            for index, raw in enumerate(reader):
                if index >= sample_rows:
                    break
                samples.append({k: v for k, v in raw.items() if k is not None})
    else:
        for row_number, row in _iter_json_rows(path):
            if row_number == 1:
                headers = list(row.keys())
            if row_number > sample_rows:
                break
            samples.append(row)
    return headers, samples


def canonical_row_hash(raw_row: dict[str, Any]) -> str:
    """Content-addressed digest of one source row (deterministic, §2.5)."""
    canonical = json.dumps(raw_row, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RowKeyAllocator:
    """Assigns the stable row-level idempotency key (§2.5 R3/R4).

    First occurrence of a mapped ``external_ref`` → ``ref:<value>`` (a
    natural business key, stable across reruns); duplicate refs and
    unmapped rows → ``row:<n>:<sha256>`` (content-addressed; the
    hash-verified source makes it deterministic). Returns
    ``(row_key, is_duplicate_ref)`` — duplicate refs become
    ``duplicate_within_file`` row failures so ``succeeded + failed =
    total`` holds and dry-run predictions match the run.
    """

    def __init__(self) -> None:
        self._ref_counts: dict[str, int] = {}

    def key_for(self, row_number: int, raw_row: dict[str, Any], external_ref: str | None) -> tuple[str, bool]:
        if external_ref:
            count = self._ref_counts.get(external_ref, 0) + 1
            self._ref_counts[external_ref] = count
            if count == 1:
                # Hash the ref into a fixed-length key so a pathological
                # >2.7KB external_ref cannot overflow the btree row-key index
                # (L1); the full ref is still stored in the custom field.
                ref_hash = hashlib.sha256(external_ref.encode("utf-8")).hexdigest()
                return f"ref:{ref_hash}", False
            return f"row:{row_number}:{canonical_row_hash(raw_row)}", True
        return f"row:{row_number}:{canonical_row_hash(raw_row)}", False


def hash_file(path: str) -> str:
    """Whole-file sha256 (used by the API-side source-change precheck)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_text_rows(handle: TextIO) -> Iterator[tuple[int, dict[str, Any]]]:  # pragma: no cover
    """Convenience kept for tests that parse in-memory text."""
    reader = csv.DictReader(handle)
    for index, raw in enumerate(reader, start=1):
        yield index, {k: v for k, v in raw.items() if k is not None}

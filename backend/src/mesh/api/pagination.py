"""Keyset (cursor) pagination (README §6.14 — 唯一权威).

Cursors are opaque base64url-encoded keyset positions ``(sort_value, id)``.
Group queries use the same "overall cursor" contract: a single ``next_cursor``
for the whole response, never per-group cursors.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, Select, String, Uuid, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.errors import ValidationError

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 1000

_TYPE_DATETIME = "dt"
_TYPE_DATE = "date"
_TYPE_STRING = "str"
_TYPE_INT = "int"
_TYPE_FLOAT = "float"


@dataclass(frozen=True)
class CursorPosition:
    """Decoded keyset cursor position."""

    sort_value: Any
    id: uuid.UUID


@dataclass(frozen=True)
class Page:
    """One page of results plus the opaque cursor for the next page."""

    items: Sequence[Any]
    next_cursor: str | None


def _encode_sort_value(value: Any) -> tuple[str, Any]:
    if isinstance(value, datetime):
        return _TYPE_DATETIME, value.isoformat()
    if isinstance(value, date):  # plain DATE sort keys (due_date, start_date)
        return _TYPE_DATE, value.isoformat()
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ValueError("bool is not a supported cursor sort value")
    if isinstance(value, int):
        return _TYPE_INT, value
    if isinstance(value, float):
        return _TYPE_FLOAT, value
    if isinstance(value, str):
        return _TYPE_STRING, value
    raise ValueError(f"unsupported cursor sort value type: {type(value)!r}")


def _decode_sort_value(tag: str, raw: Any) -> Any:
    if tag == _TYPE_DATETIME:
        return datetime.fromisoformat(raw)
    if tag == _TYPE_DATE:
        return date.fromisoformat(raw)
    if tag == _TYPE_STRING:
        return str(raw)
    if tag == _TYPE_INT:
        return int(raw)
    if tag == _TYPE_FLOAT:
        return float(raw)
    raise ValueError(f"unsupported cursor sort tag: {tag!r}")


def encode_cursor(sort_value: Any, row_id: uuid.UUID | int) -> str:
    """Encode a keyset position as an opaque base64url cursor."""
    tag, raw = _encode_sort_value(sort_value)
    payload = json.dumps(
        {"t": tag, "s": raw, "i": str(row_id)}, separators=(",", ":"), sort_keys=True
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> CursorPosition:
    """Decode an opaque cursor; malformed/tampered input → ``invalid_cursor``."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        sort_value = _decode_sort_value(payload["t"], payload["s"])
        row_id_raw = payload["i"]
        try:
            row_id: uuid.UUID | int = uuid.UUID(str(row_id_raw))
        except ValueError:
            row_id = int(row_id_raw)
    except Exception as exc:
        raise ValidationError(
            "invalid pagination cursor",
            details={"cursor": raw[:64]},
            code="invalid_cursor",
        ) from exc
    return CursorPosition(sort_value=sort_value, id=row_id)


def _compatible_with_column(value: Any, column: Any) -> bool | None:
    """Check a decoded cursor value against the column's SQL type.

    Returns ``False`` on a definite mismatch (a well-formed cursor aimed at a
    different endpoint — e.g. a datetime cursor on a string-sorted list — which
    would otherwise fail the keyset comparison at the DB layer and surface as
    a neutral 500), ``True`` when compatible, ``None`` when the column type is
    not one we can check (remain permissive).
    """
    column_type = getattr(column, "type", None)
    if column_type is None:
        return None
    if isinstance(column_type, DateTime):  # TIMESTAMP subclasses DateTime
        return isinstance(value, datetime)
    if isinstance(column_type, String):  # TEXT/VARCHAR subclass String
        return isinstance(value, str)
    if isinstance(column_type, Integer):  # bool is an int subclass — exclude it
        return type(value) is int
    if isinstance(column_type, Numeric):  # Float subclasses Numeric
        return isinstance(value, (int, float, Decimal)) and type(value) is not bool
    if isinstance(column_type, Uuid):  # postgresql.UUID subclasses Uuid
        return isinstance(value, uuid.UUID)
    return None


def _validate_cursor_for_columns(cursor: str, position: CursorPosition, sort_column, id_column) -> None:
    """Reject a well-formed cursor whose types do not match the keyset columns."""
    compatible_sort = _compatible_with_column(position.sort_value, sort_column)
    compatible_id = _compatible_with_column(position.id, id_column)
    if compatible_sort is False or compatible_id is False:
        raise ValidationError(
            "invalid pagination cursor",
            details={"cursor": cursor[:64]},
            code="invalid_cursor",
        )


async def paginate(
    session: AsyncSession,
    stmt: Select,
    *,
    sort_column: Any,
    id_column: Any,
    sort_value_of: Callable[[Any], Any],
    id_of: Callable[[Any], Any],
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    descending: bool = False,
) -> Page:
    """Run a keyset-paginated query.

    Orders by ``(sort_column, id_column)``; when ``cursor`` is given, restricts
    to rows strictly after the cursor position. Fetches ``limit + 1`` rows to
    decide whether a next page exists. ``sort_value_of`` / ``id_of`` extract
    the cursor fields from a result row.
    """
    if limit < 1:
        raise ValidationError("limit must be >= 1", code="invalid_limit")
    limit = min(limit, MAX_PAGE_LIMIT)

    if descending:
        ordered = stmt.order_by(sort_column.desc(), id_column.desc())
        if cursor is not None:
            position = decode_cursor(cursor)
            _validate_cursor_for_columns(cursor, position, sort_column, id_column)
            ordered = ordered.where(
                tuple_(sort_column, id_column) < (position.sort_value, position.id)
            )
    else:
        ordered = stmt.order_by(sort_column.asc(), id_column.asc())
        if cursor is not None:
            position = decode_cursor(cursor)
            _validate_cursor_for_columns(cursor, position, sort_column, id_column)
            ordered = ordered.where(
                tuple_(sort_column, id_column) > (position.sort_value, position.id)
            )

    rows: Sequence[Any] = (await session.execute(ordered.limit(limit + 1))).scalars().all()
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)

    kept = rows[:limit]
    last = kept[-1]
    next_cursor = encode_cursor(sort_value_of(last), id_of(last))
    return Page(items=kept, next_cursor=next_cursor)

"""List-query filter limits and structured-filter compiler (README §6.14).

Limits: structured ``filters`` trees allow at most ``MAX_FILTER_DEPTH`` (3)
nesting levels and ``MAX_FILTER_CONDITIONS`` (20) leaf conditions; anything
beyond returns ``400 filter_too_complex``. The actual query additionally runs
under ``statement_timeout`` (default 3s, issue.md §3.2); a timeout raises
``422 query_cost_exceeded`` so callers can narrow their conditions.

The structured grammar is intentionally small (flat comparisons joined by
and/or, one level of ``not``) — kanban.md's saved views consume the same list
query and stay within these bounds.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, not_, or_

from mesh.errors import ValidationError

MAX_FILTER_DEPTH = 3
MAX_FILTER_CONDITIONS = 20
LIST_STATEMENT_TIMEOUT_MS = 3000

_FILTER_TOO_COMPLEX = "filter is too complex"

_ALLOWED_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null"})


class FilterTooComplexError(ValidationError):
    """400 ``filter_too_complex`` (README §6.14)."""

    code = "filter_too_complex"
    message = _FILTER_TOO_COMPLEX


def validate_flat_condition_count(count: int) -> None:
    """Flat query-parameter filters count as conditions too (§6.14)."""
    if count > MAX_FILTER_CONDITIONS:
        raise FilterTooComplexError(
            _FILTER_TOO_COMPLEX,
            details={"conditions": count, "max": MAX_FILTER_CONDITIONS},
        )


def _walk(node: Any, depth: int, counter: list[int]) -> None:
    if depth > MAX_FILTER_DEPTH:
        raise FilterTooComplexError(
            _FILTER_TOO_COMPLEX,
            details={"depth": depth, "max_depth": MAX_FILTER_DEPTH},
        )
    if not isinstance(node, dict):
        raise ValidationError("invalid filter node", details={"node": str(node)[:64]})
    if "and" in node or "or" in node:
        key = "and" if "and" in node else "or"
        children = node[key]
        if not isinstance(children, list):
            raise ValidationError("invalid filter combinator", details={"key": key})
        for child in children:
            _walk(child, depth + 1, counter)
        return
    if "not" in node:
        _walk(node["not"], depth + 1, counter)
        return
    counter[0] += 1
    if counter[0] > MAX_FILTER_CONDITIONS:
        raise FilterTooComplexError(
            _FILTER_TOO_COMPLEX,
            details={"conditions": counter[0], "max": MAX_FILTER_CONDITIONS},
        )
    field = node.get("field")
    op = node.get("op")
    if not isinstance(field, str) or not isinstance(op, str) or op not in _ALLOWED_OPS:
        raise ValidationError(
            "invalid filter condition",
            details={"field": str(field)[:32], "op": str(op)[:16]},
        )


def validate_filter_tree(filters: Any) -> None:
    """Depth/count-validate a structured filter tree (§6.14)."""
    counter = [0]
    _walk(filters, 1, counter)


def compile_filter_tree(
    filters: Any,
    columns: dict[str, Any],
    *,
    value_coercers: dict[str, Any] | None = None,
) -> Any:
    """Compile a validated filter tree into a SQLAlchemy expression.

    ``columns`` maps allowed field names to instrumented attributes; unknown
    fields are a validation error. ``value_coercers`` optionally maps field
    names to callables that parse incoming values (dates, UUIDs, …).
    """
    validate_filter_tree(filters)
    coercers = value_coercers or {}

    def _coerce(field: str, value: Any) -> Any:
        coercer = coercers.get(field)
        if coercer is None:
            return value
        try:
            return coercer(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "invalid filter value", details={"field": field}
            ) from exc

    def _condition(node: dict) -> Any:
        if "and" in node:
            return and_(*[_build(child) for child in node["and"]])
        if "or" in node:
            return or_(*[_build(child) for child in node["or"]])
        if "not" in node:
            return not_(_build(node["not"]))
        field, op = node["field"], node["op"]
        if field not in columns:
            raise ValidationError(
                "unknown filter field", details={"field": str(field)[:32]}
            )
        column = columns[field]
        if op == "is_null":
            return column.is_(None) if node.get("value", True) else column.is_not(None)
        if op == "in":
            raw = node.get("value")
            if not isinstance(raw, list) or len(raw) > MAX_FILTER_CONDITIONS:
                raise ValidationError(
                    "invalid filter value", details={"field": field, "op": "in"}
                )
            return column.in_([_coerce(field, item) for item in raw])
        value = _coerce(field, node.get("value"))
        if op == "eq":
            return column == value
        if op == "ne":
            return column != value
        if op == "gt":
            return column > value
        if op == "gte":
            return column >= value
        if op == "lt":
            return column < value
        return column <= value  # lte

    def _build(node: Any) -> Any:
        if not isinstance(node, dict):
            raise ValidationError("invalid filter node", details={"node": str(node)[:64]})
        return _condition(node)

    return _build(filters)


def coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = [
    "FilterTooComplexError",
    "LIST_STATEMENT_TIMEOUT_MS",
    "MAX_FILTER_CONDITIONS",
    "MAX_FILTER_DEPTH",
    "coerce_date",
    "compile_filter_tree",
    "validate_filter_tree",
    "validate_flat_condition_count",
]

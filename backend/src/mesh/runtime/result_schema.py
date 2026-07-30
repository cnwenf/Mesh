"""Server-side result schema v1 validation (runtime-executor.md §3.9).

The terminal attempt ``result`` is a frozen, versioned contract: decimal-string
money, non-negative integer tokens/turns, a fixed termination vocabulary. The
daemon BUILDS conforming results (``mesh_runtime/result.py``) and its docstring
states "The server 422s anything else" — this module is that server-side
enforcement, which had never been implemented (a ``cost_usd:"nan"` slipped past
``float()`` into ``Numeric(16,6)`` and 500'd; non-conforming dicts were still
stamped with ``result_schema_version``).

This is a deliberate MIRROR of the daemon validator, not an import of daemon
code: the backend must not depend on the daemon package, and the two sides are
kept honest by independent implementations of the same frozen rules. The server
is lenient only where the contract says so — ``artifacts`` / ``redaction`` are
optional (but must be objects when present); every other section is mandatory.
"""

from __future__ import annotations

from typing import Any

from mesh.errors import BusinessRuleError

# §2.6 P0 / §3.9: the only structured result schema the server accepts.
RESULT_SCHEMA_VERSION = 1

# Frozen termination vocabulary — mirrors daemon result.py TERMINATIONS.
TERMINATIONS = frozenset(
    {
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "budget_exceeded",
        "sandbox_violation",
        "lease_lost",
    }
)

# A decimal-string amount: digits with at most one '.' (no sign, no exponent,
# no "nan"/"inf"). Mirrors daemon result.py _is_decimal_string.
_DECIMAL_CHARS = frozenset("0123456789.")

# usage counter fields that must be genuine non-negative integers.
_COUNT_FIELDS = (
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens",
    "turns",
)


def _fail(field: str, message: str) -> None:
    """Raise the 422 (BusinessRuleError) naming the offending field."""
    raise BusinessRuleError(
        "result failed schema v1 validation",
        code="invalid_result_schema",
        details={"field": field, "error": message},
    )


def _is_count(value: object) -> bool:
    """A genuine non-negative integer. ``bool`` is an ``int`` subclass in
    Python, so it is rejected explicitly (a count of ``True`` is nonsense)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_decimal_string(value: object) -> bool:
    """A finite, non-negative decimal amount as a string (never a float)."""
    if not isinstance(value, str) or not value:
        return False
    if value.count(".") > 1:
        return False
    return all(ch in _DECIMAL_CHARS for ch in value)


def _require_dict(doc: dict, field: str) -> dict:
    value = doc.get(field)
    if not isinstance(value, dict):
        _fail(field, "must be an object")
    return value  # type: ignore[return-value]


def validate_result_schema(result: dict[str, Any]) -> None:
    """Strict, fail-closed validation of a terminal result doc (schema v1).

    Raises :class:`BusinessRuleError` (HTTP 422, code ``invalid_result_schema``,
    ``details.field`` pointing at the offending field) on the FIRST non-conforming
    field. Rules mirror daemon ``mesh_runtime/result.py`` exactly, except
    ``artifacts`` / ``redaction`` are optional server-side.
    """
    if not isinstance(result, dict):
        _fail("result", "must be a JSON object")

    version = result.get("schema_version")
    if isinstance(version, bool) or version != RESULT_SCHEMA_VERSION:
        _fail("schema_version", f"must be the integer {RESULT_SCHEMA_VERSION}")

    provider = _require_dict(result, "provider")
    for key in ("name", "version", "model"):
        if not isinstance(provider.get(key), str):
            _fail(f"provider.{key}", "must be a string")
    session_id = provider.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        _fail("provider.session_id", "must be a string or null")

    usage = _require_dict(result, "usage")
    for key in _COUNT_FIELDS:
        if not _is_count(usage.get(key)):
            _fail(f"usage.{key}", "must be a non-negative integer")
    if not _is_count(usage.get("total_tokens")):
        _fail("usage.total_tokens", "must be a non-negative integer")
    component_sum = (
        usage["input_tokens"]
        + usage["cache_creation_tokens"]
        + usage["cache_read_tokens"]
        + usage["output_tokens"]
    )
    if usage["total_tokens"] != component_sum:
        _fail(
            "usage.total_tokens",
            "must equal the sum of the four token components",
        )
    if not _is_decimal_string(usage.get("cost_usd")):
        _fail("usage.cost_usd", "must be a non-negative decimal string")

    outcome = _require_dict(result, "outcome")
    if not _is_count(outcome.get("exit_code")):
        _fail("outcome.exit_code", "must be a non-negative integer")
    if outcome.get("termination") not in TERMINATIONS:
        _fail(
            "outcome.termination",
            "must be one of the frozen termination vocabulary",
        )
    if not isinstance(outcome.get("summary"), str):
        _fail("outcome.summary", "must be a string")

    # Optional sections: lenient on inner fields, but objects when present.
    for field in ("artifacts", "redaction"):
        if field in result and not isinstance(result.get(field), dict):
            _fail(field, "must be an object when present")

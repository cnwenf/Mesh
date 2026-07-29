"""Terminal result schema v1 (runtime-executor.md §3.9).

The attempt transition ``result`` payload is versioned and strict:
decimal-string money, non-negative integer tokens/turns, fixed termination
vocabulary. The server 422s anything else; building it wrong locally is a
programming error, so both build and validate raise ``ValueError``.
"""

from __future__ import annotations

from dataclasses import dataclass

RESULT_SCHEMA_VERSION = 1

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

_DECIMAL_CHARS = frozenset("0123456789.")

_COUNT_FIELDS = (
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens",
    "turns",
)


def _is_count(value: object) -> bool:
    """A frozen-schema count: a genuine non-negative integer. ``bool`` is a
    subclass of ``int`` in Python, so it is rejected explicitly."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    turns: int
    cost_usd: str  # decimal string — never float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )

    def _validate(self) -> None:
        for field_name in _COUNT_FIELDS:
            if not _is_count(getattr(self, field_name)):
                raise ValueError(f"usage.{field_name} must be a non-negative integer")
        if not isinstance(self.cost_usd, str) or not _is_decimal_string(self.cost_usd):
            raise ValueError("usage.cost_usd must be a decimal string")


def _is_decimal_string(value: str) -> bool:
    if not value:
        return False
    if value.count(".") > 1:
        return False
    return all(ch in _DECIMAL_CHARS for ch in value)


def build_result(
    *,
    provider: str,
    version: str,
    model: str,
    session_id: str | None,
    usage: Usage,
    exit_code: int,
    summary: str,
    termination: str,
    checkout_id: str | None,
    diff_ref: str | None,
    rule_version: str,
    hit_count: int,
) -> dict:
    usage._validate()
    if termination not in TERMINATIONS:
        raise ValueError(f"unknown termination {termination!r}; allowed: {sorted(TERMINATIONS)}")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "provider": {
            "name": provider,
            "version": version,
            "model": model,
            "session_id": session_id,
        },
        "usage": {
            "input_tokens": usage.input_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "turns": usage.turns,
            "cost_usd": usage.cost_usd,
        },
        "outcome": {
            "exit_code": exit_code,
            "summary": summary,
            "termination": termination,
        },
        "artifacts": {
            "checkout_id": checkout_id,
            "diff_ref": diff_ref,
        },
        "redaction": {
            "rule_version": rule_version,
            "hit_count": hit_count,
        },
    }


_REQUIRED_SECTIONS = ("provider", "usage", "outcome", "artifacts", "redaction")


def validate_result(doc: dict) -> None:
    """Strict validation of an incoming/outgoing result doc (fail-closed).

    Enforces the frozen schema end to end: counts are genuine non-negative
    integers (never ``bool``), ``total_tokens`` equals the sum of its four
    components, ``exit_code`` is an integer, and ``termination`` is in the
    frozen vocabulary. ``build_result`` produces conforming docs; this validator
    must reject anything else rather than rubber-stamp it.
    """
    if not isinstance(doc, dict):
        raise ValueError("result must be a JSON object")
    if doc.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError(f"unsupported result schema_version (want {RESULT_SCHEMA_VERSION})")
    for section in _REQUIRED_SECTIONS:
        if not isinstance(doc.get(section), dict):
            raise ValueError(f"result missing section {section!r}")
    usage = doc["usage"]
    for key in _COUNT_FIELDS:
        if not _is_count(usage.get(key)):
            raise ValueError(f"result usage.{key} must be a non-negative integer")
    if not _is_count(usage.get("total_tokens")):
        raise ValueError("result usage.total_tokens must be a non-negative integer")
    component_sum = (
        usage["input_tokens"]
        + usage["cache_creation_tokens"]
        + usage["cache_read_tokens"]
        + usage["output_tokens"]
    )
    if usage["total_tokens"] != component_sum:
        raise ValueError("result usage.total_tokens must equal the sum of token components")
    if not _is_decimal_string(usage.get("cost_usd", "")):
        raise ValueError("result usage.cost_usd must be a decimal string")
    outcome = doc["outcome"]
    if not _is_count(outcome.get("exit_code")):
        raise ValueError("result outcome.exit_code must be a non-negative integer")
    termination = outcome.get("termination")
    if termination not in TERMINATIONS:
        raise ValueError(f"result outcome.termination {termination!r} not in frozen vocabulary")

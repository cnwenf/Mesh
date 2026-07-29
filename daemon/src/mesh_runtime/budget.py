"""S-07 daemon-layer budget enforcement (runtime-executor.md §3.5).

Budgets are frozen at enqueue (§2.1); three layers enforce them at once:
server (capacity ledger), provider (``--max-budget-usd`` in the fixed argv,
gated on the manifest's ``hard_limits`` capability) and daemon — this module:
wall/idle timeouts plus live truncation on provider-reported usage. Reported
usage is audit material, not the only enforcement point; the server cross-
checks it against the workspace ledger and isolates runtimes that drift.

Every limit is the STRICTER of the frozen snapshot and the daemon's local
caps (§4.3: 取二者更严格值). Invalid values remove that limit rather than
relaxing it; ``require_usd`` makes a missing hard USD limit a fatal,
fail-closed error for real providers (§3.5 provider layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from mesh_runtime.errors import DaemonError
from mesh_runtime.providers.base import UsageObserved
from mesh_runtime.timeutil import Clock, SystemClock


class BudgetError(DaemonError):
    """A real provider cannot run without a provable hard budget (§3.5)."""


@dataclass(frozen=True)
class BudgetViolation:
    """Typed truncation reason — becomes failure_reason / termination."""

    kind: str  # usd | tokens | turns | wall | idle
    detail: str  # safe for logs: limits and counters only, never secrets

    @property
    def termination(self) -> str:
        return "timeout" if self.kind in ("wall", "idle") else "budget_exceeded"


@dataclass(frozen=True)
class DaemonCaps:
    """Operator-installed local ceilings. The snapshot may be stricter, never
    looser, than these (§4.3)."""

    usd: Decimal | None = None
    max_tokens: int | None = None
    max_turns: int | None = None
    wall_seconds: float | None = None
    idle_seconds: float | None = None


@dataclass(frozen=True)
class BudgetLimits:
    """Effective limits after the stricter-of-two merge."""

    usd: Decimal | None = None
    max_tokens: int | None = None
    max_turns: int | None = None
    wall_seconds: float | None = None
    idle_seconds: float | None = None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict,
        caps: DaemonCaps,
        *,
        require_usd: bool = False,
    ) -> BudgetLimits:
        """Read the server-frozen budget (agent/snapshot.py §2.1 field names:
        ``max_cost_usd`` / ``max_tokens`` / ``max_turns`` /
        ``max_wall_time_seconds`` / ``max_idle_time_seconds``)."""
        raw = snapshot.get("budget")
        budget = raw if isinstance(raw, dict) else {}
        limits = cls(
            usd=_min_decimal(_parse_usd(budget.get("max_cost_usd")), caps.usd),
            max_tokens=_min_int(_parse_positive_int(budget.get("max_tokens")), caps.max_tokens),
            max_turns=_min_int(_parse_positive_int(budget.get("max_turns")), caps.max_turns),
            wall_seconds=_min_float(
                _parse_positive_float(budget.get("max_wall_time_seconds")), caps.wall_seconds
            ),
            idle_seconds=_min_float(
                _parse_positive_float(budget.get("max_idle_time_seconds")), caps.idle_seconds
            ),
        )
        if require_usd and limits.usd is None:
            raise BudgetError(
                "frozen snapshot carries no hard USD budget — refusing to run a real provider"
            )
        return limits


class BudgetGuard:
    """Live truncation decisions for one attempt. Single-writer: the provider
    event loop."""

    def __init__(self, limits: BudgetLimits, *, clock: Clock | None = None) -> None:
        self._limits = limits
        self._clock = clock or SystemClock()
        self._started_at: float | None = None
        self._last_activity: float | None = None

    def mark_started(self) -> None:
        now = self._clock.now()
        self._started_at = now
        self._last_activity = now

    def mark_activity(self) -> None:
        self._last_activity = self._clock.now()

    def check_usage(self, usage: UsageObserved) -> BudgetViolation | None:
        limits = self._limits
        if limits.usd is not None:
            cost = _parse_usd(usage.cost_usd)
            if cost is None:
                return BudgetViolation("usd", "provider reported an unparseable cost")
            if cost > limits.usd:
                return BudgetViolation("usd", f"cost {cost} exceeds frozen budget {limits.usd}")
        if limits.max_tokens is not None and usage.total_tokens > limits.max_tokens:
            return BudgetViolation(
                "tokens", f"{usage.total_tokens} tokens exceed frozen limit {limits.max_tokens}"
            )
        if limits.max_turns is not None and usage.turns > limits.max_turns:
            return BudgetViolation(
                "turns", f"{usage.turns} turns exceed frozen limit {limits.max_turns}"
            )
        return None

    def check_time(self) -> BudgetViolation | None:
        limits = self._limits
        now = self._clock.now()
        if limits.wall_seconds is not None and self._started_at is not None:
            elapsed = now - self._started_at
            if elapsed > limits.wall_seconds:
                return BudgetViolation(
                    "wall", f"wall time {elapsed:.0f}s exceeds frozen limit {limits.wall_seconds:.0f}s"
                )
        if limits.idle_seconds is not None and self._last_activity is not None:
            idle = now - self._last_activity
            if idle > limits.idle_seconds:
                return BudgetViolation(
                    "idle", f"idle {idle:.0f}s exceeds frozen limit {limits.idle_seconds:.0f}s"
                )
        return None


def _parse_usd(value: object) -> Decimal | None:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite() or decimal < 0:
        return None
    return decimal


def _parse_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _min_decimal(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _min_int(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _min_float(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)

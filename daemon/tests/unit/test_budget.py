"""S-07 daemon-layer budget guard tests (runtime-executor.md §3.5).

Frozen snapshot limits vs daemon caps: the STRICTER of the two wins (§4.3).
Violations are typed (usd/tokens/turns/wall/idle) and fail-closed: a real
provider may not run without a provable hard USD limit (§3.5 provider layer).
"""

from decimal import Decimal

import pytest

from mesh_runtime.budget import (
    BudgetError,
    BudgetGuard,
    BudgetLimits,
    DaemonCaps,
)
from mesh_runtime.providers.base import UsageObserved
from mesh_runtime.timeutil import FakeClock

UNBOUNDED = DaemonCaps()  # all-None caps impose nothing


class TestLimitsFromSnapshot:
    def test_parses_frozen_budget(self):
        limits = BudgetLimits.from_snapshot(
            {"budget": {"max_cost_usd": "1.50", "max_tokens": 100_000, "max_turns": 12,
                        "max_wall_time_seconds": 600, "max_idle_time_seconds": 90}},
            UNBOUNDED,
        )
        assert limits.usd == Decimal("1.50")
        assert limits.max_tokens == 100_000
        assert limits.max_turns == 12
        assert limits.wall_seconds == 600.0
        assert limits.idle_seconds == 90.0

    def test_stricter_of_snapshot_and_caps_wins(self):
        caps = DaemonCaps(usd=Decimal("1.00"), max_tokens=50_000, max_turns=20,
                          wall_seconds=300.0, idle_seconds=120.0)
        limits = BudgetLimits.from_snapshot(
            {"budget": {"max_cost_usd": "5.00", "max_tokens": 100_000, "max_turns": 12,
                        "max_wall_time_seconds": 600, "max_idle_time_seconds": 90}},
            caps,
        )
        assert limits.usd == Decimal("1.00")  # cap stricter
        assert limits.max_tokens == 50_000  # cap stricter
        assert limits.max_turns == 12  # snapshot stricter
        assert limits.wall_seconds == 300.0  # cap stricter
        assert limits.idle_seconds == 90.0  # snapshot stricter

    def test_numeric_usd_coerced_to_decimal(self):
        limits = BudgetLimits.from_snapshot({"budget": {"max_cost_usd": 2}}, UNBOUNDED)
        assert limits.usd == Decimal("2")

    def test_invalid_budget_values_ignored_not_relaxed(self):
        limits = BudgetLimits.from_snapshot(
            {"budget": {"max_cost_usd": "not-a-number", "max_tokens": -5, "max_turns": "x"}},
            UNBOUNDED,
        )
        assert limits.usd is None
        assert limits.max_tokens is None
        assert limits.max_turns is None

    def test_missing_budget_section_yields_unbounded(self):
        limits = BudgetLimits.from_snapshot({}, UNBOUNDED)
        assert limits.usd is None
        assert limits.max_turns is None

    def test_require_usd_raises_without_hard_limit(self):
        with pytest.raises(BudgetError):
            BudgetLimits.from_snapshot({}, UNBOUNDED, require_usd=True)

    def test_require_usd_passes_with_limit(self):
        limits = BudgetLimits.from_snapshot(
            {"budget": {"max_cost_usd": "0.50"}}, UNBOUNDED, require_usd=True
        )
        assert limits.usd == Decimal("0.50")

    def test_negative_usd_rejected_even_when_required(self):
        with pytest.raises(BudgetError):
            BudgetLimits.from_snapshot(
                {"budget": {"max_cost_usd": "-1"}}, UNBOUNDED, require_usd=True
            )


class TestGuardUsage:
    def _guard(self, **limits) -> BudgetGuard:
        return BudgetGuard(BudgetLimits(**limits))

    def test_no_violation_under_limits(self):
        guard = self._guard(usd=Decimal("1.0"), max_tokens=1000, max_turns=5)
        usage = UsageObserved(input_tokens=100, output_tokens=50, cost_usd="0.500000", turns=2)
        assert guard.check_usage(usage) is None

    def test_usd_violation(self):
        guard = self._guard(usd=Decimal("1.0"))
        usage = UsageObserved(input_tokens=1, output_tokens=1, cost_usd="1.000001")
        violation = guard.check_usage(usage)
        assert violation is not None
        assert violation.kind == "usd"

    def test_usd_exact_boundary_is_not_a_violation(self):
        guard = self._guard(usd=Decimal("1.0"))
        usage = UsageObserved(input_tokens=1, output_tokens=1, cost_usd="1.000000")
        assert guard.check_usage(usage) is None

    def test_token_violation_uses_total_tokens(self):
        guard = self._guard(max_tokens=100)
        usage = UsageObserved(
            input_tokens=40, output_tokens=30,
            cache_read_tokens=20, cache_creation_tokens=20, cost_usd="0.000000",
        )
        assert usage.total_tokens == 110
        violation = guard.check_usage(usage)
        assert violation is not None
        assert violation.kind == "tokens"

    def test_turn_violation(self):
        guard = self._guard(max_turns=3)
        usage = UsageObserved(input_tokens=1, output_tokens=1, cost_usd="0.000000", turns=4)
        violation = guard.check_usage(usage)
        assert violation is not None
        assert violation.kind == "turns"

    def test_unparseable_cost_fails_closed(self):
        guard = self._guard(usd=Decimal("1.0"))
        usage = UsageObserved(input_tokens=1, output_tokens=1, cost_usd="garbage")
        violation = guard.check_usage(usage)
        assert violation is not None
        assert violation.kind == "usd"


class TestGuardTime:
    def test_wall_timeout(self):
        clock = FakeClock()
        guard = BudgetGuard(BudgetLimits(wall_seconds=60.0), clock=clock)
        guard.mark_started()
        assert guard.check_time() is None
        clock.advance(61.0)
        violation = guard.check_time()
        assert violation is not None
        assert violation.kind == "wall"

    def test_idle_timeout_reset_by_activity(self):
        clock = FakeClock()
        guard = BudgetGuard(BudgetLimits(idle_seconds=30.0), clock=clock)
        guard.mark_started()
        clock.advance(25.0)
        guard.mark_activity()
        clock.advance(25.0)
        assert guard.check_time() is None  # 25s since last activity
        clock.advance(6.0)
        violation = guard.check_time()
        assert violation is not None
        assert violation.kind == "idle"

    def test_no_time_limits_no_violation(self):
        clock = FakeClock()
        guard = BudgetGuard(BudgetLimits(), clock=clock)
        guard.mark_started()
        clock.advance(10_000.0)
        assert guard.check_time() is None

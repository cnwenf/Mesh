"""Injectable time: every loop in the daemon sleeps through a ``Clock``.

Production uses :class:`SystemClock`; tests use :class:`FakeClock`, which makes
all scheduling logic deterministic — no real waits anywhere in the test suite
(global constraint: fake clock, no real sleeps).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Monotonic clock + wall clock + sleep in one injectable seam."""

    def now(self) -> float:
        """Monotonic seconds (for intervals/deadlines)."""
        ...

    def utcnow(self) -> datetime:
        """Wall clock (for timestamps sent to the server)."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real time. The only clock used outside tests."""

    def now(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class FakeClock:
    """Deterministic clock: time only moves when told to.

    ``sleep`` records the requested delay and advances the clock by exactly
    that amount, so ``await clock.sleep(x)`` never blocks a test.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._monotonic = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._monotonic

    def utcnow(self) -> datetime:
        return datetime.fromtimestamp(self._monotonic, tz=UTC)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds > 0:
            self._monotonic += seconds
        # Yield control exactly like a real sleep would. Without this, a loop
        # driven by FakeClock never suspends and starves every other task —
        # shutdown events and task cancellations would never be delivered.
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot advance a FakeClock backwards")
        self._monotonic += seconds

    @property
    def total_sleep(self) -> float:
        return sum(s for s in self.sleeps if s > 0)


def full_jitter(base: float, cap: float, attempt: int, rand: Callable[[], float]) -> float:
    """AWS-style full-jitter exponential backoff: uniform in ``[0, min(cap, base*2^attempt)]``.

    ``rand`` is an injectable source of ``[0, 1)`` floats (``random.random``
    in production, a fixed lambda in tests) so delays are assertable.
    """
    if base <= 0 or cap <= 0:
        raise ValueError("backoff base and cap must be positive")
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    ceiling = min(cap, base * (2**attempt))
    return rand() * ceiling

"""Named backoff policies (runtime-executor.md §3.1).

| scenario            | base | cap | reset               |
|---------------------|------|-----|---------------------|
| 204 empty queue     | 1s   | 15s | any successful claim|
| network / 5xx       | 2s   | 60s | any 2xx             |
| 429                 | obey Retry-After       | next non-429        |

All delays are full-jitter exponential so a fleet of runtimes does not
synchronise on the server.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mesh_runtime.timeutil import full_jitter


@dataclass(frozen=True)
class BackoffPolicy:
    base: float
    cap: float

    def delay(self, attempt: int, rand: Callable[[], float]) -> float:
        """Delay for the Nth consecutive failure (0-based), full-jitter."""
        return full_jitter(self.base, self.cap, attempt, rand)


#: 204 — queue empty / no match. Starts at 1s, capped at 15s (spec §3.1).
EMPTY_QUEUE = BackoffPolicy(base=1.0, cap=15.0)

#: Network errors and 5xx. Starts at 2s, capped at 60s (spec §3.1).
NETWORK = BackoffPolicy(base=2.0, cap=60.0)

#: Heartbeat / renew transient failures: 1s, 2s, 4s, 8s, capped at 15s
#: (design §5.2 — heartbeat keeps its own cadence, never claim backoff).
KEEPALIVE = BackoffPolicy(base=1.0, cap=15.0)

#: 429 fallback when the server omitted Retry-After.
RATE_LIMITED_FALLBACK_SECONDS = 5.0

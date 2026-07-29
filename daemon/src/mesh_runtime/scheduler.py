"""ClaimScheduler — serial slot-filling claim loop (spec §3.1 / design §6.1).

A local semaphore bounds in-flight attempts; the server's locked runtime row
remains the capacity authority. Backoff follows the frozen table: 204 empty
queue → EMPTY_QUEUE (1s→15s full jitter), 5xx/network → NETWORK (2s→60s),
429 → obey Retry-After, 401 → stop claiming entirely. Successful claims reset
the empty-queue and network counters.

The per-iteration decision is factored into :meth:`ClaimScheduler.step` so it
is unit-testable without driving the infinite :meth:`run` loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from mesh_runtime.api import ClaimResponse, RuntimeApiClient
from mesh_runtime.backoff import EMPTY_QUEUE, NETWORK, capped_retry_after
from mesh_runtime.errors import FatalAuthError, RateLimitedError, ServerError
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger("mesh_runtime.scheduler")

OnClaimed = Callable[[ClaimResponse], Awaitable[None]]
OnAttemptError = Callable[[ClaimResponse, BaseException], None]

_CAPACITY_POLL_SECONDS = 0.05


class ClaimScheduler:
    def __init__(
        self,
        api: RuntimeApiClient,
        runtime_id: str,
        *,
        max_concurrent: int,
        clock: Clock | None = None,
        on_claimed: OnClaimed,
        on_attempt_error: OnAttemptError | None = None,
        rand: Callable[[], float] | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._api = api
        self._runtime_id = runtime_id
        self._max_concurrent = max_concurrent
        self._clock = clock or SystemClock()
        self._on_claimed = on_claimed
        self._on_attempt_error = on_attempt_error
        self._rand = rand or random.random
        self._inflight = 0
        self._empty_attempt = 0
        self._net_attempt = 0
        self._stop = asyncio.Event()
        # Strong references to in-flight attempt tasks. The event loop keeps
        # only weak references to tasks, so an unreferenced attempt awaiting a
        # long provider run could be garbage-collected mid-flight — its
        # ``finally`` would never run and ``_inflight`` would wedge, silently
        # stopping all claiming. We hold each task here until it completes.
        self._tasks: set[asyncio.Task] = set()
        self.fatal: FatalAuthError | None = None

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def tasks(self) -> set[asyncio.Task]:
        """In-flight attempt tasks (strong references; read-only view)."""
        return self._tasks

    def request_stop(self) -> None:
        self._stop.set()

    async def step(self) -> tuple[str, float]:
        """One scheduling decision. Returns ``(outcome, sleep_seconds)`` where
        outcome ∈ {claimed, empty, rate_limited, server_error, fatal,
        at_capacity}."""
        if self._inflight >= self._max_concurrent:
            return "at_capacity", _CAPACITY_POLL_SECONDS
        try:
            claim = await self._api.claim(self._runtime_id)
        except FatalAuthError as exc:
            self.fatal = exc  # stop ALL new claims (§3.1)
            return "fatal", 0.0
        except RateLimitedError as exc:
            delay = capped_retry_after(exc.retry_after)
            return "rate_limited", delay
        except ServerError:
            delay = NETWORK.delay(self._net_attempt, self._rand)
            self._net_attempt += 1
            return "server_error", delay
        if claim is None:
            delay = EMPTY_QUEUE.delay(self._empty_attempt, self._rand)
            self._empty_attempt += 1
            return "empty", delay
        # Success resets BOTH counters (§3.1).
        self._empty_attempt = 0
        self._net_attempt = 0
        self._inflight += 1
        task = asyncio.create_task(self._run_attempt(claim))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "claimed", 0.0

    async def run(self, shutdown: asyncio.Event) -> None:
        while not self._stop.is_set() and not shutdown.is_set() and self.fatal is None:
            outcome, delay = await self.step()
            if outcome == "fatal":
                break
            if delay > 0:
                await self._clock.sleep(delay)

    async def _run_attempt(self, claim: ClaimResponse) -> None:
        try:
            await self._on_claimed(claim)
        except asyncio.CancelledError:
            raise  # shutdown drain cancels in-flight attempts — propagate
        except BaseException as exc:  # noqa: BLE001 — a claim must never vanish silently
            # Surface the failure to diagnostics/audit. Without this the
            # exception would be "never retrieved" and the dropped claim would
            # leave no trace; the server's lease/reaper remains the authority.
            self._report_attempt_error(claim, exc)
        finally:
            self._inflight -= 1

    def _report_attempt_error(self, claim: ClaimResponse, exc: BaseException) -> None:
        attempt_id = getattr(claim, "attempt_id", "<unknown>")
        logger.error("attempt %s failed in on_claimed: %r", attempt_id, exc, exc_info=exc)
        if self._on_attempt_error is not None:
            self._on_attempt_error(claim, exc)

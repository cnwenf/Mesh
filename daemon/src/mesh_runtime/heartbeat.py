"""HeartbeatLoop — independent keepalive + downlink command channel (spec §3.1).

Runs on the server-returned interval with ±10% jitter, never blocked by claim
or provider work. Downlink ``cancel_execution`` commands are dispatched
idempotently (they may repeat). Transient failures back off on the KEEPALIVE
policy; a 401 is fatal.

The single-beat logic is factored into :meth:`HeartbeatLoop.beat_once` for
deterministic unit tests.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from mesh_runtime.api import HeartbeatResponse, RuntimeApiClient
from mesh_runtime.backoff import KEEPALIVE, capped_retry_after
from mesh_runtime.errors import FatalAuthError, RateLimitedError, ServerError
from mesh_runtime.timeutil import Clock, SystemClock

OnCancel = Callable[[str, float], Awaitable[None]]
InflightSource = Callable[[], list[str]]


class HeartbeatLoop:
    def __init__(
        self,
        api: RuntimeApiClient,
        runtime_id: str,
        *,
        interval_seconds: float,
        clock: Clock | None = None,
        inventory=None,
        on_cancel: OnCancel | None = None,
        inflight_source: InflightSource | None = None,
        rand: Callable[[], float] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be > 0")
        self._api = api
        self._runtime_id = runtime_id
        self._interval = interval_seconds
        self._clock = clock or SystemClock()
        self._inventory = inventory
        self._on_cancel = on_cancel
        self._inflight_source = inflight_source or (lambda: [])
        self._rand = rand or random.random
        self._stop = asyncio.Event()
        self.fatal: FatalAuthError | None = None
        self.beats = 0

    def request_stop(self) -> None:
        self._stop.set()

    def jittered_interval(self) -> float:
        # ±10% jitter so a fleet does not synchronise (§3.1 / design §5.2).
        jitter = (self._rand() * 2.0 - 1.0) * 0.10
        return self._interval * (1.0 + jitter)

    def _health(self) -> str:
        if self._inventory is not None and not self._inventory.healthy():
            return "degraded"
        return "healthy"

    def _metrics(self) -> dict:
        if self._inventory is None:
            return {}
        return {"inventory_hash": self._inventory.inventory_hash()}

    async def beat_once(self) -> tuple[str, float]:
        """Send one heartbeat and dispatch downlink commands.

        Returns ``(outcome, sleep_seconds)`` where outcome ∈
        {ok, fatal, rate_limited, server_error}. On ``ok`` the caller sleeps
        the jittered interval; on a transient failure it sleeps ``sleep_seconds``.
        """
        try:
            resp: HeartbeatResponse = await self._api.heartbeat(
                self._runtime_id,
                current_load=len(self._inflight_source()),
                health=self._health(),
                metrics=self._metrics(),
                inflight=self._inflight_source(),
            )
        except FatalAuthError as exc:
            self.fatal = exc
            return "fatal", 0.0
        except RateLimitedError as exc:
            delay = capped_retry_after(exc.retry_after)
            return "rate_limited", delay
        except ServerError:
            return "server_error", KEEPALIVE.delay(self.beats, self._rand)
        self.beats += 1
        await self._dispatch(resp.cancel_commands())
        return "ok", self.jittered_interval()

    async def run(self, shutdown: asyncio.Event) -> None:
        while not self._stop.is_set() and not shutdown.is_set() and self.fatal is None:
            outcome, delay = await self.beat_once()
            if outcome == "fatal":
                break
            if delay > 0:
                await self._clock.sleep(delay)

    async def _dispatch(self, commands) -> None:
        if self._on_cancel is None:
            return
        for cmd in commands:
            await self._on_cancel(cmd.attempt_id, cmd.grace_seconds)

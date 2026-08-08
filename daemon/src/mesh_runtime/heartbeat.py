"""HeartbeatLoop — independent keepalive + downlink command channel (spec §3.1).

Runs on the server-returned interval with ±10% jitter, never blocked by claim
or provider work. Downlink ``cancel_execution`` commands are dispatched
idempotently (they may repeat). Transient failures back off on the KEEPALIVE
policy; a 401 is fatal.

TD-D connection self-healing: the loop never dies silently. EVERY error is
caught and counted (``consecutive_failures``); the count feeds the health
judgment and two escalation steps — after ``self_heal_reset_threshold``
consecutive failures the client connection pool is rebuilt (a server-side
disconnect can leave pooled sockets in CLOSE-WAIT, hanging every later
request), and after ``self_heal_exit_threshold`` the loop signals
whole-process self-healing so the process manager restarts the daemon.

The single-beat logic is factored into :meth:`HeartbeatLoop.beat_once` for
deterministic unit tests.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from mesh_runtime.api import HeartbeatResponse, RuntimeApiClient
from mesh_runtime.backoff import KEEPALIVE, capped_retry_after
from mesh_runtime.errors import DaemonError, FatalAuthError, RateLimitedError, ServerError
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger(__name__)

OnCancel = Callable[[str, float], Awaitable[None]]
InflightSource = Callable[[], list[str]]
OnOperationalIncident = Callable[[str], None]
OnSelfHeal = Callable[[str], None]

#: Consecutive failed beats before the client connection pool is rebuilt.
DEFAULT_SELF_HEAL_RESET_THRESHOLD = 5
#: Consecutive failed beats before requesting whole-process self-healing
#: (clean exit; the process manager — docker/systemd — restarts the daemon).
DEFAULT_SELF_HEAL_EXIT_THRESHOLD = 10


class HeartbeatLoop:
    def __init__(
        self,
        api: RuntimeApiClient,
        runtime_id: str,
        *,
        interval_seconds: float,
        clock: Clock | None = None,
        inventory=None,
        operational_guard=None,
        on_operational_incident: OnOperationalIncident | None = None,
        on_cancel: OnCancel | None = None,
        inflight_source: InflightSource | None = None,
        rand: Callable[[], float] | None = None,
        self_heal_reset_threshold: int = DEFAULT_SELF_HEAL_RESET_THRESHOLD,
        self_heal_exit_threshold: int = DEFAULT_SELF_HEAL_EXIT_THRESHOLD,
        on_self_heal: OnSelfHeal | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be > 0")
        if self_heal_reset_threshold < 1:
            raise ValueError("self_heal_reset_threshold must be >= 1")
        if self_heal_exit_threshold < self_heal_reset_threshold:
            raise ValueError("self_heal_exit_threshold must be >= reset threshold")
        self._api = api
        self._runtime_id = runtime_id
        self._interval = interval_seconds
        self._clock = clock or SystemClock()
        self._inventory = inventory
        self._operational_guard = operational_guard
        self._on_operational_incident = on_operational_incident
        self._on_cancel = on_cancel
        self._inflight_source = inflight_source or (lambda: [])
        self._rand = rand or random.random
        self._self_heal_reset_threshold = self_heal_reset_threshold
        self._self_heal_exit_threshold = self_heal_exit_threshold
        self._on_self_heal = on_self_heal
        self._exit_signalled = False
        self._stop = asyncio.Event()
        self.fatal: FatalAuthError | None = None
        self.beats = 0
        # TD-D: send failures count toward the health judgment and drive the
        # two self-heal escalation steps. Reset to 0 by any successful beat.
        self.consecutive_failures = 0

    def request_stop(self) -> None:
        self._stop.set()

    def jittered_interval(self) -> float:
        # ±10% jitter so a fleet does not synchronise (§3.1 / design §5.2).
        jitter = (self._rand() * 2.0 - 1.0) * 0.10
        return self._interval * (1.0 + jitter)

    def _health(self) -> str:
        if self.consecutive_failures > 0:
            # TD-D: heartbeat send failures count toward the health judgment.
            # The server sees this on the first beat that gets through again.
            return "degraded"
        if self._operational_guard is not None:
            return "healthy" if self._operational_guard.report()[0] == "online" else "degraded"
        if self._inventory is not None and not self._inventory.healthy():
            return "degraded"
        return "healthy"

    def _metrics(self) -> dict:
        if self._inventory is None:
            return {}
        return {"inventory_hash": self._inventory.inventory_hash()}

    def _operational_report(self) -> tuple[str, list[dict]]:
        if self._operational_guard is not None:
            return self._operational_guard.report()
        if self._inventory is None or self._inventory.healthy():
            return "online", []
        return "degraded", self._inventory.operational_diagnostics()

    async def beat_once(self) -> tuple[str, float]:
        """Send one heartbeat and dispatch downlink commands.

        Returns ``(outcome, sleep_seconds)`` where outcome ∈
        {ok, fatal, rate_limited, server_error, client_error}. On ``ok`` the
        caller sleeps the jittered interval; on a transient failure it sleeps
        ``sleep_seconds``. NEVER raises: an uncaught exception here would kill
        the heartbeat coroutine silently — the exact TD-D failure mode where
        the process stays alive but the runtime goes permanently silent.
        """
        try:
            operational_state, diagnostics = self._operational_report()
            resp: HeartbeatResponse = await self._api.heartbeat(
                self._runtime_id,
                current_load=len(self._inflight_source()),
                health=self._health(),
                metrics=self._metrics(),
                inflight=self._inflight_source(),
                operational_state=operational_state,
                diagnostics=diagnostics,
            )
        except FatalAuthError as exc:
            self.fatal = exc
            if self._on_operational_incident is not None:
                self._on_operational_incident("runtime_auth_failed")
            return "fatal", 0.0
        except RateLimitedError as exc:
            self._count_failure("rate_limited")
            delay = capped_retry_after(exc.retry_after)
            return "rate_limited", delay
        except ServerError as exc:
            self._count_failure("server_error")
            logger.warning("heartbeat transport failure: %s", exc)
            return "server_error", KEEPALIVE.delay(self.consecutive_failures - 1, self._rand)
        except DaemonError as exc:
            # ProtocolError / GoneError / LeaseConflictError — fail-closed
            # responses the beat must survive: count, back off, keep trying.
            self._count_failure("client_error")
            logger.warning("heartbeat rejected: %s", exc)
            return "client_error", KEEPALIVE.delay(self.consecutive_failures - 1, self._rand)
        except Exception as exc:  # noqa: BLE001 — the loop must never die silently
            self._count_failure("client_error")
            logger.exception("heartbeat unexpected failure: %s", exc)
            return "client_error", KEEPALIVE.delay(self.consecutive_failures - 1, self._rand)
        self.beats += 1
        self.consecutive_failures = 0
        await self._dispatch(resp.cancel_commands())
        return "ok", self.jittered_interval()

    def _count_failure(self, kind: str) -> None:
        """Tally a failed beat and run the TD-D escalation steps."""
        self.consecutive_failures += 1
        if self.consecutive_failures == self._self_heal_reset_threshold:
            # Step 1: rebuild the connection pool. A server-side close can
            # leave pooled keep-alive sockets unusable (CLOSE-WAIT); fresh
            # connections are the cheapest cure, so try it before giving up.
            logger.warning(
                "heartbeat failed %d consecutive beats (%s) — resetting client transport",
                self.consecutive_failures,
                kind,
            )
            self._notify_self_heal("heartbeat_transport_reset")
        elif self.consecutive_failures >= self._self_heal_exit_threshold and not self._exit_signalled:
            # Step 2: the connection could not be healed in-process. Hand the
            # process to the manager (docker restart policy / systemd) — the
            # daemon is alive but unheard, and only a restart is left.
            logger.error(
                "heartbeat failed %d consecutive beats (%s) — requesting process self-heal",
                self.consecutive_failures,
                kind,
            )
            self._exit_signalled = True
            self._notify_self_heal("heartbeat_process_exit")

    def _notify_self_heal(self, reason: str) -> None:
        if self._on_self_heal is None:
            return
        try:
            self._on_self_heal(reason)
        except Exception:  # noqa: BLE001 — a broken observer must not kill healing
            logger.exception("on_self_heal callback failed for %s", reason)

    async def maybe_reset_transport(self) -> None:
        """Rebuild the API client connection pool if it supports it.

        Called by the escalation step; errors are logged, never raised —
        healing must not make things worse.
        """
        reset = getattr(self._api, "reset_transport", None)
        if reset is None:
            return
        try:
            await reset()
        except Exception:  # noqa: BLE001 — see above
            logger.exception("api.reset_transport failed")

    async def run(self, shutdown: asyncio.Event) -> None:
        while not self._stop.is_set() and not shutdown.is_set() and self.fatal is None:
            outcome, delay = await self.beat_once()
            if outcome == "fatal":
                break
            if self.consecutive_failures == self._self_heal_reset_threshold:
                # The beat that hit the threshold already notified; perform
                # the actual transport rebuild between beats.
                await self.maybe_reset_transport()
            if delay > 0:
                await self._clock.sleep(delay)

    async def _dispatch(self, commands) -> None:
        if self._on_cancel is None:
            return
        for cmd in commands:
            await self._on_cancel(cmd.attempt_id, cmd.grace_seconds)

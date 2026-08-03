"""Worker task supervisor (README §2.2: 独立取消域 + 存活看门狗).

Each resident worker loop runs as an isolated asyncio task.  The watchdog
checks task liveness once per tick and restarts every unexpected exit — raised
exception, cancellation, or a silent clean return — with exponential backoff.
Only the worker's shared shutdown event makes a clean return expected.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger("mesh.workers")

TaskFactory = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class TaskSpec:
    """A supervised worker loop."""

    name: str
    factory: TaskFactory


class Supervisor:
    """Runs resident loops in isolated cancel domains with liveness restart."""

    def __init__(
        self,
        tasks: Sequence[TaskSpec],
        *,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        watchdog_interval: float = 1.0,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        if base_backoff < 0:
            raise ValueError("base_backoff must be non-negative")
        if max_backoff < base_backoff:
            raise ValueError("max_backoff must be greater than or equal to base_backoff")
        if watchdog_interval <= 0:
            raise ValueError("watchdog_interval must be positive")
        self._specs = list(tasks)
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._watchdog_interval = watchdog_interval
        self._shutdown_event = shutdown_event
        self._stop = asyncio.Event()
        self._running: list[asyncio.Task[None]] = []
        self._restart_due: list[float | None] = []
        self._backoffs: list[float] = []
        self.restart_counts: dict[str, int] = {spec.name: 0 for spec in self._specs}

    def _stopping(self) -> bool:
        return self._stop.is_set() or bool(
            self._shutdown_event is not None and self._shutdown_event.is_set()
        )

    def _request_shutdown(self) -> None:
        self._stop.set()
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    @staticmethod
    async def _run_factory(spec: TaskSpec) -> None:
        """Invoke the factory inside its isolated task, including sync errors."""
        await spec.factory()

    @staticmethod
    def _start(spec: TaskSpec) -> asyncio.Task[None]:
        return asyncio.create_task(Supervisor._run_factory(spec), name=spec.name)

    @staticmethod
    def _exit_details(task: asyncio.Task[None]) -> tuple[str, BaseException | None]:
        if task.cancelled():
            return "cancelled", None
        error = task.exception()
        if error is None:
            return "returned", None
        return "exception", error

    def _schedule_restarts(self, now: float) -> None:
        """Observe newly dead tasks and schedule exactly one restart each."""
        for index, (spec, task) in enumerate(zip(self._specs, self._running, strict=True)):
            if self._restart_due[index] is not None or not task.done() or self._stopping():
                continue

            exit_kind, error = self._exit_details(task)
            self.restart_counts[spec.name] += 1
            restart_count = self.restart_counts[spec.name]
            delay = self._backoffs[index]
            self._restart_due[index] = now + delay
            self._backoffs[index] = min(delay * 2, self._max_backoff)
            log_extra = {
                "event": "worker_loop_died",
                "worker_task": spec.name,
                "exit_kind": exit_kind,
                "restart_count": restart_count,
                "restart_delay_seconds": delay,
            }
            if error is not None:
                logger.error(
                    "worker loop %r raised unexpectedly (restart #%d in %.1fs)",
                    spec.name,
                    restart_count,
                    delay,
                    exc_info=(type(error), error, error.__traceback__),
                    extra=log_extra,
                )
            else:
                logger.error(
                    "worker loop %r %s unexpectedly (restart #%d in %.1fs)",
                    spec.name,
                    exit_kind,
                    restart_count,
                    delay,
                    extra=log_extra,
                )

    def _restart_ready_tasks(self, now: float) -> None:
        """Start tasks whose backoff expired; one tick bounds liveness checks."""
        for index, spec in enumerate(self._specs):
            due = self._restart_due[index]
            if due is None or due > now or self._stopping():
                continue
            self._running[index] = self._start(spec)
            self._restart_due[index] = None
            logger.info(
                "worker loop %r restarted (restart #%d)",
                spec.name,
                self.restart_counts[spec.name],
                extra={
                    "event": "worker_loop_restarted",
                    "worker_task": spec.name,
                    "restart_count": self.restart_counts[spec.name],
                },
            )

    def _watchdog_tick(self) -> None:
        now = asyncio.get_running_loop().time()
        self._schedule_restarts(now)
        self._restart_ready_tasks(now)

    def add_task(self, spec: TaskSpec) -> None:
        """Register an additional loop BEFORE :meth:`run` (conditional tasks,
        e.g. the DingTalk Stream worker when enabled in settings)."""
        if self._running:
            raise RuntimeError("cannot add tasks after the supervisor started")
        self._specs.append(spec)
        self.restart_counts.setdefault(spec.name, 0)

    async def run(self) -> None:
        """Run and monitor all loops until shared/local stop or external cancel."""
        if self._running:
            raise RuntimeError("supervisor already started")
        if not self._specs:
            return
        self._running = [self._start(spec) for spec in self._specs]
        self._restart_due = [None for _ in self._specs]
        self._backoffs = [self._base_backoff for _ in self._specs]
        try:
            while not self._stopping():
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._watchdog_interval
                    )
                except TimeoutError:
                    pass
                if not self._stopping():
                    self._watchdog_tick()
        finally:
            self._request_shutdown()
            for task in self._running:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._running, return_exceptions=True)

    def stop(self) -> None:
        """Signal local shutdown and cancel all resident tasks promptly."""
        self._request_shutdown()
        for task in self._running:
            if not task.done():
                task.cancel()

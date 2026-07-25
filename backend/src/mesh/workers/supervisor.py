"""Worker task supervisor (README §2.2: 独立取消域 + 看门狗).

Each worker loop (relay, retention, future scheduler/reaper/fan-out/attachment
tasks) runs as an isolated asyncio task. One loop crashing or hanging must not
block the others: the supervisor restarts crashed loops with exponential
backoff and logs every restart.
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
    """Runs worker loops in isolated cancel domains with crash-restart."""

    def __init__(
        self,
        tasks: Sequence[TaskSpec],
        *,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self._specs = list(tasks)
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._stop = asyncio.Event()
        self._running: list[asyncio.Task] = []
        self.restart_counts: dict[str, int] = {spec.name: 0 for spec in self._specs}

    async def _supervise(self, spec: TaskSpec) -> None:
        backoff = self._base_backoff
        while not self._stop.is_set():
            try:
                await spec.factory()
                return  # clean exit (e.g. stop signalled inside the loop)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.restart_counts[spec.name] += 1
                logger.exception(
                    "worker loop %r crashed (restart #%d); retrying in %.1fs",
                    spec.name,
                    self.restart_counts[spec.name],
                    backoff,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return  # stop requested during backoff
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, self._max_backoff)

    async def run(self) -> None:
        """Run all loops until :meth:`stop` is called (or external cancel)."""
        self._running = [asyncio.create_task(self._supervise(spec), name=spec.name) for spec in self._specs]
        try:
            results = await asyncio.gather(*self._running, return_exceptions=True)
        except asyncio.CancelledError:
            self.stop()
            raise
        for spec, result in zip(self._specs, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.error("worker loop %r ended with error: %r", spec.name, result)

    def stop(self) -> None:
        """Signal every loop to stop and cancel the supervisors."""
        self._stop.set()
        for task in self._running:
            task.cancel()

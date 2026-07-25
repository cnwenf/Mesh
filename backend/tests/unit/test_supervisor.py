"""Supervisor: isolated cancel domains, crash-restart, watchdog semantics (§2.2)."""

from __future__ import annotations

import asyncio

from mesh.workers.supervisor import Supervisor, TaskSpec


async def test_crashing_task_restarts_without_blocking_healthy_task():
    healthy_ticks = 0
    crashes_left = 2
    stop = asyncio.Event()

    async def healthy() -> None:
        nonlocal healthy_ticks
        while not stop.is_set():
            healthy_ticks += 1
            await asyncio.sleep(0.01)

    async def crasher() -> None:
        nonlocal crashes_left
        if crashes_left > 0:
            crashes_left -= 1
            raise RuntimeError("boom")
        while not stop.is_set():
            await asyncio.sleep(0.01)

    supervisor = Supervisor(
        [TaskSpec("healthy", healthy), TaskSpec("crasher", crasher)], base_backoff=0.01
    )
    task = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0.3)

    assert supervisor.restart_counts["crasher"] == 2
    baseline = healthy_ticks
    await asyncio.sleep(0.1)
    assert healthy_ticks > baseline  # healthy loop kept running through the crashes

    supervisor.stop()
    await asyncio.wait_for(task, timeout=5)


async def test_clean_exit_ends_supervisor():
    async def short() -> None:
        return None

    supervisor = Supervisor([TaskSpec("short", short)])
    await asyncio.wait_for(supervisor.run(), timeout=5)
    assert supervisor.restart_counts["short"] == 0

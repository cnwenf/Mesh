"""Supervisor: isolated cancel domains, crash-restart, watchdog semantics (§2.2)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from mesh.workers.supervisor import Supervisor, TaskSpec


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"base_backoff": -1}, "base_backoff"),
        ({"base_backoff": 2, "max_backoff": 1}, "max_backoff"),
        ({"watchdog_interval": 0}, "watchdog_interval"),
    ],
)
def test_supervisor_rejects_invalid_timing(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Supervisor([], **kwargs)


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
        [TaskSpec("healthy", healthy), TaskSpec("crasher", crasher)],
        base_backoff=0.01,
        watchdog_interval=0.01,
    )
    task = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0.3)

    assert supervisor.restart_counts["crasher"] == 2
    baseline = healthy_ticks
    await asyncio.sleep(0.1)
    assert healthy_ticks > baseline  # healthy loop kept running through the crashes

    supervisor.stop()
    await asyncio.wait_for(task, timeout=5)


async def test_synchronous_factory_error_restarts_without_blocking_healthy_task(
    caplog, monkeypatch
):
    shutdown = asyncio.Event()
    healthy_started = asyncio.Event()
    restarted = asyncio.Event()
    factory_calls = 0

    async def healthy() -> None:
        healthy_started.set()
        await shutdown.wait()

    async def resident() -> None:
        restarted.set()
        await shutdown.wait()

    def flaky_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            raise RuntimeError("synchronous factory failure")
        return resident()

    supervisor = Supervisor(
        [TaskSpec("flaky", flaky_factory), TaskSpec("healthy", healthy)],
        base_backoff=0,
        watchdog_interval=0.01,
        shutdown_event=shutdown,
    )
    monkeypatch.setattr(logging.getLogger("mesh.workers"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="mesh.workers"):
        task = asyncio.create_task(supervisor.run())
        try:
            await asyncio.wait_for(healthy_started.wait(), timeout=1)
            await asyncio.wait_for(restarted.wait(), timeout=1)
        finally:
            shutdown.set()
            await asyncio.gather(task, return_exceptions=True)

    assert supervisor.restart_counts["flaky"] == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "worker_task", None) == "flaky"
    )
    assert record.event == "worker_loop_died"
    assert record.exit_kind == "exception"
    assert record.restart_count == 1
    assert record.restart_delay_seconds == 0


async def test_unexpected_clean_exit_restarts_with_structured_error_log(caplog, monkeypatch):
    shutdown = asyncio.Event()
    restarted = asyncio.Event()
    starts = 0

    async def short() -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            return
        restarted.set()
        await shutdown.wait()

    supervisor = Supervisor(
        [TaskSpec("short", short)],
        base_backoff=0,
        watchdog_interval=0.01,
        shutdown_event=shutdown,
    )
    monkeypatch.setattr(logging.getLogger("mesh.workers"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="mesh.workers"):
        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(restarted.wait(), timeout=1)

    assert supervisor.restart_counts["short"] == 1
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "worker_loop_died"
    )
    assert record.worker_task == "short"
    assert record.exit_kind == "returned"
    assert record.restart_count == 1
    assert record.restart_delay_seconds == 0

    shutdown.set()
    await asyncio.wait_for(task, timeout=1)


async def test_unexpected_cancellation_restarts_with_structured_error_log(caplog, monkeypatch):
    shutdown = asyncio.Event()
    first_started = asyncio.Event()
    restarted = asyncio.Event()
    starts = 0

    async def resident() -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            first_started.set()
            await asyncio.Event().wait()
        restarted.set()
        await shutdown.wait()

    supervisor = Supervisor(
        [TaskSpec("resident", resident)],
        base_backoff=0,
        watchdog_interval=0.01,
        shutdown_event=shutdown,
    )
    monkeypatch.setattr(logging.getLogger("mesh.workers"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="mesh.workers"):
        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(first_started.wait(), timeout=1)
        next(running for running in supervisor._running if running.get_name() == "resident").cancel()
        await asyncio.wait_for(restarted.wait(), timeout=1)

    assert supervisor.restart_counts["resident"] == 1
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "worker_loop_died"
    )
    assert record.worker_task == "resident"
    assert record.exit_kind == "cancelled"

    shutdown.set()
    await asyncio.wait_for(task, timeout=1)


async def test_clean_exit_after_shutdown_does_not_restart_or_alert(caplog, monkeypatch):
    shutdown = asyncio.Event()
    started = asyncio.Event()

    async def resident() -> None:
        started.set()
        await shutdown.wait()

    supervisor = Supervisor(
        [TaskSpec("resident", resident)],
        watchdog_interval=0.01,
        shutdown_event=shutdown,
    )
    monkeypatch.setattr(logging.getLogger("mesh.workers"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="mesh.workers"):
        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    assert supervisor.restart_counts["resident"] == 0
    assert not [record for record in caplog.records if hasattr(record, "event")]


async def test_cancelling_supervisor_signals_shared_shutdown_and_awaits_children():
    shutdown = asyncio.Event()
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def resident() -> None:
        started.set()
        try:
            await shutdown.wait()
        finally:
            cleaned_up.set()

    supervisor = Supervisor(
        [TaskSpec("resident", resident)],
        watchdog_interval=0.01,
        shutdown_event=shutdown,
    )
    task = asyncio.create_task(supervisor.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert shutdown.is_set()
    assert cleaned_up.is_set()
    assert all(running.done() for running in supervisor._running)


async def test_tasks_can_only_be_added_before_first_run():
    started = asyncio.Event()

    async def resident() -> None:
        started.set()
        await asyncio.Event().wait()

    supervisor = Supervisor([], watchdog_interval=0.01)
    supervisor.add_task(TaskSpec("resident", resident))
    task = asyncio.create_task(supervisor.run())
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(RuntimeError, match="cannot add"):
        supervisor.add_task(TaskSpec("late", resident))
    with pytest.raises(RuntimeError, match="already started"):
        await supervisor.run()

    supervisor.stop()
    await asyncio.wait_for(task, timeout=1)
    supervisor.stop()  # completed tasks are harmless on repeated shutdown


async def test_empty_supervisor_returns_immediately():
    await Supervisor([]).run()

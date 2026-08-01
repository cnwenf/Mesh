"""Regression tests for database cleanup under live worker contention."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import truncate_tables_with_retry


@pytest.mark.asyncio
async def test_truncate_releases_queued_readers_before_retry(db_url: str) -> None:
    """A queued TRUNCATE must not deadlock a worker's nested session.

    This recreates the e2e failure ordering with real PostgreSQL locks:

    1. a worker's outer transaction holds an AccessShare lock;
    2. cleanup queues an AccessExclusive TRUNCATE behind it;
    3. the worker's nested session queues a read behind TRUNCATE;
    4. the outer transaction cannot commit until that nested read returns.

    A bounded cleanup lock wait breaks the queue, lets the nested read finish,
    and then retries TRUNCATE after the outer transaction commits.
    """
    engine = create_async_engine(db_url, pool_size=3, max_overflow=0)
    outer = await engine.connect()
    outer_transaction = await outer.begin()
    truncate_task: asyncio.Task[None] | None = None
    nested_read_task: asyncio.Task[None] | None = None

    try:
        await outer.execute(text("SELECT 1 FROM outbox_events LIMIT 1"))
        truncate_task = asyncio.create_task(
            truncate_tables_with_retry(
                engine,
                "outbox_events",
                lock_timeout_seconds=0.1,
                retry_delay_seconds=0.1,
            )
        )
        await asyncio.sleep(0.05)

        async def nested_read() -> None:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM outbox_events LIMIT 1"))

        nested_read_task = asyncio.create_task(nested_read())
        await asyncio.wait_for(nested_read_task, timeout=2)
        await outer_transaction.commit()
        await asyncio.wait_for(truncate_task, timeout=2)
    finally:
        for task in (nested_read_task, truncate_task):
            if task is not None and not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        if outer_transaction.is_active:
            await outer_transaction.rollback()
        await outer.close()
        await engine.dispose()

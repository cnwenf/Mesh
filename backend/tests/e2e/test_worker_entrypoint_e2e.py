"""Worker process entrypoint e2e: run_worker drains the outbox for real."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from mesh.config import load_settings
from mesh.db.models.realtime import RealtimeEvent
from mesh.outbox.service import emit_realtime
from mesh.workers.main import build_relay, main, run_worker

pytestmark = pytest.mark.e2e


async def test_run_worker_drains_outbox_until_stopped(
    db_url, redis_url, session_factory, workspace_factory
):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        outbox_poll_interval=0.05,
        realtime_retention_interval=0.05,
    )
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session,
            workspace_id=workspace.id,
            channel="issue:worker-main",
            event="issue.updated",
            data={"via": "worker"},
        )

    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(settings, stop=stop))
    deadline = asyncio.get_event_loop().time() + 10
    while True:
        async with session_factory() as session:
            count = len(
                (
                    await session.execute(
                        select(RealtimeEvent).where(RealtimeEvent.channel == "issue:worker-main")
                    )
                ).all()
            )
        if count == 1:
            break
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("worker did not project the event in time")
        await asyncio.sleep(0.05)

    stop.set()
    await asyncio.wait_for(task, timeout=10)


def test_build_relay_registers_realtime_handler(session_factory):
    relay = build_relay(
        load_settings(
            database_url="postgresql+asyncpg://u:p@h:5432/db", redis_url="redis://h:6379/0"
        ),
        session_factory,
        fanout=None,
    )
    assert "realtime.publish" in relay._handlers


def test_main_exits_nonzero_on_missing_config(monkeypatch):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    assert main() == 1

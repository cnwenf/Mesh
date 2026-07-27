"""Worker process entrypoint e2e: run_worker drains the outbox for real."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text

from mesh.config import load_settings
from mesh.db.models.outbox import OutboxEvent
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
        outbox_retention_interval=0.05,
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
    # M3: a terminal outbox row older than the retention window — the real
    # worker's outbox-retention loop must purge it.
    async with session_factory() as session, session.begin():
        row = await session.execute(
            text(
                "INSERT INTO outbox_events (workspace_id, event_type, payload, status) "
                "VALUES (:ws, 'realtime.publish', '{}', 'published') RETURNING id"
            ),
            {"ws": workspace.id},
        )
        stale_outbox_id = row.scalar_one()
        await session.execute(
            text(
                "UPDATE outbox_events SET created_at = now() - interval '30 days' "
                "WHERE id = :id"
            ),
            {"id": stale_outbox_id},
        )

    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(settings, stop=stop))
    deadline = asyncio.get_event_loop().time() + 10
    projected = False
    purged = False
    while not (projected and purged):
        async with session_factory() as session:
            count = len(
                (
                    await session.execute(
                        select(RealtimeEvent).where(RealtimeEvent.channel == "issue:worker-main")
                    )
                ).all()
            )
            stale = await session.scalar(
                select(OutboxEvent.id).where(OutboxEvent.id == stale_outbox_id)
            )
        projected = count == 1
        purged = stale is None
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"worker did not finish in time (projected={projected}, purged={purged})"
            )
        await asyncio.sleep(0.05)

    stop.set()
    await asyncio.wait_for(task, timeout=10)


def test_build_relay_registers_realtime_handler(session_factory):
    from mesh.attachment.storage import ObjectStorage, StorageConfig

    storage = ObjectStorage(
        StorageConfig(
            endpoint="http://127.0.0.1:1",
            public_endpoint="http://127.0.0.1:1",
            region="us-east-1",
            access_key="x",
            secret_key="y",
            bucket="test-bucket",
        )
    )
    relay = build_relay(
        load_settings(
            database_url="postgresql+asyncpg://u:p@h:5432/db", redis_url="redis://h:6379/0"
        ),
        session_factory,
        fanout=None,
        storage=storage,
    )
    assert "realtime.publish" in relay._handlers


def test_main_exits_nonzero_on_missing_config(monkeypatch):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    assert main() == 1

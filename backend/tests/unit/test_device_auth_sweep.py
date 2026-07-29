"""Device-authorization expiry sweep worker loop (auth.md §2.4.2 过期清理)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.config import load_settings
from mesh.db.models.user import DeviceAuthorization
from mesh.workers.device_auth_sweep import device_auth_sweep_loop

pytestmark = pytest.mark.unit


async def _seed_expired_grant(session_factory) -> None:
    from mesh.auth.security import hmac_token

    async with session_factory() as session, session.begin():
        session.add(
            DeviceAuthorization(
                device_code_hash=hmac_token(uuid.uuid4().hex, "sweep-test-pepper"),
                user_code_hash=hmac_token(uuid.uuid4().hex, "sweep-test-pepper"),
                status="pending",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),  # past TTL
            )
        )


async def test_sweep_loop_expires_grants_and_stops(session_factory, db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        device_code_pepper="sweep-test-pepper",
    )
    await _seed_expired_grant(session_factory)

    stop = asyncio.Event()
    task = asyncio.create_task(
        device_auth_sweep_loop(
            session_factory, settings=settings, interval=0.1, stop=stop
        )
    )
    # Let one iteration run, then stop — the loop must exit promptly.
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    async with session_factory() as session:
        rows = (await session.execute(select(DeviceAuthorization))).scalars().all()
    assert all(row.status == "expired" for row in rows)

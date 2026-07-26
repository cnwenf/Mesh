"""Worker process entrypoint: ``python -m mesh.workers``.

Runs the outbox relay (with the realtime projector handler) and the realtime
retention purge as isolated supervised loops (README §2.2 deployment shape:
one worker process; each loop decoupled via SKIP LOCKED / independent cancel
domains).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime

import redis.asyncio as aioredis

from mesh.config import ConfigError, Settings, load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.errors import MeshError
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.issue.triggers import ASSIGN_EVENT_TYPE, assign_trigger_handler
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.realtime.pubsub import RedisFanOut
from mesh.workers.invitation_sweep import invitation_sweep_loop
from mesh.workers.retention import retention_loop
from mesh.workers.supervisor import Supervisor, TaskSpec

logger = logging.getLogger("mesh.workers")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_relay(settings: Settings, session_factory, fanout: RedisFanOut) -> OutboxRelay:
    """Assemble the relay with the current handler set.

    ``issue.assigned`` is consumed by a bridge handler until the agent.md
    increment provides the unified orchestration entry point (issue.md §3.7):
    the producing side already carries the §6.9 trigger payload, so the swap
    is handler-local.
    """
    return OutboxRelay(
        session_factory,
        handlers={
            REALTIME_PUBLISH: project_realtime_event,
            ASSIGN_EVENT_TYPE: assign_trigger_handler,
        },
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
        poll_interval=settings.outbox_poll_interval,
        fanout=fanout,
    )


async def run_worker(settings: Settings | None = None, stop: asyncio.Event | None = None) -> None:
    """Run all worker loops until ``stop`` is set (or forever)."""
    settings = settings or load_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    fanout = RedisFanOut(redis_client)
    relay = build_relay(settings, session_factory, fanout)
    stop = stop or asyncio.Event()

    supervisor = Supervisor(
        [
            TaskSpec("outbox-relay", lambda: relay.run_forever(stop)),
            TaskSpec(
                "realtime-retention",
                lambda: retention_loop(
                    session_factory,
                    retention=settings.realtime_event_retention,
                    interval=settings.realtime_retention_interval,
                    stop=stop,
                    clock=_utcnow,
                ),
            ),
            TaskSpec(
                "invitation-sweep",
                lambda: invitation_sweep_loop(
                    session_factory,
                    interval=settings.invitation_sweep_interval,
                    stop=stop,
                    clock=_utcnow,
                ),
            ),
        ]
    )
    try:
        await supervisor.run()
    finally:
        with contextlib.suppress(Exception):
            await redis_client.aclose()
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        stop.set()

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)
    try:
        loop.run_until_complete(run_worker(stop=stop))
    except (ConfigError, MeshError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

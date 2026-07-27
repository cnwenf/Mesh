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
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.agent.triggers import assign_orchestration_handler
from mesh.attachment.processing import process_blob
from mesh.attachment.scanner import HeuristicScanner
from mesh.attachment.service import SCAN_REQUESTED_EVENT_TYPE
from mesh.attachment.storage import ObjectStorage
from mesh.auth.mailer import build_mailer
from mesh.comment_inbox.notifications import FANOUT_EVENT_TYPE, NotificationFanoutHandler
from mesh.config import ConfigError, Settings, load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.attachment import AttachmentBlob
from mesh.errors import MeshError
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.issue.triggers import ASSIGN_EVENT_TYPE
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.realtime.pubsub import RedisFanOut
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE, enqueue_execution_handler
from mesh.runtime.reaper import runtime_reaper_loop
from mesh.workers.attachment_processor import (
    attachment_maintenance_loop,
    attachment_scan_loop,
)
from mesh.workers.due_soon_sweep import due_soon_sweep_loop
from mesh.workers.invitation_sweep import invitation_sweep_loop
from mesh.workers.notification_digest import notification_digest_loop
from mesh.workers.retention import outbox_retention_loop, retention_loop
from mesh.workers.supervisor import Supervisor, TaskSpec

logger = logging.getLogger("mesh.workers")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _build_scan_requested_handler(settings: Settings, storage: ObjectStorage):
    """Low-latency quarantine trigger (attachment.md §3.3 step 3).

    ``complete`` writes ``attachment.scan_requested`` in its business
    transaction; this handler processes the referenced blob immediately
    (owner role, cross-tenant). The attachment-scan loop is the crash-
    recovery sweep over the same pending set — the two are idempotent via
    the blob's scan_status.
    """
    scanner = HeuristicScanner()

    async def _handle(session, event) -> None:
        payload = event.payload or {}
        raw_blob_id = payload.get("blob_id")
        if not raw_blob_id:
            return None
        try:
            blob_id = uuid.UUID(str(raw_blob_id))
        except ValueError:
            return None
        blob = await session.scalar(
            select(AttachmentBlob)
            .where(AttachmentBlob.id == blob_id)
            .with_for_update(skip_locked=True)
        )
        if blob is None or blob.scan_status != "pending":
            return None  # already processed / claimed by the sweep loop
        await process_blob(session, blob, storage=storage, settings=settings, scanner=scanner)
        return None

    return _handle


def build_relay(
    settings: Settings,
    session_factory,
    fanout: RedisFanOut,
    storage: ObjectStorage,
    mailer=None,
) -> OutboxRelay:
    """Assemble the relay with the current handler set.

    ``issue.assigned`` / field & status changes produce ``notification.fanout``
    (comment-inbox.md = single notification authority, README §6.13 matrix);
    ``execution.enqueue`` for mentions is consumed by a bridge handler until the
    runtime.md increment provides the unified orchestration entry, while
    ``issue.assigned``-triggered execution is handled by the unified agent
    orchestration entry (agent.md §3.3: guardrail gate → §6.11 snapshot freeze →
    ``execution.enqueue`` with the README §6.5 idempotency key, or
    ``agent.trigger_skipped`` when a guardrail denies). The producing sides carry
    the §6.9 trigger payloads, so remaining swaps are handler-local.
    """
    return OutboxRelay(
        session_factory,
        handlers={
            REALTIME_PUBLISH: project_realtime_event,
            ASSIGN_EVENT_TYPE: assign_orchestration_handler,
            SCAN_REQUESTED_EVENT_TYPE: _build_scan_requested_handler(settings, storage),
            # runtime.md consumer side of the MES-60 / comment-inbox contract:
            # agent dispatch and @mention both enqueue; this handler
            # materializes task_executions (README §6.4 logical layer,
            # idempotent by §6.5 key) — replaces the stopgap bridge.
            ENQUEUE_EVENT_TYPE: enqueue_execution_handler,
            FANOUT_EVENT_TYPE: NotificationFanoutHandler(
                aggregation_window_seconds=settings.notification_aggregation_window,
                mailer=mailer,
            ),
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
    # Attachment quarantine pipeline shares the API's storage settings; the
    # worker reads quarantine objects server-side (MIME sniff / SHA-256 / AV /
    # thumbnails, attachment.md §3.3) over the internal endpoint.
    from mesh.api.app import build_object_storage

    storage = build_object_storage(settings)
    try:
        await storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage may join late; loops retry
        logger.warning("attachment bucket not ready at worker startup")
    mailer = build_mailer(settings, redis_client)
    relay = build_relay(settings, session_factory, fanout, storage, mailer=mailer)
    stop = stop or asyncio.Event()

    supervisor = Supervisor(
        [
            TaskSpec("outbox-relay", lambda: relay.run_forever(stop)),
            TaskSpec(
                "notification-digest",
                lambda: notification_digest_loop(
                    session_factory,
                    mailer=mailer,
                    interval=settings.notification_digest_interval,
                    stop=stop,
                ),
            ),
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
                "outbox-retention",
                lambda: outbox_retention_loop(
                    session_factory,
                    retention=settings.outbox_event_retention,
                    interval=settings.outbox_retention_interval,
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
            TaskSpec(
                "due-soon-sweep",
                lambda: due_soon_sweep_loop(
                    session_factory,
                    interval=settings.due_soon_sweep_interval,
                    horizon_hours=settings.due_soon_horizon_hours,
                    stop=stop,
                    clock=_utcnow,
                ),
            ),
            TaskSpec(
                "attachment-scan",
                lambda: attachment_scan_loop(
                    session_factory, storage=storage, settings=settings, stop=stop
                ),
            ),
            TaskSpec(
                "attachment-maintenance",
                lambda: attachment_maintenance_loop(
                    session_factory, storage=storage, settings=settings, stop=stop
                ),
            ),
            TaskSpec(
                "runtime-reaper",
                lambda: runtime_reaper_loop(session_factory, settings=settings, stop=stop),
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

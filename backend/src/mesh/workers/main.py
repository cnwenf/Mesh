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

from mesh.agent.triggers import assign_orchestration_handler, register_skill_matching_resolver
from mesh.attachment.processing import process_blob
from mesh.attachment.scanner import HeuristicScanner
from mesh.attachment.service import SCAN_REQUESTED_EVENT_TYPE
from mesh.attachment.storage import ObjectStorage
from mesh.auth.mailer import build_mailer
from mesh.autopilot.executor import autopilot_executor_loop
from mesh.autopilot.matcher import match_domain_event
from mesh.autopilot.scheduler import autopilot_scheduler_loop
from mesh.comment_inbox.notifications import FANOUT_EVENT_TYPE, NotificationFanoutHandler
from mesh.config import ConfigError, Settings, load_settings
from mesh.data_jobs.reaper import data_job_reaper_loop
from mesh.data_jobs.runner import (
    ENQUEUE_EVENT_TYPE as DATA_JOB_ENQUEUE_EVENT_TYPE,
)
from mesh.data_jobs.runner import (
    RESUME_EVENT_TYPE as DATA_JOB_RESUME_EVENT_TYPE,
)
from mesh.data_jobs.runner import (
    DataJobWorker,
)
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.attachment import AttachmentBlob
from mesh.errors import MeshError
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.issue.triggers import ASSIGN_EVENT_TYPE
from mesh.onboarding.consumers import consume_realtime_event as onboarding_consume_realtime_event
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.realtime.pubsub import RedisFanOut
from mesh.runtime.approvals import SQUAD_PLAN_DECIDED_EVENT_TYPE
from mesh.runtime.enqueue import (
    CHAT_GENERATION_FINISHED_EVENT,
    ENQUEUE_EVENT_TYPE,
    chat_generation_finished_handler,
    enqueue_execution_handler,
)
from mesh.runtime.reaper import runtime_reaper_loop
from mesh.runtime.result_sink import execution_finished_result_sink
from mesh.skill.content_store import ObjectStorageContentStore
from mesh.skill.importer import ImportSettings, skill_import_sweep_loop
from mesh.skill.resolvers import make_matching_resolver
from mesh.squad.relay import (
    make_squad_execution_finished_handler,
    squad_plan_decided_handler,
)
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
            select(AttachmentBlob).where(AttachmentBlob.id == blob_id).with_for_update(skip_locked=True)
        )
        if blob is None or blob.scan_status != "pending":
            return None  # already processed / claimed by the sweep loop
        await process_blob(session, blob, storage=storage, settings=settings, scanner=scanner)
        return None

    return _handle


def _compose_execution_finished(squad_handler):
    """§3.7 S-09: compose squad handler + result sink for execution.finished.

    The relay dispatches one handler per event type. Both the squad relay
    (squad task closure) and the result sink (regular issue result comment)
    must observe execution.finished. The result sink internally skips
    squad executions (checks task_spec.squad_task_id).
    """

    async def _handle(session, event):
        # Squad handler first (squad task closure).
        await squad_handler(session, event)
        # Result sink for regular (non-squad) executions.
        await execution_finished_result_sink(session, event)
        return None

    return _handle


def build_relay(
    settings: Settings,
    session_factory,
    fanout: RedisFanOut,
    storage: ObjectStorage,
    mailer=None,
    data_job_worker=None,
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
    # squad.md §S8 / §4.3-7: the leader's aggregate summary is written back to
    # the parent issue as a comment when a squad-assigned root finishes done.
    from mesh.comment_inbox.service import CommentService

    squad_comment_service = CommentService(
        session_factory,
        max_agent_chain_depth=settings.max_agent_chain_depth,
        signing_secret=settings.jwt_secret,
    )

    async def _realtime_publish_with_autopilot(session, event):
        # Autopilot is the outbox relay consumer for domain events
        # (autopilot.md §4.5, README §6.6): projection first (its frames are
        # the return value), then trigger matching — run creation is
        # idempotent through the guardrail dedup window, so relay
        # redelivery after a crash never doubles a run (§5.1 T5-style
        # kill-and-restart acceptance). Onboarding (onboarding.md §3.6)
        # chains on the same claim: member.added / issue.created /
        # execution.queued / notification.read advance checklists through
        # the §3.5 completion guards (at-least-once safe).
        frames = await project_realtime_event(session, event)
        try:
            await match_domain_event(session, event)
        except Exception:  # noqa: BLE001 — matching must not break projection
            logger.exception("autopilot event matching failed for %s", event.id)
        try:
            await onboarding_consume_realtime_event(session, event)
        except Exception:  # noqa: BLE001 — onboarding must not break projection
            logger.exception("onboarding event consumption failed for %s", event.id)
        return frames

    handlers = {
        REALTIME_PUBLISH: _realtime_publish_with_autopilot,
        ASSIGN_EVENT_TYPE: assign_orchestration_handler,
        SCAN_REQUESTED_EVENT_TYPE: _build_scan_requested_handler(settings, storage),
        # runtime.md consumer side of the MES-60 / comment-inbox contract:
        # agent dispatch and @mention both enqueue; this handler
        # materializes task_executions (README §6.4 logical layer,
        # idempotent by §6.5 key) — replaces the stopgap bridge.
        ENQUEUE_EVENT_TYPE: enqueue_execution_handler,
        # chat-session.md §4.4 衔接: platform-driven chat generations
        # finalize their trigger='chat' execution through the outbox.
        CHAT_GENERATION_FINISHED_EVENT: chat_generation_finished_handler,
        FANOUT_EVENT_TYPE: NotificationFanoutHandler(
            aggregation_window_seconds=settings.notification_aggregation_window,
            mailer=mailer,
        ),
        # squad.md: plan decisions (§6.10) and execution-terminal observation
        # (§4.4) are applied relay-side, keeping runtime decoupled from squad.
        SQUAD_PLAN_DECIDED_EVENT_TYPE: squad_plan_decided_handler,
        # §3.7 S-09: execution.finished is consumed by BOTH the squad relay
        # (squad task closure) and the result sink (regular issue comment).
        # Compose them: squad handler runs first, then result sink for
        # non-squad executions (result_sink skips squad executions internally).
        "execution.finished": _compose_execution_finished(
            make_squad_execution_finished_handler(squad_comment_service),
        ),
    }
    if data_job_worker is not None:
        # import-export.md §3.8: job execution flows through the outbox to
        # the data-jobs worker (fenced claim → batched pipeline → terminal
        # notification); ``data_job.resume`` is the reaper recovery path.
        handlers[DATA_JOB_ENQUEUE_EVENT_TYPE] = data_job_worker.handle_enqueue
        handlers[DATA_JOB_RESUME_EVENT_TYPE] = data_job_worker.handle_resume
    return OutboxRelay(
        session_factory,
        handlers=handlers,
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
        poll_interval=settings.outbox_poll_interval,
        fanout=fanout,
    )



def _autopilot_executor_services(settings: Settings, session_factory) -> dict:
    """Action-step dependencies for the autopilot run executor.

    Comment/issue actions reuse the owning modules' services (same
    transaction-per-action contract as the API routes) — no duplicated
    domain logic.
    """
    from mesh.comment_inbox.service import CommentService
    from mesh.issue.service import IssueService

    return {
        "session_factory": session_factory,
        "comment_service": CommentService(
            session_factory,
            max_agent_chain_depth=settings.max_agent_chain_depth,
            signing_secret=settings.jwt_secret,
        ),
        "issue_service": IssueService(session_factory),
    }

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
    # Data-jobs worker: import/export pipeline resident (import-export.md
    # §3.8 — outbox-dispatched, fenced, checkpoint-resumable).
    from mesh.attachment.service import AttachmentService

    data_job_worker = DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
    )
    relay = build_relay(
        settings,
        session_factory,
        fanout,
        storage,
        mailer=mailer,
        data_job_worker=data_job_worker,
    )
    stop = stop or asyncio.Event()

    # skill.md §4.5 / §6.11: matching resolver feeds the enqueue handler; the
    # crash-recovery sweep drains import tasks left mid-pipeline by a crash.
    register_skill_matching_resolver(make_matching_resolver())
    skill_content_store = ObjectStorageContentStore(storage)
    skill_import_settings = ImportSettings(
        host_allowlist=frozenset(
            h.strip().lower()
            for h in (settings.skill_source_host_allowlist or "").split(",")
            if h.strip()
        ),
        marketplace_url=settings.skill_marketplace_url,
    )

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
                lambda: attachment_scan_loop(session_factory, storage=storage, settings=settings, stop=stop),
            ),
            TaskSpec(
                "attachment-maintenance",
                lambda: attachment_maintenance_loop(
                    session_factory, storage=storage, settings=settings, stop=stop
                ),
            ),
            TaskSpec(
                "skill-import-sweep",
                lambda: skill_import_sweep_loop(
                    session_factory,
                    content_store=skill_content_store,
                    settings=skill_import_settings,
                    interval=settings.skill_import_sweep_interval,
                    stop=stop,
                    clock=_utcnow,
                ),
            ),
            TaskSpec(
                "runtime-reaper",
                lambda: runtime_reaper_loop(session_factory, settings=settings, stop=stop),
            ),
            TaskSpec(
                "autopilot-scheduler",
                lambda: autopilot_scheduler_loop(
                    session_factory,
                    interval=settings.autopilot_schedule_interval,
                    batch=settings.autopilot_schedule_batch,
                    grace_seconds=settings.autopilot_misfire_grace_seconds,
                    run_all_cap=settings.autopilot_run_all_cap,
                    stop=stop,
                ),
            ),
            TaskSpec(
                "autopilot-executor",
                lambda: autopilot_executor_loop(
                    session_factory,
                    services=_autopilot_executor_services(settings, session_factory),
                    interval=settings.autopilot_executor_interval,
                    approval_ttl=settings.autopilot_approval_ttl,
                    stop=stop,
                ),
            ),
            TaskSpec(
                "data-job-reaper",
                lambda: data_job_reaper_loop(session_factory, settings=settings, stop=stop),
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

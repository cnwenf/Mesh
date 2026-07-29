"""RuntimeApp — the daemon root (design §3).

Wires heartbeat + claim scheduling + per-attempt supervision under one
TaskGroup and owns graceful shutdown: stop claiming FIRST, then finish or
terminate in-flight attempts inside the grace window (§3.1).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from mesh_runtime import PROTOCOL_VERSION, __version__
from mesh_runtime.api import ClaimResponse, RuntimeApiClient
from mesh_runtime.attempt import AttemptContext, AttemptSupervisor
from mesh_runtime.config import DaemonConfig
from mesh_runtime.errors import DaemonError
from mesh_runtime.heartbeat import HeartbeatLoop
from mesh_runtime.inventory import Inventory
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import ExecutorAdapter, RunRequest
from mesh_runtime.reconcile import reconcile_on_startup
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.scheduler import ClaimScheduler
from mesh_runtime.spool import DEFAULT_SPOOL_MAX_BYTES, LogSpool
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger("mesh_runtime")


@dataclass(frozen=True)
class RuntimeMetadata:
    hostname: str
    os_name: str


def build_run_request(claim: ClaimResponse) -> RunRequest:
    """Assemble the frozen prompt layers from the claim (spec §8.1).

    Trusted system instructions come from the frozen snapshot; everything
    task-specific is placed in the UNTRUSTED field so it can never be parsed
    as instructions (§3.7 S-09).
    """
    snapshot = claim.config_snapshot
    execution = claim.execution
    system_parts = []
    system_instructions = snapshot.get("system_instructions")
    if isinstance(system_instructions, str) and system_instructions:
        system_parts.append(system_instructions)
    untrusted_parts = []
    for key in ("input", "trigger_summary", "issue_excerpt"):
        value = execution.get(key)
        if isinstance(value, str) and value:
            untrusted_parts.append(value)
    budget = snapshot.get("budget") or {}
    max_budget = budget.get("usd") if isinstance(budget, dict) else None
    return RunRequest(
        attempt_id=claim.attempt_id,
        system_prompt="\n\n".join(system_parts),
        untrusted_context="\n\n".join(untrusted_parts),
        max_turns=int((budget.get("turns") if isinstance(budget, dict) else None) or 0),
        max_budget_usd=str(max_budget or "0.000000"),
        tools_allowlist=(),
    )


class RuntimeApp:
    def __init__(
        self,
        config: DaemonConfig,
        api: RuntimeApiClient,
        journal: Journal,
        inventory: Inventory,
        adapters: list[ExecutorAdapter],
        *,
        clock: Clock | None = None,
        metadata: RuntimeMetadata | None = None,
        redaction_secrets: list[str] | None = None,
        rule_version: str = "redaction-v1",
    ) -> None:
        self.config = config
        self._api = api
        self._journal = journal
        self._inventory = inventory
        self._adapters = adapters
        self._clock = clock or SystemClock()
        self._metadata = metadata
        self._redaction_secrets = redaction_secrets or []
        self._rule_version = rule_version
        self._supervisors: dict[str, AttemptSupervisor] = {}
        self._contexts: dict[str, AttemptContext] = {}
        self._attempt_tasks: dict[str, asyncio.Task] = {}
        self._shutdown = asyncio.Event()
        self._runtime_id: str | None = None

    # -- runtime id ---------------------------------------------------------

    def set_runtime_id(self, runtime_id: str) -> None:
        self._runtime_id = runtime_id

    # -- main loop ----------------------------------------------------------

    async def run(self) -> None:
        if self._runtime_id is None:
            raise RuntimeError("runtime id not set — activate first")
        await reconcile_on_startup(self._journal, self._api, self._runtime_id)

        heartbeat = HeartbeatLoop(
            self._api,
            self._runtime_id,
            interval_seconds=self.config.heartbeat_interval_seconds,
            inventory=self._inventory,
            clock=self._clock,
            inflight_source=lambda: list(self._supervisors.keys()),
            on_cancel=self._handle_cancel,
        )
        scheduler = ClaimScheduler(
            self._api,
            self._runtime_id,
            max_concurrent=self.config.max_concurrent,
            clock=self._clock,
            on_claimed=self._spawn_attempt,
        )

        async with asyncio.TaskGroup() as group:
            hb_task = group.create_task(heartbeat.run(self._shutdown), name="heartbeat")
            claim_task = group.create_task(scheduler.run(self._shutdown), name="claim")
            await self._shutdown.wait()
            # Graceful drain (§3.1): stop claiming first, then in-flight work.
            scheduler.request_stop()
            await self._drain_supervisors()
            heartbeat.request_stop()
            claim_task.cancel()
            hb_task.cancel()
            for task in (claim_task, hb_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # -- attempt spawning ---------------------------------------------------

    async def _spawn_attempt(self, claim: ClaimResponse) -> None:
        """Runs inside the scheduler's per-claim task; awaiting the full
        attempt lifecycle here keeps the scheduler's concurrency slot held
        until the attempt is terminal."""
        attempt_id = claim.attempt_id
        ctx = AttemptContext(
            attempt_id=attempt_id,
            execution_id=claim.execution_id,
            runtime_id=self._runtime_id or "",
            lease_seq=claim.lease_seq,
            work_dir=str(self.config.work_dir / claim.execution_id / attempt_id),
        )
        redactor = RedactionPipeline(secrets=self._redaction_secrets, rule_version=self._rule_version)
        # Per-attempt tmpfs spool: redacted batches are durable BEFORE upload and
        # only cleared on server ack, so a crash/network blip never loses logs
        # (§3.9.3). Scoped to a subdirectory so the frozen cap is per attempt.
        spool = LogSpool(
            self.config.spool_dir / attempt_id, max_bytes=DEFAULT_SPOOL_MAX_BYTES
        )
        logs = LogUploader(self._api, self._journal, redactor, clock=self._clock, spool=spool)
        adapter = self._select_adapter()
        supervisor = AttemptSupervisor(
            self._api,
            self._journal,
            logs,
            self._clock,
            provider_name=adapter.name,
            rule_version=self._rule_version,
        )
        self._supervisors[attempt_id] = supervisor
        self._contexts[attempt_id] = ctx
        current = asyncio.current_task()
        if current is not None:
            self._attempt_tasks[attempt_id] = current
        try:
            outcome = await supervisor.supervise(ctx, adapter, build_run_request(claim))
            if outcome.terminal_reported:
                await self._journal.delete(attempt_id)  # only after confirmed terminal (§3.6)
                await logs.drain_attempt(attempt_id)  # drop residual spooled batches
            logger.info("attempt %s finished: %s", attempt_id, outcome.status)
        finally:
            self._supervisors.pop(attempt_id, None)
            self._contexts.pop(attempt_id, None)
            self._attempt_tasks.pop(attempt_id, None)

    def _select_adapter(self) -> ExecutorAdapter:
        if not self._adapters:
            raise RuntimeError("no provider adapters registered")
        return self._adapters[0]

    # -- cancel + drain -----------------------------------------------------

    async def _handle_cancel(self, attempt_id: str, grace_seconds: float) -> None:
        supervisor = self._supervisors.get(attempt_id)
        ctx = self._contexts.get(attempt_id)
        if supervisor is None or ctx is None:
            return  # unknown / already finished — cancel commands may repeat
        # Fenced: reports a ``cancelled`` terminal, then tears the provider down.
        await supervisor.stop(ctx)

    async def _drain_supervisors(self) -> None:
        grace = self.config.shutdown_grace_seconds
        for attempt_id, supervisor in list(self._supervisors.items()):
            ctx = self._contexts.get(attempt_id)
            if ctx is None:
                continue
            try:
                await asyncio.wait_for(supervisor.stop(ctx), timeout=grace)
            except (TimeoutError, DaemonError):
                logger.warning("graceful stop timed out for attempt %s", attempt_id)
        tasks = list(self._attempt_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def heartbeat_metadata(config: DaemonConfig, inventory: Inventory) -> dict:
    """Diagnostics reported at activation / heartbeat (never secrets)."""
    import os
    import platform

    return {
        "daemon_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "inventory_hash": inventory.inventory_hash(),
        "inventory": inventory.heartbeat_payload(),
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_cores": os.cpu_count() or 0,
        "max_concurrent": config.max_concurrent,
    }

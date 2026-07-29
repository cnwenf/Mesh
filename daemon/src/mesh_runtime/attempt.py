"""AttemptSupervisor — the per-attempt control loop (spec §3.1 / design §6).

Owns exactly one attempt: the lease-renew loop, the provider lifecycle, log
relay and the fenced terminal report. ``lease_seq`` advances under a single
lock shared with the log uploader, so no coroutine ever reports with a stale
value. A 409 (lease mismatch / attempt terminal) is terminal: kill the
provider, stop ALL reporting, and let the server's reaper own the truth — the
daemon never tries to "fix" the server.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.errors import DaemonError, LeaseConflictError
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import (
    FinalResult,
    RunRequest,
    SessionStarted,
    TextDelta,
    UsageObserved,
)
from mesh_runtime.result import TERMINATIONS, Usage, build_result
from mesh_runtime.timeutil import Clock, SystemClock

_MAX_RENEW_FAILURES = 3
_DEFAULT_LEASE_SECONDS = 120.0
_SUMMARY_MAX = 4096


@dataclass
class AttemptContext:
    """Mutable lease state shared (under ``lock``) with the log uploader."""

    attempt_id: str
    execution_id: str
    runtime_id: str
    lease_seq: int
    lease_seconds: float = _DEFAULT_LEASE_SECONDS
    work_dir: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class AttemptOutcome:
    attempt_id: str
    status: str
    failure_reason: str | None = None
    terminal_reported: bool = False
    lease_lost: bool = False


class AttemptSupervisor:
    def __init__(
        self,
        api: RuntimeApiClient,
        journal: Journal,
        logs: LogUploader,
        clock: Clock | None = None,
        *,
        provider_name: str = "fake",
        provider_version: str = "0.0.0-fake",
        model: str = "fake-model",
        rule_version: str = "redaction-v1",
        max_renew_failures: int = _MAX_RENEW_FAILURES,
    ) -> None:
        self._api = api
        self._journal = journal
        self._logs = logs
        self._clock = clock or SystemClock()
        self._provider_name = provider_name
        self._provider_version = provider_version
        self._model = model
        self._rule_version = rule_version
        self._max_renew_failures = max_renew_failures

        self._provider_done = False
        self._provider_task: asyncio.Task | None = None
        self._renew_task: asyncio.Task | None = None
        self._iter = None
        self._stopped = False
        self._lease_lost = False
        self._terminal_reported = False
        self._attempt_id = ""
        self._outcome: AttemptOutcome | None = None
        self._done = asyncio.Event()

    def renew_period(self, lease_seconds: float) -> float:
        return min(lease_seconds / 3.0, 40.0)

    # -- lifecycle ----------------------------------------------------------

    async def supervise(self, ctx: AttemptContext, provider, request: RunRequest) -> AttemptOutcome:
        self._attempt_id = ctx.attempt_id
        async with ctx.lock:
            await self._journal.put(
                ctx.attempt_id,
                execution_id=ctx.execution_id,
                runtime_id=ctx.runtime_id,
                lease_seq=ctx.lease_seq,
                status="claimed",
                work_dir=ctx.work_dir,
            )
        await self._report_running(ctx)
        if self._done.is_set():  # lost the lease before the provider started
            return self._finish()
        self._iter = provider.run(request).__aiter__()
        self._provider_task = asyncio.create_task(self._run_provider(ctx))
        self._renew_task = asyncio.create_task(self._renew_loop(ctx))
        await self._done.wait()
        return self._finish()

    async def stop(self, ctx: AttemptContext) -> AttemptOutcome:
        """Graceful cancellation (heartbeat downlink): report a fenced
        ``cancelled`` terminal, THEN tear the provider down."""
        self._stopped = True
        await self._report_cancelled(ctx)
        self._teardown()
        self._done.set()
        return self._finish()

    async def wait(self) -> AttemptOutcome:
        await self._done.wait()
        return self._finish()

    def _finish(self) -> AttemptOutcome:
        if self._outcome is None:
            self._outcome = AttemptOutcome(self._attempt_id, "failed", "unknown")
        return self._outcome

    def _teardown(self) -> None:
        self._provider_done = True
        if self._renew_task is not None:
            self._renew_task.cancel()
        if self._provider_task is not None:
            self._provider_task.cancel()

    # -- lease --------------------------------------------------------------

    async def _renew_loop(self, ctx: AttemptContext) -> None:
        consecutive_failures = 0
        try:
            while not self._provider_done and not self._stopped and not self._lease_lost:
                await self._clock.sleep(self.renew_period(ctx.lease_seconds))
                if self._provider_done or self._stopped or self._lease_lost:
                    break
                try:
                    async with ctx.lock:
                        info = await self._api.renew_lease(ctx.attempt_id, lease_seq=ctx.lease_seq)
                        ctx.lease_seq = info.lease_seq
                    consecutive_failures = 0
                except LeaseConflictError:
                    await self._on_lease_lost(ctx)
                    return
                except DaemonError:
                    consecutive_failures += 1
                    if consecutive_failures >= self._max_renew_failures:
                        await self._on_lease_lost(ctx)
                        return
        except asyncio.CancelledError:
            raise

    async def _on_lease_lost(self, ctx: AttemptContext) -> None:
        # Fencing: kill the provider, stop reporting, leave the attempt to the
        # server reaper (§3.1 / design §6.3). Do NOT write a terminal status.
        # Called from the renew loop, so we must NOT cancel that task here —
        # it returns right after; cancelling only the provider avoids a
        # self-cancel that would interrupt this method before _done is set.
        if self._done.is_set():
            return  # another path already went terminal
        self._lease_lost = True
        self._provider_done = True
        if self._provider_task is not None:
            self._provider_task.cancel()
        async with ctx.lock:
            await self._journal.update(ctx.attempt_id, status="lease_lost")
        self._outcome = AttemptOutcome(
            ctx.attempt_id, status="failed", failure_reason="lease_lost",
            terminal_reported=False, lease_lost=True,
        )
        self._done.set()

    # -- provider -----------------------------------------------------------

    async def _run_provider(self, ctx: AttemptContext) -> None:
        session_id: str | None = None
        usage = Usage(0, 0, 0, 0, 0, "0.000000")
        summary = ""
        exit_code = 0
        hit_count = 0
        try:
            while True:
                event = await self._iter.__anext__()
                if isinstance(event, SessionStarted):
                    session_id = event.session_id
                elif isinstance(event, TextDelta):
                    ack = await self._logs.submit(ctx, "stdout", event.text)
                    hit_count += ack.redacted_hits
                elif isinstance(event, UsageObserved):
                    usage = Usage(
                        event.input_tokens, event.cache_creation_tokens,
                        event.cache_read_tokens, event.output_tokens, 1, event.cost_usd,
                    )
                elif isinstance(event, FinalResult):
                    summary = event.summary
                    exit_code = event.exit_code
        except StopAsyncIteration:
            pass
        except asyncio.CancelledError:
            # Torn down by stop() (cancel already reported) or lease-loss
            # (no report by design). Either way: stop cleanly, no report here.
            # Do NOT cancel the renew task — the renew loop stops on its own
            # condition (provider_done / lease_lost) or via stop()'s teardown.
            self._provider_done = True
            return
        except DaemonError as exc:
            self._provider_done = True
            await self._finalize(
                ctx,
                AttemptOutcome(ctx.attempt_id, "failed", _short_reason(exc)),
                session_id, usage, "", exit_code, hit_count,
            )
            return
        finally:
            self._provider_done = True

        status = "completed" if exit_code == 0 else "failed"
        await self._finalize(
            ctx,
            AttemptOutcome(
                ctx.attempt_id, status,
                None if exit_code == 0 else "nonzero_exit",
            ),
            session_id, usage, summary, exit_code, hit_count,
        )

    async def _finalize(self, ctx, outcome, session_id, usage, summary, exit_code, hit_count) -> None:
        if self._done.is_set():  # already terminal (cancel / lease-loss won the race)
            return
        try:
            await self._logs.flush(ctx, sealed=True)
        except LeaseConflictError:
            await self._mark_lease_lost(ctx, outcome.status)
            return
        except DaemonError:
            # A sealed flush only raises on a transient (non-lease) failure or
            # spool backpressure; the redacted batch is retained in the spool
            # (§3.9.3) and the server's log offset — not this flush — stays the
            # authority. Report the terminal state rather than lose the result.
            pass
        await self._report_terminal(ctx, outcome, session_id, usage, summary, exit_code, hit_count)
        self._stop_renew()

    def _stop_renew(self) -> None:
        if self._renew_task is not None:
            self._renew_task.cancel()

    # -- reporting ----------------------------------------------------------

    async def _report_running(self, ctx: AttemptContext) -> None:
        if self._stopped or self._done.is_set():
            return  # cancelled before we even started — do not report running
        try:
            async with ctx.lock:
                await self._api.transition(ctx.attempt_id, lease_seq=ctx.lease_seq, status="running")
                await self._journal.update(ctx.attempt_id, status="running")
        except LeaseConflictError:
            await self._on_lease_lost(ctx)

    async def _report_cancelled(self, ctx: AttemptContext) -> None:
        if self._terminal_reported or self._lease_lost:
            return
        await self._send_terminal(ctx, "cancelled", "cancelled", result=None)

    async def _report_terminal(self, ctx, outcome, session_id, usage, summary, exit_code, hit_count) -> None:
        status = outcome.status if outcome.status in TERMINATIONS else "failed"
        result = build_result(
            provider=self._provider_name,
            version=self._provider_version,
            model=self._model,
            session_id=session_id,
            usage=usage,
            exit_code=exit_code,
            summary=summary[:_SUMMARY_MAX],
            termination=status,
            checkout_id=None,
            diff_ref=None,
            rule_version=self._rule_version,
            hit_count=hit_count,
        )
        await self._send_terminal(ctx, status, outcome.failure_reason, result=result)

    async def _send_terminal(self, ctx, status, failure_reason, *, result) -> None:
        if self._terminal_reported or self._lease_lost:
            return  # already terminal — cancel/completion raced; first writer wins
        try:
            async with ctx.lock:
                if self._terminal_reported or self._lease_lost:
                    return
                await self._api.transition(
                    ctx.attempt_id, lease_seq=ctx.lease_seq, status=status,
                    result=result, failure_reason=failure_reason,
                )
                await self._journal.update(ctx.attempt_id, status="terminal_reported")
                self._terminal_reported = True
            self._outcome = AttemptOutcome(
                ctx.attempt_id, status=status, failure_reason=failure_reason,
                terminal_reported=True, lease_lost=False,
            )
            self._done.set()
        except LeaseConflictError:
            await self._mark_lease_lost(ctx, status)

    async def _mark_lease_lost(self, ctx, status: str) -> None:
        self._lease_lost = True
        async with ctx.lock:
            await self._journal.update(ctx.attempt_id, status="lease_lost")
        self._outcome = AttemptOutcome(
            ctx.attempt_id, status=status, failure_reason="lease_lost",
            terminal_reported=False, lease_lost=True,
        )
        self._done.set()


def _short_reason(exc: Exception) -> str:
    return type(exc).__name__[:64]

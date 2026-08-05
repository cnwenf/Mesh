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
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.backoff import capped_retry_after
from mesh_runtime.errors import DaemonError, LeaseConflictError, RateLimitedError
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import (
    FinalResult,
    RunRequest,
    SessionStarted,
    TextDelta,
    UsageObserved,
)
from mesh_runtime.providers.sandboxed import SandboxLaunchError
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.result import TERMINATIONS, Usage, build_result
from mesh_runtime.timeutil import Clock, SystemClock

logger = logging.getLogger("mesh_runtime.attempt")

_MAX_RENEW_FAILURES = 3
_DEFAULT_LEASE_SECONDS = 120.0
_SUMMARY_MAX = 4096

# Terminal sealed-flush retry envelope (§3.9.3): transient 5xx / rate limits
# almost always clear in seconds, so retry with a capped backoff before
# demoting the attempt. Retry-After from the server is honoured but capped so
# a hostile/misconfigured server cannot park the attempt for hours.
_SEALED_FLUSH_RETRIES = 3
_SEALED_FLUSH_BACKOFF_BASE = 0.5
_SEALED_FLUSH_BACKOFF_CAP = 5.0


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
        security=None,  # AttemptSecurity | None — A2 isolation stack
        redactor=None,  # RedactionPipeline for diff/summary redaction
        on_operational_incident: Callable[[str], None] | None = None,
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
        self._security = security
        self._redactor = redactor
        self._on_operational_incident = on_operational_incident

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
        self._cleaned = False
        self._spool_flushed = True

    def renew_period(self, lease_seconds: float) -> float:
        return min(lease_seconds / 3.0, 40.0)

    @property
    def spool_flushed(self) -> bool:
        """Whether the terminal sealed flush completed. False means redacted
        batches are still spooled on disk and the journal row must be kept for
        startup replay (§3.9.3)."""
        return self._spool_flushed

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
        # §3.1 order: checkout → egress → broker BEFORE the provider starts.
        if self._security is not None:
            try:
                async with ctx.lock:
                    seq = ctx.lease_seq
                await self._security.start(lease_seq=seq)
            except DaemonError:
                self._signal_operational_incident("sandbox_security_failed")
                await self._finalize(
                    ctx,
                    AttemptOutcome(ctx.attempt_id, "failed", "executor_unavailable"),
                    None, Usage(0, 0, 0, 0, 0, "0.000000"), "", 1, 0,
                )
                await self._cleanup()
                return self._finish()
        await self._report_running(ctx)
        if self._done.is_set():  # lost the lease before the provider started
            await self._cleanup()
            return self._finish()
        self._iter = provider.run(request).__aiter__()
        self._provider_task = asyncio.create_task(self._run_provider(ctx))
        self._renew_task = asyncio.create_task(self._renew_loop(ctx))
        await self._done.wait()
        await self._cleanup()
        return self._finish()

    async def stop(self, ctx: AttemptContext) -> AttemptOutcome:
        """Graceful cancellation (heartbeat downlink): report a fenced
        ``cancelled`` terminal, THEN tear the provider down."""
        self._stopped = True
        await self._report_cancelled(ctx)
        self._teardown()
        self._done.set()
        await self._cleanup()
        return self._finish()

    async def escalate_confirm_required(
        self, ctx: AttemptContext, action: str, params: dict, resume_context: dict | None = None
    ) -> AttemptOutcome:
        """§3.3 confirm_required protocol: ask the server to cancel THIS
        attempt as awaiting_approval (lease ends, capacity released, tokens
        revoked server-side); an approved NEW attempt resumes via
        resume_context. The privileged sandbox is never parked."""
        if self._security is None:
            return self._finish()
        async with ctx.lock:
            seq = ctx.lease_seq
        await self._security.request_approval(
            lease_seq=seq, action=action, params=params, resume_context=resume_context
        )
        self._stopped = True
        self._terminal_reported = True  # server owns the state now — no report
        self._outcome = AttemptOutcome(
            ctx.attempt_id, "cancelled", "awaiting_approval", terminal_reported=True
        )
        self._teardown()
        self._done.set()
        await self._cleanup()
        return self._finish()

    async def _cleanup(self) -> None:
        if self._security is None or self._cleaned:
            return
        self._cleaned = True
        try:
            report = await self._security.finish(spool_flushed=self._spool_flushed)
            if not report.ok:
                self._signal_operational_incident("cleanup_failed")
                logger.warning(
                    "attempt %s cleanup incomplete: %s", self._attempt_id, report.failures
                )
        except Exception as exc:  # noqa: BLE001 — cleanup failure must isolate, not escape
            self._signal_operational_incident("cleanup_failed")
            logger.warning("attempt %s cleanup failed: %s", self._attempt_id, type(exc).__name__)

    def _signal_operational_incident(self, reason_code: str) -> None:
        if self._on_operational_incident is not None:
            self._on_operational_incident(reason_code)

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
                    await self._apply_token_rotation(info)
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

    async def _apply_token_rotation(self, info) -> None:
        """§2.2/§2.6: renew-lease rotates the task token in the SAME server
        transaction — the OLD plaintext is revoked there, so the broker must
        switch to the fresh token or every gated call answers 401 for the
        rest of the attempt; the rotated plaintext also joins the redaction
        pipeline (defense in depth, §5.4.7)."""
        token = getattr(info, "task_token", None)
        if not token:
            return
        if self._security is not None and self._security.broker is not None:
            await self._security.broker.rotate_task_token(token)
        if self._redactor is not None:
            self._redactor.add_secret(token)

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
        termination_hint = ""
        try:
            while True:
                event = await self._iter.__anext__()
                if isinstance(event, SessionStarted):
                    session_id = event.session_id
                elif isinstance(event, TextDelta):
                    ack = await self._logs.submit(ctx, "stdout", event.text)
                    hit_count += ack.redacted_hits
                elif isinstance(event, UsageObserved):
                    observed = Usage(
                        event.input_tokens, event.cache_creation_tokens,
                        event.cache_read_tokens, event.output_tokens,
                        event.turns, event.cost_usd,
                    )
                    try:
                        observed._validate()
                    except ValueError:
                        self._signal_operational_incident("usage_invariant_failed")
                        self._provider_done = True
                        await self._finalize(
                            ctx,
                            AttemptOutcome(
                                ctx.attempt_id,
                                "failed",
                                "executor_unavailable",
                            ),
                            session_id,
                            usage,
                            "",
                            1,
                            hit_count,
                        )
                        return
                    if _usage_regressed(usage, observed):
                        self._signal_operational_incident("usage_invariant_failed")
                        self._provider_done = True
                        await self._finalize(
                            ctx,
                            AttemptOutcome(
                                ctx.attempt_id,
                                "failed",
                                "executor_unavailable",
                            ),
                            session_id,
                            usage,
                            "",
                            1,
                            hit_count,
                        )
                        return
                    usage = observed
                elif isinstance(event, FinalResult):
                    summary = event.summary
                    exit_code = event.exit_code
                    if event.termination in ("budget_exceeded", "timeout"):
                        termination_hint = event.termination
        except StopAsyncIteration:
            pass
        except asyncio.CancelledError:
            # Torn down by stop() (cancel already reported) or lease-loss
            # (no report by design). Either way: stop cleanly, no report here.
            # Do NOT cancel the renew task — the renew loop stops on its own
            # condition (provider_done / lease_lost) or via stop()'s teardown.
            self._provider_done = True
            return
        except SandboxLaunchError:
            # fail-closed red line: the sandbox could not be provisioned or
            # verified — report sandbox_violation, NEVER run bare (§5.2).
            self._provider_done = True
            self._signal_operational_incident("sandbox_security_failed")
            await self._finalize(
                ctx,
                AttemptOutcome(ctx.attempt_id, "failed", "sandbox_violation"),
                session_id, usage, "", 1, hit_count,
            )
            return
        except DaemonError as exc:
            self._provider_done = True
            await self._finalize(
                ctx,
                AttemptOutcome(ctx.attempt_id, "failed", _short_reason(exc)),
                session_id, usage, "", exit_code, hit_count,
            )
            return
        except Exception as exc:  # noqa: BLE001 — fail closed, never hang
            # Any unexpected provider/transport error (e.g. a stdio ValueError,
            # BrokenPipe, or adapter bug) must still terminate the attempt and
            # set ``_done`` — otherwise supervise() blocks forever and, with a
            # single slot, wedges the daemon until the server reaper reclaims
            # the lease. Report a fenced failed terminal.
            logger.exception("attempt %s provider crashed", ctx.attempt_id)
            self._provider_done = True
            await self._finalize(
                ctx,
                AttemptOutcome(ctx.attempt_id, "failed", _short_reason(exc)),
                session_id, usage, "", exit_code or 1, hit_count,
            )
            return
        finally:
            self._provider_done = True

        if termination_hint:
            # S-07 / §3.5: the adapter truncated the provider — the frozen
            # budget vocabulary lands in failure_reason AND result.termination.
            await self._finalize(
                ctx,
                AttemptOutcome(ctx.attempt_id, "failed", termination_hint),
                session_id, usage, summary, exit_code, hit_count,
            )
            return
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
        except DaemonError as exc:
            try:
                flushed = await self._retry_sealed_flush(ctx, exc)
            except LeaseConflictError:
                await self._mark_lease_lost(ctx, outcome.status)
                return
            if not flushed:
                # Retries exhausted: the redacted batch is retained in the
                # spool (§3.9.3 — cleanup keeps it for diagnostics), but the
                # log stream could not be completed and sealed. NEVER certify
                # a successful run on incomplete logs — demote the terminal
                # state with a fixed reason code instead.
                self._spool_flushed = False
                outcome = AttemptOutcome(ctx.attempt_id, "failed", "log_flush_failed")
        await self._report_terminal(ctx, outcome, session_id, usage, summary, exit_code, hit_count)
        self._stop_renew()

    async def _retry_sealed_flush(self, ctx, first_error: DaemonError) -> bool:
        """Bounded retry of the terminal sealed flush (§3.9.3). Returns True
        once the flush succeeds, False when retries are exhausted. Lease
        fencing is NOT swallowed — it propagates so ``_finalize`` can map it
        to lease_lost."""
        delay = _sealed_flush_delay(first_error, attempt=0)
        for attempt in range(1, _SEALED_FLUSH_RETRIES + 1):
            await self._clock.sleep(delay)
            try:
                await self._logs.flush(ctx, sealed=True)
                return True
            except LeaseConflictError:
                raise
            except DaemonError as exc:
                delay = _sealed_flush_delay(exc, attempt=attempt)
        return False

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
        redactor = self._redactor or RedactionPipeline(secrets=[], rule_version=self._rule_version)
        summary = redactor.redact(summary).text  # §2.5: result channel is redacted too
        checkout_id = None
        diff_ref = None
        if self._security is not None:
            hit_count += await self._security.export_diff(lease_seq=ctx.lease_seq, redactor=redactor)
            checkout_id = self._security.checkout_id
            diff_ref = self._security.diff_ref
        # result.outcome.termination uses the precise frozen vocabulary when
        # the failure reason carries it (budget_exceeded/timeout/sandbox_-
        # violation/lease_lost — §3.9); otherwise it mirrors the status.
        reason = outcome.failure_reason
        termination = reason if reason in TERMINATIONS else status
        result = build_result(
            provider=self._provider_name,
            version=self._provider_version,
            model=self._model,
            session_id=session_id,
            usage=usage,
            exit_code=exit_code,
            summary=summary[:_SUMMARY_MAX],
            termination=termination,
            checkout_id=checkout_id,
            diff_ref=diff_ref,
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


def _usage_regressed(previous: Usage, current: Usage) -> bool:
    """Provider usage frames are cumulative; counters may never decrease."""
    count_fields = (
        "input_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "output_tokens",
        "turns",
    )
    if any(getattr(current, field) < getattr(previous, field) for field in count_fields):
        return True
    try:
        return Decimal(current.cost_usd) < Decimal(previous.cost_usd)
    except InvalidOperation:
        return True


def _sealed_flush_delay(exc: DaemonError, *, attempt: int) -> float:
    """Delay before sealed-flush retry ``attempt`` (0-based). Honours a
    server-provided Retry-After, capped at a minute so a hostile or
    misconfigured server cannot park the terminal flush indefinitely;
    otherwise capped exponential backoff."""
    if isinstance(exc, RateLimitedError):
        return capped_retry_after(exc.retry_after)
    return min(_SEALED_FLUSH_BACKOFF_BASE * (2**attempt), _SEALED_FLUSH_BACKOFF_CAP)

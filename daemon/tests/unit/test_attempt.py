import asyncio

import pytest

from mesh_runtime.api import LeaseInfo, LogAck
from mesh_runtime.attempt import AttemptContext, AttemptSupervisor
from mesh_runtime.errors import DaemonError, LeaseConflictError, ServerError
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import FinalResult, RunRequest, SessionStarted, TextDelta, UsageObserved
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.timeutil import FakeClock


class StubApi:
    def __init__(self, fail_terminal=False):
        self.transitions = []
        self.renews = []
        self.fail_terminal = fail_terminal

    async def transition(self, attempt_id, *, lease_seq, status, result=None, failure_reason=None):
        self.transitions.append(dict(status=status, lease_seq=lease_seq, failure_reason=failure_reason))
        if self.fail_terminal and status not in ("running",):
            raise LeaseConflictError("409", code="attempt_terminal")
        return {}

    async def renew_lease(self, attempt_id, *, lease_seq):
        self.renews.append(lease_seq)
        raise LeaseConflictError("409", code="lease_seq_mismatch")

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        end = start_offset + sum(len(line.encode()) for line in lines)
        return LogAck(accepted_end_offset=end, redacted_hits=0)


class RenewOkApi(StubApi):
    async def renew_lease(self, attempt_id, *, lease_seq):
        self.renews.append(lease_seq)
        return LeaseInfo(lease_seq=lease_seq + 1, lease_expires_at="t")


class BlockingProvider:
    name = "blocking"

    def __init__(self, gate, events_before=()):
        self.gate = gate
        self.events_before = list(events_before)

    async def run(self, request):
        for ev in self.events_before:
            yield ev
        await self.gate.wait()
        yield FinalResult(summary="done", exit_code=0)


def run_request():
    return RunRequest(
        attempt_id="att-1", system_prompt="sys", untrusted_context="ctx",
        max_turns=1, max_budget_usd="0.010000", tools_allowlist=(),
    )


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


@pytest.fixture
def ctx():
    return AttemptContext(attempt_id="att-1", execution_id="exec-1", runtime_id="rt-1", lease_seq=1)


def make_supervisor(api, journal, clock, **kw):
    redactor = RedactionPipeline(secrets=[], rule_version="v1")
    logs = LogUploader(api, journal, redactor, clock=clock)
    return AttemptSupervisor(api, journal, logs, clock, **kw)


async def drain():
    for _ in range(20):
        await asyncio.sleep(0)


class TestSupervise:
    async def test_completed_happy_path(self, journal, ctx):
        clock = FakeClock()
        api = RenewOkApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield SessionStarted(session_id="sess-1", model="fake-model")
                yield TextDelta(text="working")
                yield UsageObserved(input_tokens=10, output_tokens=5, cost_usd="0.001000")
                yield FinalResult(summary="all done", exit_code=0)

        sup = make_supervisor(api, journal, clock)
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "completed"
        assert outcome.terminal_reported is True
        assert outcome.lease_lost is False
        statuses = [t["status"] for t in api.transitions]
        assert statuses == ["running", "completed"]
        # journal marked terminal (then app would delete it)
        entry = await journal.get("att-1")
        assert entry.status == "terminal_reported"

    async def test_nonzero_exit_reports_failed(self, journal, ctx):
        api = RenewOkApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield FinalResult(summary="boom", exit_code=1)

        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "failed"
        assert outcome.failure_reason == "nonzero_exit"
        assert [t["status"] for t in api.transitions] == ["running", "failed"]

    async def test_provider_daemon_error_reports_failed(self, journal, ctx):
        api = RenewOkApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield SessionStarted(session_id="s", model="m")
                raise DaemonError("provider crashed")
                yield  # pragma: no cover

        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "failed"
        assert [t["status"] for t in api.transitions] == ["running", "failed"]

    async def test_lease_lost_during_renew_no_terminal(self, journal, ctx):
        api = StubApi()  # renew raises lease_seq_mismatch
        gate = asyncio.Event()  # never released -> provider blocks
        provider = BlockingProvider(gate, events_before=[SessionStarted("s", "m")])
        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, provider, run_request())
        assert outcome.lease_lost is True
        assert outcome.terminal_reported is False
        # only the running transition happened; no terminal written (§6.3)
        assert [t["status"] for t in api.transitions] == ["running"]
        entry = await journal.get("att-1")
        assert entry.status == "lease_lost"

    async def test_stop_reports_cancelled(self, journal, ctx):
        api = RenewOkApi()
        gate = asyncio.Event()
        provider = BlockingProvider(gate, events_before=[SessionStarted("s", "m")])
        sup = make_supervisor(api, journal, FakeClock())
        task = asyncio.create_task(sup.supervise(ctx, provider, run_request()))
        # Wait until setup finished and 'running' was reported (deterministic,
        # unlike a fixed sleep count given to_thread journal latency).
        while not any(t["status"] == "running" for t in api.transitions):
            await asyncio.sleep(0)
        outcome = await sup.stop(ctx)
        await task
        assert outcome.status == "cancelled"
        assert outcome.terminal_reported is True
        assert [t["status"] for t in api.transitions] == ["running", "cancelled"]
        assert api.transitions[-1]["failure_reason"] is None

    async def test_terminal_409_becomes_lease_lost(self, journal, ctx):
        api = StubApi(fail_terminal=True)  # completed transition -> 409

        class Provider:
            name = "fake"

            async def run(self, request):
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.lease_lost is True
        assert outcome.terminal_reported is False
        entry = await journal.get("att-1")
        assert entry.status == "lease_lost"

    async def test_apply_token_rotation_pushes_token_to_broker_and_redactor(self):
        """§2.2/§2.6: the rotated task token from renew-lease must reach the
        broker (old token is revoked server-side) and the redactor."""

        class FakeBroker:
            def __init__(self):
                self.rotated: list[str] = []

            async def rotate_task_token(self, token: str) -> None:
                self.rotated.append(token)

        class FakeSecurity:
            def __init__(self, broker):
                self.broker = broker

        broker = FakeBroker()
        redactor = RedactionPipeline(secrets=["mesh_task_initial"], rule_version="v1")
        sup = make_supervisor(
            RenewOkApi(), journal, FakeClock(),
            security=FakeSecurity(broker), redactor=redactor,
        )
        await sup._apply_token_rotation(
            LeaseInfo(lease_seq=2, lease_expires_at="t", task_token="mesh_task_rotated_2")
        )
        assert broker.rotated == ["mesh_task_rotated_2"]
        assert redactor.redact("leak mesh_task_rotated_2 end").hit_count == 1

    async def test_apply_token_rotation_no_token_is_noop(self, journal):
        """A renew response without a fresh token leaves broker/redactor alone."""

        class FakeBroker:
            def __init__(self):
                self.rotated: list[str] = []

            async def rotate_task_token(self, token: str) -> None:
                self.rotated.append(token)

        class FakeSecurity:
            def __init__(self, broker):
                self.broker = broker

        broker = FakeBroker()
        redactor = RedactionPipeline(secrets=[], rule_version="v1")
        sup = make_supervisor(
            RenewOkApi(), journal, FakeClock(),
            security=FakeSecurity(broker), redactor=redactor,
        )
        await sup._apply_token_rotation(LeaseInfo(lease_seq=2, lease_expires_at="t"))
        assert broker.rotated == []

    async def test_apply_token_rotation_without_security_or_redactor(self, journal):
        """Degrades safely when the attempt has no broker / no redactor."""
        sup = make_supervisor(RenewOkApi(), journal, FakeClock())
        await sup._apply_token_rotation(
            LeaseInfo(lease_seq=2, lease_expires_at="t", task_token="mesh_task_x")
        )  # no exception — nothing to rotate into

    async def test_renew_period_capped(self, journal, ctx):
        sup = make_supervisor(RenewOkApi(), journal, FakeClock())
        assert sup.renew_period(120.0) == 40.0  # min(40, 40)
        assert sup.renew_period(30.0) == 10.0   # min(10, 40)

    async def test_renew_success_advances_lease_seq(self, journal, ctx):
        """attempt.py lease-maintenance happy path: a successful renew advances
        ``ctx.lease_seq`` under the shared lock so no report uses a stale value."""
        api = RenewOkApi()
        gate = asyncio.Event()
        provider = BlockingProvider(gate, events_before=[SessionStarted("s", "m")])
        sup = make_supervisor(api, journal, FakeClock())
        task = asyncio.create_task(sup.supervise(ctx, provider, run_request()))
        while not api.renews:  # wait for at least one successful renew
            await asyncio.sleep(0)
        assert ctx.lease_seq > 1  # advanced from the initial 1
        gate.set()
        outcome = await task
        assert outcome.status == "completed"
        assert outcome.lease_lost is False
        # exactly one advance per successful renew, under the lock
        assert ctx.lease_seq == 1 + len(api.renews)

    async def test_sealed_flush_transient_failure_recovers_via_retry(self, journal, ctx):
        """A transient (non-lease) failure of the terminal sealed flush is
        retried with a capped backoff (§3.9.3); once the relay recovers the
        attempt still reports its real outcome — the result is not lost and
        not demoted."""
        class FlakyOnceLogsApi(RenewOkApi):
            def __init__(self):
                super().__init__()
                self.append_calls = 0

            async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
                self.append_calls += 1
                if self.append_calls == 1:
                    raise ServerError("transient 5xx")
                return await super().append_logs(
                    attempt_id, lease_seq=lease_seq, stream=stream,
                    start_offset=start_offset, lines=lines, sealed=sealed,
                )

        api = FlakyOnceLogsApi()
        clock = FakeClock()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield TextDelta(text="some output")  # buffered; flushed sealed at finalize
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(api, journal, clock)
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "completed"
        assert outcome.failure_reason is None
        assert outcome.terminal_reported is True
        assert [t["status"] for t in api.transitions] == ["running", "completed"]
        # first sealed attempt failed, retry succeeded (+1 empty stderr sealed close)
        assert api.append_calls == 3
        assert clock.sleeps  # retry waited on the (fake) clock

    async def test_sealed_flush_persistent_failure_demotes_to_log_flush_failed(self, journal, ctx):
        """When the sealed flush keeps failing past the bounded retry envelope,
        the attempt must NOT be certified completed on incomplete/unsealed
        logs: the terminal state is demoted to failed/log_flush_failed (fixed
        reason code), and the failure is reported exactly once."""
        class DeadLogsApi(RenewOkApi):
            def __init__(self):
                super().__init__()
                self.append_calls = 0

            async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
                self.append_calls += 1
                raise ServerError("log relay down")

        api = DeadLogsApi()
        clock = FakeClock()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield TextDelta(text="some output")
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(api, journal, clock)
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "failed"
        assert outcome.failure_reason == "log_flush_failed"
        assert outcome.terminal_reported is True
        assert [t["status"] for t in api.transitions] == ["running", "failed"]
        terminal = [t for t in api.transitions if t["status"] == "failed"][0]
        assert terminal["failure_reason"] == "log_flush_failed"
        # initial attempt + bounded retries, never unbounded
        assert api.append_calls == 4  # 1 + _SEALED_FLUSH_RETRIES
        assert len([s for s in clock.sleeps if s > 0]) >= 3

    async def test_sealed_flush_lease_conflict_during_retry_is_lease_lost(self, journal, ctx):
        """Lease fencing raised mid-retry is NOT swallowed by the retry loop —
        it maps to lease_lost with no terminal report."""
        class FencingLogsApi(RenewOkApi):
            def __init__(self):
                super().__init__()
                self.append_calls = 0

            async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
                self.append_calls += 1
                if self.append_calls == 1:
                    raise ServerError("transient")
                raise LeaseConflictError("409", code="lease_seq_mismatch")

        api = FencingLogsApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield TextDelta(text="out")
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.lease_lost is True
        assert outcome.terminal_reported is False
        assert [t["status"] for t in api.transitions] == ["running"]

    async def test_renew_consecutive_daemon_errors_lease_lost(self, journal, ctx):
        class RenewServerErrorApi(StubApi):
            async def renew_lease(self, attempt_id, *, lease_seq):
                self.renews.append(lease_seq)
                raise ServerError("renew boom")

        api = RenewServerErrorApi()
        gate = asyncio.Event()
        provider = BlockingProvider(gate, events_before=[SessionStarted("s", "m")])
        sup = make_supervisor(api, journal, FakeClock(), max_renew_failures=3)
        outcome = await sup.supervise(ctx, provider, run_request())
        assert outcome.lease_lost is True
        assert outcome.terminal_reported is False
        assert len(api.renews) >= 3
        assert [t["status"] for t in api.transitions] == ["running"]

    async def test_wait_returns_outcome(self, journal, ctx):
        class Provider:
            name = "fake"

            async def run(self, request):
                yield FinalResult(summary="d", exit_code=0)

        sup = make_supervisor(RenewOkApi(), journal, FakeClock())
        task = asyncio.create_task(sup.supervise(ctx, Provider(), run_request()))
        outcome = await sup.wait()
        await task
        assert outcome.status == "completed"

    async def test_stop_after_completion_is_noop(self, journal, ctx):
        class Provider:
            name = "fake"

            async def run(self, request):
                yield FinalResult(summary="d", exit_code=0)

        api = RenewOkApi()
        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "completed"
        outcome2 = await sup.stop(ctx)  # already terminal -> no extra transition
        assert outcome2.status == "completed"
        assert [t["status"] for t in api.transitions] == ["running", "completed"]

    async def test_running_report_409_lease_lost_before_start(self, journal, ctx):
        class Running409Api(StubApi):
            async def renew_lease(self, attempt_id, *, lease_seq):
                return LeaseInfo(lease_seq + 1, "t")

        api = Running409Api()
        api.fail_terminal = True  # transition(non-running) raises; but here running also...

        # Make the RUNNING transition itself raise a lease conflict.
        async def running_conflict(attempt_id, *, lease_seq, status, result=None, failure_reason=None):
            raise LeaseConflictError("409", code="lease_seq_mismatch")

        api.transition = running_conflict
        provider_ran = []

        class Provider:
            name = "fake"

            async def run(self, request):
                provider_ran.append(1)
                yield FinalResult(summary="d", exit_code=0)

        sup = make_supervisor(api, journal, FakeClock())
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.lease_lost is True
        assert outcome.terminal_reported is False
        assert provider_ran == []  # provider never started


class TestTerminationMapping:
    """A3: precise frozen termination vocabulary (runtime-executor.md §3.9)."""

    async def _run_with_final(self, journal, ctx, final: FinalResult, *, usage=None):
        clock = FakeClock()
        api = RenewOkApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield SessionStarted(session_id="sess-9", model="m")
                if usage is not None:
                    yield usage
                yield final

        sup = make_supervisor(api, journal, clock)
        outcome = await sup.supervise(ctx, Provider(), run_request())
        terminal = next(t for t in api.transitions if t["status"] != "running")
        return outcome, terminal

    async def test_budget_exceeded_maps_to_failed_with_frozen_reason(self, journal, ctx):
        outcome, terminal = await self._run_with_final(
            journal, ctx,
            FinalResult(summary="truncated", exit_code=1, termination="budget_exceeded"),
        )
        assert outcome.status == "failed"
        assert terminal["failure_reason"] == "budget_exceeded"

    async def test_timeout_maps_to_failed_with_frozen_reason(self, journal, ctx):
        outcome, terminal = await self._run_with_final(
            journal, ctx,
            FinalResult(summary="wall", exit_code=124, termination="timeout"),
        )
        assert outcome.status == "failed"
        assert terminal["failure_reason"] == "timeout"

    async def test_plain_failure_still_reports_nonzero_exit(self, journal, ctx):
        outcome, terminal = await self._run_with_final(
            journal, ctx, FinalResult(summary="boom", exit_code=3)
        )
        assert outcome.status == "failed"
        assert terminal["failure_reason"] == "nonzero_exit"

    async def test_turns_flow_from_provider_usage_into_result(self, journal, ctx):
        reported = {}
        clock = FakeClock()

        class CapturingApi(RenewOkApi):
            async def transition(self, attempt_id, *, lease_seq, status, result=None,
                                 failure_reason=None):
                if result is not None:
                    reported.update(result)
                return await super().transition(
                    attempt_id, lease_seq=lease_seq, status=status,
                    result=result, failure_reason=failure_reason,
                )

        api = CapturingApi()

        class Provider:
            name = "fake"

            async def run(self, request):
                yield SessionStarted(session_id="sess-2", model="m")
                yield UsageObserved(
                    input_tokens=11, output_tokens=7, cost_usd="0.002000", turns=4
                )
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(api, journal, clock)
        outcome = await sup.supervise(ctx, Provider(), run_request())
        assert outcome.status == "completed"
        assert reported["usage"]["turns"] == 4
        assert reported["usage"]["total_tokens"] == 18
        assert reported["provider"]["session_id"] == "sess-2"
        assert reported["outcome"]["termination"] == "completed"


class TestUnexpectedProviderError:
    async def test_invalid_usage_isolates_runtime_and_fails_attempt(self, journal, ctx):
        api = RenewOkApi()
        incidents = []

        class InvalidUsageProvider:
            name = "invalid-usage"

            async def run(self, request):
                yield UsageObserved(input_tokens=-1, output_tokens=2, cost_usd="0.001000")
                yield FinalResult(summary="must not complete", exit_code=0)

        sup = make_supervisor(
            api,
            journal,
            FakeClock(),
            on_operational_incident=incidents.append,
        )

        outcome = await sup.supervise(ctx, InvalidUsageProvider(), run_request())

        assert outcome.status == "failed"
        assert outcome.failure_reason == "executor_unavailable"
        assert incidents == ["usage_invariant_failed"]

    async def test_cumulative_usage_regression_isolates_runtime(self, journal, ctx):
        api = RenewOkApi()
        incidents = []

        class RegressingUsageProvider:
            name = "regressing-usage"

            async def run(self, request):
                yield UsageObserved(
                    input_tokens=10,
                    output_tokens=4,
                    turns=2,
                    cost_usd="0.002000",
                )
                # HIGH-4: the monotonicity gate fires on the TERMINAL cumulative
                # frame only — mark it terminal so the regression is enforced.
                yield UsageObserved(
                    input_tokens=8,
                    output_tokens=4,
                    turns=1,
                    cost_usd="0.001000",
                    terminal=True,
                )
                yield FinalResult(summary="must not complete", exit_code=0)

        sup = make_supervisor(
            api,
            journal,
            FakeClock(),
            on_operational_incident=incidents.append,
        )

        outcome = await sup.supervise(ctx, RegressingUsageProvider(), run_request())

        assert outcome.status == "failed"
        assert outcome.failure_reason == "executor_unavailable"
        assert incidents == ["usage_invariant_failed"]

    async def test_per_message_usage_regression_does_not_isolate(self, journal, ctx):
        """HIGH-4 multi-turn negative: per-message frames may legitimately
        decrease mid-stream; only the terminal cumulative frame is gated, so a
        run whose terminal frame is consistent must complete (no isolation)."""
        reported = {}
        incidents = []

        class CapturingApi(RenewOkApi):
            async def transition(self, attempt_id, *, lease_seq, status, result=None,
                                 failure_reason=None):
                if result is not None:
                    reported.update(result)
                return await super().transition(
                    attempt_id, lease_seq=lease_seq, status=status,
                    result=result, failure_reason=failure_reason,
                )

        class MultiTurnProvider:
            name = "multi-turn-usage"

            async def run(self, request):
                yield SessionStarted(session_id="sess-mt", model="m")
                # Turn 1 reports more than turn 2's per-message frame — this is
                # legal on a multi-turn stream and must NOT isolate.
                yield UsageObserved(
                    input_tokens=10, output_tokens=4, turns=1, cost_usd="0.002000"
                )
                yield UsageObserved(
                    input_tokens=3, output_tokens=2, turns=1, cost_usd="0.000500"
                )
                # Terminal cumulative frame is consistent (non-regressing) → ok.
                yield UsageObserved(
                    input_tokens=13,
                    output_tokens=6,
                    turns=2,
                    cost_usd="0.002500",
                    terminal=True,
                )
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(
            CapturingApi(),
            journal,
            FakeClock(),
            on_operational_incident=incidents.append,
        )

        outcome = await sup.supervise(ctx, MultiTurnProvider(), run_request())

        assert outcome.status == "completed"
        assert incidents == []
        assert reported["usage"]["turns"] == 2

    async def test_cleanup_failure_isolates_runtime(self, journal, ctx):
        from mesh_runtime.cleanup import CleanupReport

        api = RenewOkApi()
        incidents = []

        class Security:
            async def start(self, *, lease_seq):
                return None

            async def finish(self, *, spool_flushed):
                return CleanupReport(
                    steps_done=["broker_closed"],
                    failures={"cgroup_killed": "OSError"},
                )

            async def export_diff(self, *, lease_seq, redactor):
                return 0

            checkout_id = None
            diff_ref = None

        class Provider:
            name = "fake"

            async def run(self, request):
                yield FinalResult(summary="done", exit_code=0)

        sup = make_supervisor(
            api,
            journal,
            FakeClock(),
            security=Security(),
            on_operational_incident=incidents.append,
        )

        await sup.supervise(ctx, Provider(), run_request())

        assert incidents == ["cleanup_failed"]

    async def test_unexpected_exception_terminates_not_hangs(self, journal, ctx):
        # HIGH #1: a non-DaemonError from the provider must still finalize the
        # attempt (set _done) — otherwise supervise() blocks forever.
        clock = FakeClock()
        api = RenewOkApi()

        class BoomProvider:
            name = "boom"

            async def run(self, request):
                yield SessionStarted(session_id="s", model="m")
                raise ValueError("transport exploded")

        sup = make_supervisor(api, journal, clock)
        outcome = await asyncio.wait_for(
            sup.supervise(ctx, BoomProvider(), run_request()), timeout=10
        )
        assert outcome.status == "failed"
        assert outcome.failure_reason == "ValueError"
        statuses = [t["status"] for t in api.transitions]
        assert statuses[-1] == "failed"

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

    async def test_renew_period_capped(self, journal, ctx):
        sup = make_supervisor(RenewOkApi(), journal, FakeClock())
        assert sup.renew_period(120.0) == 40.0  # min(40, 40)
        assert sup.renew_period(30.0) == 10.0   # min(10, 40)

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

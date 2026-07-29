import asyncio

import pytest

from mesh_runtime.api import ClaimResponse, HeartbeatResponse, LeaseInfo, LogAck
from mesh_runtime.app import RuntimeApp, build_run_request
from mesh_runtime.config import DaemonConfig
from mesh_runtime.inventory import Inventory
from mesh_runtime.journal import Journal
from mesh_runtime.providers.base import FinalResult, SessionStarted, TextDelta, UsageObserved
from mesh_runtime.providers.fake import FakeProvider


class ThrottledClock:
    """Like FakeClock (advances fake monotonic time instantly) but each sleep
    also blocks a tiny real interval. That lets the event loop's selector
    service thread-pool completions (journal I/O) and stops the three infinite
    daemon loops from spinning at full speed and starving the provider task —
    which makes app-level tests fast AND deterministic."""

    def __init__(self, throttle: float = 0.0005):
        self._t = 1_000_000.0
        self._throttle = throttle
        self.sleeps = []

    def now(self):
        return self._t

    def utcnow(self):
        from datetime import UTC, datetime

        return datetime.fromtimestamp(self._t, tz=UTC)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        if seconds > 0:
            self._t += seconds
        await asyncio.sleep(self._throttle)


class AppStubApi:
    """Controllable server double for RuntimeApp end-to-end tests."""

    def __init__(self, *, claim_response=None, cancel_attempt=None):
        self.claim_response = claim_response
        self.claimed = False
        self.transitions = []
        self.cancel_attempt = cancel_attempt
        self.renews = 0

    async def claim(self, runtime_id, diagnostics=None):
        if not self.claimed and self.claim_response is not None:
            self.claimed = True
            return self.claim_response
        return None

    async def heartbeat(self, runtime_id, *, current_load, health, metrics, inflight):
        commands = []
        # Deliver the cancel only once the attempt is running (a real cancel
        # arrives on a later heartbeat, never before startup completes) and
        # keep re-sending until it is cancelled (cancels must be idempotent).
        if self.cancel_attempt and self._has_running() and not self._is_cancelled():
            commands = [
                {"type": "cancel_execution", "attempt_id": self.cancel_attempt, "grace_seconds": 15}
            ]
        return HeartbeatResponse(None, commands)

    def _has_running(self):
        return any(a == self.cancel_attempt and s == "running" for a, s in self.transitions)

    def _is_cancelled(self):
        return any(a == self.cancel_attempt and s == "cancelled" for a, s in self.transitions)

    async def renew_lease(self, attempt_id, *, lease_seq):
        self.renews += 1
        return LeaseInfo(lease_seq + 1, "t")

    async def transition(self, attempt_id, *, lease_seq, status, result=None, failure_reason=None):
        self.transitions.append((attempt_id, status))
        return {}

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        end = start_offset + sum(len(line.encode()) for line in lines)
        return LogAck(end, 0)


def make_config(tmp_path):
    state = tmp_path / "state"
    work = tmp_path / "work"
    state.mkdir(mode=0o700, exist_ok=True)
    work.mkdir(mode=0o700, exist_ok=True)
    return DaemonConfig.from_dict(
        {"server_url": "https://mesh.example.com", "state_dir": str(state), "work_dir": str(work)}
    )


def make_claim(attempt_id="att-app-1"):
    return ClaimResponse(
        execution={"id": "exec-app-1", "config_snapshot": {"system_instructions": "be helpful"}},
        attempt={"id": attempt_id, "lease_seq": 1, "lease_expires_at": "t", "credentials": []},
    )


class CompletingProvider(FakeProvider):
    def __init__(self):
        super().__init__(
            events=[
                SessionStarted(session_id="sess-app", model="fake-model"),
                TextDelta(text="doing work"),
                UsageObserved(input_tokens=10, output_tokens=4, cost_usd="0.000500"),
                FinalResult(summary="finished", exit_code=0),
            ]
        )


class BlockingProvider:
    name = "blocking"

    def __init__(self):
        self.gate = asyncio.Event()

    async def probe(self):
        from mesh_runtime.providers.base import ProbeResult

        return ProbeResult(
            available=True, name="blocking", version="0.0.0", binary_sha256=None,
            capabilities=("coding_cli.blocking",), reason=None,
        )

    async def run(self, request):
        yield SessionStarted(session_id="sess-b", model="fake-model")
        await self.gate.wait()
        yield FinalResult(summary="done", exit_code=0)


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


async def run_until(config, api, journal, adapters, predicate, *, max_iters=200000):
    inventory = await Inventory.probe(adapters)
    app = RuntimeApp(config, api, journal, inventory, adapters, clock=ThrottledClock())
    app.set_runtime_id("rt-app")
    task = asyncio.create_task(app.run())
    hit = False
    for _ in range(max_iters):
        if predicate():
            hit = True
            break
        await asyncio.sleep(0)
    app.request_shutdown()
    await task
    return app, hit


class TestRuntimeApp:
    async def test_claim_to_terminal_and_journal_cleanup(self, tmp_path, journal):
        config = make_config(tmp_path)
        api = AppStubApi(claim_response=make_claim("att-app-1"))
        app, hit = await run_until(
            config, api, journal, [CompletingProvider()],
            predicate=lambda: ("att-app-1", "completed") in api.transitions,
        )
        assert hit, "attempt never reached completed"
        statuses = [s for a, s in api.transitions if a == "att-app-1"]
        assert statuses == ["running", "completed"]
        # journal row deleted after confirmed terminal
        assert await journal.get("att-app-1") is None

    async def test_cancel_via_heartbeat_downlink(self, tmp_path, journal):
        config = make_config(tmp_path)
        api = AppStubApi(claim_response=make_claim("att-app-1"), cancel_attempt="att-app-1")
        app, hit = await run_until(
            config, api, journal, [BlockingProvider()],
            predicate=lambda: ("att-app-1", "cancelled") in api.transitions,
        )
        assert hit, "cancel never took effect"
        statuses = [s for a, s in api.transitions if a == "att-app-1"]
        assert "running" in statuses and statuses[-1] == "cancelled"

    async def test_reconciles_stale_journal_on_startup(self, tmp_path, journal):
        config = make_config(tmp_path)
        # A prior crash left an in-flight row behind.
        await journal.put(
            "att-stale", execution_id="exec-old", runtime_id="rt-app",
            lease_seq=4, status="running", work_dir="/w/att-stale",
        )
        api = AppStubApi(claim_response=None)  # no new work
        app, hit = await run_until(
            config, api, journal, [CompletingProvider()],
            predicate=lambda: ("att-stale", "failed") in api.transitions,
        )
        assert hit, "stale row not reconciled"
        stale = [t for t in api.transitions if t[0] == "att-stale"]
        assert stale == [("att-stale", "failed")]
        assert await journal.get("att-stale") is None

    async def test_run_requires_runtime_id(self, tmp_path, journal):
        config = make_config(tmp_path)
        api = AppStubApi()
        inventory = await Inventory.probe([CompletingProvider()])
        app = RuntimeApp(config, api, journal, inventory, [CompletingProvider()], clock=ThrottledClock())
        with pytest.raises(RuntimeError, match="activate"):
            await app.run()


class TestBuildRunRequest:
    def test_separates_trusted_and_untrusted_layers(self):
        claim = ClaimResponse(
            execution={
                "id": "e1",
                "input": "issue body text",
                "config_snapshot": {"system_instructions": "system voice"},
            },
            attempt={"id": "a1", "lease_seq": 1, "lease_expires_at": "t"},
        )
        req = build_run_request(claim)
        assert req.system_prompt == "system voice"
        assert req.untrusted_context == "issue body text"
        assert req.attempt_id == "a1"

    def test_defaults_when_snapshot_empty(self):
        claim = ClaimResponse(
            execution={"id": "e1"},
            attempt={"id": "a1", "lease_seq": 1, "lease_expires_at": "t"},
        )
        req = build_run_request(claim)
        assert req.system_prompt == ""
        assert req.max_budget_usd == "0.000000"

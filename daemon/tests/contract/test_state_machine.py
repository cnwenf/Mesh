"""Contract tests (spec §4.4 A1 gate): the fake-provider state machine and
crash recovery driven over the REAL HTTP contract (httpx MockTransport), with
no real LLM and no secrets. These prove claim → running → logs → terminal and
restart reconciliation against the exact wire format the server speaks.
"""

import pytest

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.attempt import AttemptContext, AttemptSupervisor
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import FinalResult, RunRequest, SessionStarted, TextDelta, UsageObserved
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.reconcile import reconcile_on_startup
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.timeutil import FakeClock

TOKEN = "mesh_rt_contract"
ATTEMPT = "44444444-4444-4444-4444-444444444444"
PATCH = f"PATCH /api/v1/daemon/attempts/{ATTEMPT}"
LOGS = f"POST /api/v1/daemon/attempts/{ATTEMPT}/logs"
RENEW = f"POST /api/v1/daemon/attempts/{ATTEMPT}:renew-lease"


def run_request():
    return RunRequest(
        attempt_id=ATTEMPT, system_prompt="sys", untrusted_context="untrusted",
        max_turns=1, max_budget_usd="0.010000", tools_allowlist=(),
    )


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


@pytest.fixture
def api(fake_server):
    return RuntimeApiClient("https://mesh.example.com", TOKEN, transport=fake_server.transport())


def script_happy_server(fake_server):
    fake_server.enqueue(PATCH, 200, {"data": {}})  # running
    fake_server.enqueue(LOGS, 200, {"data": {"accepted_end_offset": 5, "redacted_hits": 0}})
    fake_server.enqueue(LOGS, 200, {"data": {"accepted_end_offset": 0, "redacted_hits": 0}})
    fake_server.enqueue(PATCH, 200, {"data": {}})  # completed
    for _ in range(20):
        fake_server.enqueue(RENEW, 200, {"data": {"lease_seq": 2, "lease_expires_at": "t"}})


class TestAttemptStateMachineContract:
    async def test_full_lifecycle_over_real_http(self, fake_server, api, journal):
        script_happy_server(fake_server)
        redactor = RedactionPipeline(secrets=[], rule_version="contract-v1")
        logs = LogUploader(api, journal, redactor, clock=FakeClock())
        sup = AttemptSupervisor(api, journal, logs, FakeClock(), rule_version="contract-v1")
        ctx = AttemptContext(attempt_id=ATTEMPT, execution_id="exec-c", runtime_id="rt-c", lease_seq=1)
        provider = FakeProvider(
            events=[
                SessionStarted(session_id="sess-c", model="fake-model"),
                TextDelta(text="hello"),
                UsageObserved(input_tokens=12, output_tokens=3, cost_usd="0.000700"),
                FinalResult(summary="finished ok", exit_code=0),
            ]
        )
        outcome = await sup.supervise(ctx, provider, run_request())

        assert outcome.status == "completed"
        assert outcome.terminal_reported is True

        patches = fake_server.calls_for(PATCH)
        assert patches[0].body["status"] == "running"
        completed = [p for p in patches if p.body["status"] == "completed"]
        assert len(completed) == 1
        result = completed[0].body["result"]
        assert result["schema_version"] == 1
        assert result["provider"]["session_id"] == "sess-c"
        assert result["usage"]["total_tokens"] == 15
        assert result["outcome"]["termination"] == "completed"
        assert result["redaction"]["rule_version"] == "contract-v1"

        # logs uploaded redacted + sealed, offset counted in bytes
        log_calls = fake_server.calls_for(LOGS)
        stdout = [c for c in log_calls if c.body["stream"] == "stdout"]
        assert stdout[0].body["lines"] == ["hello"]
        assert stdout[0].body["sealed"] is True
        assert all(c.body["lease_seq"] >= 1 for c in log_calls)

        # journal left in terminal_reported (app would delete after confirm)
        entry = await journal.get(ATTEMPT)
        assert entry.status == "terminal_reported"

    async def test_redaction_applied_before_upload(self, fake_server, api, journal):
        secret = "sk-live-ContractSecret99"
        fake_server.enqueue(PATCH, 200, {"data": {}})
        fake_server.enqueue(LOGS, 200, {"data": {"accepted_end_offset": 3, "redacted_hits": 1}})
        fake_server.enqueue(LOGS, 200, {"data": {"accepted_end_offset": 0, "redacted_hits": 0}})
        fake_server.enqueue(PATCH, 200, {"data": {}})
        for _ in range(20):
            fake_server.enqueue(RENEW, 200, {"data": {"lease_seq": 2, "lease_expires_at": "t"}})

        redactor = RedactionPipeline(secrets=[secret], rule_version="contract-v1")
        logs = LogUploader(api, journal, redactor, clock=FakeClock())
        sup = AttemptSupervisor(api, journal, logs, FakeClock())
        ctx = AttemptContext(attempt_id=ATTEMPT, execution_id="e", runtime_id="rt", lease_seq=1)
        provider = FakeProvider(events=[TextDelta(text=f"leak {secret}"), FinalResult("d", 0)])
        outcome = await sup.supervise(ctx, provider, run_request())

        assert outcome.status == "completed"
        stdout = [c for c in fake_server.calls_for(LOGS) if c.body["stream"] == "stdout"]
        wire = stdout[0].body["lines"][0]
        assert secret not in wire
        assert "***" in wire


class TestCrashRecoveryContract:
    async def test_restart_reconciles_inflight_as_daemon_restart(self, fake_server, api, journal):
        # A previous daemon crashed leaving an in-flight attempt in the journal.
        await journal.put(
            ATTEMPT, execution_id="exec-old", runtime_id="rt-c",
            lease_seq=9, status="running", work_dir="/w/old",
        )
        fake_server.enqueue(PATCH, 200, {"data": {}})  # failed/daemon_restart accepted

        cleaned = await reconcile_on_startup(journal, api, "rt-c")

        assert cleaned == 1
        patch = fake_server.calls_for(PATCH)[0]
        assert patch.body["status"] == "failed"
        assert patch.body["failure_reason"] == "daemon_restart"
        assert patch.body["lease_seq"] == 9
        assert await journal.get(ATTEMPT) is None

    async def test_restart_drops_attempt_server_already_settled(self, fake_server, api, journal):
        await journal.put(
            ATTEMPT, execution_id="e", runtime_id="rt-c", lease_seq=2, status="claimed",
        )
        # Server says the attempt is no longer in flight (fenced out).
        fake_server.enqueue(PATCH, 409, {"error": {"code": "attempt_terminal"}})

        cleaned = await reconcile_on_startup(journal, api, "rt-c")

        assert cleaned == 1
        assert await journal.get(ATTEMPT) is None

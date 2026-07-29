"""A2 stack tests: sandboxed provider adapter, supervisor security paths, and
a full claim→sandbox→terminal run through RuntimeApp (real namespaces)."""

import os
import uuid
from pathlib import Path

import pytest

from mesh_runtime.api import ClaimResponse, LogAck
from mesh_runtime.attempt import AttemptContext, AttemptSupervisor
from mesh_runtime.cleanup import CleanupReport
from mesh_runtime.journal import Journal
from mesh_runtime.logs import LogUploader
from mesh_runtime.providers.base import FinalResult, RunRequest, SessionStarted, TextDelta
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.providers.sandboxed import SandboxLaunchError, SandboxedProcessAdapter
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.sandbox import SandboxManager, SandboxSpec

CGROUP_BASE = Path("/sys/fs/cgroup") / f"mesh-a2-{os.getpid()}"

pytestmark = pytest.mark.sandbox


def _require_root() -> None:
    if os.getuid() != 0:
        pytest.fail("A2 stack tests require root + Linux namespaces (controlled runner)")


@pytest.fixture
async def manager(tmp_path):
    _require_root()
    mgr = SandboxManager(
        state_root=tmp_path / "st", sandbox_uid=65534, sandbox_gid=65534, cgroup_base=CGROUP_BASE
    )
    await mgr.start()
    yield mgr
    await mgr.shutdown()


def make_spec(root: Path, argv: tuple[str, ...], attempt_id: str | None = None) -> SandboxSpec:
    return SandboxSpec(
        attempt_id=attempt_id or str(uuid.uuid4()),
        root=root, uid=65534, gid=65534, argv=argv, env={}, ro_binds=(),
        memory_bytes=256 * 1024 * 1024, cpu_quota_us=100_000, cpu_period_us=100_000,
        pids_max=64, tmp_bytes=64 * 1024 * 1024,
    )


def request(attempt_id: str) -> RunRequest:
    return RunRequest(attempt_id=attempt_id, system_prompt="", untrusted_context="",
                      max_turns=0, max_budget_usd="0.000000")


class TestSandboxedAdapter:
    async def test_run_streams_stdout_lines_and_exit_code(self, manager, tmp_path):
        spec = make_spec(tmp_path / "r", ("/bin/sh", "-c", "echo alpha; echo beta"))
        adapter = SandboxedProcessAdapter(sandbox_manager=manager, spec_builder=lambda req: spec)
        events = [e async for e in adapter.run(request(spec.attempt_id))]
        assert isinstance(events[0], SessionStarted)
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["alpha", "beta"]
        assert isinstance(events[-1], FinalResult)
        assert events[-1].exit_code == 0
        assert "alpha" in events[-1].summary
        await adapter.destroy()

    async def test_nonzero_exit_reported(self, manager, tmp_path):
        spec = make_spec(tmp_path / "r2", ("/bin/sh", "-c", "echo failing; exit 3"))
        adapter = SandboxedProcessAdapter(sandbox_manager=manager, spec_builder=lambda req: spec)
        events = [e async for e in adapter.run(request(spec.attempt_id))]
        assert events[-1].exit_code == 3
        await adapter.destroy()

    async def test_launch_failure_raises_sandbox_launch_error(self, manager, tmp_path):
        # uid 0 violates the fail-closed rule → provision refuses.
        bad = SandboxSpec(
            attempt_id=str(uuid.uuid4()), root=tmp_path / "r3", uid=0, gid=0,
            argv=("/bin/true",), env={}, ro_binds=(),
            memory_bytes=1024, cpu_quota_us=1000, cpu_period_us=1000, pids_max=1, tmp_bytes=1024,
        )
        adapter = SandboxedProcessAdapter(sandbox_manager=manager, spec_builder=lambda req: bad)
        with pytest.raises(SandboxLaunchError):
            async for _ in adapter.run(request(bad.attempt_id)):
                pass

    async def test_probe_reports_linux_ns_available(self, manager):
        adapter = SandboxedProcessAdapter(
            sandbox_manager=manager, spec_builder=lambda req: None  # type: ignore[arg-type]
        )
        probe = await adapter.probe()
        assert probe.available is True
        assert "sandbox.linux_ns" in probe.capabilities


# -- supervisor security paths (no sandbox needed) ------------------------------


class StubApi:
    def __init__(self, *, transitions_ok=True):
        self.transitions: list[tuple[str, str]] = []
        self.logs: list[dict] = []
        self.approvals: list[dict] = []

    async def transition(self, attempt_id, *, lease_seq, status, result=None, failure_reason=None):
        self.transitions.append((attempt_id, status, failure_reason))
        return {"status": status}

    async def renew_lease(self, attempt_id, *, lease_seq):
        from mesh_runtime.api import LeaseInfo

        return LeaseInfo(lease_seq=lease_seq + 1, lease_expires_at="t")

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        self.logs.append({"lines": list(lines), "stream": stream})
        end = start_offset + sum(len(ln.encode()) for ln in lines)
        return LogAck(accepted_end_offset=end, redacted_hits=0)

    async def request_approval(self, execution_id, *, lease_seq, attempt_id, action_summary, resume_context):
        self.approvals.append({"action_summary": action_summary, "resume_context": resume_context})
        return {"id": "ap", "execution_status": "awaiting_approval"}


class StubSecurity:
    def __init__(self, *, start_error=None, diff_hits=0):
        self.started_with: list[int] = []
        self.finished: list[bool] = []
        self.approvals: list[dict] = []
        self.export_calls = 0
        self.checkout_id = "co-9"
        self.diff_ref = "diff-9"
        self._start_error = start_error
        self._diff_hits = diff_hits

    async def start(self, *, lease_seq):
        if self._start_error is not None:
            raise self._start_error
        self.started_with.append(lease_seq)

    async def finish(self, *, spool_flushed):
        self.finished.append(spool_flushed)
        return CleanupReport(steps_done=["broker_closed", "tokens_revoked", "cgroup_killed",
                                         "mounts_released", "artifacts_removed", "spool_flushed", "done"])

    async def request_approval(self, *, lease_seq, action, params, resume_context):
        self.approvals.append({"action": action, "params": params})

    async def export_diff(self, *, lease_seq, redactor):
        self.export_calls += 1
        return self._diff_hits

    def bind_adapter_destroy(self, destroy):
        pass


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


def supervisor(api, journal, *, security=None, clock=None):
    redactor = RedactionPipeline(secrets=[], rule_version="v1")
    logs = LogUploader(api, journal, redactor, clock=clock)
    return AttemptSupervisor(
        api, journal, logs, clock, security=security, redactor=redactor, max_renew_failures=3
    )


def ctx(attempt_id="att-sec-1") -> AttemptContext:
    return AttemptContext(attempt_id=attempt_id, execution_id="exec-1", runtime_id="rt-1", lease_seq=1)


class TestSupervisorSecurity:
    async def test_security_start_failure_reports_executor_unavailable(self, journal):
        from mesh_runtime.errors import DaemonError

        api = StubApi()
        sec = StubSecurity(start_error=DaemonError("checkout refused"))
        sup = supervisor(api, journal, security=sec)
        outcome = await sup.supervise(ctx(), FakeProvider(events=[]), request("att-sec-1"))
        assert outcome.status == "failed"
        assert outcome.failure_reason == "executor_unavailable"
        assert ("att-sec-1", "failed", "executor_unavailable") in api.transitions
        assert sec.finished == [True]  # cleanup still ran

    async def test_sandbox_launch_error_reports_sandbox_violation(self, journal):
        api = StubApi()
        sec = StubSecurity()
        sup = supervisor(api, journal, security=sec)
        provider = FakeProvider(events=[], fault=SandboxLaunchError("ns failed"))
        outcome = await sup.supervise(ctx("att-sec-2"), provider, request("att-sec-2"))
        assert outcome.status == "failed"
        assert outcome.failure_reason == "sandbox_violation"
        statuses = [t[2] for t in api.transitions]
        assert "sandbox_violation" in statuses

    async def test_escalate_confirm_required_runs_approval_protocol(self, journal):
        api = StubApi()
        sec = StubSecurity()
        sup = supervisor(api, journal, security=sec)
        c = ctx("att-sec-3")
        await journal.put(c.attempt_id, execution_id=c.execution_id, runtime_id=c.runtime_id,
                          lease_seq=1, status="running")
        outcome = await sup.escalate_confirm_required(
            c, "git.push", {"repo": "r"}, resume_context={"step": 4}
        )
        assert outcome.status == "cancelled"
        assert outcome.failure_reason == "awaiting_approval"
        assert outcome.terminal_reported is True
        assert sec.approvals == [{"action": "git.push", "params": {"repo": "r"}}]
        # No terminal status PATCH — the server owns awaiting_approval state.
        assert api.transitions == []

    async def test_terminal_exports_redacted_diff_and_cleans_up(self, journal):
        api = StubApi()
        sec = StubSecurity(diff_hits=2)
        sup = supervisor(api, journal, security=sec)
        provider = FakeProvider(events=[TextDelta(text="done"), FinalResult(summary="ok", exit_code=0)])
        outcome = await sup.supervise(ctx("att-sec-4"), provider, request("att-sec-4"))
        assert outcome.status == "completed"
        assert sec.export_calls == 1
        assert sec.finished == [True]
        terminal = [t for t in api.transitions if t[1] == "completed"]
        assert terminal


# -- full stack through RuntimeApp (real sandbox) ---------------------------------


class FullStackApi(StubApi):
    pass


class TestFullStack:
    async def test_spawn_attempt_runs_provider_in_real_sandbox_and_cleans_up(self, manager, tmp_path):
        from mesh_runtime.app import RuntimeApp
        from mesh_runtime.config import DaemonConfig
        from mesh_runtime.inventory import Inventory

        _require_root()
        import shutil
        import tempfile

        # AF_UNIX caps socket paths at 108 bytes — keep work/state roots short
        # (production lives under a short prefix like /var/lib/mesh-runtime).
        short = Path(tempfile.mkdtemp(prefix="fs", dir="/tmp"))
        # Dedicated provider dir at a short HOST path (production: /opt/mesh/
        # providers/...): it becomes a read-only sandbox bind at the same
        # absolute path; /tmp-based paths are shadowed by the sandbox tmpfs.
        prov_dir = Path(f"/mesh-test-prov-{uuid.uuid4().hex[:8]}")
        prov_dir.mkdir()
        provider = prov_dir / "provider.sh"
        provider.write_text("#!/bin/sh\necho hello-from-sandbox\nexit 0\n")
        provider.chmod(0o755)
        config = DaemonConfig(
            server_url="https://mesh.example.com",
            state_dir=short / "state",
            work_dir=short / "work",
            provider_path=provider,
        )
        journal = Journal(short / "state" / "ledger.sqlite3")
        await journal.open()
        api = FullStackApi()
        app = RuntimeApp(config, api, journal, Inventory([]), adapters=[], sandbox_manager=manager)
        app.set_runtime_id("rt-full-1")
        claim = ClaimResponse(
            execution={
                "id": "exec-full-1", "issue_id": None,
                "config_snapshot": {
                    "capability_grants": [{"capability": "issue:read", "permission": "read_only"}],
                    "network_policy": {},
                },
            },
            attempt={
                "id": "att-full-1", "lease_seq": 1, "lease_expires_at": "t",
                "task_token": "mesh_task_full", "credentials": [],
            },
        )
        await app._spawn_attempt(claim)
        statuses = [t[1] for t in api.transitions]
        assert "running" in statuses
        assert "completed" in statuses
        logged = "\n".join(ln for call in api.logs for ln in call["lines"])
        assert "hello-from-sandbox" in logged
        # S-08: the attempt root is gone after terminal cleanup.
        assert not (config.work_dir / "exec-ful" / "att-full").exists()
        # Journal row deleted after confirmed terminal.
        assert await journal.get("att-full-1") is None
        await journal.close()
        shutil.rmtree(short, ignore_errors=True)
        shutil.rmtree(prov_dir, ignore_errors=True)

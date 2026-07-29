"""A2 security orchestration — checkout → egress → broker → cleanup, and the
app-level stack assembly (sandbox manager injected)."""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from mesh_runtime.checkout import CheckoutError
from mesh_runtime.journal import Journal
from mesh_runtime.redaction import RedactionPipeline
from mesh_runtime.security import AttemptSecurity, SecurityConfig

ISSUE_ID = str(uuid.uuid4())


def git(cwd, *args):
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(cwd)}
    subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True)


@pytest.fixture
def upstream(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "--quiet", "--initial-branch", "main")
    git(src, "config", "user.email", "d@example.com")
    git(src, "config", "user.name", "d")
    (src / "f.txt").write_text("one\n")
    git(src, "add", "f.txt")
    git(src, "commit", "--quiet", "-m", "c1")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(src), capture_output=True, env={"PATH": "/usr/bin:/bin"}
    ).stdout.decode().strip()
    bare = tmp_path / "u.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(src), str(bare)],
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)
    return f"file://{bare}", sha


class StubApi:
    def __init__(self):
        self.checkouts = []
        self.approvals = []

    async def report_checkout(self, attempt_id, *, lease_seq, status, **kw):
        self.checkouts.append({"status": status, **kw})
        return {"id": "co-1", "diff_ref": "diff-ref-1" if status == "diff_ready" else None}

    async def request_approval(self, execution_id, *, lease_seq, attempt_id, action_summary, resume_context):
        self.approvals.append(
            {"execution_id": execution_id, "attempt_id": attempt_id,
             "action_summary": action_summary, "resume_context": resume_context}
        )
        return {"id": "ap-1", "execution_status": "awaiting_approval"}


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


@pytest.fixture
def attempt_root():
    # AF_UNIX paths cap at 108 bytes — keep the attempt root short.
    root = Path(tempfile.mkdtemp(prefix="sec", dir="/tmp")) / "a"
    yield root
    shutil.rmtree(root.parent, ignore_errors=True)


def make_config(
    attempt_root: Path, *, repo_url=None, base_sha=None, task_token="mesh_task_sec"
) -> SecurityConfig:
    from mesh_runtime.checkout import FrozenRepo

    return SecurityConfig(
        attempt_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        attempt_root=attempt_root,
        task_token=task_token,
        issue_id=ISSUE_ID,
        grants={"issue:read": "read_only", "issue:comment:write": "write"},
        nonce=uuid.uuid4().hex,
        sandbox_uid=65534,
        cgroup_marker="",
        repo=FrozenRepo(url=repo_url, base_ref="main", base_sha=base_sha) if repo_url else None,
        allowed_repos=(repo_url,) if repo_url else (),
        platform_managed=False,
        network_policy={},
    )


class TestStart:
    async def test_start_runs_checkout_egress_broker_in_order(
        self, tmp_path, upstream, journal, attempt_root
    ):
        url, sha = upstream
        api = StubApi()
        sec = AttemptSecurity(make_config(attempt_root, repo_url=url, base_sha=sha),
                              api=api, journal=journal)
        await sec.start(lease_seq=1)
        try:
            statuses = [c["status"] for c in api.checkouts]
            assert statuses == ["cloning", "ready"]
            assert sec.checkout_result.commit_sha == sha
            assert sec.checkout_id == "co-1"
            assert sec.egress is not None and sec.egress.port > 0
            assert sec.broker is not None
            assert os.path.exists(sec.broker.socket_path)
            # worktree checked out at the frozen sha
            assert (attempt_root / "worktree" / "f.txt").read_text() == "one\n"
        finally:
            await sec.finish(spool_flushed=True)

    async def test_start_without_repo_skips_checkout(self, journal, attempt_root):
        api = StubApi()
        sec = AttemptSecurity(make_config(attempt_root), api=api, journal=journal)
        await sec.start(lease_seq=1)
        assert api.checkouts == []
        assert sec.broker is not None and sec.egress is not None
        await sec.finish(spool_flushed=True)

    async def test_start_without_task_token_skips_broker(self, journal, attempt_root):
        api = StubApi()
        sec = AttemptSecurity(make_config(attempt_root, task_token=None), api=api, journal=journal)
        await sec.start(lease_seq=1)
        assert sec.broker is None
        await sec.finish(spool_flushed=True)

    async def test_checkout_failure_propagates_fail_closed(self, upstream, journal, attempt_root):
        url, sha = upstream
        api = StubApi()
        cfg = make_config(attempt_root, repo_url=url, base_sha=sha)
        cfg = SecurityConfig(**{**cfg.__dict__, "allowed_repos": ("https://other.example/",)})
        sec = AttemptSecurity(cfg, api=api, journal=journal)
        with pytest.raises(CheckoutError):
            await sec.start(lease_seq=1)
        assert sec.egress is None  # nothing started after the failure
        await sec.finish(spool_flushed=True)

    def test_broker_grants_map_frozen_capabilities(self, journal, attempt_root):
        cfg = make_config(attempt_root)
        sec = AttemptSecurity(cfg, api=StubApi(), journal=journal)
        grants = sec._broker_grants()
        assert grants == {"issue.read": "read_only", "issue.comment": "write"}


class TestApprovalProtocol:
    async def test_request_approval_calls_server_with_resume_context(self, journal, attempt_root):
        api = StubApi()
        cfg = make_config(attempt_root)
        sec = AttemptSecurity(cfg, api=api, journal=journal)
        await sec.request_approval(
            lease_seq=3, action="git.push", params={"repo": "r"}, resume_context={"step": 2}
        )
        ap = api.approvals[0]
        assert ap["execution_id"] == cfg.execution_id
        assert ap["attempt_id"] == cfg.attempt_id
        assert ap["action_summary"] == {"action": "git.push", "params": {"repo": "r"}}
        assert ap["resume_context"] == {"step": 2}


class TestDiffAndFinish:
    async def test_export_diff_redacts_and_reports(self, upstream, journal, attempt_root):
        url, sha = upstream
        api = StubApi()
        sec = AttemptSecurity(make_config(attempt_root, repo_url=url, base_sha=sha),
                              api=api, journal=journal)
        await sec.start(lease_seq=1)
        secret = "sk-live-SuperSecret99"
        (attempt_root / "worktree" / "f.txt").write_text(f"token={secret}\n")
        redactor = RedactionPipeline(secrets=[secret], rule_version="v1")
        hits = await sec.export_diff(lease_seq=2, redactor=redactor)
        assert hits >= 1
        diff_report = [c for c in api.checkouts if c["status"] == "diff_ready"][-1]
        assert secret not in diff_report["diff"]
        assert "***" in diff_report["diff"]
        assert sec.diff_ref == "diff-ref-1"
        await sec.finish(spool_flushed=True)

    async def test_finish_is_idempotent_and_removes_socket(self, upstream, journal, attempt_root):
        url, sha = upstream
        api = StubApi()
        sec = AttemptSecurity(make_config(attempt_root, repo_url=url, base_sha=sha),
                              api=api, journal=journal)
        await sec.start(lease_seq=1)
        socket_path = sec.broker.socket_path
        first = await sec.finish(spool_flushed=True)
        second = await sec.finish(spool_flushed=True)
        assert first.ok and second.ok
        assert not os.path.exists(socket_path)
        entry = await journal.get(sec.config.attempt_id)
        # journal row may not exist (security doesn't create it) — update is a no-op then
        assert entry is None or entry.cleanup_state.endswith("done")


class TestAppWiring:
    """RuntimeApp assembles the stack when a sandbox manager is injected."""

    async def test_spawn_builds_security_and_sandboxed_adapter(self, tmp_path):
        from mesh_runtime.app import RuntimeApp
        from mesh_runtime.config import DaemonConfig
        from mesh_runtime.inventory import Inventory
        from mesh_runtime.providers.sandboxed import SandboxedProcessAdapter

        config = DaemonConfig(
            server_url="https://mesh.example.com",
            state_dir=tmp_path / "state",
            work_dir=tmp_path / "work",
            provider_path=Path("/usr/bin/true"),
        )

        class StubSandboxManager:
            cgroup_base = Path("/sys/fs/cgroup/mesh-stub")
            state_root = tmp_path / "state"

            async def destroy_attempt(self, attempt_id):
                self.destroyed = attempt_id

        from mesh_runtime.api import ClaimResponse

        app = RuntimeApp(
            config, api=None, journal=None, inventory=Inventory([]), adapters=[],
            sandbox_manager=StubSandboxManager(),
        )
        claim = ClaimResponse(
            execution={"id": "e1", "issue_id": ISSUE_ID,
                       "config_snapshot": {"repo": {"url": "file:///x.git", "base_ref": "main"},
                                           "capability_grants": [
                                               {"capability": "issue:read", "permission": "read_only"}],
                                           "network_policy": {"allowed_hosts": ["a.example"]}}},
            attempt={"id": "a1", "lease_seq": 1, "lease_expires_at": "t", "task_token": "mesh_task_x",
                     "credentials": [{"id": "c1", "kind": "repo_token", "value": "rot-v"}]},
        )
        redactor = RedactionPipeline(secrets=[], rule_version="v1")
        security = app._build_security(claim, tmp_path / "attempt", redactor)
        assert security is not None
        assert security.config.task_token == "mesh_task_x"
        assert security.config.issue_id == ISSUE_ID
        assert security.config.read_credential == "rot-v"
        assert security.config.repo.url == "file:///x.git"
        adapter = app._select_adapter(claim, tmp_path / "attempt", security)
        assert isinstance(adapter, SandboxedProcessAdapter)

    async def test_without_sandbox_manager_security_is_none(self, tmp_path):
        from mesh_runtime.api import ClaimResponse
        from mesh_runtime.app import RuntimeApp
        from mesh_runtime.config import DaemonConfig
        from mesh_runtime.inventory import Inventory

        config = DaemonConfig(server_url="https://mesh.example.com",
                              state_dir=tmp_path / "state", work_dir=tmp_path / "work")
        app = RuntimeApp(config, api=None, journal=None, inventory=Inventory([]), adapters=[])
        claim = ClaimResponse(execution={"id": "e1"},
                              attempt={"id": "a1", "lease_seq": 1, "lease_expires_at": "t"})
        assert app._build_security(claim, tmp_path, RedactionPipeline(secrets=[], rule_version="v1")) is None

"""A3 full-stack sandbox tests: the pinned Claude Code adapter driving a
stream-json emitter INSIDE the real namespace/cgroup sandbox (root required).

Validates what the hermetic unit tests cannot: the frozen §1.4 argv, the
attempt-private read-only run-dir configs and the FROM-EMPTY env reaching a
real sandboxed process, plus S-07 wall-timeout truncation killing a real
cgroup. The real LLM itself is covered by tests/integration/real_llm_e2e.py.
"""

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from mesh_runtime.budget import BudgetLimits
from mesh_runtime.manifest import ProviderManifest
from mesh_runtime.providers.base import (
    FinalResult,
    ProviderExited,
    RunRequest,
    SessionStarted,
    TextDelta,
)
from mesh_runtime.providers.claude_code import (
    ClaudeCodeAdapter,
    ClaudeLaunchPlan,
    SandboxProcessLauncher,
)
from mesh_runtime.providers.sandboxed import SandboxLaunchError
from mesh_runtime.sandbox import SandboxManager

CGROUP_BASE = Path("/sys/fs/cgroup") / f"mesh-a3-{os.getpid()}"

pytestmark = pytest.mark.sandbox


def _require_root() -> None:
    if os.getuid() != 0:
        pytest.fail("A3 stack tests require root + Linux namespaces (controlled runner)")


@pytest.fixture
async def manager(tmp_path):
    _require_root()
    mgr = SandboxManager(
        state_root=tmp_path / "st", sandbox_uid=65534, sandbox_gid=65534,
        cgroup_base=CGROUP_BASE,
    )
    await mgr.start()
    yield mgr
    await mgr.shutdown()


@pytest.fixture
def prov_root():
    """Provider binaries must live OUTSIDE /tmp — the sandbox tmpfs shadows
    /tmp after pivot_root (sandbox_init §provider layout)."""
    root = Path(f"/mesh-a3-prov-{uuid.uuid4().hex[:8]}")
    root.mkdir()
    yield root
    import shutil

    shutil.rmtree(root, ignore_errors=True)


PROVIDER_SCRIPT = r"""#!/usr/bin/env python3
import json, os, sys

# Prove what the sandboxed process actually received (written to the rw
# worktree so the daemon-side test can read it back).
with open("argv.json", "w") as fh:
    json.dump(sys.argv, fh)
with open("env.json", "w") as fh:
    json.dump(dict(os.environ), fh)
with open("stdin.txt", "w") as fh:
    fh.write(sys.stdin.read())
with open("identity.txt", "w") as fh:
    fh.write(f"{os.getuid()} {os.geteuid()}")
with open("mcp.json.copy", "w") as fh:
    with open("/run/mcp.json") as src:
        fh.write(src.read())

def emit(record):
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()

scenario = os.environ.get("A3_SCENARIO", "emit")
if scenario == "emit":
    emit({"type": "system", "subtype": "init", "session_id": "a3-sess", "model": "a3-model"})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "sandboxed hello"}],
           "usage": {"input_tokens": 3, "output_tokens": 2}}})
    emit({"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01,
           "result": "a3 done", "usage": {"input_tokens": 3, "output_tokens": 2}})
    sys.exit(0)
# stall: no output, no exit — the frozen wall budget must kill this cgroup.
import time
emit({"type": "system", "subtype": "init", "session_id": "a3-stall", "model": "a3-model"})
time.sleep(120)
"""


def install_provider(prov_root: Path, scenario: str = "emit") -> Path:
    prov_dir = prov_root / "fake-claude"
    prov_dir.mkdir(parents=True)
    script = PROVIDER_SCRIPT.replace('"emit")\n', f'"{scenario}")\n', 1)
    binary = prov_dir / "claude"
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def manifest_for(binary: Path) -> ProviderManifest:
    import hashlib

    return ProviderManifest(
        provider="claude-code",
        version="0.0.0-a3-fake",
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        required_flags=("--print", "--max-budget-usd"),
        hard_limits_usd_budget=True,
        hard_limits_wall_timeout=True,
    )


def make_adapter(manager, tmp_path, binary: Path, *, budget: BudgetLimits,
                 scenario: str = "emit") -> tuple[ClaudeCodeAdapter, ClaudeLaunchPlan, Path]:
    attempt_root = Path(f"/mesh-a3-att-{uuid.uuid4().hex[:8]}")
    run_dir = attempt_root / "run"
    run_dir.mkdir(parents=True)
    plan = ClaudeLaunchPlan(
        attempt_id=uuid.uuid4().hex,
        execution_id=uuid.uuid4().hex,
        host_run_dir=run_dir,
        sandbox_run_dir="/run",
        worktree_cwd="/worktree",
        broker_socket_sandbox_path=None,
        broker_nonce=None,
        proxy_url=None,
        provider_env={"A3_SCENARIO": scenario},
        budget=budget,
    )
    launcher = SandboxProcessLauncher(
        sandbox_manager=manager,
        attempt_id=plan.attempt_id,
        attempt_root=attempt_root,
        uid=65534,
        gid=65534,
        ro_binds=(str(binary.parent),),
        memory_bytes=256 * 1024 * 1024,
        cpu_quota_us=100_000,
        cpu_period_us=100_000,
        pids_max=64,
        tmp_bytes=64 * 1024 * 1024,
    )
    adapter = ClaudeCodeAdapter(
        manifest=manifest_for(binary), binary_path=str(binary),
        launcher=launcher, plan=plan,
    )
    return adapter, plan, attempt_root


def request_for(plan: ClaudeLaunchPlan) -> RunRequest:
    return RunRequest(
        attempt_id=plan.attempt_id,
        system_prompt="A3 trusted system policy",
        untrusted_context="A3 untrusted context",
        max_turns=2,
        max_budget_usd="0.50",
        tools_allowlist=("Read",),
    )


class TestClaudeAdapterInRealSandbox:
    async def test_frozen_argv_env_configs_reach_sandboxed_process(self, manager, tmp_path, prov_root):
        binary = install_provider(prov_root)
        adapter, plan, attempt_root = make_adapter(
            manager, tmp_path, binary, budget=BudgetLimits(usd=Decimal("0.5"))
        )
        events = [e async for e in adapter.run(request_for(plan))]

        kinds = [type(e) for e in events]
        assert SessionStarted in kinds
        assert TextDelta in kinds
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.termination == "completed"
        assert isinstance(events[-1], ProviderExited)
        assert events[-1].exit_code == 0

        worktree = attempt_root / "worktree"
        argv = json.loads((worktree / "argv.json").read_text())
        assert argv[0] == str(binary)
        for flag in ("--print", "--bare", "--strict-mcp-config", "--no-session-persistence",
                     "--disable-slash-commands"):
            assert flag in argv
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert argv[argv.index("--mcp-config") + 1] == "/run/mcp.json"
        assert argv[argv.index("--settings") + 1] == "/run/settings.json"
        assert argv[argv.index("--system-prompt-file") + 1] == "/run/system.md"
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        assert argv[argv.index("--max-budget-usd") + 1] == "0.50"

        env = json.loads((worktree / "env.json").read_text())
        assert env["HOME"] == "/home"
        assert env["XDG_CONFIG_HOME"].startswith("/xdg")
        assert env["MESH_ATTEMPT_ID"] == plan.attempt_id
        # FROM-EMPTY construction: no daemon leakage into the sandbox env.
        assert not any(k.startswith(("MESH_DAEMON_", "MESH_INTERNAL_", "LD_")) for k in env)
        assert "ANTHROPIC_API_KEY" not in env  # no credentials configured

        stdin_lines = [ln for ln in (worktree / "stdin.txt").read_text().splitlines() if ln]
        assert len(stdin_lines) == 1
        prompt = json.loads(stdin_lines[0])
        assert prompt["type"] == "user"
        assert "A3 untrusted context" in prompt["message"]["content"]

        identity = (worktree / "identity.txt").read_text().split()
        assert identity == ["65534", "65534"]  # dropped to nobody, irreversible

        mcp_seen = json.loads((worktree / "mcp.json.copy").read_text())
        assert mcp_seen["mcpServers"] == {}  # no broker socket in this test

        system_md = (attempt_root / "run" / "system.md").read_text()
        assert system_md == "A3 trusted system policy"

        await adapter.destroy()

    async def test_wall_timeout_kills_real_cgroup(self, manager, tmp_path, prov_root):
        binary = install_provider(prov_root, scenario="stall")
        adapter, plan, _ = make_adapter(
            manager, tmp_path, binary,
            budget=BudgetLimits(wall_seconds=1.0),
            scenario="stall",
        )
        events = [e async for e in adapter.run(request_for(plan))]
        await adapter.destroy()
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.termination == "timeout"
        exited = events[-1]
        assert isinstance(exited, ProviderExited)
        assert exited.exit_code != 0

    async def test_sandbox_provision_failure_raises_sandbox_launch_error(
        self, manager, tmp_path, prov_root
    ):
        binary = install_provider(prov_root)
        attempt_root = Path("/mesh-a3-att-bad")
        (attempt_root / "run").mkdir(parents=True)
        plan = ClaudeLaunchPlan(
            attempt_id=uuid.uuid4().hex,
            execution_id=uuid.uuid4().hex,
            host_run_dir=attempt_root / "run",
            sandbox_run_dir="/run",
            worktree_cwd="/worktree",
            broker_socket_sandbox_path=None,
            broker_nonce=None,
            proxy_url=None,
            provider_env={},
            budget=BudgetLimits(),
        )
        launcher = SandboxProcessLauncher(
            sandbox_manager=manager,
            attempt_id=plan.attempt_id,
            attempt_root=attempt_root,
            uid=0,  # violates the unprivileged-uid red line → provision refuses
            gid=0,
            ro_binds=(str(binary.parent),),
            memory_bytes=256 * 1024 * 1024,
            cpu_quota_us=100_000,
            cpu_period_us=100_000,
            pids_max=64,
            tmp_bytes=64 * 1024 * 1024,
        )
        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(binary), binary_path=str(binary),
            launcher=launcher, plan=plan,
        )
        with pytest.raises(SandboxLaunchError):
            [e async for e in adapter.run(request_for(plan))]
        await adapter.destroy()
        import shutil

        shutil.rmtree(attempt_root, ignore_errors=True)

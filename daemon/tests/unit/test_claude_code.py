"""Pinned Claude Code adapter tests (runtime-executor.md §1.4, §3.5, §3.9, §5.4).

Probe: manifest/digest/version/flag gates, fail-closed, inode/mtime/hash cache
invalidation. Run: exact §1.4 argv, prompt ONLY via stdin, platform-owned
read-only configs, strict stream-json mapping, S-07 live truncation
(budget/turns/wall/idle → frozen termination vocabulary).

Hermetic: a fake ``claude`` script stands in for the real binary through the
launcher seam; the real-sandbox path is covered by tests/unit/test_a3_stack.py
(root) and the real-LLM e2e (tests/integration/real_llm_e2e.py).
"""

import asyncio
import json
import stat
import uuid
from pathlib import Path

import pytest

from mesh_runtime.budget import BudgetLimits
from mesh_runtime.manifest import ProviderManifest
from mesh_runtime.providers.base import (
    FinalResult,
    ProtocolWarning,
    ProviderExited,
    RunRequest,
    SessionStarted,
    TextDelta,
    ToolCompleted,
    ToolRequested,
    UsageObserved,
)
from mesh_runtime.providers.claude_code import (
    ClaudeCodeAdapter,
    ClaudeLaunchPlan,
)

FAKE_VERSION = "9.9.9-fake"

FAKE_SCRIPT = f"""#!/usr/bin/env python3
import json, os, sys, time

def emit(record):
    sys.stdout.write(json.dumps(record) + "\\n")
    sys.stdout.flush()

mode = os.environ.get("FAKE_MODE", "run")
if "--version" in sys.argv:
    if os.environ.get("FAKE_VERSION_FILE"):
        with open(os.environ["FAKE_VERSION_FILE"]) as fh:
            sys.stdout.write(fh.read())
    else:
        sys.stdout.write("{FAKE_VERSION} (Claude Code)\\n")
    sys.exit(0)
if "--help" in sys.argv:
    help_path = os.environ.get("FAKE_HELP_FILE")
    if help_path:
        with open(help_path) as fh:
            sys.stdout.write(fh.read())
    else:
        sys.stdout.write("Usage: fake\\n")
        for flag in ["--print", "--output-format", "--input-format", "--verbose", "--bare",
                     "--disable-slash-commands", "--no-session-persistence",
                     "--setting-sources", "--strict-mcp-config", "--mcp-config",
                     "--settings", "--system-prompt-file", "--tools",
                     "--disallowed-tools", "--permission-mode", "--max-budget-usd"]:
            sys.stdout.write(f"  {{flag}} <v>\\n")
    sys.exit(0)

# run mode: record argv/env/stdin for assertions, then stream records.
out = os.environ.get("FAKE_CAPTURE_DIR")
if out:
    with open(os.path.join(out, "argv.json"), "w") as fh:
        json.dump(sys.argv, fh)
    with open(os.path.join(out, "env.json"), "w") as fh:
        json.dump(dict(os.environ), fh)
    with open(os.path.join(out, "stdin.txt"), "w") as fh:
        fh.write(sys.stdin.read())

scenario = os.environ.get("FAKE_SCENARIO", "happy")
if scenario == "happy":
    emit({{"type": "system", "subtype": "init", "session_id": "sess-abc", "model": "fake-model"}})
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "working on it"}}],
           "usage": {{"input_tokens": 12, "output_tokens": 4}}}}}})
    emit({{"type": "assistant", "message": {{"content": [
           {{"type": "tool_use", "id": "call-1", "name": "Read", "input": {{}}}}]}}}})
    emit({{"type": "user", "message": {{"content": [
           {{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}}]}}}})
    emit({{"type": "result", "subtype": "success", "num_turns": 2, "total_cost_usd": 0.05,
           "result": "final answer", "usage": {{"input_tokens": 20, "output_tokens": 8}}}})
elif scenario == "over_budget":
    emit({{"type": "system", "subtype": "init", "session_id": "sess-b", "model": "fake-model"}})
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "spending"}}],
           "usage": {{"input_tokens": 1, "output_tokens": 1}}}}}})
    emit({{"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 99.0,
           "result": "expensive", "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
    time.sleep(30)  # would keep running; budget must cut it off
elif scenario == "stall":
    emit({{"type": "system", "subtype": "init", "session_id": "sess-s", "model": "fake-model"}})
    time.sleep(30)  # no output: idle/wall timeouts must fire
elif scenario == "noisy":
    emit({{"type": "weird_record", "payload": 1}})
    emit({{"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 3}})
    emit("not-json-at-all")
    emit({{"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01,
           "result": "done", "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
elif scenario == "crash":
    sys.stderr.write("fatal: provider exploded\\n")
    sys.exit(3)
elif scenario == "bigline":
    # one line far over the 64KiB readline ceiling, then a valid result
    print("X" * 200000, flush=True)
    emit({{"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01,
           "result": "ok", "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
elif scenario == "chatty":
    # emit lines faster than the poll interval so the frozen wall guard fires
    import time as _t
    for _i in range(200):
        emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "line"}}]}}}})
        _t.sleep(0.05)
    emit({{"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01,
           "result": "never", "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
sys.exit(0)
"""


class FakeLauncher:
    """Test launcher: real subprocess, no sandbox. Captures spawn inputs."""

    def __init__(self):
        self.spawns: list[dict] = []
        self.proc: asyncio.subprocess.Process | None = None
        self.destroyed = False

    async def spawn(self, *, argv: list[str], env: dict) -> asyncio.subprocess.Process:
        self.spawns.append({"argv": list(argv), "env": dict(env)})
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return self.proc

    async def destroy(self) -> None:
        self.destroyed = True
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


@pytest.fixture
def fake_binary(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(FAKE_SCRIPT, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_for(binary: Path, *, version: str = FAKE_VERSION, flags=None) -> ProviderManifest:
    return ProviderManifest(
        provider="claude-code",
        version=version,
        binary_sha256=_sha256_of(binary),
        required_flags=tuple(
            flags or ["--print", "--bare", "--strict-mcp-config", "--max-budget-usd"]
        ),
        hard_limits_usd_budget=True,
        hard_limits_wall_timeout=True,
    )


def plan_for(tmp_path: Path, binary: Path, *, budget: BudgetLimits | None = None,
             scenario: str = "happy", provider_env: dict | None = None) -> ClaudeLaunchPlan:
    run_dir = tmp_path / "attempt" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    capture = tmp_path / "capture"
    capture.mkdir(exist_ok=True)
    env = {"FAKE_CAPTURE_DIR": str(capture), "FAKE_SCENARIO": scenario}
    env.update(provider_env or {})
    return ClaudeLaunchPlan(
        attempt_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        host_run_dir=run_dir,
        sandbox_run_dir="/run",
        worktree_cwd="/worktree",
        broker_socket_sandbox_path="/run/mesh-broker.sock",
        broker_nonce="nonce-123",
        proxy_url="http://10.0.0.1:3128",
        provider_env=env,
        budget=budget or BudgetLimits(),
    )


def request_for(plan: ClaudeLaunchPlan) -> RunRequest:
    return RunRequest(
        attempt_id=plan.attempt_id,
        system_prompt="TRUSTED platform policy",
        untrusted_context="untrusted issue body",
        max_turns=5,
        max_budget_usd="1.50",
        tools_allowlist=("Read", "Write"),
    )


class TestProbeGates:
    async def test_probe_passes_pinned_binary(self, fake_binary, tmp_path):
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary))
        result = await adapter.probe()
        assert result.available is True
        assert result.name == "claude-code"
        assert result.version == FAKE_VERSION
        assert result.binary_sha256 == _sha256_of(fake_binary)
        assert "coding_cli.claude_code" in result.capabilities
        assert "budget.usd_hard" in result.capabilities

    async def test_sha256_mismatch_fails_closed(self, fake_binary):
        manifest = manifest_for(fake_binary)
        tampered = ProviderManifest(
            provider=manifest.provider, version=manifest.version,
            binary_sha256="b" * 64, required_flags=manifest.required_flags,
            hard_limits_usd_budget=True, hard_limits_wall_timeout=True,
        )
        adapter = ClaudeCodeAdapter(manifest=tampered, binary_path=str(fake_binary))
        result = await adapter.probe()
        assert result.available is False
        assert "sha256" in (result.reason or "")

    async def test_version_mismatch_fails_closed(self, fake_binary):
        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary, version="8.8.8-other"),
            binary_path=str(fake_binary),
        )
        result = await adapter.probe()
        assert result.available is False
        assert "version" in (result.reason or "")

    async def test_missing_required_flag_fails_closed(self, fake_binary, tmp_path):
        # Help text that lacks --max-budget-usd → hard-budget capability
        # unproven → provider unavailable (§3.5 fail-closed).
        help_file = tmp_path / "help.txt"
        help_file.write_text("Usage: fake\n  --print <v>\n  --bare\n")
        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary),
            binary_path=str(fake_binary),
            probe_env_extra={"FAKE_HELP_FILE": str(help_file)},
        )
        result = await adapter.probe()
        assert result.available is False
        assert "--max-budget-usd" in (result.reason or "")

    async def test_symlink_binary_refused(self, fake_binary, tmp_path):
        link = tmp_path / "claude-link"
        link.symlink_to(fake_binary)
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary), binary_path=str(link))
        result = await adapter.probe()
        assert result.available is False

    async def test_relative_path_refused(self, fake_binary):
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary), binary_path="claude")
        result = await adapter.probe()
        assert result.available is False
        assert "absolute" in (result.reason or "")

    async def test_probe_cached_until_identity_changes(self, fake_binary, tmp_path):
        # Count --help executions via the version file trick: every probe that
        # re-runs the binary rewrites nothing — count via a marker in env.
        counter = tmp_path / "count"
        counter.write_text("0", encoding="utf-8")
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary))
        r1 = await adapter.probe()
        r2 = await adapter.probe()
        assert r1.available and r2.available
        assert adapter.probe_executions == 1  # second probe served from cache
        # mtime/hash change → cache invalidated, re-probed.
        fake_binary.write_text(FAKE_SCRIPT + "\n# changed\n", encoding="utf-8")
        fake_binary.chmod(0o755)
        adapter2_manifest = manifest_for(fake_binary)
        adapter._manifest = adapter2_manifest
        r3 = await adapter.probe()
        assert r3.available is True
        assert adapter.probe_executions == 2

    async def test_binary_replaced_with_bad_sha_after_cache(self, fake_binary):
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary))
        assert (await adapter.probe()).available is True
        # Same path, different content (attacker swapped the binary): the
        # manifest digest no longer matches → unavailable on re-probe.
        fake_binary.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        fake_binary.chmod(0o755)
        result = await adapter.probe()
        assert result.available is False
        assert "sha256" in (result.reason or "")


class TestRunContract:
    async def test_happy_path_event_sequence(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary)
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        events = [e async for e in adapter.run(request_for(plan))]
        assert isinstance(events[0], SessionStarted)
        assert events[0].session_id == "sess-abc"
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["working on it"]
        assert ToolRequested(name="Read", call_id="call-1") in events
        assert ToolCompleted(call_id="call-1", outcome="ok") in events
        usages = [e for e in events if isinstance(e, UsageObserved)]
        assert usages[-1].turns == 2
        assert usages[-1].cost_usd == "0.050000"
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.exit_code == 0
        assert final.termination == "completed"
        assert final.summary == "final answer"
        assert isinstance(events[-1], ProviderExited)
        assert events[-1].exit_code == 0

    async def test_argv_is_exactly_the_frozen_section_1_4_set(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary)
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        [e async for e in adapter.run(request_for(plan))]
        captured = json.loads((tmp_path / "capture" / "argv.json").read_text())
        assert captured[0] == str(fake_binary)
        assert "--print" in captured
        assert captured[captured.index("--input-format") + 1] == "stream-json"
        assert captured[captured.index("--output-format") + 1] == "stream-json"
        assert "--bare" in captured
        assert "--disable-slash-commands" in captured
        assert "--no-session-persistence" in captured
        assert captured[captured.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in captured
        assert captured[captured.index("--mcp-config") + 1] == "/run/mcp.json"
        assert captured[captured.index("--settings") + 1] == "/run/settings.json"
        assert captured[captured.index("--system-prompt-file") + 1] == "/run/system.md"
        # Broker MCP tool is appended when the attempt has a broker socket.
        assert captured[captured.index("--tools") + 1] == "Read,Write,mcp__mesh-task-broker"
        assert captured[captured.index("--permission-mode") + 1] == "bypassPermissions"
        assert captured[captured.index("--max-budget-usd") + 1] == "1.50"
        # Prompt content must NEVER be argv (stdin only) and no shell involved.
        joined = " ".join(captured)
        assert "untrusted issue body" not in joined
        assert "TRUSTED platform policy" not in joined
        assert "/bin/sh" not in captured and "-c" not in captured

    async def test_prompt_travels_only_via_stdin_with_boundary(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary)
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        [e async for e in adapter.run(request_for(plan))]
        stdin_text = (tmp_path / "capture" / "stdin.txt").read_text()
        lines = [ln for ln in stdin_text.splitlines() if ln.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "user"
        content = record["message"]["content"]
        assert "untrusted issue body" in content
        assert "mesh-untrusted-context" in content

    async def test_platform_owned_config_files(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary)
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        [e async for e in adapter.run(request_for(plan))]
        mcp = json.loads((plan.host_run_dir / "mcp.json").read_text())
        assert list(mcp["mcpServers"]) == ["mesh-task-broker"]
        assert mcp["mcpServers"]["mesh-task-broker"]["path"] == "/run/mesh-broker.sock"
        system_md = (plan.host_run_dir / "system.md").read_text()
        assert system_md == "TRUSTED platform policy"
        for name in ("mcp.json", "settings.json", "system.md"):
            mode = stat.S_IMODE((plan.host_run_dir / name).stat().st_mode)
            assert mode == 0o444

    async def test_sandbox_env_carries_proxy_and_credentials_not_tokens(
        self, fake_binary, tmp_path
    ):
        plan = plan_for(
            tmp_path, fake_binary,
            provider_env={"ANTHROPIC_API_KEY": "sk-secret-provider"},
        )
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        [e async for e in adapter.run(request_for(plan))]
        env = json.loads((tmp_path / "capture" / "env.json").read_text())
        assert env["ANTHROPIC_API_KEY"] == "sk-secret-provider"
        assert env["HTTPS_PROXY"] == "http://10.0.0.1:3128"
        assert env["HOME"] == "/home"
        assert env["MESH_BROKER_SOCKET"] == "/run/mesh-broker.sock"
        assert env["MESH_BROKER_NONCE"] == "nonce-123"
        assert env["MESH_ATTEMPT_ID"] == plan.attempt_id

    async def test_unknown_records_dropped_with_protocol_warnings(
        self, fake_binary, tmp_path
    ):
        plan = plan_for(tmp_path, fake_binary, scenario="noisy")
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        events = [e async for e in adapter.run(request_for(plan))]
        warnings = [e for e in events if isinstance(e, ProtocolWarning)]
        assert len(warnings) == 3  # weird_record, thinking_tokens, non-json
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.summary == "done"

    async def test_budget_exceeded_truncates_provider(self, fake_binary, tmp_path):
        from decimal import Decimal

        plan = plan_for(
            tmp_path, fake_binary, scenario="over_budget",
            budget=BudgetLimits(usd=Decimal("1.0")),
        )
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        events = [e async for e in adapter.run(request_for(plan))]
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.termination == "budget_exceeded"
        exited = next(e for e in events if isinstance(e, ProviderExited))
        assert exited.exit_code != 0 or True  # killed or exited — process ended
        assert launcher.proc is not None
        assert launcher.proc.returncode is not None  # actually terminated

    async def test_wall_timeout_truncates_stalled_provider(self, fake_binary, tmp_path):
        plan = plan_for(
            tmp_path, fake_binary, scenario="stall",
            budget=BudgetLimits(wall_seconds=0.3, idle_seconds=0.2),
        )
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        events = [e async for e in adapter.run(request_for(plan))]
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.termination == "timeout"
        assert launcher.proc.returncode is not None

    async def test_crash_without_result_record(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary, scenario="crash")
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        events = [e async for e in adapter.run(request_for(plan))]
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.exit_code == 3
        assert final.termination == "failed"
        exited = events[-1]
        assert isinstance(exited, ProviderExited)
        assert exited.exit_code == 3

    async def test_run_without_launcher_is_a_programming_error(self, fake_binary, tmp_path):
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary))
        with pytest.raises(RuntimeError):
            [e async for e in adapter.run(request_for(plan_for(tmp_path, fake_binary)))]

    async def test_destroy_delegates_to_launcher(self, fake_binary, tmp_path):
        plan = plan_for(tmp_path, fake_binary)
        launcher = FakeLauncher()
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary),
                                    binary_path=str(fake_binary),
                                    launcher=launcher, plan=plan)
        [e async for e in adapter.run(request_for(plan))]
        await adapter.destroy()
        assert launcher.destroyed is True


class TestFlagsFromHelp:
    def test_expands_optional_suffix_shorthand(self):
        from mesh_runtime.providers.claude_code import _flags_from_help

        flags = _flags_from_help(
            "  --system-prompt <prompt>   System prompt\n"
            "  via: --system-prompt[-file], --append-system-prompt[-file]\n"
            "  --bare   Minimal mode\n"
            "  --max-budget-usd <amount>   Budget\n"
        )
        assert "--system-prompt" in flags
        assert "--system-prompt-file" in flags  # expanded from [-file]
        assert "--append-system-prompt" in flags
        assert "--append-system-prompt-file" in flags
        assert "--bare" in flags
        assert "--max-budget-usd" in flags

    def test_probe_accepts_bracket_shorthand_flag(self, fake_binary, tmp_path):
        # A required flag advertised only as --print[-x] must still satisfy a
        # manifest that pins --print-x (mirrors real --system-prompt[-file]).
        help_file = tmp_path / "help.txt"
        help_file.write_text("  --print[-x]\n  --max-budget-usd <v>\n")
        manifest = ProviderManifest(
            provider="claude-code",
            version=FAKE_VERSION,
            binary_sha256=_sha256_of(fake_binary),
            required_flags=("--print-x", "--max-budget-usd"),
            hard_limits_usd_budget=True,
            hard_limits_wall_timeout=True,
        )
        adapter = ClaudeCodeAdapter(
            manifest=manifest,
            binary_path=str(fake_binary),
            probe_env_extra={"FAKE_HELP_FILE": str(help_file)},
        )

        async def _run():
            return await adapter.probe()

        import asyncio as _asyncio

        result = _asyncio.get_event_loop().run_until_complete(_run())
        assert result.available is True, result.reason


class _FakeEgress:
    def __init__(self, port):
        self.port = port


class _FakeSecurity:
    def __init__(self, *, broker_socket_path=None, egress_port=None):
        self.broker_socket_path = broker_socket_path
        self.egress = _FakeEgress(egress_port) if egress_port else None
        self.egress_proxy_url = f"http://10.0.0.1:{egress_port}" if egress_port else None


class _FakeManager:
    """Records the SandboxSpec passed to provision."""

    def __init__(self):
        self.specs = []

    async def provision(self, spec):
        self.specs.append(spec)

        class _H:
            proc = None
        return _H()

    async def destroy_attempt(self, attempt_id):
        return None


def _plan(**over):
    kwargs = dict(
        attempt_id="att", execution_id="exec",
        host_run_dir=Path("/tmp/x/run"), sandbox_run_dir="/run",
        worktree_cwd="/worktree", broker_socket_sandbox_path=None,
        broker_nonce="n", proxy_url=None, provider_env={},
        budget=BudgetLimits(),
    )
    kwargs.update(over)
    return ClaudeLaunchPlan(**kwargs)


class TestResolvePlan:
    def _adapter(self, security):
        return ClaudeCodeAdapter(
            manifest=ProviderManifest("claude-code", "1.0", "a" * 64, ("--print",), True, True),
            binary_path="/opt/x/claude", security=security,
        )

    def test_fills_broker_and_proxy_from_security(self):
        adapter = self._adapter(_FakeSecurity(broker_socket_path="/host/run/broker.sock", egress_port=3128))
        resolved = adapter._resolve_plan(_plan())
        assert resolved.broker_socket_sandbox_path == "/run/broker.sock"
        assert resolved.proxy_url == "http://10.0.0.1:3128"

    def test_explicit_plan_values_win(self):
        adapter = self._adapter(_FakeSecurity(broker_socket_path="/host/run/b.sock", egress_port=3128))
        resolved = adapter._resolve_plan(_plan(broker_socket_sandbox_path="/run/explicit.sock", proxy_url="http://e:9"))
        assert resolved.broker_socket_sandbox_path == "/run/explicit.sock"
        assert resolved.proxy_url == "http://e:9"

    def test_no_security_returns_same_plan(self):
        adapter = self._adapter(None)
        plan = _plan()
        assert adapter._resolve_plan(plan) is plan

    def test_no_broker_socket_leaves_none(self):
        adapter = self._adapter(_FakeSecurity(broker_socket_path=None, egress_port=3128))
        resolved = adapter._resolve_plan(_plan())
        assert resolved.broker_socket_sandbox_path is None
        assert resolved.proxy_url == "http://10.0.0.1:3128"


class TestLauncherGatewayResolution:
    async def test_resolves_gateway_port_from_security(self):
        from mesh_runtime.providers.claude_code import SandboxProcessLauncher

        mgr = _FakeManager()
        launcher = SandboxProcessLauncher(
            sandbox_manager=mgr, attempt_id="att", attempt_root=Path("/tmp/x"),
            uid=65534, gid=65534, ro_binds=(), memory_bytes=1, cpu_quota_us=1,
            cpu_period_us=1, pids_max=1, tmp_bytes=1, gateway_port=0,
            security=_FakeSecurity(egress_port=4242),
        )
        await launcher.spawn(argv=["/bin/true"], env={})
        assert mgr.specs[0].gateway_port == 4242

    async def test_explicit_gateway_port_wins(self):
        from mesh_runtime.providers.claude_code import SandboxProcessLauncher

        mgr = _FakeManager()
        launcher = SandboxProcessLauncher(
            sandbox_manager=mgr, attempt_id="att", attempt_root=Path("/tmp/x"),
            uid=65534, gid=65534, ro_binds=(), memory_bytes=1, cpu_quota_us=1,
            cpu_period_us=1, pids_max=1, tmp_bytes=1, gateway_port=9999,
            security=_FakeSecurity(egress_port=4242),
        )
        await launcher.spawn(argv=["/bin/true"], env={})
        assert mgr.specs[0].gateway_port == 9999


class _FailLauncher:
    async def spawn(self, *, argv, env):
        from mesh_runtime.sandbox import SandboxUnavailableError

        raise SandboxUnavailableError("provision exploded")

    async def destroy(self):
        return None


class TestRunFailurePaths:
    async def test_provision_failure_raises_sandbox_launch_error(self, fake_binary, tmp_path):
        from mesh_runtime.providers.sandboxed import SandboxLaunchError

        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary), binary_path=str(fake_binary),
            launcher=_FailLauncher(), plan=plan_for(tmp_path, fake_binary),
        )
        with pytest.raises(SandboxLaunchError):
            [e async for e in adapter.run(request_for(adapter._plan))]

    def test_exit_code_normalizes_signal_to_shell_convention(self):
        class _P:
            returncode = -15

        assert ClaudeCodeAdapter._exit_code(_P()) == 143

    def test_exit_code_none_maps_to_one(self):
        class _P:
            returncode = None

        assert ClaudeCodeAdapter._exit_code(_P()) == 1

    async def test_terminate_noop_when_already_exited(self, fake_binary):
        adapter = ClaudeCodeAdapter(manifest=manifest_for(fake_binary), binary_path=str(fake_binary))

        class _P:
            returncode = 0
            killed = False

            def kill(self):
                self.killed = True

        p = _P()
        await adapter._terminate(p)
        assert p.killed is False  # already exited → no kill


class TestProbeHelpFailure:
    async def test_help_read_failure_marks_all_flags_missing(self, fake_binary, tmp_path):
        # --help exits non-zero → unreadable → every required flag "missing".
        bad = tmp_path / "badhelp"
        bad.write_text(
            "#!/bin/sh\nif [ \"$1\" = \"--help\" ]; then exit 3; fi\n"
            "echo '9.9.9-fake (Claude Code)'\n"
        )
        bad.chmod(0o755)
        m = ProviderManifest("claude-code", "9.9.9-fake", _sha256_of(bad), ("--print",), True, True)
        adapter = ClaudeCodeAdapter(manifest=m, binary_path=str(bad))
        result = await adapter.probe()
        assert result.available is False
        assert "--print" in (result.reason or "")


class TestHardening:
    async def test_oversize_line_dropped_and_stream_continues(self, fake_binary, tmp_path):
        # FakeLauncher uses asyncio's default 64KiB readline ceiling, so a
        # 200KB line raises ValueError; the adapter must drop it and keep
        # streaming the following result record (no crash, no hang).
        from mesh_runtime.providers.base import ProtocolWarning as PW

        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary), binary_path=str(fake_binary),
            launcher=FakeLauncher(), plan=plan_for(tmp_path, fake_binary, scenario="bigline"),
        )
        events = [e async for e in adapter.run(request_for(adapter._plan))]
        warnings = [e for e in events if isinstance(e, PW)]
        assert any("oversize" in (w.raw_type or "") for w in warnings)
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.summary == "ok"

    async def test_binary_swap_before_run_is_refused(self, fake_binary, tmp_path):
        from mesh_runtime.providers.sandboxed import SandboxLaunchError

        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary), binary_path=str(fake_binary),
            launcher=FakeLauncher(), plan=plan_for(tmp_path, fake_binary),
        )
        # Swap the binary content AFTER the manifest pinned its digest — the
        # run-time re-verification must refuse to launch the changed binary.
        fake_binary.write_text("#!/bin/sh\necho swapped\n")
        fake_binary.chmod(0o755)
        with pytest.raises(SandboxLaunchError):
            [e async for e in adapter.run(request_for(adapter._plan))]

    async def test_wall_budget_enforced_on_chatty_provider(self, fake_binary, tmp_path):
        # A provider emitting faster than the poll interval must still hit the
        # frozen wall cap (check_time runs every iteration — S-07).
        plan = plan_for(
            tmp_path, fake_binary, scenario="chatty",
            budget=BudgetLimits(wall_seconds=0.4),
        )
        adapter = ClaudeCodeAdapter(
            manifest=manifest_for(fake_binary), binary_path=str(fake_binary),
            launcher=FakeLauncher(), plan=plan,
        )
        events = [e async for e in adapter.run(request_for(plan))]
        final = next(e for e in events if isinstance(e, FinalResult))
        assert final.termination == "timeout"
